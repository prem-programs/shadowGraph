"""Every Cypher query the product runs, in one place. The README's query section is generated from here.

Graphs: {tenant}_live (the company), {tenant}_sim_{run}_{hyp} (GRAPH.COPY of live), {tenant}_memory.
All verified against FalkorDB 4.18 (see tests/test_smoke.py).
"""

# ---------------------------------------------------------------- live graph: PRs and decisions

PR_CONTEXT = """
MATCH (pr:PullRequest {number: $pr})
OPTIONAL MATCH (pr)-[m:MODIFIES]->(t)
OPTIONAL MATCH (prov:Service)-[:EXPOSES]->(t)
OPTIONAL MATCH (author:Engineer)-[:AUTHORED]->(pr)
OPTIONAL MATCH (pr)-[:CITES]->(c:DecisionRecord)
RETURN pr.number AS number, pr.title AS title, pr.description AS description, pr.status AS status,
       author.handle AS author, collect(DISTINCT c.id) AS cites,
       collect(DISTINCT {target: t.id, change: m.change, route: t.route, provider: prov.id}) AS modifies
"""

# Q1. Which decision is in force for this PR (follows SUPERSEDED_BY from the cited decision).
Q1_TRACE_DECISION = """
MATCH (pr:PullRequest {number: $pr})-[:CITES]->(cited:DecisionRecord)
MATCH p = (cited)-[:SUPERSEDED_BY*0..5]->(current:DecisionRecord)
WHERE NOT (current)-[:SUPERSEDED_BY]->()
OPTIONAL MATCH (current)-[:DISCUSSED_IN]->(chat:ChatThread)
OPTIONAL MATCH (author:Engineer)-[:AUTHORED]->(current)
OPTIONAL MATCH (current)-[:GOVERNS]->(target)
RETURN cited.id AS cited_adr, cited.title AS cited_title, current.id AS active_adr, current.title AS title,
       current.summary AS summary, current.blocks_removal AS blocks_removal,
       [n IN nodes(p) | n.id] AS chain, [r IN relationships(p) | r.reason] AS reasons,
       chat.url AS discussion, chat.channel AS channel, author.handle AS author,
       collect(DISTINCT target.id) AS governs
"""

# Decisions in force that govern a target directly (catches PRs that cite nothing).
GOVERNING_IN_FORCE = """
MATCH (d:DecisionRecord)-[:GOVERNS]->(t)
WHERE t.id = $target AND NOT (d)-[:SUPERSEDED_BY]->()
OPTIONAL MATCH (d)-[:DISCUSSED_IN]->(chat:ChatThread)
OPTIONAL MATCH (author:Engineer)-[:AUTHORED]->(d)
RETURN d.id AS adr, d.title AS title, d.summary AS summary, d.blocks_removal AS blocks_removal,
       chat.url AS discussion, chat.channel AS channel, author.handle AS author
ORDER BY d.valid_from DESC
"""

# Q2. Search decisions in force: fetch 20, drop replaced ones, keep 5 (score = distance, lower is closer).
Q2_SEARCH_DECISIONS = """
CALL db.idx.vector.queryNodes('DecisionRecord', 'embedding', 20, vecf32($q))
YIELD node AS doc, score
WHERE NOT (doc)-[:SUPERSEDED_BY]->()
WITH doc, score ORDER BY score ASC LIMIT 5
OPTIONAL MATCH (doc)-[:DISCUSSED_IN]->(chat:ChatThread)
OPTIONAL MATCH (doc)-[:GOVERNS]->(t)
OPTIONAL MATCH (old:DecisionRecord)-[:SUPERSEDED_BY]->(doc)
RETURN doc.id AS adr, doc.title AS title, doc.summary AS summary, score, chat.url AS source,
       collect(DISTINCT t.id) AS governs, collect(DISTINCT old.id) AS replaces
ORDER BY score ASC
"""

# ---------------------------------------------------------------- shadow graph: the change

