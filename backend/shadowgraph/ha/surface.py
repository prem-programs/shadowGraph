"""The public surface of a Python module: what another package can import from it, and what is deprecated.

Everything here is static analysis of one file at a time (no code is imported or executed). The Resolver
then answers "does `module.name` exist?" across modules, following re-exports, aliases and base classes.
It answers "unknown" whenever static analysis cannot prove the answer (third-party bases, star imports,
custom module __getattr__), so a missing name is only reported when we are sure.
"""
from __future__ import annotations

import ast
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from .pyparse import parse

DEPRECATION_CALLS = {"DeprecatedConstant", "DeprecatedConstantEnum", "DeprecatedAlias"}
DEPRECATION_DECORATORS = {"deprecated_class", "deprecated_function"}
GUARD_EXCEPTIONS = {"ImportError", "ModuleNotFoundError", "AttributeError", "Exception", "BaseException"}
SKIP_DUNDERS = {"__all__", "__getattr__", "__dir__", "__path__", "__file__", "__name__", "__doc__", "__spec__"}
EXISTS, MISSING, UNKNOWN = "exists", "missing", "unknown"
IMPLICIT_MODULE_ATTRS = {"__path__", "__file__", "__name__", "__doc__", "__spec__", "__package__", "__loader__"}
STDLIB_NO_MEMBERS = {"enum", "typing", "typing_extensions", "abc", "dataclasses", "collections"}


@dataclass
class Export:
    name: str
    kind: str                      # class | function | constant | reexport | member | alias
    line: int
    origin: str | None = None      # re-export: the module it came from (may be relative, e.g. ".const")
    origin_name: str | None = None  # re-export: the name in that module
    alias_of: str | None = None    # `Platform = EntityPlatforms`: the name it points to
    bases: list[str] | None = None  # classes: base class expressions as written
    in_all: bool = False           # listed in __all__ (an intentional export)
    private: bool = False          # leading underscore: importable, but not meant as API
    deprecated: dict[str, Any] | None = None   # {"breaks_in", "replacement", "how"}
    params: list[list[Any]] | None = None      # functions: [[name, kind, has_default], ...]

    def public(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, False, [])}


@dataclass
class Surface:
    module: str
    exports: dict[str, Export] = field(default_factory=dict)
    parsed: bool = True
    star_imports: list[str] = field(default_factory=list)  # origins of `from x import *`
    custom_getattr: bool = False   # a module-level `def __getattr__` (lazy names we cannot see)


def module_name(path: str) -> str:
    """'homeassistant/components/sensor/__init__.py' -> 'homeassistant.components.sensor'."""
    mod = path[:-3].replace("/", ".") if path.endswith(".py") else path.replace("/", ".")
    return mod[: -len(".__init__")] if mod.endswith(".__init__") else mod


def dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    if isinstance(node, ast.Subscript):  # Generic[T] -> Generic
        return dotted(node.value)
    return None


def _is_type_checking(test: ast.AST) -> bool:
    return dotted(test) in ("TYPE_CHECKING", "typing.TYPE_CHECKING")


def runtime_statements(body: list[ast.stmt]):
    """Module-level statements that run on import (skips `if TYPE_CHECKING:` bodies)."""
    for st in body:
        if isinstance(st, ast.If):
            if not _is_type_checking(st.test):
                yield from runtime_statements(st.body)
            yield from runtime_statements(st.orelse)
        elif isinstance(st, ast.Try) or type(st).__name__ == "TryStar":
            yield from runtime_statements(st.body)
            for h in st.handlers:
                yield from runtime_statements(h.body)
            yield from runtime_statements(st.orelse)
            yield from runtime_statements(st.finalbody)
        else:
            yield st


def _deprecation_from_call(call: ast.Call) -> dict[str, Any] | None:
    fn = (dotted(call.func) or "").rsplit(".", 1)[-1]
    if fn not in DEPRECATION_CALLS:
        return None
    kw = {k.arg: k.value for k in call.keywords if k.arg}
    breaks = kw.get("breaks_in_ha_version")
    breaks_s = breaks.value if isinstance(breaks, ast.Constant) else None
    if fn == "DeprecatedConstantEnum":
        replacement = dotted(call.args[0]) if call.args else None
        if breaks_s is None and len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
            breaks_s = call.args[1].value
    else:
        repl = call.args[1] if len(call.args) > 1 else kw.get("replacement")
        replacement = repl.value if isinstance(repl, ast.Constant) and isinstance(repl.value, str) else None
        if breaks_s is None and len(call.args) > 2 and isinstance(call.args[2], ast.Constant):
            breaks_s = call.args[2].value
    return {"how": fn, "replacement": replacement, "breaks_in": breaks_s}


