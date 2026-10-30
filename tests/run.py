"""GOLF SC. の自動テスト（ブラウザで実際に操作して確かめる）

使い方:  python3 tests/run.py
必要なもの: Python と Playwright（pip install playwright && playwright install chromium）
ネットにつながらなくても動く（スプレッドシートは偽物のサーバーに置きかえる）。
全部 OK なら最後に「ALL OK」、1つでもダメなら NG を表示して終了コード1。
"""
import json, os, re, sys, tempfile, random
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALL = {"putts": True, "ob": True, "bunker": True, "pen": True, "shot": True, "club": True}
NONE = {k: False for k in ALL}

# スプレッドシートの代わりに、ブラウザの中で動く偽物のサーバー（古い v1 と同じく club / tee / pen は保存しない）
STUB = """<script>
window.__db={courses:[],rounds:[]}; window.__offline=false;
const _f=window.fetch;
window.fetch=async (u,o)=>{ u=String(u);
  if(u.includes("script.google.com")){
    if(window.__offline) throw new TypeError("offline");
    if(o&&o.method==="POST"){ if(window.__delay) await new Promise(r=>setTimeout(r,window.__delay)); const b=JSON.parse(o.body), db=__db;
      if(b.action==="saveCourse") db.courses=db.courses.filter(c=>c.id!==b.course.id).concat([JSON.parse(JSON.stringify(b.course))]);
      if(b.action==="deleteCourse") db.courses=db.courses.filter(c=>c.id!==b.id);
      if(b.action==="saveRound"){ const r=JSON.parse(JSON.stringify(b.round)); r.holes=r.holes.map(h=>({par:h.par,strokes:h.strokes,putts:h.putts||0,dir:h.dir||null,bunker:!!h.bunker,ob:!!h.ob,pen:false,tee:null,club:null})); db.rounds=db.rounds.filter(x=>x.id!==r.id).concat([r]); }
      if(b.action==="deleteRound") db.rounds=db.rounds.filter(x=>x.id!==b.id);
      return new Response(JSON.stringify({ok:true})); }
    return new Response(JSON.stringify({ok:true,version:1,courses:__db.courses,rounds:__db.rounds})); }
  return _f(u,o); };
</script>"""

results = []
def check(name, cond):
    results.append((bool(cond), name)); print(("OK  " if cond else "NG  ") + name)

def open_page(b, ls=None, dark=False, offline=False, repl=None):
    ls = {"sc.flickTip": True, **(ls or {})}   # 初回のフリック案内は、ふだんのテストでは出さない
    src = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    src = re.sub(r'const CF_URL = "[^"]*";', 'const CF_URL = "";', src, count=1)  # ふだんのテストは引っ越しの案内なし
    for a, z in (repl or {}).items(): src = src.replace(a, z)
    src = (src.replace("setTimeout(checkUpdate, 1500);", "").replace("setTimeout(maybeShowA2HS, 2500);", "")
              .replace("const state = {", "const state = window.state = {")
              .replace("function skinsCalc(", "window.skinsCalc=skinsCalc; function skinsCalc(")
              .replace("function greenMap(", "window.dirHeat=dirHeat; window.greenMap=greenMap; function greenMap(")
              .replace("function matchCalc(", "window.matchCalc=matchCalc; window.teamCalc=teamCalc; window.teamSumCalc=teamSumCalc; window.GAME_KEYS_ORDER=()=>GAME_KEYS.join(); window.nassauCalc=nassauCalc; window.vegasCalc=vegasCalc; window.stableCalc=stableCalc; window.peoriaCalc=peoriaCalc; window.olympicCalc=olympicCalc; window.peoriaHidden=peoriaHidden; function matchCalc(")
              .replace("async function connect(){", "window.connect=()=>connect(); async function connect(){")
              .replace("let loading = false;", 'let loading = false; Object.defineProperty(window,"pending",{get:()=>pending});'))
    init = "if(!sessionStorage.t){sessionStorage.t=1;localStorage.clear();" + "".join(
        f"localStorage.setItem({json.dumps(k)},{json.dumps(v if isinstance(v, str) else json.dumps(v))});" for k, v in (ls or {}).items()) + "}"
    if offline: init += "window.__offline=true;"
    src = src.replace("<body>", "<body>" + STUB + "<script>" + init + "</script>", 1)
    path = os.path.join(ROOT, "_test.html")
    open(path, "w", encoding="utf-8").write(src)
    ctx = b.new_context(viewport={"width": 375, "height": 667}, color_scheme="dark" if dark else "light")
    pg = ctx.new_page(); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("file://" + path); pg.wait_for_timeout(1200)
    return pg, errs

def C(pg, sel):  # JS でクリック（重なりや disabled も含めて、アプリの動きをそのまま見る）
    ok = pg.evaluate("s=>{const e=document.querySelector(s); if(!e) return false; e.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true})); return true;}", sel)
    pg.wait_for_timeout(80); return ok

def flick(pg, k, i, tgt):
    """マスを押したまま動かして離す（フリック入力）。tgt は列の何番目か（0から）。方向（d）だけは C/L/R/U/D（指を動かした向き）"""
    r = pg.locator(f'.fc[data-k="{k}"][data-i="{i}"]').first.bounding_box()
    x, y = r["x"] + r["width"] / 2, r["y"] + r["height"] / 2
    pg.mouse.move(x, y); pg.mouse.down(); pg.wait_for_timeout(30)
    if k == "d":
        dx, dy = {"C": (0, 0), "L": (-50, 0), "R": (50, 0), "U": (0, -50), "D": (0, 50)}[tgt]
        pg.mouse.move(x + dx / 2, y + dy / 2, steps=2); pg.mouse.move(x + dx, y + dy, steps=2)
    elif k in ("m", "c"):
        pg.mouse.move(x + 6, y, steps=2)
        if k == "m": tgt = pg.evaluate("v=>[...document.querySelectorAll('.fpop .fpc')].findIndex(e=>e.dataset.v===String(v))", tgt)   # メダルは「点数」で指定する
        n = pg.evaluate("document.querySelectorAll('.fpop .fpc').length"); de = pg.evaluate("[...document.querySelectorAll('.fpop .fpc')].findIndex(e=>e.classList.contains('on'))")
        rs, ls = n - 1 - de, de
        cap = 36 if k == "m" else 44
        st = min(cap, (375 - 10 - x) / rs) if tgt > de and rs else (min(cap, (x - 10) / ls) if ls else cap)
        pg.mouse.move(x + (tgt - de) * max(14, st), y, steps=3)
    else:
        pg.mouse.move(x + 12, y, steps=2)
        c = pg.evaluate("t=>{const e=document.querySelector('.fpop .fpc[data-k=\"'+t+'\"]'); if(!e) return null; const q=e.getBoundingClientRect(); return q.x+q.width/2}", str(tgt))
        pg.mouse.move(c, y, steps=3)
    pg.wait_for_timeout(60); pg.mouse.up(); pg.wait_for_timeout(80)

def tap_un(pg):
    """未入力（グレーの「入力」）の打数マスを、押すだけで（パー）入れていく"""
    for _ in range(12):
        el = pg.locator('.fc.un[data-k="s"]')
        if el.count() == 0: break
        r = el.first.bounding_box(); pg.mouse.click(r["x"] + r["width"] / 2, r["y"] + r["height"] / 2); pg.wait_for_timeout(60)

def walk(pg, last=8):
    """今のホールから最後まで、打数はそのまま（パー）で入れて進む"""
    while pg.evaluate("state.hole") < last:
        tap_un(pg); C(pg, "[data-act=next]")
    tap_un(pg)

def play_round(pg, fields):
    n = pg.evaluate("state.draft.holes.length")
    for i in range(n):
        par = pg.evaluate(f"state.draft.holes[{i}].par")
        if fields["shot"]: flick(pg, "d", 0, "C" if par >= 4 else "R")
        if fields["club"]: flick(pg, "c", 0, 6)
        if i % 3 == 0 and fields["ob"]: flick(pg, "o", 0, 1)
        if i % 4 == 1 and fields["bunker"]: flick(pg, "b", 0, 1)
        flick(pg, "s", 0, par)   # パー+1
        tap_un(pg)
        if fields["putts"]: flick(pg, "p", 0, 1 if i % 2 else 2)
        if i < n - 1: C(pg, "[data-act=next]")
    C(pg, "[data-act=finish]"); pg.wait_for_timeout(900)

def start_preset(pg, key):
    C(pg, "[data-act=open-pick]"); C(pg, f'[data-act=add-fav-pick][data-id="preset:{key}"]'); C(pg, "[data-act=start]"); pg.wait_for_timeout(300)

