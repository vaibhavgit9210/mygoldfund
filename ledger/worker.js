// mygoldfund US panel ledger: Cloudflare Worker + one SQLite Durable Object.
//
// POST /record            {rows:[...]} from scripts/us_build.py, Bearer LEDGER_KEY, max 4 MB
// GET  /                  JSON banner with the endpoint list
// GET  /ledger?kind=score&from=YYYY-MM-DD&to=YYYY-MM-DD&sym=SPY&limit=5000   public, {rows:[...]}
// GET  /summary           public: counts per kind, first/last date, last run row, dayNumber
// GET  /latest?kind=rec&limit=10   public: last rows of one kind, or the last row of every kind
// cron (UTC)              dispatches .github/workflows/us.yml when the GH_PAT secret exists
//
// Every ledger kind (spec section 6) has its own table. The natural key is UNIQUE and rows
// go in with INSERT OR REPLACE, so re-posting a batch replaces instead of duplicating.
// Nested objects live in JSON text columns. Fields the schema does not know are kept in
// an `extra` JSON column and merged back on read, so the mirror is lossless.

const MAX_BODY = 4 * 1024 * 1024;
const MAX_LIMIT = 20000;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const GH_DISPATCH_URL =
  'https://api.github.com/repos/vaibhavgit9210/mygoldfund/actions/workflows/us.yml/dispatches';

const ALLOWED_ORIGINS = [
  'https://vaibhavkumar.is-a.dev',
  'https://vaibhavgit9210.github.io',
];

// Column types: t text, n real, i integer, b boolean (stored 0/1), j JSON text.
const KINDS = {
  run: {
    table: 'runs', key: ['date', 'session'],
    cols: { date: 't', session: 't', ranAt: 't', etTime: 't', lateMin: 'n', degraded: 'b',
            srcCounts: 'j', nSyms: 'i', dayNumber: 'i' },
  },
  reading: {
    table: 'readings', key: ['date', 'session', 'sym'],
    cols: { date: 't', session: 't', sym: 't', pre: 'n', preTime: 'n', post: 'n', postTime: 'n',
            open: 'n', high: 'n', low: 'n', close: 'n', adjClose: 'n', vol: 'n', prevClose: 'n', src: 't' },
  },
  forecast: {
    table: 'forecasts', key: ['target', 'made', 'session', 'sym'], dateCol: 'target',
    cols: { target: 't', made: 't', session: 't', sym: 't', mu: 'n', sigma: 'n', pUp: 'n',
            lo90: 'n', hi90: 'n', lo50: 'n', hi50: 'n', alpha90: 'n', alpha50: 'n',
            experts: 'j', varExperts: 'j' },
  },
  score: {
    table: 'scores', key: ['date', 'session', 'sym'],
    cols: { date: 't', sym: 't', session: 't', r: 'n', gap: 'n', intraday: 'n', rvYz: 'n', hit: 'n',
            brier: 'n', logs: 'n', crps: 'n', pin: 'j', cov90: 'n', cov50: 'n', pit: 'n',
            qlike: 'j', base: 'j' },
  },
  rec: {
    table: 'recs', key: ['month', 'sleeve', 'date'],
    cols: { month: 't', date: 't', sleeve: 't', shadow: 'b', budgetUsd: 'n', deployFrac: 'n',
            regime: 'j', vixRatio: 'n', picks: 'j', heldUsd: 'n', baseline: 'j', cost: 'j', fill: 'j' },
  },
  fan: {
    table: 'fans', key: ['date', 'sym'],
    cols: { date: 't', sym: 't', q: 'j', drift: 'n', driftLR: 'n', w: 'n' },
  },
  paper: {
    table: 'paper', key: ['date', 'sleeve'],
    cols: { date: 't', sleeve: 't', value: 'n', invested: 'n', baselineValue: 'n' },
  },
};

const SQL_TYPE = { t: 'TEXT', n: 'REAL', i: 'INTEGER', b: 'INTEGER', j: 'TEXT' };
const dateCol = spec => spec.dateCol || 'date';
// Own properties only: kind="constructor" or "__proto__" must not resolve to Object.prototype.
const kindSpec = k => (typeof k === 'string' && Object.prototype.hasOwnProperty.call(KINDS, k)) ? KINDS[k] : null;

function encode(type, v) {
  if (v === undefined || v === null) return null;
  if (type === 'j') return JSON.stringify(v);
  if (typeof v === 'boolean') return v ? 1 : 0;
  if (typeof v === 'object') return JSON.stringify(v);
  return v;
}

function decode(kind, row) {
  const spec = KINDS[kind];
  const out = { kind };
  for (const [name, type] of Object.entries(spec.cols)) {
    const v = row[name];
    out[name] = v == null ? null : type === 'j' ? JSON.parse(v) : type === 'b' ? !!v : v;
  }
  if (row.extra) Object.assign(out, JSON.parse(row.extra));
  return out;
}