def _deprecation_from_decorators(decorators: list[ast.expr]) -> dict[str, Any] | None:
    for d in decorators:
        if not isinstance(d, ast.Call):
            continue
        fn = (dotted(d.func) or "").rsplit(".", 1)[-1]
        if fn not in DEPRECATION_DECORATORS:
            continue
        kw = {k.arg: k.value for k in d.keywords if k.arg}
        breaks = kw.get("breaks_in_ha_version")
        repl = d.args[0] if d.args else kw.get("replacement")
        return {"how": fn,
                "replacement": repl.value if isinstance(repl, ast.Constant) else None,
                "breaks_in": breaks.value if isinstance(breaks, ast.Constant) else None}
    return None


def _params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[list[Any]]:
    a = fn.args
    out: list[list[Any]] = []
    positional = a.posonlyargs + a.args
    n_def = len(a.defaults)
    for i, arg in enumerate(positional):
        kind = "posonly" if i < len(a.posonlyargs) else "pos"
        out.append([arg.arg, kind, i >= len(positional) - n_def])
    if a.vararg:
        out.append([a.vararg.arg, "vararg", True])
    for arg, default in zip(a.kwonlyargs, a.kw_defaults):
        out.append([arg.arg, "kwonly", default is not None])
    if a.kwarg:
        out.append([a.kwarg.arg, "kwarg", True])
    return out


def _all_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for st in tree.body:
        if isinstance(st, ast.Assign):
            targets = st.targets
        elif isinstance(st, (ast.AnnAssign, ast.AugAssign)):
            targets = [st.target]
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets) and st.value is not None:
            try:
                value = ast.literal_eval(st.value)
            except (ValueError, SyntaxError, TypeError):
                continue
            if isinstance(value, (list, tuple)):
                names.update(v for v in value if isinstance(v, str))
    return names


def _class_members(cls: ast.ClassDef, out: dict[str, Export]) -> None:
    def add(n: str, line: int, kind: str = "member", params=None, dep=None) -> None:
        if not n.startswith("_"):
            out[f"{cls.name}.{n}"] = Export(name=f"{cls.name}.{n}", kind=kind, line=line, params=params, deprecated=dep)

    for st in cls.body:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(st.name, st.lineno, params=_params(st), dep=_deprecation_from_decorators(st.decorator_list))
        elif isinstance(st, ast.Assign):
            for t in st.targets:
                if isinstance(t, ast.Name):
                    add(t.id, st.lineno)
                elif isinstance(t, ast.Tuple):
                    for e in t.elts:
                        if isinstance(e, ast.Name):
                            add(e.id, st.lineno)
        elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
            add(st.target.id, st.lineno)
        elif isinstance(st, ast.ClassDef):  # nested class
            add(st.name, st.lineno)
        elif isinstance(st, ast.If):  # members defined conditionally still exist on some path
            sub = ast.ClassDef(name=cls.name, bases=[], keywords=[], body=st.body + st.orelse,
                               decorator_list=[], type_params=[])
            _class_members(sub, out)


