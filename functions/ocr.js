/* スコアカードの写真を読み取る（Cloudflare Workers AI の画像を読めるAI）
   GET  /ocr          → {ok, ready}   ready=AI がつながっているか（つながっていないときはアプリがボタンを出さない）
   POST /ocr {image}  → {ok, result, model}   image は data:image/jpeg;base64,...（アプリ側で縮小ずみ）
   同じ写真を2つのAIに同時に読ませ、印刷された合計と合う方を採用する。
   Pages プロジェクトに「AI」という名前で Workers AI をつないで使う。無料枠をこえないよう、1日の回数に上限をつけている。 */

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};
const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...CORS } });

export const MODELS = ["@cf/meta/llama-4-scout-17b-16e-instruct", "@cf/mistralai/mistral-small-3.1-24b-instruct"];
const DAILY_LIMIT = 200;          // 1日の合計回数（無料枠を使い切られないため）
const MAX_BYTES = 2_500_000;      // 受け取る写真の大きさ

const PROMPT = `This is a photo of a golf course scorecard (a Japanese short course). Read ONLY the printed course data.
IGNORE: handwriting, pencil marks, circles, player names, player score columns, handicap columns (HDCP, HCP, Hdcp, ハンディ) and NET/HANDICAP rows.
Some cards print the front half (OUT) and the back half (IN) side by side or one above the other: list every hole in order (1, 2, 3, ...).
Some cards have several yard columns (for example A/B/C, L/R, MG/SG, or colored tees): report every yard column, left to right.
Return ONLY one JSON object, no explanation:
{"yard_columns":["A","B","C"],"holes":[{"hole":1,"par":3,"yards":[104,104,104]}],"totals":{"par":36,"yards":[1027,1044,1042]},"subtotals":{"first":[521,530,529],"second":[506,514,513]}}
- "yard_columns": the printed headings of the yard columns (A, B, C, L, R, MG, SG...). If there is only one column or no heading, use ["1"].
- "yards" has exactly one number per yard column, in the same order. Use null for a number you cannot read.
- "totals": the printed TOTAL row (par total, and the yard total of each column). Use null if not printed. Use the all-holes total, not the OUT or IN subtotal.
- "subtotals": the printed OUT (front half) and IN (back half) subtotal rows, one number per yard column each. Use null if not printed.
IMPORTANT: if any digit of a number is hidden or unclear (covered by ink, a marker, a sticker, a smudge, a fold, glare, or crossed out), use null for that whole number, even if part of it is visible. Never guess or complete a hidden digit. If a cell shows two numbers (for example "45/48"), use null.
Do not invent numbers.`;

/* AI の答えから JSON を取り出して、形を整える（読めなければ null） */
export function parseAnswer(raw) {
  let t = raw;
  if (t && typeof t === "object") t = t.response ?? t.result ?? t?.choices?.[0]?.message?.content ?? t;
  if (t && typeof t === "object") { var obj = t; }
  else {
    t = String(t || "");
    const a = t.indexOf("{"), b = t.lastIndexOf("}");
    if (a < 0 || b <= a) return null;
    try { obj = JSON.parse(t.slice(a, b + 1)); } catch { return null; }
  }
  if (!obj || !Array.isArray(obj.holes) || !obj.holes.length) return null;
  const num = (v, lo, hi) => { const n = Number(v); return Number.isFinite(n) && n >= lo && n <= hi ? Math.round(n) : null; };
  const cols = Array.isArray(obj.yard_columns) && obj.yard_columns.length ? obj.yard_columns.slice(0, 6).map((x) => String(x).slice(0, 6)) : null;
  const nCol = Math.max(1, cols ? cols.length : 0, ...obj.holes.map((h) => (Array.isArray(h.yards) ? h.yards.length : 1)));
  const names = Array.from({ length: Math.min(nCol, 6) }, (_, i) => (cols && cols[i]) || String(i + 1));
  const holes = obj.holes.slice(0, 18).map((h, i) => {
    const ys = Array.isArray(h.yards) ? h.yards : [h.yards];
    return { hole: i + 1, par: num(h.par, 3, 5), yards: names.map((_, k) => num(ys[k], 15, 700)) };
  });
  const tot = obj.totals || {};
  const sub = obj.subtotals || {};
  const arr = (a) => names.map((_, k) => (Array.isArray(a) ? num(a[k], 50, 9999) : null));
  const totals = { par: num(tot.par, 6, 100), yards: names.map((_, k) => (Array.isArray(tot.yards) ? num(tot.yards[k], 100, 9999) : null)), first: arr(sub.first), second: arr(sub.second) };
  return { cols: names, holes, totals };
}

