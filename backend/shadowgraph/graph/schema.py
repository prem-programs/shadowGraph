"""Index DDL for the live graph and the memory graph."""
from __future__ import annotations

from ..embed import DIM

LIVE_INDEXES = [
    "CREATE INDEX FOR (s:Service) ON (s.id)",
    "CREATE INDEX FOR (e:Endpoint) ON (e.id)",
    "CREATE INDEX FOR (p:PullRequest) ON (p.number)",
    "CREATE INDEX FOR (d:DecisionRecord) ON (d.id)",
    "CREATE INDEX FOR (g:Engineer) ON (g.handle)",
    "CREATE INDEX FOR (t:Team) ON (t.id)",
    "CREATE INDEX FOR (c:ChatThread) ON (c.id)",
    f"CREATE VECTOR INDEX FOR (d:DecisionRecord) ON (d.embedding) OPTIONS {{dimension: {DIM}, similarityFunction: 'cosine'}}",
    "CALL db.idx.fulltext.createNodeIndex('DecisionRecord', 'title', 'summary')",
]

MEMORY_INDEXES = [
    "CREATE INDEX FOR (h:Hypothesis) ON (h.id)",
    "CREATE INDEX FOR (h:Hypothesis) ON (h.status)",
    "CREATE INDEX FOR (e:Episode) ON (e.id)",
    "CREATE INDEX FOR (f:Fact) ON (f.entity_id)",
    "CREATE INDEX FOR (s:Session) ON (s.id)",
    "CREATE INDEX FOR (m:Message) ON (m.id)",
    "CREATE INDEX FOR (p:Playbook) ON (p.id)",
    f"CREATE VECTOR INDEX FOR (e:Episode) ON (e.symptom_embedding) OPTIONS {{dimension: {DIM}, similarityFunction: 'cosine'}}",
    f"CREATE VECTOR INDEX FOR (m:Message) ON (m.embedding) OPTIONS {{dimension: {DIM}, similarityFunction: 'cosine'}}",
]


async def ensure_indexes(db, graph: str, statements: list[str]) -> None:
    for stmt in statements:
        try:
            await db.query(graph, stmt, emit=False)
        except Exception as exc:  # already exists is fine
            msg = str(exc).lower()
            if "already" not in msg and "exists" not in msg:
                raise
