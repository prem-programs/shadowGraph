# Deploying ShadowGraph (live link for judges)

One Docker image serves the API, `/mcp` and the dashboard. FalkorDB runs on FalkorDB Cloud.

## 1. FalkorDB Cloud

1. Sign up at <https://app.falkordb.cloud> and create a free instance.
2. Copy host, port and password, and build the URL: `redis://default:PASSWORD@HOST:PORT`
   (if the instance requires TLS, use `rediss://` instead of `redis://`).
3. Check that the instance supports everything ShadowGraph needs, from your machine:

   ```bash
   cd backend
   FALKORDB_URL="redis://default:PASSWORD@HOST:PORT" python -m pytest -q tests/test_smoke.py
   ```

   The tests only create and delete graphs named `test_*`. If `GRAPH.COPY` or an `algo.*` procedure is missing on
   the free tier, host FalkorDB yourself instead (for example the `falkordb/falkordb` image on Railway or Fly.io).

## 2. Render (free web service)

1. Push this project to GitHub.
2. Render → New → Blueprint → choose the repo. `render.yaml` defines the service.
3. Fill in the environment variables:
   - `FALKORDB_URL`: the URL from step 1
   - `PUBLIC_URL`: `https://<service-name>.onrender.com`
   - `GITHUB_TOKEN`: a **read-only** token (fine-grained, "Public repositories (read-only)") so the Home Assistant
     feed gets real PR titles and 5,000 API requests an hour instead of 60. It is used for reads only.
   - optional: `GEMINI_API_KEY`; `GITHUB_REPO` only for the Acme sample's PR comments (needs a write token for
     that repo instead, see the README)
   - the blueprint turns the Home Assistant live feed on (`HA_FEED=1`, every 5 minutes)
   - `GITHUB_WEBHOOK_SECRET` and `ADMIN_TOKEN` are generated; copy the webhook secret for step 3.
4. Deploy. The first build takes several minutes (it downloads the embedding model). Check
   `https://<service>.onrender.com/api/health`, then `/api/ha/overview` (`"loaded": true` about a minute after
   the first start, while the snapshot loads into FalkorDB).

FalkorDB memory: the Home Assistant graph is about 266,000 edges and 15,000 nodes. If a small free FalkorDB instance runs out of
memory, set `TENANTS=ha` (skip the samples) or use a larger instance.

Render's free tier sleeps after 15 minutes idle; open the URL a minute before judging or recording.

With `ADMIN_TOKEN` set (the blueprint generates one), the dashboard's **Reset demo** button is refused for
visitors. Reset between takes with
`curl -X POST -H "X-Admin-Token: <token>" https://<service>.onrender.com/api/admin/reset/acme`.

## 3. GitHub webhook (optional)

In the demo repo: Settings → Webhooks → Add webhook

- Payload URL: `https://<service>.onrender.com/api/webhooks/github`
- Content type: `application/json`
- Secret: the `GITHUB_WEBHOOK_SECRET` value
- Events: "Let me select individual events" → **Pull requests**

Opening or editing a PR now triggers a check, and the verdict appears on the PR.

## Other hosts

Any host that runs a Dockerfile works (Railway, Fly.io, a VM): build the image, set the same variables, expose port
8000. To host the dashboard separately (for example on Vercel), build `frontend/` with
`VITE_API_URL=https://<backend-host>` and set `CORS_ORIGINS=https://<dashboard-host>` on the backend.
