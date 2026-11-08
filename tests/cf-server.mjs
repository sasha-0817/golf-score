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
const ocr = await import(pathToFileURL(join(ROOT, "functions", "ocr.js")).href);

// 写真読み取りのテスト用の偽のAI: 1つ目のモデルは数字を1つ読み間違え、2つ目は正しく読む（合計が合う方が選ばれるかを確かめる）
const L = [97, 65, 117, 68, 101, 65, 85, 92, 103], R = [95, 63, 123, 66, 118, 60, 76, 110, 120];
const answer = (bad) => JSON.stringify({ yard_columns: ["L", "R"], holes: L.map((l, i) => ({ hole: i + 1, par: 3, yards: [bad && i === 2 ? 177 : l, R[i]] })), totals: { par: 27, yards: [793, 831] } });
const AI = { async run(model) { return { response: model.includes("llama") ? answer(true) : "```json\n" + answer(false) + "\n```" }; } };

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
    if (url.pathname === "/ocr") {
      const body = await new Promise((ok) => { let d = ""; req.on("data", (c) => (d += c)); req.on("end", () => ok(d)); });
      const request = new Request(url, { method: req.method, headers: req.headers, body: req.method === "POST" ? body : undefined });
      const fn = { GET: ocr.onRequestGet, POST: ocr.onRequestPost, OPTIONS: ocr.onRequestOptions }[req.method];
      const r = fn ? await fn({ request, env: { DB, AI } }) : new Response("method", { status: 405 });
      res.writeHead(r.status, Object.fromEntries(r.headers)); res.end(Buffer.from(await r.arrayBuffer())); return;
    }
    const p = url.pathname === "/" ? "/index.html" : decodeURIComponent(url.pathname);
    const data = await readFile(join(ROOT, p));
    res.writeHead(200, { "Content-Type": TYPES[extname(p)] || "application/octet-stream" }); res.end(data);
  } catch (e) {
    res.writeHead(404); res.end("not found");
  }
}).listen(port, "127.0.0.1", () => console.log("ready " + port));