# Q3. Apply the change inside the shadow graph (FalkorDB takes one statement per call).
Q3_DELETE_CALLS_VIA = "MATCH ()-[c:CALLS {via: $endpoint_id}]->() DELETE c RETURN count(c) AS removed_calls"
Q3_DELETE_ENDPOINT = "MATCH (e:Endpoint {id: $endpoint_id}) DETACH DELETE e RETURN count(e) AS removed_endpoints"
Q3_ISOLATE_SERVICE = "MATCH (s:Service {id: $service_id})-[c:CALLS]-() DELETE c RETURN count(c) AS removed_calls"
CALLS_VIA = """
MATCH (a:Service)-[c:CALLS {via: $endpoint_id}]->(b:Service)
RETURN a.id AS source, b.id AS target, c.via AS via
"""
CALLS_TOUCHING = """
MATCH (a:Service)-[c:CALLS]->(b:Service)
WHERE a.id = $service_id OR b.id = $service_id
RETURN a.id AS source, b.id AS target, c.via AS via
"""

# Q4. Partition check (run on live and on the shadow).
Q4_WCC = """
CALL algo.WCC({nodeLabels: ['Service'], relationshipTypes: ['CALLS']})
YIELD node, componentId
RETURN componentId, count(node) AS services, collect(node.id) AS ids, min(node.tier) AS best_tier
ORDER BY services DESC
"""

# Q5. Chokepoints: EXACT betweenness, computed inside FalkorDB from all shortest paths.
# FalkorDB's algo.betweenness samples source nodes (with replacement), so its scores are estimates and the
# sample shifts between two graphs; before/after deltas built on it are noise (see tests/test_smoke.py).
# With 25 services, all pairs' shortest paths take a few milliseconds. Services on no path score 0 (filled in code).
Q5_BETWEENNESS = """
MATCH (a:Service), (b:Service) WHERE a <> b
WITH a, b
MATCH p = allShortestPaths((a)-[:CALLS*]->(b))
WITH a, b, collect(p) AS paths
WITH paths, toFloat(size(paths)) AS n
UNWIND paths AS p
WITH p, n, nodes(p) AS ns
UNWIND ns[1..size(ns) - 1] AS mid
RETURN mid.id AS service, sum(1.0 / n) AS score
ORDER BY score DESC
"""
# FalkorDB's sampled GraphBLAS betweenness, kept for reference and the smoke test only.
Q5_SAMPLED_BETWEENNESS = """
CALL algo.betweenness({nodeLabels: ['Service'], relationshipTypes: ['CALLS']})
YIELD node, score
RETURN node.id AS service, score
ORDER BY score DESC
"""
SERVICE_TIERS = "MATCH (s:Service) RETURN s.id AS service, s.tier AS tier"

# Q6. Who breaks, and the cascade chain. Runs on the LIVE graph: in the shadow the path is gone.
Q6_DIRECT_DEPENDENTS = """
MATCH (d:Service)-[:DEPENDS_ON]->(e:Endpoint {id: $endpoint_id})
RETURN d.id AS service, d.tier AS tier
ORDER BY d.tier ASC
"""
Q6_CASCADE = """
MATCH (d:Service)-[:DEPENDS_ON]->(:Endpoint {id: $endpoint_id})
MATCH (entry:Service) WHERE entry.tier = 0 AND entry <> d
WITH entry, d, shortestPath((entry)-[:CALLS*1..6]->(d)) AS p
WHERE p IS NOT NULL
RETURN entry.id AS entry, d.id AS breaks_at, length(p) AS hops, [n IN nodes(p) | n.id] AS chain
ORDER BY hops ASC LIMIT 5
"""
TIER0_ENTRIES = "MATCH (s:Service) WHERE s.tier = 0 RETURN s.id AS id"

# Q7. Expert for a service: PageRank over who defers to whom, times commits to this service.
Q7_EXPERT = """
CALL algo.pageRank('Engineer', 'DEFERRED_TO') YIELD node, score
MATCH (node)-[c:CONTRIBUTED_TO]->(:Service {id: $service_id})
OPTIONAL MATCH (node)-[:MEMBER_OF]->(t:Team)
RETURN node.handle AS engineer, node.name AS name, node.timezone AS timezone, score AS authority,
       c.commits AS commits, score * c.commits AS expertise, t.oncall_channel AS channel
ORDER BY expertise DESC LIMIT 3
"""

# ---------------------------------------------------------------- memory graph

