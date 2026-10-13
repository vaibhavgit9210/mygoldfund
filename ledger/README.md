# mygoldfund-ledger

Cloudflare Worker + one SQLite Durable Object that mirrors the US board's ledger
(`us/ledger.ndjson`). The GitHub Action POSTs every batch of rows it appends; the page
reads the public GET endpoints for history charts and falls back to the committed ndjson
if the Worker is unreachable. Account vaibhavpro9210, same setup as `../../site-analytics`.

Live at `https://mygoldfund-ledger.vaibhavpro9210.workers.dev` once deployed.

## Endpoints

| Path | Auth | What |
|---|---|---|
| `GET /` | public | JSON banner listing the endpoints |
| `POST /record` | `Authorization: Bearer <LEDGER_KEY>` | body `{rows:[...]}`, each row has a `kind`; returns `{inserted, byKind, skipped, skippedRows}` |
| `GET /ledger?kind=score&from=YYYY-MM-DD&to=YYYY-MM-DD&sym=SPY&limit=5000` | public | `{kind, n, truncated, rows}` ordered by date then natural key. `kind` is required. `from`/`to` filter the row's date (`target` for forecasts), `sym` only applies to kinds that have one. `limit` defaults to 5000, max 20000 |
| `GET /summary` | public | `{kinds:{kind:{n, first, last}}, firstDate, lastDate, dayNumber, lastRun}`. `dayNumber` = distinct dates with a `run` row |
| `GET /latest?kind=rec&limit=10` | public | last rows of one kind, newest first. Without `kind`: `{latest:{kind: lastRow}}` for every kind |

CORS on the GETs is open to `https://vaibhavkumar.is-a.dev`, `https://vaibhavgit9210.github.io`,
`http://localhost:<any port>` and the `null` origin (a `file://` page). Anything else gets the
is-a.dev origin back, which the browser then refuses. POST is server to server and needs the key.

## Storage

One table per kind: `runs`, `readings`, `forecasts`, `scores`, `recs`, `fans`, `paper`. Columns are the
fields from the spec's ledger section; nested objects (`srcCounts`, `experts`, `varExperts`, `pin`, `qlike`,
`base`, `regime`, `picks`, `baseline`, `cost`, `fill`, `q`) are JSON text and come back parsed. Booleans
(`degraded`, `shadow`) are stored 0/1 and come back as booleans. Any field the schema does not know goes into
an `extra` JSON column and is merged back on read, so nothing the builder sends is lost.

Natural keys are `UNIQUE` and rows go in with `INSERT OR REPLACE`, so re-posting a batch (a rerun with
`--force`, a retried request) replaces rather than duplicates:

| kind | natural key |
|---|---|
| run | date, session |
| reading | date, session, sym |
| forecast | target, made, session, sym |
| score | date, session, sym |
| rec | month, sleeve, date |
| fan | date, sym |
| paper | date, sleeve |

Rows with an unknown `kind` or a missing key field are skipped and reported in `skippedRows`
(first 20), the rest of the batch still goes in.

## Limits

- `POST /record` body max 4 MB (413 above that). A full post run is a few hundred KB.
- `GET /ledger` max 20000 rows per call; page through with `from`/`to` if you need more.
- GET responses carry `Cache-Control: public, max-age=60`.
- Cloudflare free plan: one Durable Object, a few requests a day, well inside the limits.

## Cron

`35 12,13 * * 1-5` and `25 20,21 * * 1-5` UTC, which land after the Action's own 08:35 and 16:25 ET
crons under both EDT and EST. `scheduled()` POSTs a `workflow_dispatch` for `.github/workflows/us.yml`
(`{"ref":"main","inputs":{"session":"auto"}}`) **only if the `GH_PAT` secret is set**; otherwise it logs
and returns. This is the belt and braces against GitHub dropping scheduled runs (and disabling them after
60 days of inactivity on a public repo). The Action is idempotent per (date, session) so a double firing
does nothing.

## Deploy

```bash
cd ledger
npx wrangler deploy
npx wrangler secret put LEDGER_KEY   # same value as the GitHub Actions secret LEDGER_KEY
npx wrangler secret put GH_PAT       # optional: fine grained PAT, Actions read/write on mygoldfund
```

`wrangler login` once in Brave (vaibhavpro9210) if the machine is not already logged in.

## Local test

```bash
cd ledger
printf 'LEDGER_KEY=devkey\n' > .dev.vars       # gitignored
npx wrangler dev --local --port 8787
```

Then from another shell:

```bash
curl -X POST http://localhost:8787/record -H 'Authorization: Bearer devkey' \
  -H 'Content-Type: application/json' \
  -d '{"rows":[{"kind":"paper","date":"2026-10-13","sleeve":"etf","value":30.4,"invested":30,"baselineValue":30.2}]}'
curl 'http://localhost:8787/ledger?kind=paper'
curl http://localhost:8787/summary
curl 'http://localhost:8787/latest?kind=paper'
# cron handler, no GH_PAT so it only logs
curl -X POST 'http://localhost:8787/cdn-cgi/local/explorer/api/local/scheduled?worker=mygoldfund-ledger' \
  -H 'Content-Type: application/json' -d '{"cron":"35 12,13 * * 1-5"}'
```

Local state lives in `.wrangler/` (gitignored); delete it to start from an empty database.
