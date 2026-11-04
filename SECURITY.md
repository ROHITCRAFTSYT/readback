# Security

Readback is decision support for air traffic control units, not a certified ATM system. It handles recorded radio, reviewer notes and API keys, so security reports are welcome.

## Reporting a vulnerability

Please report it privately: use "Report a vulnerability" on the repository's Security tab. If that button isn't there, open an issue that only asks for a private contact, with no details of the problem.

## What Readback protects

- **Keys stay local.** `MODEL_API_KEY` and other secrets are read from `.env`, which is gitignored. `.env.example` holds placeholders only.
- **Access control.** With `READBACK_TOKEN` set, every `/api` and audio request needs the token, as a bearer header or an HttpOnly, SameSite=Strict cookie, compared in constant time. `/healthz`, `/readyz` and `/metrics` stay open for probes and expose counts only, never names, units or callsigns.
- **Uploads.** Size-limited even for chunked bodies, decoded audio capped at the length limit, the job queue bounded (`READBACK_MAX_QUEUE`), and the files served by id only.
- **Data at rest.** Recordings, verdicts and reports live in one SQLite file plus audio under `READBACK_DATA`. SQL is parameterised, and only known columns can be updated. `READBACK_RETENTION_DAYS` deletes old recordings.
- **Exports.** The CSV alert register neutralises spreadsheet formulas.
- **Response headers.** `nosniff`, `X-Frame-Options: DENY` and `no-referrer`.

## What is checked

- Every push runs CI: tests, ruff, and a Docker build with a smoke test. Workflow actions are pinned to commit SHAs, the token is read-only, and checkout keeps no credentials.
- GitHub secret scanning and push protection are on for the repository.
- The last full audit was on Nov 4, 2026:
  - no secrets anywhere in git history
  - no known vulnerabilities in the locked dependencies (pip-audit)
  - no workflow findings (zizmor)
  - bandit's medium findings reviewed: the SQL it flags builds only from fixed column names, with values passed as parameters

## Data

ATCO2-derived labels are never committed; only their hashes are. UWB-ATCC-derived labels contain no transcript text and are shared under CC BY-NC-SA 4.0. See "Data and licenses" in the README.