OPEN_EPISODE = """
CREATE (e:Episode {id: $id, pr_number: $pr, title: $title, symptom_text: $text,
                   symptom_embedding: vecf32($embedding), status: 'open', started_at: timestamp(), run_id: $run_id})
RETURN e.id AS id
"""
CLOSE_EPISODE = """
MATCH (e:Episode {id: $id})
SET e.status = 'closed', e.verdict = $verdict, e.summary = $summary, e.steps = $steps, e.ms = $ms,
    e.ended_at = timestamp()
RETURN e.id AS id
"""

# Q8. Hypotheses: post, claim (specific, then next of a kind), resolve.
Q8_POST_HYPOTHESES = """
MATCH (e:Episode {id: $episode_id})
UNWIND $hyps AS h
MERGE (x:Hypothesis {id: h.id})
ON CREATE SET x.label = h.label, x.kind = h.kind, x.check = h.check, x.target_id = h.target_id,
  x.summary = h.summary, x.priority = h.priority, x.status = 'OPEN', x.created_at = timestamp()
MERGE (e)-[:INVESTIGATING]->(x)
RETURN x.id AS id, x.label AS label, x.kind AS kind, x.status AS status
"""
Q8_CLAIM_SPECIFIC = """
MATCH (:Episode {id: $episode_id})-[:INVESTIGATING]->(h:Hypothesis {id: $hyp_id})
WHERE h.status = 'OPEN' AND h.kind = $kind
SET h.status = 'CLAIMED', h.claimed_by = $agent_id, h.claimed_at = timestamp()
RETURN h.id AS claimed, h.label AS label, h.check AS check, h.target_id AS target_id, h.summary AS summary
"""
Q8_CLAIM_NEXT = """
MATCH (:Episode {id: $episode_id})-[:INVESTIGATING]->(h:Hypothesis)
WHERE h.status = 'OPEN' AND h.kind = $kind
WITH h ORDER BY h.priority DESC, h.id ASC LIMIT 1
SET h.status = 'CLAIMED', h.claimed_by = $agent_id, h.claimed_at = timestamp()
RETURN h.id AS claimed, h.label AS label, h.check AS check, h.target_id AS target_id, h.summary AS summary
"""
HYPOTHESIS_OWNER = "MATCH (h:Hypothesis {id: $hyp_id}) RETURN h.status AS status, h.claimed_by AS claimed_by"
Q8_RESOLVE = """
MATCH (h:Hypothesis {id: $hyp_id, claimed_by: $agent_id})
SET h.status = $status, h.result = $result_json, h.resolved_at = timestamp()
RETURN h.id AS id, h.status AS status
"""
EPISODE_HYPOTHESES = """
MATCH (:Episode {id: $episode_id})-[:INVESTIGATING]->(h:Hypothesis)
RETURN h.id AS id, h.label AS label, h.kind AS kind, h.check AS check, h.status AS status,
       h.claimed_by AS claimed_by, h.result AS result
ORDER BY h.label
"""

# Q9. Record a fact; if an older fact on the same attribute is still valid, the new one overrides it.
Q9_RECORD_FACT = """
CREATE (new:Fact {id: $id, entity_id: $entity, attribute: $attr, value: $value, valid_from: timestamp(),
                  source: $source, source_agent: $agent})
WITH new
MATCH (old:Fact {entity_id: $entity, attribute: $attr})
WHERE old.valid_until IS NULL AND old <> new
SET old.valid_until = new.valid_from
CREATE (new)-[:OVERRIDES]->(old)
RETURN old.id AS overridden, old.value AS old_value, old.source AS old_source
"""
LINK_FACT_EPISODE = """
MATCH (f:Fact {id: $fact_id}), (e:Episode {id: $episode_id})
CREATE (e)-[:PRODUCED]->(f)
"""