function allowOrigin(origin) {
  if (ALLOWED_ORIGINS.includes(origin)) return origin;
  if (/^http:\/\/localhost(:\d+)?$/.test(origin)) return origin;
  if (origin === 'null') return 'null';
  return ALLOWED_ORIGINS[0];
}

function corsHeaders(req) {
  return {
    'Access-Control-Allow-Origin': allowOrigin(req.headers.get('Origin') || ''),
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    'Access-Control-Max-Age': '86400',
    'Vary': 'Origin',
  };
}

const json = (obj, status = 200, headers = {}) =>
  new Response(JSON.stringify(obj), {
    status, headers: { 'Content-Type': 'application/json', ...headers },
  });

export class Ledger {
  constructor(state) {
    this.sql = state.storage.sql;
    for (const spec of Object.values(KINDS)) {
      const cols = Object.entries(spec.cols).map(([n, t]) => `${n} ${SQL_TYPE[t]}`).join(', ');
      this.sql.exec(`CREATE TABLE IF NOT EXISTS ${spec.table}(${cols}, extra TEXT,
        UNIQUE(${spec.key.join(', ')}))`);
      this.sql.exec(`CREATE INDEX IF NOT EXISTS idx_${spec.table}_date ON ${spec.table}(${dateCol(spec)})`);
      if ('sym' in spec.cols)
        this.sql.exec(`CREATE INDEX IF NOT EXISTS idx_${spec.table}_sym ON ${spec.table}(sym)`);
    }
  }

  async fetch(req) {
    const url = new URL(req.url);
    let out;
    if (url.pathname === '/record' && req.method === 'POST') out = this.record(await req.json());
    else if (url.pathname === '/ledger') out = this.ledger(url.searchParams);
    else if (url.pathname === '/summary') out = this.summary();
    else if (url.pathname === '/latest') out = this.latest(url.searchParams);
    else return json({ error: 'not found' }, 404);
    return json(out, out.error ? 400 : 200);
  }

  record(body) {
    const rows = body && Array.isArray(body.rows) ? body.rows : null;
    if (!rows) return { error: 'body must be {rows:[...]}' };
    const byKind = {};
    const skipped = [];
    rows.forEach((row, i) => {
      const spec = row && kindSpec(row.kind);
      if (!spec) { skipped.push({ i, reason: `unknown kind ${row && row.kind}` }); return; }
      const missing = spec.key.filter(k => row[k] == null);
      if (missing.length) { skipped.push({ i, reason: `${row.kind} missing ${missing.join(', ')}` }); return; }
      const names = Object.keys(spec.cols);
      const extra = {};
      for (const k of Object.keys(row)) if (k !== 'kind' && !(k in spec.cols)) extra[k] = row[k];
      this.sql.exec(
        `INSERT OR REPLACE INTO ${spec.table}(${names.join(',')},extra) VALUES(${names.map(() => '?').join(',')},?)`,
        ...names.map(n => encode(spec.cols[n], row[n])),
        Object.keys(extra).length ? JSON.stringify(extra) : null);
      byKind[row.kind] = (byKind[row.kind] || 0) + 1;
    });
    const inserted = rows.length - skipped.length;
    return { inserted, byKind, skipped: skipped.length, skippedRows: skipped.slice(0, 20) };
  }

  ledger(params) {
    const kind = params.get('kind');
    const spec = kindSpec(kind);
    if (!spec) return { error: `kind must be one of ${Object.keys(KINDS).join(', ')}` };
    const dc = dateCol(spec);
    const where = [];
    const args = [];
    for (const [name, op] of [['from', '>='], ['to', '<=']]) {
      const v = params.get(name);
      if (!v) continue;
      if (!DATE_RE.test(v)) return { error: `${name} must be YYYY-MM-DD` };
      where.push(`${dc} ${op} ?`);
      args.push(v);
    }
    const sym = params.get('sym');
    if (sym && 'sym' in spec.cols) { where.push('sym = ?'); args.push(sym); }
    const limit = Math.min(MAX_LIMIT, Math.max(1, parseInt(params.get('limit'), 10) || 5000));
    const rows = this.sql.exec(
      `SELECT * FROM ${spec.table}` + (where.length ? ' WHERE ' + where.join(' AND ') : '') +
      ` ORDER BY ${dc}, ${spec.key.join(', ')} LIMIT ?`, ...args, limit)
      .toArray().map(r => decode(kind, r));
    return { kind, n: rows.length, truncated: rows.length === limit, rows };
  }

  summary() {
    const kinds = {};
    let firstDate = null, lastDate = null;
    for (const [kind, spec] of Object.entries(KINDS)) {
      const dc = dateCol(spec);
      const s = this.sql.exec(`SELECT COUNT(*) n, MIN(${dc}) first, MAX(${dc}) last FROM ${spec.table}`).one();
      kinds[kind] = { n: s.n, first: s.first, last: s.last };
      if (s.first && (!firstDate || s.first < firstDate)) firstDate = s.first;
      if (s.last && (!lastDate || s.last > lastDate)) lastDate = s.last;
    }
    const lastRun = this.sql.exec('SELECT * FROM runs ORDER BY date DESC, ranAt DESC LIMIT 1').toArray()[0];
    const dayNumber = this.sql.exec('SELECT COUNT(DISTINCT date) c FROM runs').one().c;
    return { kinds, firstDate, lastDate, dayNumber, lastRun: lastRun ? decode('run', lastRun) : null };
  }

