# AGENT_HANDOFF.md: finish what the first build could not do

**For the agent reading this:** you are continuing ShadowGraph on a normal laptop with internet and Docker.
The code is complete and 46 tests passed against a real FalkorDB 4.18 server, but both builds ran in a
locked-down cloud sandbox. The second build added the Home Assistant workspace (Tasks 14-19 cover what it
could not check: the GitHub API was blocked there, git was not). Container registries (Docker Hub, GHCR, ECR, Quay), Hugging Face and Google were
blocked there. There was no PowerShell, no API keys and no GitHub token. Every item below is something that could
not run there. Work through the tasks **in order**, check each "Done when", fix what fails, and finish by filling
in the report at the end.

**For the person:** open this project folder in your agent (Claude Code, Cursor or similar) and say:
*"Read AGENT_HANDOFF.md and complete it task by task. Ask me before using any token or account."*

---

## 0. Context in 60 seconds

- **What it is:** "know who you'll break before you merge." Main workspace: the real Home Assistant codebase
  (home-assistant/core plus all 3,304 HACS custom integrations, snapshot in `data/ha_snapshot.json.gz`). For each
  PR it diffs the API, finds every user with file:line and what fails, and shows when it ships; plus a retirement
  board, a "broken by core" list, draft fixes, Ask, and MCP pre-flight tools for coding agents. **Read-only:
  it never posts to Home Assistant or HACS repositories.** The original services sample (Acme, Globex) is still
  there: it copies the company graph in FalkorDB (`GRAPH.COPY`), simulates removing an endpoint, and posts BLOCK,
  WARN or PASS to your own demo repo. Agents coordinate through hypothesis locks in a memory graph. The project is
  for the WeMakeDevs × FalkorDB "Graph Hacks" hackathon, all three tracks.
- **Read first:** `README.md` (overview), `implemented.md` (Part 2 then Part 1: how everything works, how it was
  verified, deviations), `docs/HOME_ASSISTANT.md`.
- **Layout:** `backend/shadowgraph/` (Python: `api.py`, `mcp_server.py`, `ha/` (Home Assistant), `agents/`,
  `core/`, `graph/`), `backend/tests/` (46 tests), `frontend/src/` (React + Vite + Cytoscape), `Dockerfile`, `docker-compose.yml`,
  `scripts/`, `demo-repo/acme-payments-demo/`, `docs/`.
- **Expected Home Assistant results** (snapshot of Oct 18, 2026; replay from the Pull requests page):
  - #186863: **Breaks**, 32 custom integrations on 2026.11, 10 used names removed; the agent run's cross-check
    says "agree: 32 vs 32".
  - #184756: **Breaks**, 11 custom integrations; announced by the blog post "Replacing `async_track_state_change`...".
  - #186267: **Retires**, `CONN_CLASS_*` (6 names), 343 integrations still use them, removal 2027.5.
  - Broken by core: 59 break in 2026.11, 95 already broken. Retirements: 17, of which 10 ready.
- **Expected sample results:** these are the numbers to confirm. Tenant `acme`:
  - PR 1 (remove `/v1/token-verify`): **BLOCK**, services go from 1 component to 3, 2 lock conflicts, 3 shadow
    copies, 35 agent steps.
  - PR 2 (remove `/v1/session-refresh`): **BLOCK**, "Playbook reused" from PR 1, 23 steps.
  - PR 3 (remove `/v1/jwks`): **PASS**.

## 1. Ground rules

1. **Do not change** the verdict rules (`backend/shadowgraph/core/verdict.py`), the seed topology
   (`graph/seed_acme.py`) or the three FalkorDB workarounds listed below. The demo story and the tests depend on them.
   - `GRAPH.COPY` calls are queued in `db.py`, because concurrent copies fail with "could not fork".
   - Betweenness is computed exactly with `allShortestPaths` (Q5 in `queries.py`), because `algo.betweenness`
     samples and its before/after deltas are noise.
   - `DEFERRED_TO` is stored once per pair with a count, because `algo.pageRank` ignores parallel edges.
