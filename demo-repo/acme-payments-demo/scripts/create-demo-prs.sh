#!/usr/bin/env bash
# Creates the three demo PRs in your demo repo. Run from the repo root (Git Bash works on Windows).
# Needs: git, and the GitHub CLI (gh) logged in:  gh auth login
set -euo pipefail
command -v gh >/dev/null || { echo "Install the GitHub CLI first: https://cli.github.com (or follow README.md by hand)"; exit 1; }
git checkout main
make_pr () {
  local branch="$1" title="$2" body="$3" start="$4" end="$5"
  git checkout -B "$branch" main
  # delete the route: the decorator line and the function under it
  python - "$start" "$end" <<'PY'
import re, sys
start, end = sys.argv[1], sys.argv[2]
path = "auth_service/main.py"
src = open(path, encoding="utf-8").read()
pattern = re.compile(r"\n\n@app\.(?:get|post)\(\"" + re.escape(start) + r"\"\).*?(?=\n\n\n|\n\n@app|\Z)", re.S)
new, n = pattern.subn("", src)
if n != 1:
    sys.exit(f"route {start} not found")
open(path, "w", encoding="utf-8").write(new.rstrip() + "\n")
PY
  git commit -am "$title"
  git push -u origin "$branch" --force
  gh pr create --title "$title" --body-file "$body" --base main --head "$branch" || echo "PR for $branch may already exist"
  git checkout main
}
make_pr remove-token-verify   "Remove /v1/token-verify"    pr-bodies/1-remove-token-verify.md    /v1/token-verify x
make_pr remove-session-refresh "Remove /v1/session-refresh" pr-bodies/2-remove-session-refresh.md /v1/session-refresh x
make_pr remove-jwks           "Remove unused /v1/jwks"     pr-bodies/3-remove-jwks.md            /v1/jwks x
gh pr list