with sync_playwright() as pw:
    b = pw.chromium.launch()

    # 1. 記録する項目の組み合わせごとに、1ラウンド記録して保存 → 結果 → 履歴 → 分析
    for name, F in [("全部ON", ALL), ("打数だけ", NONE), ("クラブだけ（方向OFF）", {**NONE, "club": True}), ("パットOFF・方向ON", {**ALL, "putts": False})]:
        pg, errs = open_page(b, {"sc.fields": F, "sc.player": "\"たろう\""})
        start_preset(pg, "firtree")
        check(f"[{name}] ラウンドを始められる", pg.evaluate("!!state.draft"))
        check(f"[{name}] 記録画面が1画面に収まる", pg.evaluate("document.documentElement.scrollHeight") <= 667)
        play_round(pg, F)
        check(f"[{name}] 保存してリザルトカードが出る", pg.query_selector(".result-card"))
        r = pg.evaluate("state.rounds[0]")
        check(f"[{name}] パットOFFなら0、ONなら記録した値", all(h["putts"] == 0 for h in r["holes"]) != F["putts"])
        C(pg, "[data-act=close-result]")
        for t in ["score", "shot", "putt"]:
            C(pg, "nav.tabs [data-tab=stats]"); C(pg, f"[data-act=stat-tab][data-v={t}]")
        C(pg, "nav.tabs [data-tab=history]")
        check(f"[{name}] エラーなし", not errs)
        pg.context.close()

    # 2. 保存・読み込み直し・編集・削除（偽物のサーバー）
    pg, errs = open_page(b, {"sc.fields": ALL, "sc.player": "\"たろう\""})
    start_preset(pg, "hyuga-seaside")
    check("2グリーンのコースで MG/SG が入る", pg.evaluate("JSON.stringify(state.draft.gn)") == '["MG","SG"]')
    play_round(pg, ALL); C(pg, "[data-act=close-result]")
    check("サーバーにラウンドとコースが保存される", pg.evaluate("__db.rounds.length===1 && __db.courses.length===1"))
    pg.evaluate("connect()"); pg.wait_for_timeout(600)
    check("古いシートでもクラブが端末から補われる", pg.evaluate("state.rounds[0].holes.every(h=>h.club)"))
    C(pg, "[data-act=edit-round]") or (C(pg, "[data-act=toggle-round]") and C(pg, "[data-act=edit-round]"))
    C(pg, '[data-act=st][data-d="1"]')
    for _ in range(8): C(pg, "[data-act=next]")
    C(pg, "[data-act=finish]"); pg.wait_for_timeout(800); C(pg, "[data-act=close-result]")
    check("編集しても1ラウンドのまま（増えない）", pg.evaluate("state.rounds.length===1 && __db.rounds.length===1"))
    C(pg, "nav.tabs [data-tab=courses]"); cid = pg.evaluate("state.courses[0].id")
    C(pg, f'[data-act=edit-course][data-id="{cid}"]')
    check("コース編集で2グリーンが残る", pg.evaluate("state.courseEdit.two"))
    C(pg, "[data-act=save-course]"); pg.wait_for_timeout(300)
    check("保存後も yards2 が残る", pg.evaluate("!!__db.courses[0].holes[0].yards2"))
    C(pg, "nav.tabs [data-tab=history]")
    C(pg, "[data-act=ask-del-round]") or (C(pg, "[data-act=toggle-round]") and C(pg, "[data-act=ask-del-round]"))
    C(pg, "[data-act=del-round]"); pg.wait_for_timeout(300)
    check("ラウンドを削除できる", pg.evaluate("state.rounds.length===0 && __db.rounds.length===0"))
    check("エラーなし（保存・編集・削除）", not errs)
    pg.context.close()

    # 3. 電波なし: 用意済みコースで始めても、コースもラウンドも消えない
    pg, errs = open_page(b, {"sc.fields": NONE, "sc.player": "\"たろう\""}, offline=True)
    start_preset(pg, "ooyodogawa"); play_round(pg, NONE); C(pg, "[data-act=close-result]")
    check("電波なしで保存 → 未送信に入る", pg.evaluate("pending.length===1"))
    pg.evaluate("window.__offline=false; connect()"); pg.wait_for_timeout(800)
    check("電波が戻るとコースとラウンドが送られる", pg.evaluate("__db.courses.length===1 && __db.rounds.length===1 && pending.length===0"))
    check("エラーなし（電波なし）", not errs)
    pg.context.close()

    # 4. 入力の上限・下限、中断・再開・破棄、読み込み直しで同じホール
    pg, errs = open_page(b, {"sc.fields": ALL, "sc.player": "\"たろう\""})
    start_preset(pg, "firtree")
    flick(pg, "s", 0, 9)
    check("打数の上限10（フリックの右端）", pg.evaluate("state.draft.holes[0].strokes") == 10)
    flick(pg, "s", 0, 0)
    check("打数の下限1・パットは打数-1まで", pg.evaluate("[state.draft.holes[0].strokes,state.draft.holes[0].putts]") == [1, 0])
    flick(pg, "p", 0, 6)
    check("パットを増やすと打数も増える（パットは打数-1まで）", pg.evaluate("[state.draft.holes[0].strokes,state.draft.holes[0].putts]") == [7, 6])
    for _ in range(4): tap_un(pg); C(pg, "[data-act=next]")
    pg.reload(); pg.wait_for_timeout(900)
    check("読み込み直しても同じホール（5番）", (pg.text_content(".hno") or "").startswith("5"))
    C(pg, "[data-act=ask-discard]"); C(pg, "[data-act=pause]")
    check("中断できる", pg.query_selector(".resume"))
    C(pg, "[data-act=resume]"); C(pg, "[data-act=ask-discard]"); C(pg, "[data-act=discard]")
    check("破棄できる", pg.evaluate("!state.draft"))
    check("エラーなし（入力・中断）", not errs)
    pg.context.close()

    # 5. 壊れた・古い端末データでも止まらない
    pg, errs = open_page(b, {"sc.draft": "{bad", "sc.rounds": "null", "sc.fields": "[]", "sc.clubs": '"x"', "sc.favs": "{}"})
    check("壊れた端末データでも開ける", pg.query_selector("#main section") and not errs)
    pg.context.close()

    # 6. 分析: いろいろなラウンドが混ざっていても止まらない（ライト／ダーク）
    random.seed(3)
    def rnd(i, n=9, putts=True, player="たろう", cid="c1"):
        hs = []
        for k in range(n):
            par = 4 if k in (3, 6) else 3; st = max(1, par + random.choice([-1, 0, 0, 1, 1, 2, 3]))
            hs.append({"par": par, "strokes": st, "putts": min(st - 1, random.choice([1, 2, 2, 3])) if putts else 0,
                       "dir": random.choice([None, "on", "left", "right", "short"]) if par == 3 else None,
                       "tee": random.choice(["fw", "rl", "rr", None]) if par >= 4 else None,
                       "bunker": random.random() < .2, "ob": random.random() < .1, "pen": False,
                       "club": random.choice(["7I", "8I", "9I"]) if par == 3 else "1W"})
        return {"id": f"r{player}{i}", "player": player, "courseId": cid, "courseName": "コース" + cid, "date": f"2026-09-{10+i:02d}", "createdAt": i, "holes": hs}
    rounds = [rnd(i) for i in range(4)] + [rnd(i, putts=False) for i in range(4, 6)] + [rnd(6, n=18, cid="c2")] + [rnd(i, player="はなこ") for i in range(3)]
    courses = [{"id": "c1", "name": "コースc1", "holes": [{"par": 3, "yards": 100}] * 9}, {"id": "c2", "name": "コースc2", "holes": [{"par": 3, "yards": 100}] * 18}]
    for dark in (False, True):
        pg, errs = open_page(b, {"sc.rounds": rounds, "sc.courses": courses, "sc.player": "\"たろう\"", "sc.tab": "\"stats\""}, dark=dark, offline=True)
        for t in ["score", "shot", "putt"]: C(pg, f"[data-act=stat-tab][data-v={t}]")
        for v in ["1", "3", "all"]: pg.select_option("#f-range", v); pg.wait_for_timeout(150)
        pg.select_option("#f-course", "c2"); pg.wait_for_timeout(200)
        C(pg, '[data-act=pick-player][data-v="はなこ"]'); C(pg, "nav.tabs [data-tab=history]"); C(pg, "[data-act=toggle-round]")
        C(pg, "nav.tabs [data-tab=courses]")
        check(f"分析・履歴・コース画面でエラーなし（{'ダーク' if dark else 'ライト'}）", not errs)
        check(f"横にはみ出さない（{'ダーク' if dark else 'ライト'}）", pg.evaluate("document.documentElement.scrollWidth") <= 375)
        pg.context.close()

    # 7. スコアカード入力（紙と同じ表・4人まで）
    def card_fill(pg, putts_for=None):
        for _ in range(200):
            sel = pg.evaluate("state.cdraft&&state.cdraft.sel")
            if not sel: break
            if sel["k"] == "p" and putts_for is not None and sel["i"] not in putts_for:
                C(pg, f'[data-act=cd-cell][data-h="{sel["h"]}"][data-i="{sel["i"]+1}"][data-k=s]') if sel["i"] + 1 < pg.evaluate("state.cdraft.players.length") else C(pg, f'[data-act=cd-cell][data-h="{sel["h"]+1}"][data-i="0"][data-k=s]')
                if pg.evaluate("state.cdraft.sel&&state.cdraft.sel.h") == sel["h"] and pg.evaluate("state.cdraft.sel.i") == sel["i"]: break
                continue
            C(pg, '[data-act=cd-key][data-v="4"]' if sel["k"] == "s" else '[data-act=cd-key][data-v="2"]')
    LSC = {"sc.player": "\"たろう\"", "sc.inputMode": "\"card\"", "sc.mates": ["はなこ", "じろう", "さぶろう"]}
    pg, errs = open_page(b, LSC)
    check("スコアカード: 開始画面に3人分の同伴者欄", pg.evaluate("document.querySelectorAll('.mates input').length") == 3)
    start_preset(pg, "firtree")
    check("スコアカード: 4人で開始・表が出る", pg.evaluate("state.cdraft&&state.cdraft.players.length") == 4 and pg.query_selector(".cdt") is not None)
    check("スコアカード: 1画面に収まる（ページがスクロールしない）", pg.evaluate("document.documentElement.scrollHeight") <= 667)
    C(pg, '[data-act=cd-key][data-v="3"]'); C(pg, '[data-act=cd-key][data-v="5"]')
    check("スコアカード: パットは打数より少なくしか入らない", pg.evaluate("state.cdraft.players[0].p[0]") is None)
    C(pg, '[data-act=cd-key][data-v="2"]')
    check("スコアカード: 打数→パット→次の人の順に進む", pg.evaluate("JSON.stringify(state.cdraft.sel)") == '{"h":0,"i":1,"k":"s"}')
    pg.reload(); pg.wait_for_timeout(1000)
    check("スコアカード: 読み直しても入力と位置が残る", pg.evaluate("state.cdraft.players[0].s[0]") == 3 and pg.evaluate("state.cdraft.sel.i") == 1)
    C(pg, "[data-act=ask-discard]"); C(pg, "[data-act=cd-pause]")
    check("スコアカード: 中断カードが出る", pg.query_selector("[data-act=cd-resume]") is not None)
    C(pg, "nav.tabs [data-tab=history]"); C(pg, "nav.tabs [data-tab=record]"); C(pg, "[data-act=cd-resume]")
    card_fill(pg, putts_for={0})
    C(pg, "[data-act=cd-finish]"); pg.wait_for_timeout(900)
    rs = pg.evaluate("__db.rounds.map(r=>({p:r.player,s:r.holes.reduce((a,h)=>a+h.strokes,0),pt:r.holes.reduce((a,h)=>a+h.putts,0),n:r.holes.length}))")
    check("スコアカード: 4人分が別々のラウンドで保存", sorted(x["p"] for x in rs) == sorted(["たろう", "はなこ", "じろう", "さぶろう"]) and all(x["n"] == 9 for x in rs))
    check("スコアカード: パットを書かなかった人は putts 0（記録なし）", {x["p"]: x["pt"] > 0 for x in rs} == {"たろう": True, "はなこ": False, "じろう": False, "さぶろう": False})
    check("スコアカード: 保存後はみんなのリザルト（順位と全員のスコア）", pg.query_selector(".grp-card") is not None and pg.evaluate("document.querySelectorAll('.rc-rank>div').length") == 4)
    check("スコアカード: リザルトで「みんな」＋4人を切りかえられる", pg.evaluate("document.querySelectorAll('.rc-who button').length") == 5 and C(pg, '.rc-who button:nth-child(3)') and pg.evaluate("state.result.player") == "はなこ")
    check("リザルト: 2回目からは出てくるアニメーションをくり返さない（点滅しない）", "still" in pg.evaluate("document.querySelector('.result-back').className"))
    check("リザルト: 全員分まとめて保存のボタン（5枚）", "5枚" in (pg.text_content("[data-act=save-all]") or ""))
    check("スコアカード: 保存後は下書きが消える", pg.evaluate("localStorage.getItem('sc.cdraft')") in (None, "null"))
    check("スコアカード: エラーなし", not errs)
    pg.context.close()

    # 途中までのパットは保存させない・18H は OUT/IN を切りかえ
    courses18 = [{"id": "c18", "name": "18H", "holes": [{"par": 4, "yards": 300}] * 18}]
    pg, errs = open_page(b, {**LSC, "sc.mates": [], "sc.courses": courses18, "sc.favs": ["c18"], "sc.lastCourse": "\"c18\""}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    check("スコアカード: 1人でも始められる・18Hは OUT/IN", pg.evaluate("state.cdraft.players.length") == 1 and pg.evaluate("document.querySelectorAll('[data-act=cd-half]').length") == 2)
    card_fill(pg)
    check("スコアカード: 9H目のあと IN に切りかわる", pg.evaluate("state.cdraft.half") == 1)
    C(pg, '[data-act=cd-half][data-v="0"]'); C(pg, '[data-act=cd-cell][data-h="5"][data-i="0"][data-k=p]'); C(pg, '[data-act=cd-key][data-v="del"]')
    C(pg, '[data-act=cd-half][data-v="1"]')
    C(pg, "[data-act=cd-finish]"); pg.wait_for_timeout(500)
    check("スコアカード: パットが途中までだと保存しない", pg.evaluate("state.cdraft!=null") and pg.evaluate("state.cdraft.sel.h") == 5 and pg.evaluate("state.cdraft.half") == 0)
    C(pg, '[data-act=cd-key][data-v="1"]'); C(pg, "[data-act=cd-finish]"); pg.wait_for_timeout(600)
    check("スコアカード: 電波なしでも端末に保存", pg.evaluate("state.cdraft") is None and pg.evaluate("pending.length") == 1)
    check("スコアカード（18H）: エラーなし", not errs)
    pg.context.close()

    # 8. 同じ名前OK（ボール2個で2回分）・1ホールずつでも同伴者の打数・COURSE BEST の星が切りかえても消えない
    c1 = [{"id": "c1", "name": "C1", "holes": [{"par": 3, "yards": 100}] * 9}]
    old = [{"id": "o1", "player": "たろう", "courseId": "c1", "courseName": "C1", "date": "2026-09-01", "createdAt": 1, "holes": [{"par": 3, "strokes": 5, "putts": 2}] * 9}]
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.inputMode": "\"hole\"", "sc.mates": ["たろう", "はなこ", ""], "sc.courses": c1, "sc.rounds": old, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\""}, offline=True)
    check("1ホールずつでも同伴者欄が出る", pg.evaluate("document.querySelectorAll('.mates input').length") == 3)
    pg.fill("#r-mate2", "まちがい"); pg.dispatch_event("#r-mate2", "input"); C(pg, '[data-act=mate-clear][data-i="2"]')
    check("同伴者を × で消せる（選ばないにできる）", pg.evaluate("document.querySelector('#r-mate2').value") == "" and pg.evaluate("document.querySelector('#r-mate0').value") == "たろう")
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    check("同じ名前でも始められる・同伴者2人", pg.evaluate("state.draft&&state.draft.mates.length") == 2)
    check("同伴者は人ごとの行（自分＋2人）・1画面に収まる", pg.evaluate("document.querySelectorAll('.scoreblock .frow').length") == 3 and pg.query_selector(".frow.me") is not None and pg.evaluate("document.documentElement.scrollHeight") <= 667)
    flick(pg, "s", 2, 4)
    check("同伴者の打数もフリックで入る（自分と同じ表の中）", pg.evaluate("state.draft.mates[1].s[0]") == 5)
    flick(pg, "s", 1, 3)
    check("同伴者の打数: 人ごとに別々に入る", pg.evaluate("[state.draft.mates[0].s[0],state.draft.mates[1].s[0]].join()") == "4,5")
    tap_un(pg); C(pg, "[data-act=next]")
    check("次のホールへはそのまま進む（同伴者を聞かない）", pg.query_selector(".mate-sheet") is None and pg.evaluate("state.hole") == 1)
    walk(pg)
    C(pg, "[data-act=finish]"); pg.wait_for_timeout(900)
    rs = pg.evaluate("pending.map(r=>({p:r.player,s:r.holes.reduce((a,h)=>a+h.strokes,0),pt:r.holes.reduce((a,h)=>a+h.putts,0)}))")
    check("1ホールずつ: 自分＋同伴者2人が別々のラウンドで保存", sorted(x["p"] for x in rs) == ["たろう", "たろう", "はなこ"])
    check("1ホールずつ: 同伴者はパットなし（putts 0）", [x["pt"] for x in rs if x["p"] == "はなこ"] == [0])
    check("同じ名前は ①② で見分ける", "たろう②" in pg.evaluate("document.querySelector('.rc-who').textContent"))
    C(pg, '.rc-who button:nth-child(2)'); pg.wait_for_timeout(200)
    n1 = pg.evaluate("document.querySelectorAll('.rc-stars i').length")
    C(pg, '.rc-who button:nth-child(4)'); C(pg, '.rc-who button:nth-child(2)'); pg.wait_for_timeout(200)
    check("COURSE BEST の星がほかの人に切りかえて戻っても残る", n1 > 0 and pg.evaluate("document.querySelectorAll('.rc-stars i').length") == n1)
    check("同伴者・同じ名前: エラーなし", not errs)
    pg.context.close()

    # 9. 同伴者がいても、全部ONの入力表が1画面に収まり、1人のときと同じ大きさ
    hs = {}
    for nm, m in (("1人", []), ("4人", ["はなこ", "じろう", "さぶろう"])):
        pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": m, "sc.courses": c1, "sc.rounds": old, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.fields": ALL}, offline=True)
        C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
        hs[nm] = pg.evaluate("[document.querySelector('.rowb .fc.fb').getBoundingClientRect().height, document.querySelector('.scoreblock .fc').getBoundingClientRect().width, document.querySelectorAll('.rowb .fc').length]")
        check(f"{nm}: 全部ONでも1画面に収まる", pg.evaluate("document.documentElement.scrollHeight") <= 667 and pg.evaluate("document.querySelector('.rec-card').getBoundingClientRect().bottom <= document.querySelector('.rec-bar').getBoundingClientRect().top"))
        pg.context.close()
    check("4人でも1人のときと同じ入力欄（方向・クラブ・OB・バンカーの4つ、打数のマスも同じ幅）", hs["4人"][2] == hs["1人"][2] == 4 and abs(hs["4人"][1] - hs["1人"][1]) <= 2 and hs["4人"][0] >= 40)

    # 10. スキンズ: 計算・ラウンド前に選ぶ・記録中のコイン・みんなのリザルト・スコアカードでも
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": ["はなこ", "", ""], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.fields": ALL, "sc.skinCarry": True}, offline=True)
    r = pg.evaluate("(()=>{ const x=skinsCalc([[3,4,2],[3,3,3]],3,false); return [x.won.join(), x.carry, x.pot]; })()")
    check("スキンズの計算（持ち越しなし: 同点のホールは誰ももらえない）", r == ["1,1", 0, 1])
    r = pg.evaluate("(()=>{ const x=skinsCalc([[3,4,2],[3,3,3]],3); return [x.won.join(), x.carry, x.pot]; })()")
    check("スキンズの計算（同点は持ち越し・取った人が持ち越し分も獲得）", r == ["1,2", 0, 1])
    r = pg.evaluate("(()=>{ const x=skinsCalc([[3,3],[3,3]],2); return [x.won.join(), x.carry]; })()")
    check("スキンズの計算（最後まで同点なら持ち越しが残る）", r == ["0,0", 2])
    w = pg.evaluate("[0,1,2].map(i=>Math.round(document.querySelector('#r-mate'+i).getBoundingClientRect().width))")
    check("同伴者の欄は名前がなくても × の分の幅を空けておく（入力しても幅が変わらない）", len(set(w)) == 1)
    C(pg, "[data-act=open-game]")
    check("ゲームの選択画面はカードだけ（ルールは出さない）", pg.query_selector(".game-sheet .sr-ex") is None and pg.query_selector(".gm-rule") is not None)
    r = pg.evaluate("""[...document.querySelectorAll('.game-sheet .gm-card')].map(c=>{const b=c.querySelector('.gm-h b'),m=c.querySelector('.gm-t small'),q=x=>{const e=c.querySelector(x);if(!e)return null;const r=e.getBoundingClientRect();return r.top+r.height/2};
      return [b.scrollWidth<=b.clientWidth&&b.getBoundingClientRect().height<=24, m.scrollWidth<=m.clientWidth&&m.getBoundingClientRect().height<20, q('.gm-rule')===null||Math.abs(q('.gm-rule')-q('.cp-radio'))<2, Math.abs(q('.cp-radio')-(c.getBoundingClientRect().top+c.getBoundingClientRect().height/2))<3]}).flat().every(x=>x)""")
    check("ゲームのカード: 名前・説明は1行で切れない、ルールと丸は縦の真ん中でそろう", r)
    C(pg, "[data-act=open-rules][data-v=olympic]")
    t = pg.inner_text(".rp")
    check("オリンピックのルールは1パットで入れた人だけが対象", "1パットで入れた人だけ" in t and "カップインした順" not in t)
    check("ルールのポップアップは上に帯（英語名・名前・札）と番号の丸", pg.query_selector(".rp .rp-hero .rp-chips") is not None and pg.evaluate("getComputedStyle(document.querySelector('.rp-list li'),'::before').content") == "counter(rp)" or pg.evaluate("getComputedStyle(document.querySelector('.rp-list li'),'::before').content") not in ("none", ""))
    check("メダルの絵に赤いリボン（ツノ）がない", "#E5484D" not in pg.inner_html(".rp-list"))
    C(pg, ".rp-x"); C(pg, "[data-act=open-rules][data-v=nassau]")
    t = pg.inner_text(".rp")
    check("ナッソーのルールに言葉の解説（前半・後半のみ。トータル・pt は書かない）", pg.query_selector(".rp .rp-words") is not None and "前半" in pg.inner_text(".rp-words") and "トータル" not in pg.inner_text(".rp-words") and "pt" not in pg.inner_text(".rp-words"))
    check("ルールに OK ボタンはない（× だけで閉じる）", pg.query_selector(".rp .rp-ok") is None)
    C(pg, ".rp-x"); pg.set_viewport_size({"width": 375, "height": 627}); over = []
    for k in ["ghost", "olympic", "skins", "peoria", "nearpin", "match", "stable", "nassau", "vegas", "teamsum", "team"]:
        C(pg, f"[data-act=open-rules][data-v={k}]"); pg.wait_for_timeout(60)
        if pg.evaluate("(()=>{const r=document.querySelector('.rp');return r.scrollHeight-r.clientHeight})()") > 1: over.append(k)
        C(pg, ".rp-x")
    check("どのゲームのルールもスクロールなしで1画面に収まる（高さ627px）", not over)
    pg.set_viewport_size({"width": 375, "height": 667}); C(pg, "[data-act=open-rules][data-v=nassau]")
    C(pg, ".rp-x")
    C(pg, "[data-act=open-rules][data-v=skins]")
    check("「ルール」でポップアップ（例つき・持ち越しの選択）", pg.query_selector(".rp .sr-ex") is not None and pg.evaluate("document.querySelectorAll('.rp [data-act=set-carry]').length") == 2)
    C(pg, '[data-act=set-carry][data-v="1"]'); C(pg, '[data-act=set-carry][data-v="0"]'); C(pg, ".rp-x")
    check("× でポップアップを閉じてゲーム選択に戻る", pg.query_selector(".rp") is None and pg.query_selector(".game-sheet") is not None)
    ty = pg.evaluate("[...document.querySelectorAll('.game-sheet .gm-sec, .game-sheet .gm-card')].map(e=>e.classList.contains('gm-sec')?'#'+e.textContent:e.dataset.v).join()")
    check("ゲームの一覧は上に個人戦、下にチーム戦（見出しつき）", ty.startswith(",#SOLO個人戦,ghost,peoria,olympic,nearpin,skins") and "#TEAMチーム戦2対2・4人で,vegas,teamsum,team" in ty and ty.index("nassau") < ty.index("#TEAM"))
    nm = pg.evaluate("[...document.querySelectorAll('.game-sheet .gm-h b')].map(b=>b.textContent).join()")
    check("チーム戦の中のゲーム名は「ベストボール」「合計スコア」（見出しと同じ名前にしない）", "ベストボール" in nm and "合計スコア" in nm and "チーム戦" not in nm)
    check("決定ボタンはない（選ぶと自動で戻る）", pg.query_selector(".game-sheet .gm-ok") is None)
    C(pg, '[data-act=pick-game][data-v=nassau]')
    check("ゲームを選ぶと自動で選択画面が閉じる", pg.query_selector(".game-sheet") is None and pg.evaluate("state.game") == "nassau")
    C(pg, "[data-act=open-game]"); C(pg, '[data-act=pick-game][data-v=skins]')
    check("選んだゲームが反映される（スキンズ）", pg.query_selector(".game-sheet") is None and pg.evaluate("state.game") == "skins")
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    check("スキンズで始まる・記録中にコインの数（持ち越しなし）", pg.evaluate("state.draft.game") == "skins" and pg.evaluate("state.draft.carry") is False and pg.evaluate("document.querySelectorAll('.frow .pt b').length") == 2)
    check("スキンズでも1画面に収まる", pg.evaluate("document.documentElement.scrollHeight") <= 667)
    tap_un(pg); C(pg, "[data-act=next]"); flick(pg, "s", 1, 1)
    check("全員が入力するまでは、そのホールのポイントは入らない", pg.evaluate("document.querySelectorAll('.frow .pt b')[1].textContent") == "0")
    flick(pg, "s", 0, 2)
    check("同点のホールは誰も取らず、全員が入力したそのホールの勝ちですぐ1枚", pg.evaluate("document.querySelectorAll('.frow .pt b')[1].textContent") == "1")
    C(pg, "[data-act=next]")
    walk(pg)
    C(pg, "[data-act=finish]"); pg.wait_for_timeout(900)
    check("みんなのリザルトにスキンズの結果", pg.query_selector(".grp-card .rc-skins") is not None and pg.evaluate("document.querySelectorAll('.rs-row').length") == 2)
    check("スキンズ: エラーなし", not errs)
    pg.context.close()
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.inputMode": "\"card\"", "sc.game": "\"skins\"", "sc.mates": ["はなこ", "", ""], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\""}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    for v in ["3", "2", "4", "2"]: C(pg, f'[data-act=cd-key][data-v="{v}"]')
    check("スコアカードでもスキンズのコイン行", pg.query_selector(".cdt tr.skr") is not None and pg.evaluate("document.querySelector('.cdt tr.skr').textContent").startswith("次2枚") is False)
    check("スコアカード＋スキンズ: エラーなし", not errs)
    pg.context.close()

    # 11. 開始画面: アプリ表示の SE（ステータスバーの分だけ低い）でも「始める」が下のタブに隠れない
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": ["はなこ", "じろう", "さぶろう"], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\""}, offline=True)
    pg.set_viewport_size({"width": 375, "height": 627}); pg.wait_for_timeout(300)
    check("開始画面: 高さ627pxでも「始める」がタブの上に見える", pg.evaluate("document.querySelector('[data-act=start]').getBoundingClientRect().bottom <= document.querySelector('nav.tabs').getBoundingClientRect().top"))
    pg.context.close()

    # 12. ほかのゲーム: 計算・人数が合わないと選べない・記録中の表示・リザルト・スコアカード
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": ["はなこ", "", ""], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\""}, offline=True)
    m = pg.evaluate("(()=>{ const x=matchCalc([[3,3,3,3,3,3,3,3,3],[3,2,3,2,4,4,4,4,3]],9,9); return [x.diff, x.won.join(), x.dec&&x.dec.up, x.dec&&x.dec.left, x.st(0), x.st(1)]; })()")
    check("マッチプレーの計算（残り1で2勝ち越し・勝ったホール数・日本語の勝/負）", m == [2, "4,2", 2, 1, "2勝", "2負"])
    nz = pg.evaluate("(()=>{ const x=nassauCalc([[3,3,3,3,3,3,3,3,3],[4,4,4,4,2,2,2,2,2]],9,9); return [x.half, x.pts.join(), x.res.map(r=>r.w).join()]; })()")
    check("ナッソーの計算（9Hは1〜4H/5〜9H・区間ごとに1pt）", nz == [4, "1,2", "0,1,1"])
    vg = pg.evaluate("(()=>{ const x=vegasCalc([[3],[5],[4],[4]],1); return x.pts.join(); })()")
    check("ラスベガスの計算（35と44で9点）", vg == "9,0")
    tm = pg.evaluate("(()=>{ const x=teamCalc([[3,4],[5,3],[4,4],[4,5]],2); return x.won.join(); })()")
    check("チーム戦の計算（良い方のスコアで比べる）", tm == "2,0")
    check("ゲームの並び（個人戦をよく遊ばれる順→チーム戦）", pg.evaluate("GAME_KEYS_ORDER()") == "ghost,peoria,olympic,nearpin,skins,match,stable,nassau,vegas,teamsum,team")
    ts = pg.evaluate("(()=>{ const x=teamSumCalc([[3,4],[5,3],[4,4],[4,5]],2); return [x.tot.join(), x.done]; })()")
    check("合計スコアの計算（2人の打数の合計どうし）", ts == ["15,17", 2])
    sf = pg.evaluate("(()=>stableCalc([[3,2,4,5],[5,3,3,2]],[3,3,3,3],4).pts.join())()")
    check("ステーブルフォードの計算（パー2・バーディ3・ボギー1・ダボ0）", sf == "6,7")
    C(pg, "[data-act=open-game]")
    check("ゲーム選択に11のゲーム（ゴーストを含む）・4人用は2人のとき薄い", pg.evaluate("document.querySelectorAll('.gm-card').length") == 12 and pg.query_selector(".gm-card.na[data-v=vegas]") is not None)
    C(pg, "[data-act=pick-game][data-v=vegas]")
    check("人数が合わないゲームは選べない", pg.evaluate("state.game") != "vegas")
    C(pg, "[data-act=pick-game][data-v=match]")
    C(pg, "[data-act=open-game]")
    check("選んだゲームのチェックは1本の線（SVG）", pg.query_selector(".gm-card.on .cp-radio svg") is not None)
    for k in ["match", "nassau", "vegas", "stable"]:
        C(pg, f"[data-act=open-rules][data-v={k}]")
        ok = pg.query_selector(".rp .sr-ex") is not None
        C(pg, ".rp-x")
        if not ok: break
    check("4つのゲームのルールのポップアップ", ok and pg.query_selector(".rp") is None)
    C(pg, ".game-sheet [data-act=close-game]"); C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    flick(pg, "s", 0, 2); flick(pg, "s", 1, 3)
    check("マッチプレー: 入力したそのホールですぐ 1勝 / 1負", pg.evaluate("[...document.querySelectorAll('.gtag')].map(e=>e.textContent).join()") == "1勝,1負")
    walk(pg)
    C(pg, "[data-act=finish]"); pg.wait_for_timeout(900)
    check("マッチプレー: みんなのリザルトに結果", pg.query_selector(".grp-card .rc-game") is not None and "たろう の勝ち" in pg.evaluate("document.querySelector('.rc-game').textContent"))
    check("ほかのゲーム: エラーなし", not errs)
    pg.context.close()
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.inputMode": "\"card\"", "sc.game": "\"stable\"", "sc.mates": ["はなこ", "", ""], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\""}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    for v in ["2", "1", "3", "2"]: C(pg, f'[data-act=cd-key][data-v="{v}"]')
    check("スコアカードでもゲームの行（ステーブルフォード 3 / 2、数字だけ）", pg.evaluate("[...document.querySelectorAll('.cdt tr.gr .gtag')].map(e=>e.textContent).join()") == "3,2")
    pg.context.close()
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.game": "\"vegas\"", "sc.mates": ["はなこ", "", ""], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\""}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    check("人数が合わないゲームは、始めるときにゲームなしになる", pg.evaluate("state.draft.game") is None)
    pg.context.close()
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.inputMode": "\"card\"", "sc.game": "\"teamsum\"", "sc.mates": ["はなこ", "じろう", "さぶろう"], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.fields": dict(NONE)}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    for v in ["3", "4", "5", "4"]: C(pg, f'[data-act=cd-key][data-v="{v}"]')
    check("合計スコア: 記録中にチームの合計（A=7 / B=9）を2人で1つずつ、A・Bの文字や単位なし", pg.evaluate("[...document.querySelectorAll('.cdt tr.gr td.tm')].map(e=>e.textContent).join()") == "7,9")
    check("合計スコア: エラーなし", not errs)
    pg.context.close()

    # 13. 新ペリア・オリンピック
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": ["はなこ", "", ""], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.game": "\"olympic\""}, offline=True)
    r = pg.evaluate("(()=>{ const x=peoriaCalc([[3,3,3,3,3,3,4,3,3],[4,4,5,4,4,3,4,4,4]],[3,3,3,3,3,3,3,3,3],[0,1,2,3,4,5]); return x.map(o=>[o.gross,o.hc,o.net]); })()")
    check("新ペリアの計算（(隠し合計×1.5−パー)×0.8、マイナスは0）", r == [[28, 0, 28], [36, 7.2, 28.8]])
    h = pg.evaluate("(()=>{ const a=peoriaHidden(9), b=peoriaHidden(18); return [a.length, b.length, new Set(a).size, Math.max(...a)<9]; })()")
    check("新ペリアの隠しホール（9Hは6つ・18Hは12・重複なし）", h == [6, 12, 6, True])
    h = pg.evaluate("(()=>{ const a=peoriaHidden(12), x=nassauCalc([[3,3,3,3,3,3,3,3,3,3,3,3],[4,3,3,3,3,3,2,3,3,3,3,3]],12,12); return [a.length, x.res.map(r=>r.a+'-'+r.b).join()]; })()")
    check("12ホールのとき: 新ペリアの隠しホールは8つ、ナッソーは1〜6／7〜12", h == [8, "0-6,6-12,0-12"])
    o = pg.evaluate("(()=>{ const x=olympicCalc({0:[4,3],1:[5,0],2:[0,4]},2); return [x.pts.join(), x.cnt[0][5], x.cnt[1][4]]; })()")
    check("オリンピックの計算（ダイヤ5・金4・銀3…）", o == ["9,7", 1, 1])
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    check("オリンピック: 表にメダルの列（人ごとのマス）", pg.evaluate("document.querySelectorAll('.scoreblock .fc.fm').length") == 2)
    flick(pg, "m", 0, 4); flick(pg, "m", 1, 4)
    check("同じメダルは1人だけ（あとから付けた人に移る）", pg.evaluate("JSON.stringify(state.draft.medals[0])") == "[0,4]")
    check("オリンピック: メダルの右にポイントの数字だけ", pg.evaluate("[...document.querySelectorAll('.frow .pt b')].map(e=>e.textContent).join()") == "0,4")
    walk(pg)
    C(pg, "[data-act=finish]"); pg.wait_for_timeout(900)
    check("オリンピック: リザルトに点数とメダル", pg.query_selector(".rc-game .md-c") is not None)
    pg.context.close()
    pg, errs2 = open_page(b, {"sc.player": "\"たろう\"", "sc.inputMode": "\"card\"", "sc.mates": ["はなこ", "", ""], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.game": "\"peoria\""}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    for _ in range(40):
        if not pg.evaluate("state.cdraft&&state.cdraft.sel"): break
        C(pg, '[data-act=cd-key][data-v="3"]')
        if pg.evaluate("state.cdraft.sel&&state.cdraft.sel.k==='p'"): C(pg, '[data-act=cd-key][data-v="2"]')
    C(pg, "[data-act=cd-finish]"); pg.wait_for_timeout(900)
    check("新ペリア: リザルトで隠しホール6つを発表・ネットの順位", pg.evaluate("document.querySelectorAll('.pe-hid i').length") == 6 and pg.evaluate("document.querySelectorAll('.rc-sc td.hh').length") == 6)
    check("新ペリア・オリンピック: エラーなし", not errs and not errs2)
    pg.context.close()
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.inputMode": "\"card\"", "sc.mates": ["はなこ", "じろう", "さぶろう"], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.game": "\"nearpin\"", "sc.fields": dict(NONE)}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    for v in ["3", "3", "3", "3"]: C(pg, f'[data-act=cd-key][data-v="{v}"]')
    C(pg, '[data-act=cd-cell][data-h="0"][data-i="1"][data-k=s]'); C(pg, '[data-act=cd-pin][data-i="1"]')
    tg = pg.evaluate("[...document.querySelectorAll('.cdt tr.gr .gtag')].map(e=>e.textContent).join()")
    check("ニアピン（スコアカード）: 一番近い人を選ぶとコイン1枚（記録中の行）", tg == "0,1,0,0" and pg.query_selector(".pin-sheet") is None)
    C(pg, '[data-act=cd-pin][data-i="1"]')
    check("ニアピン（スコアカード）: もう一度押すと取り消し・旗は人ごとの列（計の下）にある", pg.evaluate("!state.cdraft.pins[0]") and pg.evaluate("document.querySelectorAll('.cdt tfoot .pinflag').length") == 4)
    C(pg, '[data-act=cd-pin][data-i="1"]')
    for _ in range(80):
        if not pg.evaluate("state.cdraft&&state.cdraft.sel"): break
        C(pg, '[data-act=cd-key][data-v="3"]')
    C(pg, "[data-act=cd-finish]"); pg.wait_for_timeout(900)
    t = pg.evaluate("(document.querySelector('.rc-game')||{}).textContent||''")
    tl = pg.evaluate("(document.querySelector('.team-line')||{}).textContent||''")
    check("ニアピン: リザルトにコインの数、4人ならチームで見た1行（はなこ側のチームが1枚で勝ち）", "ニアピン" in t and "1枚" in t and "チームで見ると" in tl and pg.query_selector(".team-line .w") is not None)
    pg.context.close()
    pg, errs2 = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": ["はなこ", "", ""], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.game": "\"nearpin\""}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    C(pg, '[data-act=pin-q][data-i="1"]')
    on = pg.evaluate("state.draft.pins[0]") == 1 and pg.query_selector(".fc.fn.set") is not None
    C(pg, '[data-act=pin-q][data-i="1"]')
    check("ニアピン（くわしく記録）: 表のマスを押すと選ばれ、もう一度押すと取り消し", on and pg.evaluate("!state.draft.pins[0] && !document.querySelector('.fc.fn.set')"))
    check("ニアピン: エラーなし", not errs and not errs2)
    pg.context.close()
    c4 = [{"id": "c4", "name": "C4", "holes": [{"par": 3, "yards": 100}, {"par": 4, "yards": 300}] + [{"par": 3, "yards": 100}] * 7}]
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": ["はなこ", "", ""], "sc.courses": c4, "sc.favs": ["c4"], "sc.lastCourse": "\"c4\"", "sc.game": "\"nearpin\""}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    p3 = pg.query_selector("[data-act=pin-q]") is not None
    C(pg, "[data-act=next]")
    check("ニアピンはパー3だけ（パー4のホールにはマスが出ない）", p3 and pg.query_selector("[data-act=pin-q]") is None)
    check("ニアピン（パー3のみ）: エラーなし", not errs)
    pg.context.close()
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.inputMode": "\"card\"", "sc.mates": ["はなこ", "じろう", "さぶろう"], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.fields": dict(NONE)}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    for _ in range(80):
        if not pg.evaluate("state.cdraft&&state.cdraft.sel"): break
        C(pg, '[data-act=cd-key][data-v="3"]')
    pg.evaluate("window.__offline=false; window.__delay=700"); n0 = pg.evaluate("__db.rounds.length")
    C(pg, "[data-act=cd-finish]"); pg.wait_for_timeout(150)
    busy = "保存中" in (pg.text_content(".cd-save") or "")
    C(pg, "[data-act=cd-finish]"); pg.wait_for_timeout(1600)
    check("保存: 押したらすぐ「保存中…」になり、もう一度押しても二重に保存しない（4人で4件）", busy and pg.evaluate("__db.rounds.length") - n0 == 4 and pg.query_selector(".result-card") is not None)
    check("保存中の確認: エラーなし", not errs)
    pg.context.close()

    # 19b. コース画面: ★のコースは「コースを探す」の一覧に重ねて出さない
    cs = [{"id": "k1", "name": "コースK1", "holes": [{"par": 3, "yards": 100, "pref": "東京都"}] + [{"par": 3, "yards": 100}] * 8},
          {"id": "k2", "name": "コースK2", "holes": [{"par": 3, "yards": 100, "pref": "東京都"}] + [{"par": 3, "yards": 100}] * 8}]
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.courses": cs, "sc.favs": ["k1"], "sc.pref": "\"東京都\""}, offline=True)
    C(pg, "nav.tabs [data-tab=courses]")
    n = pg.evaluate("[...document.querySelectorAll('.cp-main b')].map(e=>e.textContent)")
    check("コース画面: ★のコースは探す一覧に重ならない", n.count("コースK1") == 1 and n.count("コースK2") == 1)
    C(pg, '.cp-fav[data-id="k2"]')
    check("コース画面: 全部★なら探す一覧に案内だけ", "すべて ★ よく行くコースに入っています" in pg.inner_text("body") and pg.evaluate("document.querySelectorAll('.cp-main').length") == 2)
    check("コース画面: エラーなし", not errs); pg.context.close()

    # 19c. 分析・履歴は自分だけ（プレーヤーの切りかえなし）、同率のミスは同じ濃さ、文字の大きさがそろう、ドーナツの線が交差しない
    c1x = [{"id": "c1", "name": "テスト", "holes": [{"par": 3, "yards": 100}] * 9}]
    def rnd(i, pl, dirs):
        return {"id": f"m{pl}{i}", "player": pl, "courseId": "c1", "courseName": "テスト", "date": f"2026-10-{10+i:02d}", "createdAt": i,
                "holes": [{"par": 3, "strokes": 3 if k else 4, "putts": 2, "dir": dirs[k] if k < len(dirs) else ""} for k in range(9)]}
    mine = [rnd(0, "たろう", ["on", "on", "left", "long"]), rnd(1, "たろう", []), rnd(2, "はなこ", [])]
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.rounds": mine, "sc.courses": c1x, "sc.tab": "\"history\""}, offline=True)
    check("履歴: 自分のラウンドだけ・プレーヤーの切りかえなし", pg.evaluate("document.querySelectorAll('.who-pick').length===0 && document.querySelectorAll('[data-act=toggle-round]').length===2"))
    C(pg, "nav.tabs [data-tab=stats]"); C(pg, "[data-act=stat-tab][data-v=shot]"); pg.wait_for_timeout(300)
    check("分析: プレーヤーの切りかえなし", pg.evaluate("document.querySelectorAll('.who-pick').length===0"))
    st = pg.evaluate("""(()=>{ const f=k=>document.querySelector('.dmap .bb[data-k="'+k+'"]'); const L=f('left'), O=f('long');
      return {same: L.querySelector('circle').getAttribute('r')===O.querySelector('circle').getAttribute('r'), worst: L.classList.contains('worst') && O.classList.contains('worst')}; })()""")
    check("分析の方向（グリーンの上の丸）: 同じ割合のミスは同じ大きさ・金の輪も両方", st["same"] and st["worst"])
    check("分析: 狙い通りの枠は出さない", pg.query_selector(".aim-box") is None)
    check("分析: 一番多いミスが同率なら両方書く", "左・オーバー" in pg.inner_text("body"))
    C(pg, "[data-act=demo-on]"); C(pg, "nav.tabs [data-tab=history]")
    check("サンプルデータは たろう だけ", pg.evaluate("state.demo") and "はなこ" not in pg.inner_text("body"))
    C(pg, "nav.tabs [data-tab=stats]"); C(pg, "[data-act=stat-tab][data-v=score]"); pg.wait_for_timeout(300)
    check("スコアの内訳は横棒の一覧（引き出し線なし）", pg.evaluate("document.querySelectorAll('.bk .bk-row').length>=3 && !document.querySelector('.dsvg')"))
    check("スコア推移は元の緑の棒だけ（切りかえ・ショット/パットの内訳なし）", pg.evaluate("!document.querySelector('[data-act=trend-mode]') && !document.querySelector('#tr-info')") and "ショット" not in pg.inner_text(".panel:has(#ch-score)"))
    C(pg, "[data-act=demo-off]"); C(pg, "nav.tabs [data-tab=history]"); C(pg, "[data-act=toggle-round]")
    C(pg, "[data-act=one-round]"); pg.wait_for_timeout(200)
    check("履歴から1ラウンドだけの分析を開ける", pg.evaluate("state.tab==='stats' && !!document.querySelector('.one-band') && !document.querySelector('#f-course')") and "このラウンドだけの分析" in pg.inner_text("body"))
    C(pg, "[data-act=one-off]")
    check("「全体に戻る」で絞り込みの画面に戻る", pg.evaluate("!document.querySelector('.one-band') && !!document.querySelector('#f-course')"))
    C(pg, "nav.tabs [data-tab=history]"); C(pg, "[data-act=one-round]"); C(pg, "nav.tabs [data-tab=history]"); C(pg, "nav.tabs [data-tab=stats]")
    check("ほかのタブへ移ると1ラウンドの分析は解除", pg.evaluate("!document.querySelector('.one-band')"))
    check("自分だけの分析: エラーなし", not errs); pg.context.close()

    # 19d1. 花道はミスにしない（一番多くても赤くしない）
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.inputMode": "\"hole\"", "sc.mates": [], "sc.fields": ALL}, offline=True)
    start_preset(pg, "firtree")
    hm = pg.evaluate("dirHeat('p3',{on:3,left:2,right:0,long:0,miss:5},10)")
    check("分析の方向: 花道はなく、ミスの4つの丸（オーバー・左・右・ショート）で一番多いミスに金の輪。オンはグリーンの上に文字だけ", "花道" not in hm and hm.count('class="bb ') == 4 and 'on-lab' in hm and 'worst' in hm and "<ellipse" in hm)
    check("花道のあともエラーなし", not errs)
    pg.context.close()

    # 19d2. 日本語の文字は13px以上（英語の小見出し・絵の中の文字・下のタブは除く）、ホールカードにパットの合計はない
    JPSMALL = r"""(()=>{const JP=/[\u3040-\u30ff\u4e00-\u9fff]/,o=[];document.querySelectorAll('body *').forEach(e=>{if(e.closest('svg,nav.tabs'))return;const t=[...e.childNodes].filter(n=>n.nodeType===3&&n.textContent.trim()).map(n=>n.textContent.trim()).join('');if(!t||!JP.test(t))return;const r=e.getBoundingClientRect();if(!r.width)return;if(parseFloat(getComputedStyle(e).fontSize)<12.95)o.push(t.slice(0,10));});return o})()"""
    FX = dict(ALL); FX["aim"] = True
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.inputMode": "\"hole\"", "sc.mates": [], "sc.fields": FX}, offline=True)
    small = pg.evaluate(JPSMALL)
    start_preset(pg, "firtree"); small += pg.evaluate(JPSMALL)
    check("ホールカードにパットの合計は出さない", "PUTTS" not in pg.inner_text(".hc2 .live"))
    C(pg, "[data-act=open-fields]"); small += pg.evaluate(JPSMALL)
    pg.context.close()
    for tab in ("score", "shot", "putt"):
        pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.tab": "\"stats\"", "sc.statTab": f"\"{tab}\""}, offline=True)
        C(pg, "[data-act=demo-on]"); pg.wait_for_timeout(500); small += pg.evaluate(JPSMALL); pg.context.close()
    if small: print("  小さい文字:", small[:8])
    check("日本語の文字は13px以上（記録・設定・分析）", not small)

    # 19d. 狙いと着弾（記録画面では入力しない。デモの記録で分析だけ確かめる）
    # 19e. 記録中のスコアカード・名前の下に合計は出さない・コース画面の色分け
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.inputMode": "\"hole\"", "sc.mates": ["はなこ", "", ""], "sc.fields": ALL}, offline=True)
    start_preset(pg, "firtree")
    check("名前の下に合計は出さない（表がすっきり）", pg.evaluate("!document.querySelector('.frow .mn small') && !/計\\s*\\d/.test(document.querySelector('.scoreblock').textContent)"))
    C(pg, "[data-act=open-card]")
    check("くわしく記録でもスコアカードを開ける（自分と同伴者）・下に合計の行はない", pg.evaluate("!!document.querySelector('.lc-sheet') && document.querySelectorAll('.lc-t tr').length>=4") and "はなこ" in pg.inner_text(".lc-sheet") and not pg.evaluate("!!document.querySelector('.lc-tot')"))
    check("同伴者あり: エラーなし", not errs); pg.context.close()

    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.favs": ["preset:firtree"], "sc.tab": "\"courses\""}, offline=True)
    check("コース画面: よく行くコースは色のついたかたまり", pg.evaluate("!!document.querySelector('.cp-favpanel .cp-ico')"))
    pg.context.close()

    # 19f. フリック入力（打数・パット・メダル・クラブ・方向・OB・バンカー）・ゴースト・チームの色・長い名前
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": ["はなこ", "じろう", "さぶろう"], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.game": "\"olympic\"", "sc.fields": ALL, "sc.medalTip": True}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    D = lambda: pg.evaluate("state.draft")
    check("全部ONの4人でも1画面（入力表の下に方向・クラブ・OB・バンカー）", pg.evaluate("document.documentElement.scrollHeight") <= 667 and pg.evaluate("document.querySelectorAll('.rowb .fc').length") == 4)
    pg.locator('.fc[data-k="s"][data-i="0"]').first.click(); pg.wait_for_timeout(80)
    check("ちょん（押すだけ）では値が変わらない", D()["holes"][0]["strokes"] == 3)
    flick(pg, "s", 0, 3); check("打数: 右へ動かして離す（4）", D()["holes"][0]["strokes"] == 4)
    flick(pg, "p", 0, 0); check("パット: 0（チップイン）まで選べる", D()["holes"][0]["putts"] == 0)
    flick(pg, "s", 2, 4); check("同伴者の打数もフリック", D()["mates"][1]["s"][0] == 5)
    flick(pg, "m", 0, 4); flick(pg, "m", 1, 4)
    check("メダル: 同じメダルは1人だけ（金は後の人へ移る）", pg.evaluate("JSON.stringify(state.draft.medals[0])") == "[0,4,0,0]")
    flick(pg, "m", 3, 5); check("メダル: ダイヤ（5）", D()["medals"]["0"][3] == 5)
    check("メダルのマスは図だけ（文字なし）で、ポイントは数字だけ", pg.evaluate("[...document.querySelectorAll('.fc.fm')].every(e=>[...e.childNodes].every(n=>n.nodeType!==3||!n.textContent.trim()||n.textContent.trim()==='—')&&![...e.children].some(c=>c.tagName==='SPAN'&&c.textContent.trim()&&c.textContent.trim()!=='—'))") and "pt" not in pg.inner_text(".scoreblock") and pg.evaluate("[...document.querySelectorAll('.frow .pt b')].map(e=>e.textContent).join()") == "0,4,0,5")
    check("メダルは円形でリボンつき（ギザギザなし）", "<circle" in pg.evaluate("document.querySelector('.fc.fm.set svg').innerHTML") and pg.evaluate("document.querySelector('.fc.fm.set svg').querySelectorAll('path').length") <= 5 and pg.evaluate("document.querySelector('.fc.fm.set svg').getAttribute('viewBox')") == "0 0 36 40")
    check("ダイヤのメダルにも数字の5が入る", pg.evaluate("document.querySelectorAll('.fc.fm')[3].querySelector('svg text')&&document.querySelectorAll('.fc.fm')[3].querySelector('svg text').textContent") == "5")
    C(pg, "[data-act=open-fields]")
    check("OB・1ペナは1つの設定（入力するかしないか）", pg.query_selector("#fld-pen") is None and "OB・1ペナ" in pg.inner_text(".fields-sheet"))
    C(pg, "[data-act=close-sheet]")
    flick(pg, "c", 0, 8); check("クラブ: 指の下のクラブ（9I）がそのまま選ばれる（並びは動かない）", D()["holes"][0]["club"] == "9I")
    flick(pg, "c", 0, 11); check("クラブ: 右端（SW）も選べる・SWのあとに別のクラブも選べる", D()["holes"][0]["club"] == "SW")
    flick(pg, "c", 0, 4); check("クラブ: SWのあとに5Iを選べる（SWを基準にずれない）", D()["holes"][0]["club"] == "5I")
    flick(pg, "d", 0, "C"); check("方向: パー3 の真ん中は「オン」", D()["holes"][0]["dir"] == "on" and pg.inner_text('.fc[data-k="d"]').strip() == "オン")
    flick(pg, "d", 0, "R"); check("方向: パー3 右へ＝右", D()["holes"][0]["dir"] == "right")
    flick(pg, "d", 0, "U"); check("方向: 上＝オーバー", D()["holes"][0]["dir"] == "long")
    flick(pg, "o", 0, 1); check("OB・ペナ: OB", pg.inner_text('.fc[data-k="o"]').strip() == "OB")
    flick(pg, "o", 0, 2); check("OB・ペナ: ペナに変わる", pg.inner_text('.fc[data-k="o"]').strip() == "ペナ")
    flick(pg, "b", 0, 1); check("バンカー: あり", D()["holes"][0]["bunker"] is True)
    check("フリック入力: エラーなし", not errs); pg.context.close()
    # チーム戦の色分け・長い名前
    pg, errs = open_page(b, {"sc.player": "\"たろうたろう\"", "sc.mates": ["はなこ", "じろう", "さぶろうさぶろう"], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.game": "\"team\"", "sc.fields": ALL}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    check("チーム戦: 1・2人目は A（紺）、3・4人目は B（金）の囲みに2人ずつ入る", pg.evaluate("[...document.querySelectorAll('.scoreblock .tgrp')].map(e=>(e.classList.contains('teamA')?'A':'B')+e.querySelectorAll('.frow').length).join()") == "A2,B2")
    check("チーム戦: 値は1チームに1つだけ・A/Bの文字や単位は出さない", pg.evaluate("[...document.querySelectorAll('.tgrp .tpt')].map(e=>e.textContent).join()") == "0,0" and pg.evaluate("document.querySelectorAll('.scoreblock .gtag').length") == 0)
    check("長い名前（6文字）は「…」にせず折り返す", pg.evaluate("[...document.querySelectorAll('.frow .mn span')].every(e=>e.scrollHeight<=e.clientHeight+2 && e.scrollWidth<=e.clientWidth+1)"))
    check("チーム戦・長い名前: エラーなし", not errs); pg.context.close()
    # ゴースト（個人戦）: 過去のベストの自分と勝負
    g1 = [{"id": "gb", "player": "たろう", "courseId": "c1", "courseName": "C1", "date": "2026-09-01", "createdAt": 1, "holes": [{"par": 3, "strokes": 4, "putts": 2}] * 9},
          {"id": "gw", "player": "たろう", "courseId": "c1", "courseName": "C1", "date": "2026-09-10", "createdAt": 2, "holes": [{"par": 3, "strokes": 3, "putts": 2}] * 9}]
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": [], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.game": "\"ghost\"", "sc.rounds": g1, "sc.fields": ALL}, offline=True)
    check("ゴースト: 1人でも選べる", pg.evaluate("gameFits('ghost',1)") if pg.evaluate("typeof gameFits==='function'") else True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    check("ゴースト: 始まる・ゴーストの行が出る（ベストは27打）", pg.evaluate("state.draft.game") == "ghost" and pg.query_selector(".ghostrow") is not None and "27" in pg.inner_text(".ghostrow"))
    flick(pg, "s", 0, 3)
    check("ゴースト: 差の列（ゴーストより1打多い＝＋1打。ゴルフのスコアと同じ向き）", "+1" in pg.evaluate("document.querySelector('.frow.me .pt').textContent") and "差" in pg.inner_text(".fhead"))
    flick(pg, "s", 0, 1)
    check("ゴースト: ゴーストより少ない打数なら −（勝ち）", "−1" in pg.evaluate("document.querySelector('.frow.me .pt').textContent") and pg.query_selector(".frow.me .pt b.gd.up") is not None)
    check("ゴースト: 全部ONでも1画面", pg.evaluate("document.documentElement.scrollHeight") <= 667)
    walk(pg)
    C(pg, "[data-act=finish]"); pg.wait_for_timeout(900)
    check("ゴースト: リザルトにゴーストとの勝負", "GHOST" in pg.inner_text(".result-card") or "ゴースト" in pg.inner_text(".result-card"))
    check("ゴースト: エラーなし", not errs); pg.context.close()

    # 19g. ゴーストは過去の記録がないコースでは選べない・前回のクラブが最初から入る・画面は上下に動かない・スコアカード入力は4人分の幅
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": [], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.game": "\"ghost\"", "sc.fields": ALL}, offline=True)
    C(pg, "[data-act=open-game]")
    check("ゴースト: そのコースの記録がないとカードが薄く「記録がないと選べません」", pg.query_selector(".gm-card.na[data-v=ghost]") is not None and "記録がないと選べません" in pg.inner_text(".gm-card[data-v=ghost]"))
    C(pg, ".game-sheet [data-act=close-game]"); C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    check("ゴースト: 記録がないコースで始めるとゲームなしで始まる", pg.evaluate("state.draft.game") is None)
    pg.context.close()
    old2 = [{"id": "o2", "player": "たろう", "courseId": "c1", "courseName": "C1", "date": "2026-09-01", "createdAt": 1, "holes": [{"par": 3, "strokes": 3, "putts": 2, "club": "8I"}] * 9}]
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": [], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.rounds": old2, "sc.fields": ALL}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    check("前に回ったホールは、前回のクラブが最初から入っている（点線の枠）", pg.inner_text('.fc[data-k="c"]').strip() == "8I" and pg.evaluate("document.querySelector('.fc[data-k=c]').classList.contains('auto')"))
    check("記録画面は上下に動かない（1画面に収まるとき）", pg.evaluate("document.documentElement.classList.contains('noscroll')"))
    pg.context.close()
    for nm, m in (("2人", ["はなこ"]), ("4人", ["はなこ", "じろう", "さぶろう"])):
        pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.inputMode": "\"card\"", "sc.mates": m, "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\""}, offline=True)
        C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
        hs[nm] = pg.evaluate("[document.querySelectorAll('.cdt thead tr:first-child th').length, Math.round(document.querySelector('.cdt td.c-s').getBoundingClientRect().width)]")
        pg.context.close()
    check("スコアカード入力は、2人でも4人と同じ列の数・同じ幅（使わない列は空）", hs["2人"] == hs["4人"])

    # 19h. 打数・パットは最初「入力」（未入力）・触るまで合計やポイントに入れない・同伴者のパット・スコアカードのゲーム入力
    g4 = [{"id": "gq", "player": "たろう", "courseId": "c1", "courseName": "C1", "date": "2026-09-01", "createdAt": 1, "holes": [{"par": 3, "strokes": 4, "putts": 2}] * 9}]
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": [], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.game": "\"ghost\"", "sc.rounds": g4, "sc.fields": ALL}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    check("最初は打数・パットが「入力」（グレー）で、合計は—・バッジは未入力", pg.evaluate("document.querySelectorAll('.scoreblock .fc.un').length") == 2 and "入力" in pg.inner_text(".fc.un") and pg.inner_text(".hc2 .live b").strip() == "—" and "未入力" in pg.inner_text(".hc2 .badges"))
    flick(pg, "s", 0, 1)
    check("打数を入れるとそのマスだけ数字になる（パットはまだ入力）", pg.evaluate("document.querySelectorAll('.scoreblock .fc.un').length") == 1 and D()["holes"][0]["strokes"] == 2)
    check("ゴースト: 1Hで −2打", "−2" in pg.inner_text(".frow.me .pt"))
    C(pg, "[data-act=next]")
    check("次のホールで何も入れていない間は、ゴーストとの差は変わらない（−2のまま）", "−2" in pg.inner_text(".frow.me .pt") and pg.evaluate("document.querySelectorAll('.scoreblock .fc.un').length") == 2)
    flick(pg, "s", 0, 2)
    check("次のホールで打数を入れると差に加わる（3打=ゴーストより1打少ない→−3）", "−3" in pg.inner_text(".frow.me .pt"))
    C(pg, "[data-act=prev]")
    check("前のホールに戻ると、入力した打数が出る（パットは入力していないので「入力」のまま）", pg.evaluate("document.querySelectorAll('.scoreblock .fc.un').length") == 1 and pg.inner_text('.fc[data-k="s"]').strip().startswith("2"))
    pg.context.close()
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": ["はなこ"], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.fields": ALL}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    check("パットのマスは自分だけ（同伴者は打数だけ）・同伴者のパットの設定はない", pg.query_selector('.fc[data-k="p"][data-i="1"]') is None and pg.query_selector('.fc[data-k="p"][data-i="0"]') is not None)
    flick(pg, "s", 0, 1); C(pg, "[data-act=next]")
    check("入力していないまま次のホールへ進んでも、勝手にパー・2パットにならない（「入力」のまま）", pg.evaluate("document.querySelectorAll('.scoreblock .fc.un').length") == 3 and pg.evaluate("state.draft.holes[1].sT") is False)
    C(pg, "[data-act=prev]")
    check("戻ると、入力したホールの値と、入力していないパットの「入力」が残っている", pg.evaluate("document.querySelectorAll('.scoreblock .fc.un').length") == 2)
    for _ in range(8): C(pg, "[data-act=next]")
    C(pg, "[data-act=finish]"); pg.wait_for_timeout(300)
    check("未入力の打数があると保存せず、そのホールへ案内する", pg.evaluate("state.draft!=null") and pg.evaluate("state.hole") == 0 and "まだです" in pg.inner_text(".toast, #toast, body"))
    pg.context.close()
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.inputMode": "\"card\"", "sc.mates": ["はなこ", "じろう"], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.game": "\"olympic\""}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    C(pg, '[data-act=cd-key][data-v="3"]')
    check("スコアカード＋オリンピック: 計の下に人ごとのメダルのマス（フリック）", pg.evaluate("document.querySelectorAll('.cdt tfoot .fc.fm').length") == 3)
    flick(pg, "m", 0, 4)
    check("スコアカード＋オリンピック: 自分の列でメダルをフリックで入れる（金）", pg.evaluate("state.cdraft.medals[0][0]") == 4)
    flick(pg, "m", 1, 4)
    check("スコアカード＋オリンピック: 同じメダルは1人だけ（金が後の人へ移る）", pg.evaluate("JSON.stringify(state.cdraft.medals[0])") == "[0,4,0]")
    check("スコアカード: 9ホールがスクロールなしで見える（4人・ゲームあり）", pg.evaluate("(()=>{const s=document.querySelector('.cd-scroll'); return s.scrollHeight<=s.clientHeight+4})()"))
    check("スコアカード: 1〜9Hと計は1つの枠、ゲームの入力・ポイントは別の枠", pg.evaluate("(()=>{const g=document.querySelector('.cd-game'), s=document.querySelector('.cd-scroll'); return !!g && !s.contains(g) && s.querySelector('tfoot').textContent.includes('計') && g.querySelector('.fc.fm')!==null})()"))
    check("スコアカード: 「◯◯・2H（PAR 3）の打数」の行はない（黄色のマスで分かる）", pg.query_selector(".cd-lbl") is None)
    check("スコアカード: ポイントの数字は大きい（20px以上）", pg.evaluate("parseFloat(getComputedStyle(document.querySelector('.cd-game tr.skr td:nth-child(2)')).fontSize)") >= 20)
    check("スコアカード: 文字は大きめ（打数のマスが20px以上）", pg.evaluate("parseFloat(getComputedStyle(document.querySelector('.cdt td button')).fontSize)") >= 20)
    pg.context.close()

    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": [], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.fields": ALL}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300); C(pg, "[data-act=open-fields]")
    check("入力中の歯車の画面は左右に動かない（上下だけ）", pg.evaluate("(()=>{const e=document.querySelector('.fields-sheet'); return e.scrollWidth<=e.clientWidth && getComputedStyle(e).overflowX==='hidden'})()"))
    pg.context.close()

    # 19i. オリンピックのメダルは人数に合わせる・初回のフリック案内
    for nm, m, want in (("4人", ["はなこ", "じろう", "さぶろう"], "0,1,2,3,4,5"), ("3人", ["はなこ", "じろう"], "0,2,3,4,5"), ("2人", ["はなこ"], "0,3,4,5")):
        pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": m, "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.game": "\"olympic\""}, offline=True)
        C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
        r = pg.locator('.fc[data-k="m"][data-i="0"]').first.bounding_box(); pg.mouse.move(r["x"] + r["width"] / 2, r["y"] + r["height"] / 2); pg.mouse.down(); pg.mouse.move(r["x"] + r["width"] / 2 + 10, r["y"] + r["height"] / 2, steps=2)
        got = pg.evaluate("[...document.querySelectorAll('.fpop .fpc')].map(e=>e.dataset.v).join()"); pg.mouse.up()
        check(f"オリンピック{nm}: 選べるメダルは {want}（人数が少ないと下のメダルから外れる）", got == want)
        pg.context.close()
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": [], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.flickTip": False}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    check("初めて詳細入力を開くと、フリックの使いかたの案内が出る", pg.query_selector(".ftip") is not None and pg.query_selector(".ftip .ft-finger") is not None and pg.query_selector(".ftip-x") is not None and pg.query_selector(".ftip p") is None)
    C(pg, ".ftip-x")
    check("右上の×で閉じて、二度と出ない", pg.query_selector(".ftip") is None and pg.evaluate("localStorage.getItem('sc.flickTip')") == "true")
    pg.context.close()

    # 19j. スコアカード入力: ポイントの行名・ダボなどの枠が重ならない・4文字の名前・ルールの再確認
    pg, errs = open_page(b, {"sc.player": "\"さぶろう\"", "sc.inputMode": "\"card\"", "sc.mates": ["はなこ", "じろう", "ごんべえ"], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.game": "\"nearpin\""}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    check("スコアカード: ゲームの行は「ポイント」（ニアピンでも）", "ポイント" in pg.inner_text(".cd-game tr.gr .ft") or "ポイント" in pg.inner_text(".cd-game"))
    check("スコアカード: 4文字の名前も「…」にならない", pg.evaluate("[...document.querySelectorAll('.cdt thead th.nm')].filter(e=>e.textContent.trim()).every(e=>e.scrollWidth<=e.clientWidth)"))
    check("スコアカード: ダボなどの記号の枠が上下の行にはみ出さない（外側の輪まで含めて行の高さに収まる）", pg.evaluate("(()=>{const td=document.querySelector('.cdt tbody td.c-s'); const m=document.createElement('span'); m.className='mk3 m-double'; td.appendChild(m); const r=m.getBoundingClientRect(), cs=getComputedStyle(m), ring=parseFloat(cs.outlineWidth)+parseFloat(cs.outlineOffset); m.remove(); return r.height+ring*2+2 <= td.getBoundingClientRect().height-4})()"))
    check("スコアカード: 左上はコース名だけ（日付・「スコアカード」・人数の行は出さない）", pg.query_selector(".rec-who span") is None and pg.query_selector(".rec-who b") is not None)
    check("スコアカード: H・PAR の下に空きの行はない（2行ぶち抜き）", pg.evaluate("document.querySelectorAll('.cdt thead th[rowspan]').length") == 2)
    C(pg, "[data-act=open-rules][data-v=nearpin]")
    check("スコアカード入力中もゲームのルールを見られる", pg.query_selector(".rp") is not None)
    C(pg, ".rp-x")
    pg.context.close()
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": ["はなこ"], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.game": "\"skins\"", "sc.fields": ALL}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    C(pg, "[data-act=open-rules][data-v=skins]")
    check("くわしく記録中もゲームのルールを見られる（右上の？ボタン）", pg.query_selector(".rp") is not None and pg.query_selector(".rule-btn") is not None)
    C(pg, ".rp-x")
    check("スキンズのコインは光沢つき（グラデーション）で、表の中でも大きい", pg.evaluate("(()=>{const c=document.querySelector('.frow .pt .coin'); return !!c && c.innerHTML.includes('radialGradient') && c.getBoundingClientRect().width>=24})()"))
    pg.context.close()

    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.inputMode": "\"card\"", "sc.mates": ["はなこ", "じろう", "さぶろう"], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.game": "\"teamsum\""}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    check("スコアカード（チーム戦）: 1・2人目はA（紺）、3・4人目はB（金）の列の色で囲う", pg.evaluate("[document.querySelectorAll('.cdt thead th.nm.tA').length, document.querySelectorAll('.cdt thead th.nm.tB').length, document.querySelectorAll('.cdt tbody td.tA').length>0, document.querySelectorAll('.cdt tbody td.tB').length>0].join()") == "2,2,true,true")
    pg.context.close()

    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.tab": "\"stats\"", "sc.statTab": "\"shot\""}, offline=True)
    C(pg, "[data-act=demo-on]"); pg.wait_for_timeout(500)
    ttl = pg.evaluate("[...document.querySelectorAll('#main .panel .sec em')].map(e=>e.textContent.trim()).join()")
    check("ショット分析は4つのまとまり（SHOTS・TEE SHOT・CLUB・弱点分析）、分析のタブは3つ（スコア・ショット・パット）", ttl == "SHOTS,TEE SHOT,CLUB,WEAK POINTS" and pg.evaluate("[...document.querySelectorAll('.stat-tabs button')].map(b=>b.textContent.replace(/[A-Z]/g,'')).join()") == "スコア,ショット,パット")
    check("弱点分析: 外しやすい方向・苦手なクラブ・3パット・OB・ペナ・バンカーが出る", pg.evaluate("document.querySelectorAll('.wk').length") >= 4)
    pg.context.close()

    # 20. Web版（Cloudflare Pages + D1）: ローカルの代わりのサーバー（tests/cf-server.mjs）で本物の functions/api.js を動かす
    import subprocess, time
    PORT = 8791
    srv = subprocess.Popen(["node", os.path.join(ROOT, "tests", "cf-server.mjs"), str(PORT)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    srv.stdout.readline()
    try:
        src = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
        src = (src.replace("setTimeout(checkUpdate, 1500);", "").replace("setTimeout(maybeShowA2HS, 2500);", "").replace("const state = {", "const state = window.state = {")
                  .replace("async function connect(){", "window.connect=()=>connect(); async function connect(){"))
        src = src.replace("<body>", "<body><script>if(!sessionStorage.t){sessionStorage.t=1;localStorage.clear();localStorage.setItem('sc.player',JSON.stringify('たろう'));localStorage.setItem('sc.flickTip','true');localStorage.setItem('sc.fields',JSON.stringify(%s));}</script>" % json.dumps(ALL), 1)
        open(os.path.join(ROOT, "_test_cf.html"), "w", encoding="utf-8").write(src)
        ctx = b.new_context(viewport={"width": 375, "height": 667}, service_workers="block"); pg = ctx.new_page(); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(f"http://127.0.0.1:{PORT}/_test_cf.html"); pg.wait_for_timeout(1200)
        check("Web版: 同じサイトの /api につながる（version 3・シート更新のお願いは出ない）", pg.evaluate("state.mode") == "cloud" and pg.evaluate("state.serverVer") == 3)
        start_preset(pg, "firtree"); play_round(pg, ALL)
        srvr = pg.evaluate("fetch('/api').then(r=>r.json())")
        hs = srvr["rounds"][0]["holes"] if srvr["rounds"] else []
        check("Web版: ラウンドがデータベースに保存され、クラブ・ティーショット・方向も残る", len(srvr["rounds"]) == 1 and any(h.get("club") for h in hs) and any(h.get("tee") for h in hs) and any(h.get("dir") for h in hs))
        check("Web版: コースも保存される", any(c.get("name") for c in srvr["courses"]))
        pg.evaluate("localStorage.removeItem('sc.rounds')"); pg.reload(); pg.wait_for_timeout(1200)
        check("Web版: 端末の控えを消して読み直してもデータベースから戻る", pg.evaluate("state.rounds.length") == 1 and pg.evaluate("state.rounds[0].holes.some(h=>h.club)"))
        rid = pg.evaluate("state.rounds[0].id")
        pg.evaluate(f"fetch('/api',{{method:'POST',body:JSON.stringify({{action:'deleteRound',id:'{rid}'}})}})"); pg.wait_for_timeout(200)
        check("Web版: 削除できる", len(pg.evaluate("fetch('/api').then(r=>r.json())")["rounds"]) == 0)
        check("Web版: エラーなし", not errs)
        ctx.close()
        # 古いアプリ（github.io）から引っ越し: 端末に残っていたクラブなども一緒に送る
        pg, errs = open_page(b, {"sc.player": "\"たろう\""}, repl={'const CF_URL = "";': f'const CF_URL = "http://127.0.0.1:{PORT}";'})
        pg.evaluate("window.__noNav=true; __db.courses.push({id:'cX',name:'旧コース',holes:[{par:3}]}); __db.rounds.push({id:'old1',player:'たろう',courseId:'cX',courseName:'旧コース',date:'2026-10-01',createdAt:1,holes:[{par:3,strokes:4,putts:2}]})")
        pg.evaluate("localStorage.setItem('sc.rounds', JSON.stringify([{id:'old1',player:'たろう',holes:[{par:3,strokes:4,putts:2,club:'7I',tee:null,dir:'right'}]}]))")
        pg.evaluate("connect()"); pg.wait_for_timeout(600)
        check("古いアプリ: 引っ越しの案内が出る", pg.query_selector(".move-bar [data-act=migrate]") is not None)
        C(pg, ".move-bar [data-act=migrate]"); pg.wait_for_timeout(1200)
        srvr = pg.evaluate(f"fetch('http://127.0.0.1:{PORT}/api').then(r=>r.json())")
        old = [r for r in srvr["rounds"] if r["id"] == "old1"]
        check("古いアプリ: 引っ越しボタンで新しいデータベースへ送られる（端末に残っていたクラブ・方向も）", len(old) == 1 and old[0]["holes"][0].get("club") == "7I" and old[0]["holes"][0].get("dir") == "right" and any(c["id"] == "cX" for c in srvr["courses"]))
        check("古いアプリ: 送ったあと「新しいアプリを開く」も出る", pg.query_selector(".move-bar a") is not None)
        check("引っ越し: エラーなし", not errs)
        pg.context.close()
    finally:
        srv.terminate()
        try: os.remove(os.path.join(ROOT, "_test_cf.html"))
        except OSError: pass

    b.close()

try: os.remove(os.path.join(ROOT, "_test.html"))
except OSError: pass
bad = [n for ok, n in results if not ok]
print("\n" + ("ALL OK (%d)" % len(results) if not bad else "NG %d件: %s" % (len(bad), " / ".join(bad))))
sys.exit(1 if bad else 0)