2. **Keep the tests green:** `cd backend && python -m pytest -q` must say `46 passed` (or more, if you add tests)
   after every change. Tests only touch FalkorDB graphs named `test_*`.
2b. **Never post, comment, push or open PRs on home-assistant/* or any HACS repository**, and never add code that
   does. Their AI policy treats automated comments as spam. Drafts stay drafts.
3. **Never commit `.env`** or paste tokens into files that get committed. Ask the person for tokens and accounts.
4. **Use Python 3.12 or 3.13**, not 3.14: `onnxruntime` (needed by `fastembed`) was only confirmed for 3.12/3.13.
5. **Write down what you change** in `implemented.md` (sections 5 and 8) as you go.

## 2. Ask the person for these (only when the task needs them)

| Needed for | What | Where they get it |
| --- | --- | --- |
| Task 5 | `GEMINI_API_KEY` | <https://aistudio.google.com/apikey> (free) |
| Task 6 | A GitHub account, a public repo `acme-payments-demo`, a fine-grained token for that repo only with **Pull requests: Read and write** and **Commit statuses: Read and write** | github.com → Settings → Developer settings → Fine-grained tokens |
| Task 8–9 | FalkorDB Cloud instance URL `redis://default:PASSWORD@HOST:PORT` | <https://app.falkordb.cloud> (free) |
| Task 9 | Render account connected to GitHub | <https://render.com> |
| Task 7 | Optional: ngrok or Cloudflare account for a temporary public URL | ngrok.com / cloudflare |
| Task 14 | A GitHub token for **reads only** (a fine-grained token with "Public repositories (read-only)" is enough) | github.com → Settings → Developer settings → Fine-grained tokens |

---

## Task 1. Baseline on this laptop

**Why not done before:** different machine; confirm nothing broke in transfer.

```bash
docker run -d --name falkordb-test -p 6379:6379 falkordb/falkordb:latest
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
```

**Done when:** `46 passed`. Then remove the test container: `docker rm -f falkordb-test`.
**If it fails:** if FalkorDB isn't reachable, check `FALKORDB_URL` (default `redis://localhost:6379`) and that
port 6379 is free. If a test fails on FalkorDB behaviour (`test_smoke.py`), the `:latest` image may have changed;
run `docker run --rm falkordb/falkordb:latest redis-server --version` and note the module version. Pin the image in
`docker-compose.yml` to the tag that passes (the build was verified on module version 41803, i.e. v4.18.3).

## Task 2. Build and run the Docker image

**Why not done before:** every container registry was blocked, so the image was never built. Its steps were
replayed by hand in a clean environment and worked, but the real build is untested.

```bash
cp .env.example .env          # Windows: copy .env.example .env
docker compose up --build
```

**Done when:**
- the build finishes; the line `embedding model not downloaded` does **not** appear (the model downloaded);
- `docker compose ps` shows both services **healthy**;
- <http://localhost:8000> loads, the top bar says "FalkorDB connected";
- Run check on PR 1, then PR 2, then PR 3 gives the results in section 0;
- <http://localhost:8000/api/health> shows `"embedder": "fastembed:sentence-transformers/all-MiniLM-L6-v2"`;
- FalkorDB Browser at <http://localhost:3000> opens and shows the graphs `acme_live`, `acme_memory`, `globex_live`,
  `globex_memory`;
- after `docker compose down` then `docker compose up`, memory (Memory tab counts) survives.

**Points to check if it fails:**
- `npm ci` in the `ui` stage needs `frontend/package-lock.json`: it is in the repo.
- FalkorDB healthcheck uses `redis-cli ping`; if the image lacks `redis-cli`, change the healthcheck to
  `["CMD-SHELL", "echo PING | nc -w 2 localhost 6379 | grep -q PONG"]` or drop the `condition: service_healthy`
  and rely on the app's own retries (it reconnects every request).
- The app runs as the non-root user `shadow`; if fastembed fails writing under `/app/models` at runtime, keep
  `chown -R shadow /app` after the model download step (it already is) and check `FASTEMBED_CACHE_PATH=/app/models`.
- Image size: expect about 1 GB (onnxruntime). That is fine.
- Data volume path `/var/lib/falkordb/data` matches FalkorDB's docs.

## Task 3. Real embeddings: retune two thresholds

**Why not done before:** Hugging Face was blocked, so every test and demo run used the built-in hashing embedder.
Two similarity thresholds were tuned on hashing-embedder distances, and MiniLM distances are distributed differently.

1. Run the suite with real embeddings (outside Docker, in the Task 1 venv):

   ```bash
   cd backend
   EMBED_BACKEND=fastembed python -m pytest -q        # PowerShell: $env:EMBED_BACKEND="fastembed"; python -m pytest -q
   ```

2. In the dashboard (Task 2), press **Reset demo**, run PR 1, then PR 2. PR 2 must show **"Playbook reused"**.
   The timeline event `playbook_reused` / `playbook_miss` prints the distance.
3. Ask in chat "what is our retry policy?". The answer must name **ADR-17**, and say it replaced ADR-04.
   Ask something unrelated ("what's the weather"). It should say it found nothing close.

**Thresholds** (change only if a check above fails):
- `PLAYBOOK_MAX_DISTANCE` (env var, default `0.6`, in `core/memory.py`): PR 1 → PR 2 must be under it. A
  completely unrelated PR text must be over it.
- `RELATED = 0.95` in `core/chat.py`: what chat counts as related. With MiniLM, unrelated text is often around
  0.7–0.9, so this may need to drop to about 0.6. Pick the value from measured distances; print them with
  `python -m shadowgraph run acme 2 --json` (look at `playbook_reused.distance`) and by calling
  `POST /api/chat` and reading the `context` field.

**Done when:** all three checks pass with `fastembed`, the suite still passes with both embedders (default run
uses hashing), and any new threshold is recorded in `implemented.md`.

## Task 4. PowerShell scripts (Windows only)

**Why not done before:** no PowerShell in the sandbox. Both scripts were reviewed, never run.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1           # docker mode
powershell -ExecutionPolicy Bypass -File scripts\package.ps1         # writes ..\shadowgraph.zip
```

**Done when:** `start.ps1` brings the app up and opens the browser. `package.ps1` writes a zip with no
`node_modules`, `dist`, `.venv`, `__pycache__` or `.env` inside (check with Explorer or `tar -tf`). Test
`start.ps1 -Mode local` only if you have a FalkorDB Cloud URL (FalkorDB has no Windows build). It needs
`py -3.13`, the Python launcher.
**If it fails:** robocopy exit codes 0–7 mean success. Do not treat them as errors.

## Task 5. Real Gemini calls (LLM mode)

**Why not done before:** no API key and Google was blocked. The plumbing was tested with a fake client that builds
real `google-genai` request objects (`tests/test_llm.py`), but no real request was ever sent.

**Important:** the default `GEMINI_MODEL=gemini-2.5-flash` may not work for a new key. Google's deprecations page
says the 2.5 models are served only to accounts that used them recently, and recommends newer Flash models for new
projects ([source](https://ai.google.dev/gemini-api/docs/deprecations)).

1. List the models the key can use:

   ```bash
   python -c "from google import genai; c=genai.Client(api_key='KEY'); print([m.name for m in c.models.list() if 'flash' in m.name])"
   ```

2. Pick a current Flash model that supports function calling. Set it as `GEMINI_MODEL` in `.env`. If 2.5 is not
   available, also update the default in `backend/shadowgraph/config.py`, `.env.example`, `docker-compose.yml` and
   the README configuration table.
3. Restart, choose **LLM** in the top bar, Reset demo, run PR 1.

**Done when:** the timeline shows a `plan` event ("Gemini chose …") and an `llm_text` event, with **no amber
"Switched to rules" banner**. The PR comment's opening paragraph is Gemini's, and the verdict is still BLOCK.
Chat in LLM mode answers with the right ADR ids.
**If it fails** (the banner shows the error message; also check `docker compose logs app`):
- 404 / permission errors mean the model id is wrong: back to step 1.
- Errors about function response roles or thought signatures: in `agents/llm.py`, function responses are sent as
  `types.Content(role="user", parts=[types.Part.from_function_response(...)])` and the model's own turn is appended
  unchanged (`resp.candidates[0].content`, which keeps thought signatures). Check the current `google-genai` docs
  for function calling and adjust only that loop.
- 429 means the free-tier rate limit: wait a minute; the code makes at most about 10 calls per run.

## Task 6. Real GitHub posting

**Why not done before:** no GitHub token or account access. `core/github.py` has never talked to the real API.

1. Follow `demo-repo/acme-payments-demo/README.md`: create an **empty** public repo, push the folder, and open the
   three PRs **in order** (the script `scripts/create-demo-prs.sh` needs `gh` and `python` on PATH; on Windows Git
   Bash use `py` if `python` opens the Microsoft Store). If the numbers are not 1, 2, 3, set `PR_TOKEN_VERIFY`,
   `PR_SESSION_REFRESH`, `PR_JWKS` in `.env`.
2. Put `GITHUB_TOKEN` and `GITHUB_REPO=<owner>/acme-payments-demo` in `.env`; restart.
3. Run PR 1 from the dashboard; then run it again.

**Done when:**
- PR 1 on GitHub has **one** ShadowGraph comment (the second run *edits* it, it does not add another);
- the PR's checks show `shadowgraph/blast-radius` **failed** with "BLOCK: …", and its Details link opens the
  dashboard on that run;
- PR 3 gets a **successful** status;
- the dashboard's verdict card says "Posted to GitHub (failure)" with a working link.

**If it fails:** 403 on comments means add **Issues: write** to the token (either Issues or Pull requests write
is enough for issue comments). 403/404 on statuses means **Commit statuses: write** is missing. 404 on the PR
means `GITHUB_REPO` or the PR numbers are wrong.

## Task 7. GitHub webhook end to end

**Why not done before:** needs a public URL and a real repo. Only an in-process test with a fake payload exists.

1. Expose the local app: `ngrok http 8000` (or `cloudflared tunnel --url http://localhost:8000`), or use the
   deployed URL from Task 9. Set `PUBLIC_URL` to it. Set `MCP_ALLOWED_HOSTS` to its hostname if you also want
   `/mcp` through it.
2. In the demo repo: Settings → Webhooks → payload `https://<url>/api/webhooks/github`, content type JSON, secret =
   a random string also set as `GITHUB_WEBHOOK_SECRET`, events: **Pull requests**.
3. Check that the "ping" delivery shows 200.
4. Open a **new** PR (#4) in the demo repo that deletes `/v1/session-refresh` with the description:

   ```text
   Cleanup.

   Removes: /v1/session-refresh
   Cites: ADR-12
   ```

**Done when:** within seconds PR #4 gets the BLOCK comment and failed status, without touching the dashboard.
Redelivering with a wrong secret returns 401.

## Task 8. FalkorDB Cloud compatibility

**Why not done before:** no account. The free tier might restrict `GRAPH.COPY`, `algo.*` procedures or the
number of graphs.

```bash
cd backend
FALKORDB_URL="redis://default:PASSWORD@HOST:PORT" python -m pytest -q tests/test_smoke.py
```

**Done when:** 7 smoke tests pass (they create and delete `test_*` graphs only). Then run the full suite the same
way.
**If it fails:** if TLS is required, use `rediss://` instead of `redis://`. If `GRAPH.COPY` or `algo.WCC` /
`algo.pageRank` is unavailable, the cloud tier cannot run ShadowGraph. Use a self-hosted FalkorDB
(`falkordb/falkordb` image on Railway or Fly.io) and note it in `docs/DEPLOY.md`.

## Task 9. Live deployment (the hackathon prefers a live link)

**Why not done before:** no Render or FalkorDB Cloud account; the Docker image was never built.

Follow `docs/DEPLOY.md` (Render blueprint `render.yaml` + FalkorDB from Task 8).

**Done when:**
- `https://<service>.onrender.com/api/health` returns `ok: true` with the fastembed embedder;
- the dashboard runs PR 1 → BLOCK there;
- MCP over HTTP works remotely, checked with `npx @modelcontextprotocol/inspector` or a short Python client
  (`mcp.Client("https://<service>.onrender.com/mcp")` listing 17 tools). If you get HTTP 421/403, the host is not
  allowed: set `PUBLIC_URL` correctly or add `MCP_ALLOWED_HOSTS`.

Render's free plan has 512 MB of RAM. If the service is killed for memory, set `EMBED_BACKEND=hash` there; it
still works, with weaker similarity. Note it in `implemented.md`.

## Task 10. MCP clients (Cursor, Claude Desktop)

**Why not done before:** neither app was available in the sandbox. MCP was verified with the official Python
client over HTTP and stdio only.

Follow `docs/MCP_CLIENTS.md`.

**Done when:**
- Cursor (`.cursor/mcp.json` with `"url": "http://localhost:8000/mcp"`) lists 17 tools, and the prompt "Use
  shadowgraph to check PR 1 in tenant acme" returns BLOCK.
- Claude Desktop works with option A (`docker compose -f <abs path> exec -T app python -m shadowgraph mcp`) or
  option B (venv Python).

Record a 10-second screen clip of Cursor doing this for the demo video.

## Task 11. CI on GitHub Actions

**Why not done before:** the workflow `.github/workflows/ci.yml` was written but never run.

Push the project to the person's public GitHub repo (`shadowgraph`). The person should make the commits so the
history is theirs. Open the Actions tab.

**Done when:** jobs `backend`, `frontend` and `docker` are green. The `docker` job builds the image, starts it with
FalkorDB, runs PR 1 and greps for BLOCK.
**If it fails:** the `services.falkordb.options` health command needs `redis-cli` in the image (see Task 2). The
smoke step sleeps 5 seconds before reading runs; raise it if the runner is slow.

## Task 12. Moving agent memory between machines (optional)

**Why not done before:** needs Docker. The commands in `docs/TRANSFER.md` (export and import of the volume
`shadowgraph_falkordb-data`) are untested.

**Done when:** after export on one machine and import on another, the Memory tab shows the same counts. If the
volume name differs (compose prefixes the folder name), find it with `docker volume ls` and fix the doc.

## Task 13. Hackathon submission (help the person; they own these)

- [ ] Confirm the deadline extension in writing; the official page says Oct 18, 2026, 11:59 PM IST.
- [ ] Public repo pushed by the person, with README links filled in: repo URL, live URL (Task 9), video URL.
- [ ] Demo video, about 2:45, following `docs/DEMO_SCRIPT.md` (Home Assistant first), using the numbers on screen.
- [ ] Blog post: edit `docs/BLOG_DRAFT.md` into the person's own voice and publish (Dev.to or Hashnode). Replace
      `<link>` with the repo URL.
- [ ] Submission form: name **all three tracks**, link the repo, video and live demo, and mention the AI-assistance
      disclosure in the README.
- [ ] Optional polish: a GIF of a replay at the top of the README (`docs/img/`), and a CI badge.

---

---

## Task 14. Home Assistant through the GitHub API (real PR titles)

**Why not done before:** api.github.com was blocked in the build sandbox (git worked, so the git source was used).

1. Put a read-only token in `.env`: `GITHUB_TOKEN=...` (ask the person). Leave `HA_CLONE` empty, `HA_SOURCE=auto`.
2. Start the app, open Pull requests, press **Poll now**.

**Done when:** the newest open PRs appear with their real titles (not commit subjects), and
`curl -X POST localhost:8000/api/ha/feed/poll` returns `"source": "github"` without errors. Replay #186863 by
number: it must still report 32 (the API path uses `pulls/N/files` and the compare endpoint for the merge base).
**If it fails:** `ha/sources.py` `GitHubSource`. A 403 with `x-ratelimit-remaining: 0` means no token was sent.

## Task 15. Home Assistant through a local clone (git source)

```bash
git clone --shallow-since="$(date -d '-14 days' +%F)" --branch dev --single-branch https://github.com/home-assistant/core ../ha-core
# Windows PowerShell: --shallow-since=(Get-Date).AddDays(-14).ToString('yyyy-MM-dd')
```

Set `HA_CLONE=../ha-core` (absolute path is safer), restart, **Poll now**, and replay #186863.
**Done when:** both work and `../ha-core-prs.git` exists (PR refs are fetched there, never into the clone).
`git -C ../ha-core log origin/dev --oneline | wc -l` must not shrink after a poll.

## Task 16. Refresh the snapshot (optional, takes about 30 minutes)

```bash
python scripts/fetch_ha_sources.py --dir ../ha-sources --build --out ../ha_snapshot_new.json.gz
```

Compare with the shipped one: `python -c "import gzip,json;print(json.load(gzip.open('../ha_snapshot_new.json.gz'))['meta'])"`.
If counts are in the same range (1,500+ core, 3,000+ HACS integrations, parse failures in single digits), copy it
to `data/ha_snapshot.json.gz`, restart, and update the numbers in README.md, docs/HOME_ASSISTANT.md and
docs/DEMO_SCRIPT.md from the dashboard. **Done when:** the app loads it and the self-check holds: no core use of a
missing name (see `tests/test_ha.py::test_snapshot_self_check` for the rule).

## Task 17. Install counts (feature, about 1 hour)

Home Assistant publishes opt-in analytics per custom integration at
<https://analytics.home-assistant.io/custom_integrations.json> (domain → installations). Add an optional
`--analytics` step to `ha/importer.py` that stores `installs` on each external integration, load it in
`ha/load.py`, and show "~N installations" next to each affected integration and in the verdict headline
("Breaks 32 custom integrations (about N installations)"). Keep it optional: the build must work without it.
**Done when:** a test covers the loader path with a small fake file, and the report shows the numbers.

## Task 18. Docker image with the snapshot

The Dockerfile now installs git and copies `data/`. Build and run it (`docker compose up --build`).
**Done when:** the image starts, `/api/ha/overview` says `"loaded": true` within a minute, and the three replays
work with `GITHUB_TOKEN` set (Docker has no clone by default). Note the image size in `implemented.md`.

## Task 19. Live feed on the deployment

On Render (Task 9), set `HA_FEED=1`, `HA_FEED_INTERVAL=300`, `GITHUB_TOKEN` (read-only) and `ADMIN_TOKEN`.
**Done when:** after an hour the Pull requests page shows a dozen or more checked open PRs and the server log has
no feed errors. Watch the rate limit: each poll costs about 3 requests per new PR.

## Report back (fill this in, then add it to `implemented.md` section 8)

| Task | Result (pass / fixed / blocked) | What changed | Measured numbers |
| --- | --- | --- | --- |
| 1 Baseline tests | | | tests passed: |
| 2 Docker build + compose | | | image size, startup time |
| 3 Real embeddings + thresholds | | | PR1→PR2 distance, unrelated distance, new thresholds |
| 4 PowerShell scripts | | | |
| 5 Gemini | | | model id used |
| 6 GitHub posting | | | |
| 7 Webhook | | | |
| 8 FalkorDB Cloud | | | |
| 9 Live deployment | | | URL |
| 10 MCP clients | | | |
| 11 CI | | | |
| 12 Memory transfer | | | |
| 13 Submission | | | |

Also report: PR 1/2/3 verdicts, agent steps and run time; `GRAPH.COPY` time; anything in sections 0–1 that turned
out wrong.