def module_surface(source: str, module: str, members: bool = True) -> Surface:
    tree = parse(source, module)
    surf = Surface(module=module)
    if tree is None:
        surf.parsed = False
        return surf
    in_all = _all_names(tree)
    ex = surf.exports
    for st in runtime_statements(tree.body):
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if st.name == "__getattr__":
                surf.custom_getattr = True
                continue
            ex[st.name] = Export(st.name, "function", st.lineno, params=_params(st),
                                 deprecated=_deprecation_from_decorators(st.decorator_list))
        elif isinstance(st, ast.ClassDef):
            ex[st.name] = Export(st.name, "class", st.lineno,
                                 bases=[b for b in (dotted(x) for x in st.bases) if b],
                                 deprecated=_deprecation_from_decorators(st.decorator_list))
            if members:
                _class_members(st, ex)
        elif type(st).__name__ == "TypeAlias":  # PEP 695: `type ConfigType = dict[str, Any]`
            name = getattr(st.name, "id", None)
            if name:
                ex[name] = Export(name, "alias", st.lineno)
        elif isinstance(st, (ast.Assign, ast.AnnAssign)):
            targets = st.targets if isinstance(st, ast.Assign) else [st.target]
            flat = []
            for t in targets:
                flat += t.elts if isinstance(t, ast.Tuple) else [t]
            for t in flat:
                if not isinstance(t, ast.Name):
                    continue
                name = t.id
                if name == "__getattr__":
                    # `__getattr__ = partial(check_if_deprecated_constant, ...)` only serves _DEPRECATED_ names
                    if not (isinstance(st.value, ast.Call) and "check_if_deprecated_constant" in ast.dump(st.value)):
                        surf.custom_getattr = True
                    continue
                dep = None
                if name.startswith("_DEPRECATED_"):
                    name = name[len("_DEPRECATED_"):]
                    if isinstance(st.value, ast.Call):
                        dep = _deprecation_from_call(st.value)
                    dep = dep or {"how": "DeprecatedConstant", "replacement": None, "breaks_in": None}
                alias = dotted(st.value) if isinstance(st.value, (ast.Name, ast.Attribute)) else None
                ex[name] = Export(name, "constant", st.lineno, deprecated=dep, alias_of=alias)
        elif isinstance(st, ast.ImportFrom):
            origin = ("." * st.level) + (st.module or "")
            for a in st.names:
                if a.name == "*":
                    surf.star_imports.append(origin)
                    continue
                n = a.asname or a.name
                ex.setdefault(n, Export(n, "reexport", st.lineno, origin=origin, origin_name=a.name))
        elif isinstance(st, ast.Import):
            for a in st.names:
                if a.asname:
                    ex.setdefault(a.asname, Export(a.asname, "reexport", st.lineno, origin=a.name, origin_name=None))
                else:
                    top = a.name.split(".")[0]
                    ex.setdefault(top, Export(top, "reexport", st.lineno, origin=top, origin_name=None))
    for name in list(ex):
        base = name.split(".")[0]
        if base in SKIP_DUNDERS:
            del ex[name]
        elif base.startswith("_") and not (base.startswith("__") and base.endswith("__")):
            ex[name].private = True
    for name in in_all:
        if name in ex:
            ex[name].in_all = True
    return surf


def abs_module(mod: str, is_package: bool, origin: str) -> str:
    """Resolve a relative import origin ('.const', '..helpers') against the importing module."""
    if not origin.startswith("."):
        return origin
    level = len(origin) - len(origin.lstrip("."))
    parts = mod.split(".")
    base = parts if is_package else parts[:-1]
    base = base[: len(base) - (level - 1)] if level > 1 else base
    rest = origin.lstrip(".")
    return ".".join(base + ([rest] if rest else []))


