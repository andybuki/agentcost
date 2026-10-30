// Minimal stand-in for Cloudflare D1 on top of node:sqlite, for tests and
// local runs only. Implements the subset the worker uses.
import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";

export function makeD1(path = ":memory:") {
  const db = new DatabaseSync(path);
  db.exec(readFileSync(new URL("../schema.sql", import.meta.url), "utf8"));
  const prepare = (sql, params = []) => ({
    bind: (...p) => prepare(sql, p),
    run: async () => ({ meta: { changes: Number(db.prepare(sql).run(...params).changes) } }),
    all: async () => ({ results: db.prepare(sql).all(...params) }),
  });
  return {
    prepare,
    async batch(stmts) {
      db.exec("BEGIN");
      try {
        const out = [];
        for (const s of stmts) out.push(await s.run());
        db.exec("COMMIT");
        return out;
      } catch (e) { db.exec("ROLLBACK"); throw e; }
    },
  };
}
