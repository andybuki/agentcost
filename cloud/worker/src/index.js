// agentcost collector: receives usage metadata from Claude Code cloud sessions
// and hands it to `python -m agentcost sync` on your PC.
//
//   POST /ingest          Authorization: Bearer INGEST_TOKEN   {"turns": [...]}
//   GET  /turns?after=N   Authorization: Bearer READ_TOKEN     -> {"turns": [...], "last": M}
//
// Two tokens: a leaked ingest token (it sits in your cloud environment) can add
// rows but never read them.

const MAX_TURNS = 1000;       // per request
const MAX_BODY = 2_000_000;   // bytes
const PAGE = 1000;

const STR_FIELDS = { msg_id: 300, request_id: 200, ts: 40, session: 100, cwd: 1000, branch: 300, model: 100 };
const INT_FIELDS = ["in", "out", "cw", "cr"];

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status, headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}

// Constant-time comparison, so response timing reveals nothing about the token.
// Both sides are trimmed: pasting a secret into `wrangler secret put` on Windows
// can add an invisible line break, which would otherwise never match.
function tokenOk(request, expectedRaw) {
  const expected = (expectedRaw || "").trim();
  const got = (request.headers.get("authorization") || "").replace(/^Bearer /, "").trim();
  if (!expected || got.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < got.length; i++) diff |= got.charCodeAt(i) ^ expected.charCodeAt(i);
  return diff === 0;
}

export function validTurn(t) {
  if (!t || typeof t !== "object") return null;
  const row = {};
  for (const [k, max] of Object.entries(STR_FIELDS)) {
    const v = t[k] ?? "";
    if (typeof v !== "string" || v.length > max) return null;
    row[k] = v;
  }
  if (!row.msg_id || !row.ts) return null;
  for (const k of INT_FIELDS) {
    const v = t[k] ?? 0;
    if (!Number.isSafeInteger(v) || v < 0) return null;
    row[k] = v;
  }
  return row;
}

async function ingest(request, env) {
  if (!tokenOk(request, env.INGEST_TOKEN)) return json({ error: "unauthorized" }, 401);
  const text = await request.text();
  if (text.length > MAX_BODY) return json({ error: "body too large" }, 413);
  let body;
  try { body = JSON.parse(text); } catch { return json({ error: "invalid JSON" }, 400); }
  const turns = Array.isArray(body?.turns) ? body.turns : null;
  if (!turns || turns.length > MAX_TURNS) return json({ error: `'turns' must be an array of at most ${MAX_TURNS}` }, 400);
  const rows = turns.map(validTurn);
  if (rows.some((r) => !r)) return json({ error: "invalid turn" }, 400);
  if (!rows.length) return json({ received: 0, added: 0 });

  const now = new Date().toISOString();
  const stmt = env.DB.prepare(
    `INSERT OR IGNORE INTO turns (msg_id, request_id, ts, session, cwd, branch, model,
       tok_in, tok_out, cache_write, cache_read, received_at)
     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)`);
  const results = await env.DB.batch(rows.map((r) => stmt.bind(
    r.msg_id, r.request_id, r.ts, r.session, r.cwd, r.branch, r.model,
    r.in, r.out, r.cw, r.cr, now)));
  const added = results.reduce((a, res) => a + (res.meta?.changes || 0), 0);
  return json({ received: rows.length, added });
}

async function list(request, env, url) {
  if (!tokenOk(request, env.READ_TOKEN)) return json({ error: "unauthorized" }, 401);
  const after = Number.parseInt(url.searchParams.get("after") || "0", 10);
  if (!Number.isSafeInteger(after) || after < 0) return json({ error: "'after' must be a number" }, 400);
  const { results } = await env.DB.prepare(
    `SELECT seq, msg_id, request_id, ts, session, cwd, branch, model,
            tok_in AS "in", tok_out AS out, cache_write AS cw, cache_read AS cr
     FROM turns WHERE seq > ?1 ORDER BY seq LIMIT ?2`).bind(after, PAGE).all();
  return json({ turns: results, last: results.length ? results[results.length - 1].seq : after,
                more: results.length === PAGE });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    try {
      if (request.method === "POST" && url.pathname === "/ingest") return await ingest(request, env);
      if (request.method === "GET" && url.pathname === "/turns") return await list(request, env, url);
      if (request.method === "GET" && url.pathname === "/") return json({ service: "agentcost collector" });
      return json({ error: "not found" }, 404);
    } catch (e) {
      return json({ error: "server error" }, 500);
    }
  },
};
