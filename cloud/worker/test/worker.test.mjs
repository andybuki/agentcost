import { test } from "node:test";
import assert from "node:assert/strict";
import worker, { validTurn } from "../src/index.js";
import { makeD1 } from "./d1-shim.mjs";

const env = () => ({ DB: makeD1(), INGEST_TOKEN: "in-tok", READ_TOKEN: "read-tok" });
const turn = (id, extra = {}) => ({ msg_id: id, request_id: "r", ts: "2026-10-30T10:00:00Z", session: "s",
  cwd: "/home/user/acme", branch: "main", model: "claude-sonnet-4-6", in: 1, out: 2, cw: 3, cr: 4, ...extra });
const post = (e, body, token = "in-tok") => worker.fetch(new Request("http://x/ingest", {
  method: "POST", headers: { authorization: `Bearer ${token}` }, body: JSON.stringify(body) }), e);
const get = (e, after = 0, token = "read-tok") => worker.fetch(new Request(`http://x/turns?after=${after}`,
  { headers: { authorization: `Bearer ${token}` } }), e);

test("ingest then list, idempotent", async () => {
  const e = env();
  let r = await post(e, { turns: [turn("a"), turn("b")] });
  assert.deepEqual(await r.json(), { received: 2, added: 2 });
  r = await post(e, { turns: [turn("a"), turn("c")] });
  assert.deepEqual(await r.json(), { received: 2, added: 1 });
  const page = await (await get(e)).json();
  assert.deepEqual(page.turns.map((t) => t.msg_id), ["a", "b", "c"]);
  assert.equal(page.turns[0].cr, 4);
  // Skipped duplicates can leave gaps in seq; the cursor only needs it to increase.
  assert.equal(page.last, page.turns[2].seq);
  assert.ok(page.turns[0].seq < page.turns[1].seq && page.turns[1].seq < page.turns[2].seq);
  const next = await (await get(e, page.last)).json();
  assert.deepEqual(next.turns, []);
});

test("tokens are separate", async () => {
  const e = env();
  assert.equal((await post(e, { turns: [turn("a")] }, "read-tok")).status, 401);
  assert.equal((await get(e, 0, "in-tok")).status, 401);
  assert.equal((await get(e, 0, "")).status, 401);
  assert.equal((await post({ ...e, INGEST_TOKEN: undefined }, { turns: [] }, "")).status, 401);
});

test("secrets with stray whitespace still match", async () => {
  const e = { ...env(), INGEST_TOKEN: "in-tok\r\n", READ_TOKEN: " read-tok " };
  assert.equal((await post(e, { turns: [turn("a")] })).status, 200);
  assert.equal((await get(e)).status, 200);
  assert.equal((await post({ ...e, INGEST_TOKEN: "\r\n" }, { turns: [] }, "")).status, 401);
});

test("rejects bad input", async () => {
  const e = env();
  assert.equal((await post(e, { turns: [turn("a", { in: -1 })] })).status, 400);
  assert.equal((await post(e, { turns: [turn("a", { cwd: 5 })] })).status, 400);
  assert.equal((await post(e, { turns: "x" })).status, 400);
  assert.equal((await post(e, { turns: Array(1001).fill(turn("a")) })).status, 400);
  const bad = await worker.fetch(new Request("http://x/ingest", {
    method: "POST", headers: { authorization: "Bearer in-tok" }, body: "{" }), e);
  assert.equal(bad.status, 400);
  assert.equal(validTurn(turn("")), null);
});

test("unknown fields are dropped, not stored", async () => {
  const e = env();
  await post(e, { turns: [turn("a", { prompt: "SECRET" })] });
  const page = await (await get(e)).json();
  assert.ok(!JSON.stringify(page).includes("SECRET"));
});