class Resolver:
    """Existence of `module.name` (or `module.Class.member`) with re-exports, aliases and bases followed.

    get_surface(module) returns a Surface, or None when the module is not part of the codebase
    (a third-party package: we can never prove a name missing there).
    is_package(module) says whether the module is a package __init__ (for relative imports).
    """

    def __init__(self, get_surface: Callable[[str], Surface | None], is_package: Callable[[str], bool]) -> None:
        self.get = get_surface
        self.is_package = is_package

    def _origin(self, mod: str, e: Export) -> str:
        return abs_module(mod, self.is_package(mod), e.origin or "")

    def lookup(self, mod: str, name: str, depth: int = 0) -> tuple[str, Export | None, str]:
        """Return (state, export, defining module) for a plain top-level name."""
        if depth > 8:
            return UNKNOWN, None, mod
        surf = self.get(mod)
        if surf is None:
            return UNKNOWN, None, mod  # third-party module
        if not surf.parsed:
            return UNKNOWN, None, mod
        if name in IMPLICIT_MODULE_ATTRS:
            return EXISTS, Export(name, "module-attribute", 0), mod
        e = surf.exports.get(name)
        if e is not None:
            return EXISTS, e, mod
        # a submodule is also importable as an attribute of its package (`from x import submodule`)
        if self.get(f"{mod}.{name}") is not None:
            return EXISTS, Export(name, "module", 0), f"{mod}.{name}"
        for star in surf.star_imports:
            state, se, smod = self.lookup(abs_module(mod, self.is_package(mod), star), name, depth + 1)
            if state != MISSING:
                return state, se, smod
        if surf.custom_getattr:
            return UNKNOWN, None, mod
        return MISSING, None, mod

    def _class(self, mod: str, name: str, depth: int = 0) -> tuple[str, Export | None, str]:
        """Find where a class is defined, following re-exports and simple aliases."""
        state, e, where = self.lookup(mod, name, depth)
        if state != EXISTS or e is None or depth > 8:
            return state, e, where
        if e.kind == "reexport":
            origin = self._origin(where, e)
            if e.origin_name is None:
                return UNKNOWN, e, where  # a module bound by `import x`, not a class
            return self._class(origin, e.origin_name, depth + 1)
        if e.kind == "constant" and e.alias_of and "." not in e.alias_of:
            return self._class(where, e.alias_of, depth + 1)
        return state, e, where

    def member(self, mod: str, cls_name: str, member: str, depth: int = 0) -> str:
        state, c, where = self._class(mod, cls_name)
        if state != EXISTS or c is None:
            return state
        if c.kind != "class":
            return UNKNOWN  # an instance, a module or something we do not model
        surf = self.get(where)
        if surf and f"{c.name}.{member}" in surf.exports:
            return EXISTS
        if depth > 6:
            return UNKNOWN
        verdict = MISSING
        for base in c.bases or []:
            if base in ("object",):
                continue
            head, _, rest = base.partition(".")
            if not rest:
                st, be, _bw = self.lookup(where, head)
                if st == MISSING:
                    continue  # a builtin (Exception, dict, ...): no class-level members anyone imports
                if st == EXISTS and be is not None and be.kind == "reexport" and \
                        (be.origin or "").split(".")[0] in STDLIB_NO_MEMBERS:
                    continue  # Enum, StrEnum, Generic, Protocol, ABC, ... add no public class attributes
            if rest:  # module.Class written in the base list
                st, be, bwhere = self.lookup(where, head)
                if st != EXISTS or be is None or be.kind != "reexport":
                    return UNKNOWN
                r = self.member(self._origin(bwhere, be), rest.split(".")[-1], member, depth + 1) \
                    if self.get(self._origin(bwhere, be)) else UNKNOWN
            else:
                r = self.member(where, head, member, depth + 1)
            if r == EXISTS:
                return EXISTS
            if r == UNKNOWN:
                verdict = UNKNOWN
        return verdict

    def state(self, mod: str, name: str) -> str:
        if "." in name:
            cls, member = name.split(".", 1)
            return self.member(mod, cls, member.split(".")[0])
        return self.lookup(mod, name)[0]

    def export(self, mod: str, name: str) -> Export | None:
        if "." in name:
            cls, member = name.split(".", 1)
            state, c, where = self._class(mod, cls)
            if state == EXISTS and c is not None and self.get(where):
                return self.get(where).exports.get(f"{c.name}.{member}")
            return None
        return self.lookup(mod, name)[1]


def signature_change(old: list[list[Any]] | None, new: list[list[Any]] | None) -> list[str]:
    """Human-readable breaking differences between two parameter lists (empty if compatible)."""
    if not old or not new:
        return []
    o = {p[0]: p for p in old}
    n = {p[0]: p for p in new}
    takes_kwargs = any(p[1] == "kwarg" for p in new)
    out: list[str] = []
    for name, p in o.items():
        if p[1] in ("vararg", "kwarg"):
            continue
        if name not in n and not takes_kwargs:
            out.append(f"parameter `{name}` removed")
    for name, p in n.items():
        if p[1] in ("vararg", "kwarg"):
            continue
        if name not in o and not p[2]:
            out.append(f"new required parameter `{name}`")
        elif name in o and o[name][2] and not p[2]:
            out.append(f"parameter `{name}` lost its default")
    old_pos = [p[0] for p in old if p[1] in ("posonly", "pos")]
    new_pos = [p[0] for p in new if p[1] in ("posonly", "pos")]
    common = [x for x in old_pos if x in new_pos]
    if common != [x for x in new_pos if x in old_pos]:
        out.append("positional parameters reordered")
    return out