  latest(params) {
    const last = (kind, n) => {
      const spec = KINDS[kind];
      return this.sql.exec(`SELECT * FROM ${spec.table} ORDER BY ${dateCol(spec)} DESC, rowid DESC LIMIT ?`, n)
        .toArray().map(r => decode(kind, r));
    };
    const kind = params.get('kind');
    if (kind) {
      if (!kindSpec(kind)) return { error: `kind must be one of ${Object.keys(KINDS).join(', ')}` };
      const limit = Math.min(500, Math.max(1, parseInt(params.get('limit'), 10) || 10));
      return { kind, rows: last(kind, limit) };
    }
    const latest = {};
    for (const k of Object.keys(KINDS)) latest[k] = last(k, 1)[0] || null;
    return { latest };
  }
}

// Minutes past midnight in New York for a Date (the Worker clock is UTC).
function etMinutes(d) {
  const p = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: 'numeric', hour12: false })
    .formatToParts(d);
  const get = t => Number(p.find(x => x.type === t).value);
  return (get('hour') % 24) * 60 + get('minute');
}

async function dispatchUsRun(env, cron) {
  // The two UTC hours per slot cover EDT and EST; only one of them is the real slot. The other
  // lands at 07:35 or 15:25 ET: the pre run would fire an hour early (and the 08:35 slot would
  // then be a no-op), the post one would find the market open. Skip those.
  const m = etMinutes(new Date());
  const slot = (m >= 8 * 60 + 30 && m < 9 * 60 + 30) || (m >= 16 * 60 + 15 && m < 18 * 60);
  if (!slot) {
    console.log(`cron ${cron}: ${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')} ET is not a run slot, skipping`);
    return;
  }
  if (!env.GH_PAT) {
    console.log(`cron ${cron}: GH_PAT not set, nothing to dispatch`);
    return;
  }
  const r = await fetch(GH_DISPATCH_URL, {
    method: 'POST',
    headers: {
      'Accept': 'application/vnd.github+json',
      'Authorization': `Bearer ${env.GH_PAT}`,
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'mygoldfund-ledger',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ ref: 'main', inputs: { session: 'auto' } }),
  });
  console.log(`cron ${cron}: us.yml dispatch -> ${r.status}${r.status === 204 ? '' : ' ' + await r.text()}`);
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const cors = corsHeaders(req);
    if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });

    if (url.pathname === '/' && req.method === 'GET') {
      return json({
        name: 'mygoldfund-ledger',
        endpoints: {
          'POST /record': 'Bearer LEDGER_KEY, body {rows:[...]}, max 4 MB',
          'GET /ledger': 'kind=run|reading|forecast|score|rec|fan|paper, from, to, sym, limit (default 5000)',
          'GET /summary': 'counts per kind, first/last date, last run, dayNumber',
          'GET /latest': 'kind=..., limit (default 10); without kind, the last row of every kind',
        },
      }, 200, cors);
    }

    const stub = env.LEDGER.get(env.LEDGER.idFromName('main'));

    if (url.pathname === '/record') {
      if (req.method !== 'POST') return json({ error: 'method not allowed' }, 405, cors);
      const auth = req.headers.get('Authorization') || '';
      if (!env.LEDGER_KEY || auth !== `Bearer ${env.LEDGER_KEY}`)
        return json({ error: 'unauthorized' }, 401, cors);
      if (Number(req.headers.get('Content-Length') || 0) > MAX_BODY)
        return json({ error: 'body over 4 MB' }, 413, cors);
      const text = await req.text();
      if (new TextEncoder().encode(text).length > MAX_BODY)
        return json({ error: 'body over 4 MB' }, 413, cors);
      let body;
      try { body = JSON.parse(text); } catch { return json({ error: 'invalid JSON' }, 400, cors); }
      if (!body || !Array.isArray(body.rows)) return json({ error: 'body must be {rows:[...]}' }, 400, cors);
      const r = await stub.fetch('https://do/record', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: text,
      });
      return new Response(await r.text(), { status: r.status, headers: { ...cors, 'Content-Type': 'application/json' } });
    }

    if (req.method === 'GET' && ['/ledger', '/summary', '/latest'].includes(url.pathname)) {
      const r = await stub.fetch('https://do' + url.pathname + url.search);
      return new Response(await r.text(), {
        status: r.status,
        headers: { ...cors, 'Content-Type': 'application/json', 'Cache-Control': 'public, max-age=60' },
      });
    }

    return json({ error: 'not found' }, 404, cors);
  },

  async scheduled(event, env, ctx) {
    ctx.waitUntil(dispatchUsRun(env, event.cron));
  },
};
