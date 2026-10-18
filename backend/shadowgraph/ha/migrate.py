"""Draft a migration patch for one consumer file. Drafts are shown to the user and never posted anywhere.

Three mechanical cases are drafted; anything else gets written instructions instead of a guess:
  move      the name still exists in another core module (a removed re-export): import it from there
  replace   a deprecation names a dotted replacement (e.g. UnitOfRatio.PARTS_PER_MILLION): swap the
            expression and import the replacement's head
  drop      a deprecation without replacement (e.g. CONN_CLASS_*): delete simple assignments that only
            store it, and its import
The patched file is parsed and re-scanned; if the old name is still used or the file no longer parses,
the draft is discarded and only instructions are returned.
"""
from __future__ import annotations

import ast
import difflib
import re
from dataclasses import dataclass
from typing import Any

from .pyparse import parse
from .usage import scan_source


@dataclass
class Plan:
    kind: str                   # move | replace | drop | manual
    old_module: str
    old_name: str
    new_module: str | None = None
    new_expr: str | None = None  # replacement expression, e.g. "UnitOfRatio.PARTS_PER_MILLION"
    note: str = ""


def _line_starts(src: str) -> list[int]:
    starts, pos = [0], 0
    for line in src.splitlines(keepends=True):
        pos += len(line)
        starts.append(pos)
    return starts


def _span(src_starts: list[int], node: ast.AST) -> tuple[int, int]:
    return (src_starts[node.lineno - 1] + node.col_offset, src_starts[node.end_lineno - 1] + node.end_col_offset)


def _render_import(module: str, names: list[ast.alias], indent: str, multiline: bool) -> str:
    parts = [a.name + (f" as {a.asname}" if a.asname else "") for a in names]
    if multiline:  # keep the file's own style
        body = "".join(f"{indent}    {p},\n" for p in parts)
        return f"from {module} import (\n{body}{indent})"
    return f"from {module} import {', '.join(parts)}"


