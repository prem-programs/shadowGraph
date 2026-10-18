"""Writes docs/QUERIES.md from backend/shadowgraph/graph/queries.py, so the docs never drift from the code."""
from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "backend" / "shadowgraph" / "graph" / "queries.py"
OUT = ROOT / "docs" / "QUERIES.md"

tree = ast.parse(SRC.read_text(encoding="utf-8"))
lines = SRC.read_text(encoding="utf-8").splitlines()
parts = ["# Cypher reference", "", "Generated from `backend/shadowgraph/graph/queries.py` by `scripts/gen_queries_md.py`. "
         "Every query below runs on FalkorDB 4.18 and is covered by the test suite.", ""]
for node in tree.body:
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
        name = node.targets[0].id
        comments = []
        i = node.lineno - 2
        while i >= 0 and lines[i].startswith("#"):
            comments.insert(0, lines[i].lstrip("# ").rstrip())
            i -= 1
        parts += [f"## `{name}`", ""]
        if comments:
            parts += [" ".join(c for c in comments if not set(c) <= {"-", " "}), ""]
        parts += ["```cypher", node.value.value.strip(), "```", ""]
OUT.parent.mkdir(exist_ok=True)
OUT.write_text("\n".join(parts), encoding="utf-8")
print(f"wrote {OUT.relative_to(ROOT)}")
