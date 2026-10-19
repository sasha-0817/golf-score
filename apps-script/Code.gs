/**
 * ショートコース スコアカード — 保存用プログラム
 * Googleスプレッドシートの「拡張機能 → Apps Script」に貼り付けて使います。
 * シート「courses」「holes」は初回の保存時に自動で作られます。
 */

const SHEETS = {
  courses: ["id", "name", "holes", "createdAt"],
  holes: ["roundId", "date", "player", "courseId", "courseName", "createdAt",
          "hole", "par", "strokes", "putts", "green", "bunker", "ob", "pen", "tee", "club"]
};
const DIR_JA = { on: "オン", left: "左", right: "右", long: "奥", short: "ショート", miss: "ミス" };
const TEE_JA = { fw: "フェアウェイ", rl: "ラフ左", rr: "ラフ右", miss: "ミス" };
const TEE_EN = Object.fromEntries(Object.entries(TEE_JA).map(([k, v]) => [v, k]));
const DIR_EN = Object.fromEntries(Object.entries(DIR_JA).map(([k, v]) => [v, k]));

function doGet() {
  try {
    ensureHeaders("holes");
    const courses = readRows("courses").map(c => ({
      id: String(c.id), name: String(c.name),
      holes: JSON.parse(c.holes || "[]"), createdAt: Number(c.createdAt) || 0
    }));
    const byRound = {};
    readRows("holes").forEach(h => {
      const id = String(h.roundId);
      const r = byRound[id] || (byRound[id] = {
        id, date: asDate(h.date), player: String(h.player), courseId: String(h.courseId),
        courseName: String(h.courseName), createdAt: Number(h.createdAt) || 0, holes: []
      });
      r.holes[Number(h.hole) - 1] = {
        par: Number(h.par), strokes: Number(h.strokes), putts: Number(h.putts),
        dir: DIR_EN[h.green] || null, bunker: h.bunker === true || h.bunker === "TRUE",
        ob: h.ob === true || h.ob === "TRUE",
        pen: h.pen === true || h.pen === "TRUE",
        tee: TEE_EN[h.tee] || null,
        club: h.club ? String(h.club) : null
      };
    });
    const rounds = Object.values(byRound).map(r => ({ ...r, holes: r.holes.filter(Boolean) }));
    return json({ ok: true, version: 2, courses, rounds });
  } catch (err) {
    return json({ ok: false, error: String(err) });
  }
}

function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const body = JSON.parse(e.postData.contents);
    switch (body.action) {
      case "saveCourse": {
        const c = body.course;
        replaceWhere("courses", r => String(r[0]) === c.id,
          [[c.id, c.name, JSON.stringify(c.holes), c.createdAt || Date.now()]]);
        break;
      }
      case "deleteCourse":
        replaceWhere("courses", r => String(r[0]) === body.id, []);
        break;
      case "saveRound": {
        ensureHeaders("holes");
        const r = body.round;
        const rows = r.holes.map((h, i) => [
          r.id, "'" + r.date, r.player, r.courseId, r.courseName, r.createdAt || Date.now(),
          i + 1, h.par, h.strokes, h.putts, h.dir ? DIR_JA[h.dir] : "", !!h.bunker, !!h.ob,
          !!h.pen, h.tee ? TEE_JA[h.tee] : "", h.club || ""
        ]);
        replaceWhere("holes", row => String(row[0]) === r.id, rows);
        break;
      }
      case "deleteRound":
        replaceWhere("holes", row => String(row[0]) === body.id, []);
        break;
      default:
        return json({ ok: false, error: "unknown action" });
    }
    return json({ ok: true });
  } catch (err) {
    return json({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}

/* ---------- helpers ---------- */
function sheet(name) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(name);
  if (!sh) {
    sh = ss.insertSheet(name);
    sh.appendRow(SHEETS[name]);
    sh.setFrozenRows(1);
    sh.getRange(1, 1, 1, SHEETS[name].length).setFontWeight("bold");
  }
  return sh;
}

/** 列を追加したときに、既存シートの見出し行を新しい列まで広げる */
function ensureHeaders(name) {
  const sh = sheet(name);
  const want = SHEETS[name];
  const have = sh.getRange(1, 1, 1, Math.max(sh.getLastColumn(), 1)).getValues()[0];
  if (have.length < want.length || want.some((h, i) => have[i] !== h)) {
    sh.getRange(1, 1, 1, want.length).setValues([want]).setFontWeight("bold");
  }
}

function readRows(name) {
  const values = sheet(name).getDataRange().getValues();
  const head = values.shift() || [];
  return values.filter(r => r[0] !== "").map(r => Object.fromEntries(head.map((k, i) => [k, r[i]])));
}

/** Removes rows matching `drop`, then appends `add` — one rewrite, so the sheet never ends up half-changed. */
function replaceWhere(name, drop, add) {
  const sh = sheet(name);
  const width = SHEETS[name].length;
  const last = sh.getLastRow();
  const kept = last > 1 ? sh.getRange(2, 1, last - 1, width).getValues().filter(r => r[0] !== "" && !drop(r)) : [];
  const keptFixed = kept.map(r => r.map((v, i) => (name === "holes" && i === 1 ? "'" + asDate(v) : v)));
  const all = keptFixed.concat(add);
  if (last > 1) sh.getRange(2, 1, last - 1, width).clearContent();
  if (all.length) sh.getRange(2, 1, all.length, width).setValues(all);
}

function asDate(v) {
  if (v instanceof Date) return Utilities.formatDate(v, Session.getScriptTimeZone(), "yyyy-MM-dd");
  return String(v).replace(/^'/, "");
}

function json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
