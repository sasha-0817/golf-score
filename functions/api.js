/* GOLF SC. のサーバー側（Cloudflare Pages Functions + D1）
   今までの Apps Script と同じ形で読み書きする:
   GET  /api            → {ok, version, courses, rounds}
   POST /api {action}   → saveCourse / deleteCourse / saveRound / deleteRound / import
   データベースは Pages プロジェクトに「DB」という名前でつないだ D1。表は最初のアクセスで自動で作る。
   ラウンドとコースは丸ごと JSON で保存する（クラブ・ティーショット・1ペナなども全部残る）。 */

const VERSION = 3;
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};
const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...CORS } });

let ready = false;
async function setup(db) {
  if (ready) return;
  await db.batch([
    db.prepare("CREATE TABLE IF NOT EXISTS courses (id TEXT PRIMARY KEY, data TEXT NOT NULL, updatedAt INTEGER)"),
    db.prepare("CREATE TABLE IF NOT EXISTS rounds (id TEXT PRIMARY KEY, data TEXT NOT NULL, createdAt INTEGER, updatedAt INTEGER)"),
  ]);
  ready = true;
}

const okId = (id) => typeof id === "string" && id.length > 0 && id.length <= 80;

/* 引っ越しで同じラウンドが2台から届いたとき、空いている項目だけ埋める（クラブ・方向などを残すため） */
function mergeRound(oldR, newR) {
  if (!oldR) return newR;
  const out = { ...newR, ...oldR };
  const oh = oldR.holes || [], nh = newR.holes || [];
  out.holes = (oh.length >= nh.length ? oh : nh).map((_, i) => {
    const a = oh[i] || {}, b = nh[i] || {}, h = { ...b, ...a };
    for (const k of Object.keys(b)) if (h[k] == null || h[k] === "" || h[k] === false) { if (b[k] != null && b[k] !== "") h[k] = b[k]; }
    return h;
  });
  return out;
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: CORS });
}

export async function onRequestGet({ env }) {
  try {
    await setup(env.DB);
    const [c, r] = await env.DB.batch([
      env.DB.prepare("SELECT data FROM courses"),
      env.DB.prepare("SELECT data FROM rounds ORDER BY createdAt"),
    ]);
    return json({ ok: true, version: VERSION, courses: c.results.map((x) => JSON.parse(x.data)), rounds: r.results.map((x) => JSON.parse(x.data)) });
  } catch (e) {
    return json({ ok: false, error: String(e && e.message || e) }, 500);
  }
}

export async function onRequestPost({ request, env }) {
  let body;
  try { body = JSON.parse(await request.text()); } catch (e) { return json({ ok: false, error: "bad json" }, 400); }
  try {
    await setup(env.DB);
    const db = env.DB, now = Date.now();
    const putCourse = (c) => db.prepare("INSERT INTO courses (id, data, updatedAt) VALUES (?1, ?2, ?3) ON CONFLICT(id) DO UPDATE SET data=?2, updatedAt=?3").bind(c.id, JSON.stringify(c), now);
    const putRound = (r) => db.prepare("INSERT INTO rounds (id, data, createdAt, updatedAt) VALUES (?1, ?2, ?3, ?4) ON CONFLICT(id) DO UPDATE SET data=?2, updatedAt=?4").bind(r.id, JSON.stringify(r), r.createdAt || now, now);
    switch (body.action) {
      case "saveCourse":
        if (!body.course || !okId(body.course.id)) return json({ ok: false, error: "bad course" }, 400);
        await putCourse(body.course).run(); break;
      case "deleteCourse":
        if (!okId(body.id)) return json({ ok: false, error: "bad id" }, 400);
        await db.prepare("DELETE FROM courses WHERE id=?1").bind(body.id).run(); break;
      case "saveRound":
        if (!body.round || !okId(body.round.id)) return json({ ok: false, error: "bad round" }, 400);
        await putRound(body.round).run(); break;
      case "deleteRound":
        if (!okId(body.id)) return json({ ok: false, error: "bad id" }, 400);
        await db.prepare("DELETE FROM rounds WHERE id=?1").bind(body.id).run(); break;
      case "import": {
        // 引っ越し: 今までのスプレッドシート（＋端末に残っていたクラブなど）を丸ごと入れる。同じIDは空いている項目だけ埋める
        const courses = (body.courses || []).filter((c) => c && okId(c.id));
        const rounds = (body.rounds || []).filter((r) => r && okId(r.id));
        const have = {};
        for (let i = 0; i < rounds.length; i += 50) {
          const ids = rounds.slice(i, i + 50).map((r) => r.id);
          const q = await db.prepare(`SELECT id, data FROM rounds WHERE id IN (${ids.map(() => "?").join(",")})`).bind(...ids).all();
          q.results.forEach((x) => (have[x.id] = JSON.parse(x.data)));
        }
        const stmts = [...courses.map(putCourse), ...rounds.map((r) => putRound(mergeRound(have[r.id], r)))];
        for (let i = 0; i < stmts.length; i += 50) await db.batch(stmts.slice(i, i + 50));
        return json({ ok: true, courses: courses.length, rounds: rounds.length });
      }
      default:
        return json({ ok: false, error: "unknown action" }, 400);
    }
    return json({ ok: true });
  } catch (e) {
    return json({ ok: false, error: String(e && e.message || e) }, 500);
  }
}