def apply_plan(src: str, plan: Plan, known_modules: set[str]) -> tuple[str | None, list[str]]:
    """Return (new source or None, notes)."""
    tree = parse(src)
    if tree is None:
        return None, ["the file could not be parsed"]
    starts = _line_starts(src)
    edits: list[tuple[int, int, str]] = []
    notes: list[str] = []
    local_names: set[str] = set()      # names bound by `from old_module import old_name [as x]`
    module_aliases: set[str] = set()   # names bound to old_module itself
    last_import_end = 0
    anchor: int | None = None  # insert the new import next to the one we edit, so import groups stay intact
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("homeassistant") and not node.level:
                last_import_end = max(last_import_end, _span(starts, node)[1])
            elif not last_import_end:
                last_import_end = max(last_import_end, _span(starts, node)[1])
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            if node.module == plan.old_module:
                keep = [a for a in node.names if a.name != plan.old_name]
                hit = [a for a in node.names if a.name == plan.old_name]
                if hit:
                    local_names |= {a.asname or a.name for a in hit}
                    a0, b0 = _span(starts, node)
                    line_start = starts[node.lineno - 1]
                    if anchor is None and node in tree.body:
                        anchor = b0
                    indent = src[line_start:a0]
                    if keep:
                        text = _render_import(node.module, keep, indent, node.lineno != node.end_lineno)
                        edits.append((a0, b0, text))
                    else:
                        # drop the whole statement and its line break
                        end = b0 + 1 if src[b0:b0 + 1] == "\n" else b0
                        edits.append((line_start, end, ""))
            parent, _, leaf = plan.old_module.rpartition(".")
            if node.module == parent:
                module_aliases |= {a.asname or a.name for a in node.names if a.name == leaf}
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name == plan.old_module and a.asname:
                    module_aliases.add(a.asname)

    head = (plan.new_expr or plan.old_name).split(".")[0]
    for node in ast.walk(tree):
        # module_alias.OLD_NAME  ->  replacement
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and \
                node.value.id in module_aliases and node.attr == plan.old_name:
            a0, b0 = _span(starts, node)
            if plan.kind == "drop":
                continue  # handled below with the assignment
            edits.append((a0, b0, plan.new_expr or plan.old_name))
        elif isinstance(node, ast.Name) and node.id in local_names and plan.kind == "replace":
            a0, b0 = _span(starts, node)
            edits.append((a0, b0, plan.new_expr or plan.old_name))

    if plan.kind == "drop":
        for node in ast.walk(tree):
            if not isinstance(node, (ast.ClassDef, ast.Module)):
                continue
            for st in node.body:
                if isinstance(st, (ast.Assign, ast.AnnAssign)):
                    v = st.value
                    uses_it = (isinstance(v, ast.Name) and v.id in local_names) or (
                        isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name)
                        and v.value.id in module_aliases and v.attr == plan.old_name)
                    if uses_it:
                        a0, b0 = _span(starts, st)
                        line_start = starts[st.lineno - 1]
                        end = b0 + 1 if src[b0:b0 + 1] == "\n" else b0
                        if src[line_start:a0].strip() == "":
                            edits.append((line_start, end, ""))
                            notes.append(f"removed `{ast.get_source_segment(src, st)}` (line {st.lineno})")
    if plan.kind in ("move", "replace") and plan.new_module:
        names_needed = {plan.old_name} if plan.kind == "move" else {head}
        if plan.kind == "move" and not local_names:
            names_needed = set()  # only `module.NAME` uses: those were rewritten to the new expression
        already = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                   and n.module == plan.new_module for a in n.names}
        add = sorted(names_needed - already)
        if plan.kind == "move" and module_aliases:
            add = sorted(set(add) | ({plan.old_name} - already))
        if add:
            line = f"from {plan.new_module} import {', '.join(add)}\n"
            pos = anchor if anchor is not None else last_import_end
            if src[pos:pos + 1] == "\n":
                pos += 1
            edits.append((pos, pos, line))
    if not edits:
        return None, notes + ["nothing to change automatically"]
    edits.sort(key=lambda e: (e[0], e[1]), reverse=True)
    out = src
    last = None
    for a0, b0, text in edits:
        if last is not None and b0 > last:  # overlapping edits: give up rather than guess
            return None, notes + ["overlapping edits"]
        out = out[:a0] + text + out[b0:]
        last = a0
    return out, notes


def validate(new_src: str, symbol: str, known: set[str]) -> str | None:
    if parse(new_src) is None:
        return "the patched file does not parse"
    scan = scan_source(new_src, known, [])
    if any(u.key() == symbol for u in scan.uses):
        return "the old name is still used after patching"
    return None


def unified(old: str, new: str, path: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                        fromfile=f"a/{path}", tofile=f"b/{path}", n=2))


def plan_for(symbol: dict[str, Any], alternatives: list[dict[str, Any]], known: set[str]) -> Plan:
    mod, name = symbol["module"], symbol["name"]
    if "." in name:
        return Plan("manual", mod, name, note="members of classes are not migrated automatically")
    repl = symbol.get("replacement") or ""
    if symbol.get("deprecated"):
        if repl and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*", repl):
            head = repl.split(".")[0]
            # DeprecatedConstantEnum replacements live next to the constant (homeassistant.const)
            return Plan("replace", mod, name, new_module=mod, new_expr=repl,
                        note=f"core says: use {repl} instead (head `{head}` imported from {mod})")
        if not repl:
            return Plan("drop", mod, name, note="core deprecates it with no replacement: the value has no effect")
        return Plan("manual", mod, name, note=f"core says: {repl}")
    alt = next((a for a in alternatives if a["module"] != mod), None)
    if alt:
        return Plan("move", mod, name, new_module=alt["module"], new_expr=name,
                    note=f"`{name}` still exists in {alt['module']}")
    return Plan("manual", mod, name, note="no other core module exports this name; it needs a code change")
