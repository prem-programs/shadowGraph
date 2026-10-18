# Moving ShadowGraph to another laptop

Three ways, best first. Never copy `.env` through a public place: it holds your tokens. Recreate it on the new
laptop from `.env.example`.

## Option 1. Through GitHub (recommended)

The project lives at <https://github.com/prem-programs/shadowGraph>. On the new laptop:

```bash
git clone https://github.com/prem-programs/shadowGraph.git shadowgraph
cd shadowgraph
copy .env.example .env      # Windows (macOS/Linux: cp .env.example .env), then fill in values
docker compose up --build
```

`.gitignore` keeps out `.env`, `node_modules`, builds and virtualenvs; the Home Assistant snapshot
(`data/ha_snapshot.json.gz`, about 6 MB) is in the repository, so nothing else needs downloading.
This is also what the hackathon needs: a public repository.

## Option 2. Zip file (USB drive, Google Drive, WeTransfer)

Use the `shadowgraph.zip` delivered with this build, or make a fresh one that leaves out bulky folders:

- Windows: `powershell -ExecutionPolicy Bypass -File scripts\package.ps1` (writes `shadowgraph.zip` next to the folder)
- macOS/Linux/Git Bash: `bash scripts/package.sh`

On the new laptop: unzip, then in the folder run `copy .env.example .env` and `docker compose up --build`
(or `scripts\start.ps1`).

## Option 3. A ready-built Docker image (no build on the new laptop)

On a laptop where the image is built (`docker compose build`):

```bash
docker save shadowgraph:latest falkordb/falkordb:latest -o shadowgraph-images.tar     # roughly 1 GB
```

Copy `shadowgraph-images.tar` and the project zip. On the new laptop:

```bash
docker load -i shadowgraph-images.tar
docker compose up            # no --build: uses the loaded images
```

Agent memory lives in the Docker volume `shadowgraph_falkordb-data`. To move it too:
`docker run --rm -v shadowgraph_falkordb-data:/d -v ${PWD}:/b alpine tar czf /b/falkordb-data.tgz -C /d .`
(PowerShell, in the project folder, with the app stopped) on the old laptop, and
`docker run --rm -v shadowgraph_falkordb-data:/d -v ${PWD}:/b alpine tar xzf /b/falkordb-data.tgz -C /d` on the new one. Usually not needed: **Reset demo** rebuilds
everything from the seed.

## What the new laptop needs

| For | Install |
| --- | --- |
| Docker route (simplest) | Docker Desktop (Windows: with WSL 2; a restart may be needed) |
| Running without Docker | Python 3.12 or 3.13, Node.js 20.19+, and a FalkorDB URL (FalkorDB Cloud) |
| Editing and pushing | Git (and optionally VS Code or Cursor) |

Check: open <http://localhost:8000>; the top bar should say "FalkorDB connected".
