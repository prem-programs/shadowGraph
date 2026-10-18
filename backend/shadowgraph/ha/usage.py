"""Find where a consumer's code uses Home Assistant core names, and when that code runs.

Each use records the exact file and line, plus a context that decides what a removal would do:
  load        runs on import (module level or class body): a missing name fails the import
  runtime     inside a function: fails when that code path runs
  shimmed     inside try/except ImportError (or similar): the author already has a fallback
  conditional inside a module-level `if` (often a version check)
  type        annotation or `if TYPE_CHECKING:` only: no runtime effect (annotations are lazy in 3.14)
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field

from .pyparse import parse
from .surface import GUARD_EXCEPTIONS, _is_type_checking, dotted

CONTEXTS = ("load", "runtime", "shimmed", "conditional", "type")


@dataclass
class Use:
    module: str
    name: str
    line: int
    context: str
    how: str  # import | attr

    def key(self) -> str:
        return f"{self.module}.{self.name}"


@dataclass
class FileScan:
    uses: list[Use] = field(default_factory=list)
    local_imports: list[str] = field(default_factory=list)  # module-level imports of sibling modules
    parsed: bool = True


def _guards(node: ast.Try) -> bool:
    for h in node.handlers:
        if h.type is None:
            return True
        types = h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]
        if any((dotted(t) or "").rsplit(".", 1)[-1] in GUARD_EXCEPTIONS for t in types):
            return True
    return False


class _Scanner:
    def __init__(self, known_modules: set[str], package_parts: list[str]) -> None:
        self.known = known_modules
        self.pkg = package_parts  # this file's package, relative to the integration root
        self.aliases: dict[str, str] = {}
        self.out = FileScan()

    # -- context bookkeeping ---------------------------------------------------------------------
    def visit(self, node: ast.AST, ctx: tuple[str, ...], shadow: frozenset[str] = frozenset()) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in node.decorator_list:
                self.visit(d, ctx, shadow)
            for default in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
                self.visit(default, ctx, shadow)
            for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs + [node.args.vararg, node.args.kwarg]:
                if a is not None and a.annotation is not None:
                    self.visit(a.annotation, ctx + ("type",), shadow)
            if node.returns is not None:
                self.visit(node.returns, ctx + ("type",), shadow)
            inner = shadow | _local_bindings(node)
            for st in node.body:
                self.visit(st, ctx + ("runtime",), inner)
            return
        if isinstance(node, ast.Lambda):
            args = node.args
            names = {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs}
            self.visit(node.body, ctx + ("runtime",), shadow | names)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            names = set()
            for g in node.generators:
                names |= _target_names(g.target)
            inner = shadow | names
            for g in node.generators:
                self.visit(g.iter, ctx, inner)
                for cond in g.ifs:
                    self.visit(cond, ctx, inner)
            for part in ([node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]):
                self.visit(part, ctx, inner)
            return
        if isinstance(node, ast.AnnAssign):
            self.visit(node.annotation, ctx + ("type",), shadow)
            if node.value is not None:
                self.visit(node.value, ctx, shadow)
            self.visit(node.target, ctx, shadow)
            return
        if isinstance(node, ast.If):
            inner = ctx + ("type",) if _is_type_checking(node.test) else ctx + ("conditional",)
            self.visit(node.test, ctx, shadow)
            for st in node.body:
                self.visit(st, inner, shadow)
            for st in node.orelse:
                self.visit(st, ctx + ("conditional",), shadow)
            return
        if isinstance(node, ast.Try) or type(node).__name__ == "TryStar":
            guarded = _guards(node)
            inner = ctx + ("shimmed",) if guarded else ctx
            for st in node.body:
                self.visit(st, inner, shadow)
            for h in node.handlers:
                for st in h.body:
                    self.visit(st, inner, shadow)
            for st in node.orelse + node.finalbody:
                self.visit(st, ctx, shadow)
            return
        if isinstance(node, ast.ImportFrom):
            self._import_from(node, ctx)
            return
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("homeassistant.") or a.name == "homeassistant":
                    if a.asname:
                        self.aliases[a.asname] = a.name
                    elif a.name in self.known:
                        # `import homeassistant.helpers.entity` binds `homeassistant`; resolved in _attr
                        self.aliases.setdefault("homeassistant", "homeassistant")
            return
        if isinstance(node, ast.Attribute):
            if self._attr(node, ctx, shadow):
                return
        for child in ast.iter_child_nodes(node):
            self.visit(child, ctx, shadow)

    @staticmethod
    def context(ctx: tuple[str, ...]) -> str:
        if "type" in ctx:
            return "type"
        for c in reversed(ctx):  # innermost decides
            if c in ("runtime", "shimmed", "conditional"):
                return c
        return "load"

    # -- uses -----------------------------------------------------------------------------------
    def _import_from(self, node: ast.ImportFrom, ctx: tuple[str, ...]) -> None:
        here = self.context(ctx)
        if node.level:  # relative import inside the consumer package
            if here == "load" and ("runtime" not in ctx):
                base = self.pkg[: len(self.pkg) - (node.level - 1)] if node.level > 1 else list(self.pkg)
                if node.module:
                    self.out.local_imports.append("/".join(base + node.module.split(".")))
                else:
                    self.out.local_imports += ["/".join(base + [a.name]) for a in node.names]
            return
        mod = node.module or ""
        if not (mod == "homeassistant" or mod.startswith("homeassistant.")):
            return
        for a in node.names:
            if a.name == "*":
                continue
            full = f"{mod}.{a.name}"
            if full in self.known:
                self.aliases[a.asname or a.name] = full  # imported a module, uses come as attributes
            else:
                self.aliases[a.asname or a.name] = f"@{full}"  # a symbol (class/function/constant)
                self.out.uses.append(Use(mod, a.name, node.lineno, here, "import"))

    def _attr(self, node: ast.Attribute, ctx: tuple[str, ...], shadow: frozenset[str]) -> bool:
        chain = dotted(node)
        if not chain:
            return False
        parts = chain.split(".")
        if parts[0] in shadow:
            return False  # a local variable that happens to share the alias's name
        target = self.aliases.get(parts[0])
        if target is None:
            return False
        here = self.context(ctx)
        if target.startswith("@"):  # Class.member (only for names that look like classes)
            sym = target[1:]
            mod, cls = sym.rsplit(".", 1)
            if len(parts) >= 2 and _looks_like_class(cls) and not parts[1].startswith("_"):
                self.out.uses.append(Use(mod, f"{cls}.{parts[1]}", node.lineno, here, "attr"))
                return True
            return False
        mod = target
        i = 1
        while i < len(parts) and f"{mod}.{parts[i]}" in self.known:
            mod = f"{mod}.{parts[i]}"
            i += 1
        if i < len(parts) and mod != "homeassistant":
            name = parts[i]
            if name.startswith("_") and not (name.startswith("__") and name.endswith("__")):
                return True  # private name: not API (still a smell, but not ours to report)
            self.out.uses.append(Use(mod, name, node.lineno, here, "attr"))
            if i + 1 < len(parts) and _looks_like_class(name) and not parts[i + 1].startswith("_"):
                self.out.uses.append(Use(mod, f"{name}.{parts[i + 1]}", node.lineno, here, "attr"))
        return True


def _target_names(t: ast.AST) -> set[str]:
    out: set[str] = set()
    for n in ast.walk(t):
        if isinstance(n, ast.Name):
            out.add(n.id)
    return out


def _local_bindings(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> frozenset[str]:
    """Names a function binds locally (parameters, assignments, loop/with/except targets),
    excluding names it imports from homeassistant (those are aliases, not shadows)."""
    a = fn.args
    names = {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs}
    names |= {x.arg for x in (a.vararg, a.kwarg) if x is not None}
    declared_global: set[str] = set()
    stack = list(fn.body)
    while stack:
        st = stack.pop()
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(st.name)
            continue  # nested scopes bind their own names
        if isinstance(st, (ast.Global, ast.Nonlocal)):
            declared_global |= set(st.names)
        elif isinstance(st, ast.Assign):
            for t in st.targets:
                names |= _target_names(t)
        elif isinstance(st, (ast.AnnAssign, ast.AugAssign)):
            names |= _target_names(st.target)
        elif isinstance(st, (ast.For, ast.AsyncFor)):
            names |= _target_names(st.target)
        elif isinstance(st, (ast.With, ast.AsyncWith)):
            for item in st.items:
                if item.optional_vars is not None:
                    names |= _target_names(item.optional_vars)
        elif isinstance(st, ast.ExceptHandler) and st.name:
            names.add(st.name)
        elif isinstance(st, ast.Import):
            names |= {(x.asname or x.name).split(".")[0] for x in st.names if not x.name.startswith("homeassistant")}
        elif isinstance(st, ast.ImportFrom):
            if not (st.module or "").startswith("homeassistant"):
                names |= {x.asname or x.name for x in st.names}
        for child in ast.iter_child_nodes(st):
            if isinstance(child, ast.NamedExpr):
                names |= _target_names(child.target)
            if isinstance(child, (ast.stmt, ast.ExceptHandler)):
                stack.append(child)
            elif isinstance(child, ast.expr):
                for sub in ast.walk(child):
                    if isinstance(sub, ast.NamedExpr):
                        names |= _target_names(sub.target)
    return frozenset(names - declared_global)


def _looks_like_class(name: str) -> bool:
    """CapWords (SensorDeviceClass), not CONSTANTS (PLATFORM_SCHEMA) and not functions."""
    return name[:1].isupper() and not name.isupper() and "_" not in name


def scan_source(source: str, known_modules: set[str], package_parts: list[str]) -> FileScan:
    tree = parse(source)
    if tree is None:
        return FileScan(parsed=False)
    s = _Scanner(known_modules, package_parts)
    for st in tree.body:
        s.visit(st, ())
    # one record per (name, line, context): attribute chains can be visited twice
    seen: set[tuple] = set()
    uniq = []
    for u in s.out.uses:
        k = (u.module, u.name, u.line, u.context)
        if k not in seen:
            seen.add(k)
            uniq.append(u)
    s.out.uses = uniq
    return s.out
