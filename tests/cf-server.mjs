/* テスト用: Cloudflare Pages の代わりにローカルで動かすサーバー（ネットなし）
   - リポジトリのファイルをそのまま配り、/api は functions/api.js に渡す
   - D1 の代わりに Node の組み込み SQLite（:memory:）を使う（prepare/bind/run/all/batch だけ）
   使い方: node tests/cf-server.mjs 8788 */
import http from "node:http";
import { readFile } from "node:fs/promises";
import { join, extname, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { DatabaseSync } from "node:sqlite";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const port = +(process.argv[2] || 8788);
const api = await import(pathToFileURL(join(ROOT, "functions", "api.js")).href);

const sql = new DatabaseSync(":memory:");
class Stmt {
  constructor(q, args = []) { this.q = q; this.args = args; }
  bind(...a) { return new Stmt(this.q, a); }
  exec() {
    const s = sql.prepare(this.q);
    if (/^\s*(SELECT|WITH)/i.test(this.q)) return { results: s.all(...this.args), success: true };
    s.run(...this.args); return { results: [], success: true };
  }
  async run() { return this.exec(); }
  async all() { return this.exec(); }
}
const DB = {
  prepare: (q) => new Stmt(q),
  async batch(list) { sql.exec("BEGIN"); try { const r = list.map((s) => s.exec()); sql.exec("COMMIT"); return r; } catch (e) { sql.exec("ROLLBACK"); throw e; } },
};

const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".mjs": "text/javascript", ".png": "image/png", ".webmanifest": "application/manifest+json", ".json": "application/json", ".css": "text/css" };

http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  try {
    if (url.pathname === "/api") {
      const body = await new Promise((ok) => { let d = ""; req.on("data", (c) => (d += c)); req.on("end", () => ok(d)); });
      const request = new Request(url, { method: req.method, headers: req.headers, body: req.method === "POST" ? body : undefined });
      const fn = { GET: api.onRequestGet, POST: api.onRequestPost, OPTIONS: api.onRequestOptions }[req.method];
      const r = fn ? await fn({ request, env: { DB } }) : new Response("method", { status: 405 });
      res.writeHead(r.status, Object.fromEntries(r.headers)); res.end(Buffer.from(await r.arrayBuffer())); return;
    }
    const p = url.pathname === "/" ? "/index.html" : decodeURIComponent(url.pathname);
    const data = await readFile(join(ROOT, p));
    res.writeHead(200, { "Content-Type": TYPES[extname(p)] || "application/octet-stream" }); res.end(data);
  } catch (e) {
    res.writeHead(404); res.end("not found");
  }
}).listen(port, "127.0.0.1", () => console.log("ready " + port));