/* 印刷された合計と、読んだ数字の合計が合うか（合う列が多いほど信用できる） */
export function checkResult(r) {
  const checks = r.cols.map((name, k) => {
    const vals = r.holes.map((h) => h.yards[k]);
    const sum = vals.every((v) => v != null) ? vals.reduce((a, v) => a + v, 0) : null;
    const printed = r.totals.yards[k];
    const total = sum != null && printed != null && sum === printed;
    // 印刷された合計を読み違えることがあるので、前半・後半の計（OUT・IN）が両方合っていれば、それでも合っているとみなす
    const h = Math.ceil(vals.length / 2), a = vals.slice(0, h), b = vals.slice(h), f = r.totals.first[k], s2 = r.totals.second[k];
    const halves = vals.length >= 2 && f != null && s2 != null && a.every((v) => v != null) && b.every((v) => v != null) && a.reduce((x, v) => x + v, 0) === f && b.reduce((x, v) => x + v, 0) === s2;
    return { col: name, sum, printed, ok: total || halves, viaHalves: !total && halves };
  });
  const parSum = r.holes.every((h) => h.par != null) ? r.holes.reduce((a, h) => a + h.par, 0) : null;
  const parOk = parSum != null && r.totals.par != null && parSum === r.totals.par;
  const nulls = r.holes.reduce((a, h) => a + (h.par == null ? 1 : 0) + h.yards.filter((v) => v == null).length, 0);
  const score = checks.filter((c) => c.ok).length * 10 + (parOk ? 5 : 0) - nulls;
  return { checks, parSum, parOk, nulls, score };
}

/* 2つのAIで、数字ごとに見比べる。答えが割れた数字は空欄にする（AIは、隠れている数字を、もっともらしく補って答えることがあるため）。
   表の形（列の数・ホール数）が違う答えは、比べられないので使わない */
export function mergeCells(best, others) {
  const out = { ...best, holes: best.holes.map((h) => ({ ...h, yards: h.yards.slice() })) };
  for (const o of others) {
    if (o.cols.length !== best.cols.length || o.holes.length !== best.holes.length) continue;
    out.holes.forEach((h, i) => {
      if (h.par !== o.holes[i].par) h.par = null;
      h.yards = h.yards.map((y, k) => (y === o.holes[i].yards[k] ? y : null));
    });
  }
  return { ...out, ...checkResult(out) };
}

async function ask(AI, model, image) {
  const out = await AI.run(model, {
    messages: [{ role: "user", content: [{ type: "text", text: PROMPT }, { type: "image_url", image_url: { url: image } }] }],
    max_tokens: 1800, temperature: 0,
  });
  const r = parseAnswer(out);
  return r ? { model, ...r, ...checkResult(r) } : null;
}

async function underLimit(db) {
  if (!db) return true;
  const day = new Date().toISOString().slice(0, 10);
  await db.prepare("CREATE TABLE IF NOT EXISTS ocr_usage (day TEXT PRIMARY KEY, n INTEGER NOT NULL)").run();
  const row = (await db.prepare("SELECT n FROM ocr_usage WHERE day = ?").bind(day).all()).results[0];
  if (row && row.n >= DAILY_LIMIT) return false;
  await db.prepare("INSERT INTO ocr_usage (day, n) VALUES (?, 1) ON CONFLICT(day) DO UPDATE SET n = n + 1").bind(day).run();
  return true;
}

export async function onRequestOptions() { return new Response(null, { status: 204, headers: CORS }); }
// Workers AI の接続だけを本物とみなす（同じ名前で別の種類（D1など）をつないでしまっても、ボタンを出さない）
const hasAI = (env) => !!(env && env.AI && typeof env.AI.run === "function");
export async function onRequestGet({ env }) { return json({ ok: true, ready: hasAI(env) }); }
export async function onRequestPost({ request, env }) {
  try {
    if (!hasAI(env)) return json({ ok: false, error: "not_ready" }, 503);
    const text = await request.text();
    if (text.length > MAX_BYTES * 1.4) return json({ ok: false, error: "too_big" }, 413);
    let body; try { body = JSON.parse(text); } catch { return json({ ok: false, error: "bad_request" }, 400); }
    const image = body && body.image;
    if (typeof image !== "string" || !/^data:image\/(jpeg|png|webp);base64,/.test(image) || image.length > MAX_BYTES) return json({ ok: false, error: "bad_image" }, 400);
    if (!(await underLimit(env.DB))) return json({ ok: false, error: "limit" }, 429);
    const results = (await Promise.allSettled(MODELS.map((m) => ask(env.AI, m, image)))).map((x) => (x.status === "fulfilled" ? x.value : null)).filter(Boolean);
    if (!results.length) return json({ ok: false, error: "unreadable" }, 422);
    let best = results.reduce((a, b) => (b.score > a.score ? b : a));
    best = mergeCells(best, results.filter((x) => x !== best));
    return json({ ok: true, model: best.model, result: { cols: best.cols, holes: best.holes, totals: best.totals, checks: best.checks, parSum: best.parSum, parOk: best.parOk } });
  } catch (e) {
    return json({ ok: false, error: "server" }, 500);
  }
}
