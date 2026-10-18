# acme-payments-demo

A tiny public repo that holds the three pull requests ShadowGraph checks in its demo. The company
("Acme Payments"), its services and its people are synthetic.

## Set it up (once)

1. On GitHub, create an **empty public repo** named `acme-payments-demo` (no README, no license), so its first
   PRs get the numbers 1, 2 and 3.
2. Push this folder to it:

   ```bash
   cd acme-payments-demo
   git init -b main
   git add .
   git commit -m "auth-service stub"
   git remote add origin https://github.com/<you>/acme-payments-demo.git
   git push -u origin main
   ```

3. Create the three PRs, **in this order**:
   - With the GitHub CLI: `bash scripts/create-demo-prs.sh`
   - Or by hand on github.com: open `auth_service/main.py`, click the pencil, delete the route (the `@app...`
     line and its function), choose "Create a new branch and start a pull request", use the title below and
     paste the matching file from `pr-bodies/` as the description.

| PR | Title | Delete | Description file |
| --- | --- | --- | --- |
| #1 | Remove /v1/token-verify | `/v1/token-verify` | `pr-bodies/1-remove-token-verify.md` |
| #2 | Remove /v1/session-refresh | `/v1/session-refresh` | `pr-bodies/2-remove-session-refresh.md` |
| #3 | Remove unused /v1/jwks | `/v1/jwks` | `pr-bodies/3-remove-jwks.md` |

If your PRs got other numbers, set `PR_TOKEN_VERIFY`, `PR_SESSION_REFRESH` and `PR_JWKS` in ShadowGraph's `.env`.

The description lines `Removes:` and `Cites:` are what ShadowGraph's webhook reads to update the company graph.