# Q10. Playbooks: save, recall by similar episode, record a reuse.
Q10_SAVE_PLAYBOOK = """
MATCH (e:Episode {id: $episode_id})
CREATE (pb:Playbook {id: $pb_id, name: $name, trigger_pattern: $pattern, decisive: $decisive,
                     success_rate: 1.0, executions: 1, created_at: timestamp()})
CREATE (e)-[:CRYSTALLIZED_INTO]->(pb)
WITH pb
UNWIND $steps AS st
CREATE (t:ToolStep {id: st.id, order: st.order, tool: st.tool, args_template: st.args, description: st.desc})
CREATE (pb)-[:HAS_STEP {order: st.order}]->(t)
RETURN DISTINCT pb.id AS playbook
"""
Q10_RECALL_PLAYBOOK = """
CALL db.idx.vector.queryNodes('Episode', 'symptom_embedding', 3, vecf32($symptom_vec))
YIELD node AS past, score
WHERE past.status = 'closed' AND past.id <> $exclude
MATCH (past)-[:CRYSTALLIZED_INTO]->(pb:Playbook)-[s:HAS_STEP]->(step:ToolStep)
WHERE pb.success_rate >= 0.8
RETURN pb.id AS playbook, pb.name AS name, pb.decisive AS decisive, pb.executions AS executions,
       pb.success_rate AS success_rate, past.id AS from_episode, past.pr_number AS from_pr,
       past.steps AS from_steps, past.ms AS from_ms, score,
       s.order AS step, step.tool AS tool, step.args_template AS args
ORDER BY score ASC, step ASC
"""
Q10_REUSE_PLAYBOOK = """
MATCH (pb:Playbook {id: $pb_id}), (e:Episode {id: $episode_id})
SET pb.success_rate = $success_rate, pb.executions = $executions
CREATE (e)-[:REUSED]->(pb)
RETURN pb.id AS playbook, pb.executions AS executions, pb.success_rate AS success_rate
"""

# Q11. Recall earlier conversations.
Q11_RECALL_CONVERSATION = """
CALL db.idx.vector.queryNodes('Message', 'embedding', 5, vecf32($q)) YIELD node AS m, score
MATCH (s:Session)-[:HAS_TURN]->(m)
OPTIONAL MATCH (s)-[:ABOUT]->(e:Episode)
RETURN s.id AS session, m.id AS message, m.role AS role, m.text AS text, m.ts AS ts,
       collect(DISTINCT e.pr_number) AS episodes_pr, score
ORDER BY score ASC
"""
SIMILAR_EPISODES = """
CALL db.idx.vector.queryNodes('Episode', 'symptom_embedding', 3, vecf32($q)) YIELD node AS e, score
WHERE e.status = 'closed'
RETURN e.id AS episode, e.pr_number AS pr_number, e.title AS title, e.verdict AS verdict, e.summary AS summary,
       e.ended_at AS ended_at, score
ORDER BY score ASC
"""
ENSURE_SESSION = """
MERGE (s:Session {id: $id})
ON CREATE SET s.user = $user, s.started_at = timestamp(), s.title = $title
RETURN s.id AS id
"""
LAST_MESSAGE = """
MATCH (:Session {id: $sid})-[:HAS_TURN]->(m:Message)
WHERE NOT (m)-[:NEXT]->()
RETURN m.id AS id
"""
APPEND_MESSAGE = """
MATCH (s:Session {id: $sid})
CREATE (m:Message {id: $id, role: $role, text: $text, ts: timestamp(), sources: $sources,
                   embedding: vecf32($embedding)})
CREATE (s)-[:HAS_TURN]->(m)
RETURN m.id AS id, m.ts AS ts
"""
LINK_MESSAGES = "MATCH (a:Message {id: $prev}), (b:Message {id: $next}) CREATE (a)-[:NEXT]->(b)"
LINK_SESSION_EPISODE = """
MATCH (s:Session {id: $sid}), (e:Episode {pr_number: $pr})
WHERE e.status = 'closed'
MERGE (s)-[:ABOUT]->(e)
"""
SESSIONS = """
MATCH (s:Session)
OPTIONAL MATCH (s)-[:HAS_TURN]->(m:Message)
RETURN s.id AS id, s.title AS title, s.started_at AS started_at, count(m) AS messages
ORDER BY started_at DESC LIMIT 20
"""
SESSION_MESSAGES = """
MATCH (:Session {id: $sid})-[:HAS_TURN]->(m:Message)
RETURN m.id AS id, m.role AS role, m.text AS text, m.ts AS ts, m.sources AS sources
ORDER BY m.ts ASC
"""
MEMORY_COUNTS = """
OPTIONAL MATCH (e:Episode) WITH count(e) AS episodes
OPTIONAL MATCH (f:Fact) WITH episodes, count(f) AS facts
OPTIONAL MATCH (p:Playbook) WITH episodes, facts, count(p) AS playbooks
OPTIONAL MATCH (s:Session) WITH episodes, facts, playbooks, count(s) AS sessions
OPTIONAL MATCH (h:Hypothesis) RETURN episodes, facts, playbooks, sessions, count(h) AS hypotheses
"""
RECENT_FACTS = """
MATCH (f:Fact)
OPTIONAL MATCH (f)-[:OVERRIDES]->(old:Fact)
RETURN f.id AS id, f.entity_id AS entity, f.attribute AS attribute, f.value AS value, f.source AS source,
       f.source_agent AS agent, f.valid_from AS valid_from, f.valid_until AS valid_until, old.id AS overrides
ORDER BY f.valid_from DESC LIMIT 12
"""
PLAYBOOK_STATS = "MATCH (pb:Playbook {id: $pb_id}) RETURN pb.executions AS executions, pb.success_rate AS success_rate"
PLAYBOOKS = """
MATCH (pb:Playbook)
OPTIONAL MATCH (pb)-[:HAS_STEP]->(t:ToolStep)
RETURN pb.id AS id, pb.name AS name, pb.executions AS executions, pb.success_rate AS success_rate,
       pb.decisive AS decisive, pb.created_at AS created_at, count(t) AS steps
ORDER BY created_at DESC
"""
RECENT_EPISODES = """
MATCH (e:Episode)
RETURN e.id AS id, e.pr_number AS pr_number, e.title AS title, e.status AS status, e.verdict AS verdict,
       e.steps AS steps, e.ms AS ms, e.started_at AS started_at
ORDER BY e.started_at DESC LIMIT 10
"""

