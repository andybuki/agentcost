// Serve the worker locally over HTTP with the D1 stand-in:
//   INGEST_TOKEN=a READ_TOKEN=b node test/dev-server.mjs 8787
import { createServer } from "node:http";
import worker from "../src/index.js";
import { makeD1 } from "./d1-shim.mjs";

const env = { DB: makeD1(), INGEST_TOKEN: process.env.INGEST_TOKEN, READ_TOKEN: process.env.READ_TOKEN };
const port = Number(process.argv[2] || 8787);
createServer(async (req, res) => {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const body = chunks.length ? Buffer.concat(chunks) : undefined;
  const request = new Request(`http://127.0.0.1:${port}${req.url}`,
    { method: req.method, headers: req.headers, body: req.method === "GET" ? undefined : body });
  const r = await worker.fetch(request, env);
  res.writeHead(r.status, Object.fromEntries(r.headers));
  res.end(Buffer.from(await r.arrayBuffer()));
}).listen(port, "127.0.0.1", () => console.log(`collector on http://127.0.0.1:${port}`));