# ---------------------------------------------------------------- dashboard reads (read-only)

GRAPH_SERVICES = """
MATCH (s:Service)
OPTIONAL MATCH (t:Team)-[:OWNS]->(s)
RETURN s.id AS id, s.tier AS tier, s.description AS description, t.name AS team
"""
GRAPH_CALLS = """
MATCH (a:Service)-[c:CALLS]->(b:Service)
RETURN a.id AS source, b.id AS target, c.via AS via, c.p99_ms AS p99_ms, c.rpm AS rpm
"""
PR_LIST = """
MATCH (pr:PullRequest)
WHERE pr.status = 'open'
OPTIONAL MATCH (a:Engineer)-[:AUTHORED]->(pr)
RETURN pr.number AS number, pr.title AS title, a.handle AS author
ORDER BY pr.number ASC
"""
META = "MATCH (m:Meta {id: 'meta'}) RETURN m.seed_version AS seed_version, m.embed_backend AS embed_backend, m.signature AS signature"

# ---------------------------------------------------------------- webhook ingest (keeps the graph current)

UPSERT_PR = """
MERGE (pr:PullRequest {number: $number})
ON CREATE SET pr.id = 'PR-' + toString($number), pr.created_at = $created_at
SET pr.title = $title, pr.description = $description, pr.status = $status, pr.recorded_at = timestamp()
RETURN pr.number AS number
"""
CLEAR_PR_LINKS = "MATCH (pr:PullRequest {number: $number})-[r:MODIFIES|CITES]->() DELETE r"
LINK_PR_TARGET = """
MATCH (pr:PullRequest {number: $number}), (t:Endpoint)
WHERE t.route = $route AND ($service IS NULL OR t.service = $service)
CREATE (pr)-[:MODIFIES {change: $change}]->(t)
RETURN t.id AS target
"""
LINK_PR_CITES = """
MATCH (pr:PullRequest {number: $number}), (d:DecisionRecord {id: $adr})
CREATE (pr)-[:CITES]->(d)
RETURN d.id AS adr
"""
LINK_PR_AUTHOR = """
MATCH (pr:PullRequest {number: $number}), (g:Engineer {handle: $handle})
MERGE (g)-[:AUTHORED]->(pr)
"""
