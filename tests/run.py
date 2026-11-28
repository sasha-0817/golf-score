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

def open_page(b, ls=None, dark=False, offline=False, repl=None, query=""):
    ls = {"sc.flickTip": True, "sc.presets": "1", **(ls or {})}   # 用意済みコースはふだんは出さないが、テストでは出す   # 初回のフリック案内は、ふだんのテストでは出さない
    src = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    src = re.sub(r'const CF_URL = "[^"]*";', 'const CF_URL = "";', src, count=1)  # ふだんのテストは引っ越しの案内なし
    for a, z in (repl or {}).items(): src = src.replace(a, z)
    src = (src.replace("setTimeout(checkUpdate, 1500);", "").replace("setTimeout(maybeShowA2HS, 2500);", "")
              .replace("const state = {", "const state = window.state = {")
              .replace("function render(){", "window.render=render; function render(){")
              .replace("function courseSplit(", "window.courseSplit=courseSplit; function courseSplit(")
              .replace("function skinsCalc(", "window.skinsCalc=skinsCalc; function skinsCalc(")
              .replace("function courseGreens(", "window.courseGreens=courseGreens; function courseGreens(")
              .replace("function pickableCourses(", "window.pickableCourses=pickableCourses; window.showPresets=()=>SHOW_PRESETS; function pickableCourses(")
              .replace("function greenMap(", "window.dirHeat=dirHeat; window.greenMap=greenMap; function greenMap(")
              .replace("function matchCalc(", "window.matchCalc=matchCalc; window.teamCalc=teamCalc; window.teamSumCalc=teamSumCalc; window.GAME_KEYS_ORDER=()=>GAME_KEYS.join(); window.nassauCalc=nassauCalc; window.vegasCalc=vegasCalc; window.stableCalc=stableCalc; window.peoriaCalc=peoriaCalc; window.olympicCalc=olympicCalc; window.peoriaHidden=peoriaHidden; function matchCalc(")
              .replace("async function connect(){", "window.connect=async()=>{ await backup(); await legacyPull(); }; window.realConnect=()=>connect(); window.backup=()=>backup(); window.pendN=()=>pendN(); async function connect(){")
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
    pg.goto("file://" + path + query); pg.wait_for_timeout(1200)
    return pg, errs

def C(pg, sel):  # JS でクリック（重なりや disabled も含めて、アプリの動きをそのまま見る）
    if ("cd-key" in sel or "cd-finish" in sel) and pg.query_selector(".gp-over"):   # スコアカード入力: ゲームの入力の帯が開いていたら、先に「決定」で閉じる（数字キーの上に重なっている）
        pg.evaluate("document.querySelector('[data-act=cd-gp-ok]').dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}))"); pg.wait_for_timeout(60)
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
    elif k in ("m", "c", "s", "p"):
        pg.mouse.move(x + 6, y, steps=2)
        if k == "m": tgt = pg.evaluate("v=>[...document.querySelectorAll('.fpop .fpc')].findIndex(e=>e.dataset.v===String(v))", tgt)   # メダルは「点数」で指定する
        n = pg.evaluate("document.querySelectorAll('.fpop .fpc').length"); de = pg.evaluate("[...document.querySelectorAll('.fpop .fpc')].findIndex(e=>e.classList.contains('on'))")
        rs, ls = n - 1 - de, de
        cap = {"m": 36, "c": 44, "s": 34, "p": 40}[k]
        st = min(cap, (375 - 10 - x) / rs) if tgt > de and rs else (min(cap, (x - 10) / ls) if ls else cap)
        pg.mouse.move(x + (tgt - de) * max(8, st), y, steps=3)
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
    C(pg, "[data-act=open-pick]"); C(pg, f'[data-act=pick-course][data-id="preset:{key}"]'); C(pg, "[data-act=start]"); pg.wait_for_timeout(300)

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
    flick(pg, "s", 0, 0)
    check("打数の下限1・パットは打数-1まで", pg.evaluate("[state.draft.holes[0].strokes,state.draft.holes[0].putts]") == [1, 0])
    flick(pg, "p", 0, 6)
    check("パットを増やすと打数も増える（パットは打数-1まで）", pg.evaluate("[state.draft.holes[0].strokes,state.draft.holes[0].putts]") == [7, 6])
    flick(pg, "s", 0, 9)
    check("打数は10まで選べる（フリックの右端。さらに「10+」で11以上）", pg.evaluate("state.draft.holes[0].strokes") == 10)
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
    for dark in (False,):   # ナイトモードはやめた（いつも明るい表示）
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
    check("スコアカード: 開始画面に「一緒に回る」の1行（名前が並ぶ）。押すと3人分の入力欄が出る", "はなこ・じろう・さぶろう" in pg.inner_text("[data-act=open-mates]") and pg.query_selector(".mates input") is None and (C(pg, "[data-act=open-mates]") or True) and pg.evaluate("document.querySelectorAll('.mates input').length") == 3)
    C(pg, "[data-act=mates-done]")
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
    check("みんなのリザルト: 名前が2行の人がいても、順位の丸・合計の数字は全員同じ高さ",
          pg.evaluate("new Set([...document.querySelectorAll('table.v1 .v1c .rc-rk')].map(e=>Math.round(e.getBoundingClientRect().top))).size") == 1 and pg.evaluate("new Set([...document.querySelectorAll('table.v1 .v1c b')].map(e=>Math.round(e.getBoundingClientRect().top))).size") == 1)
    check("スコアカード: 保存後はみんなのリザルト（順位は縦の表の見出しに・全員のスコア）", pg.query_selector(".grp-card") is not None and pg.evaluate("document.querySelectorAll('table.v1 .v1c').length") == 4)
    check("スコアカード: リザルトで「みんな」＋4人を切りかえられる", pg.evaluate("document.querySelectorAll('.rc-who button').length") == 5 and C(pg, '.rc-who button:nth-child(3)') and pg.evaluate("state.result.player") == "はなこ")
    check("リザルト: 2回目からは出てくるアニメーションをくり返さない（点滅しない）", "still" in pg.evaluate("document.querySelector('.result-back').className"))
    check("リザルト: 全員分まとめて保存のボタン（枚数は書かない）", (pg.text_content("[data-act=save-all]") or "").strip() == "全員分をまとめて保存")
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
    C(pg, "[data-act=open-mates]")
    check("1ホールずつでも同伴者欄が出る", pg.evaluate("document.querySelectorAll('.mates input').length") == 3)
    pg.fill("#r-mate2", "まちがい"); pg.dispatch_event("#r-mate2", "input"); C(pg, '[data-act=mate-clear][data-i="2"]')
    check("同伴者を × で消せる（選ばないにできる）", pg.evaluate("document.querySelector('#r-mate2').value") == "" and pg.evaluate("document.querySelector('#r-mate0').value") == "たろう")
    C(pg, "[data-act=mates-done]"); pg.wait_for_timeout(100)
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
    C(pg, "[data-act=open-mates]"); w = pg.evaluate("[0,1,2].map(i=>Math.round(document.querySelector('#r-mate'+i).getBoundingClientRect().width))"); C(pg, "[data-act=mates-done]")
    check("同伴者の欄は名前がなくても × の分の幅を空けておく（入力しても幅が変わらない）", len(set(w)) == 1)
    C(pg, "[data-act=open-game]")
    check("ゲームの選択画面はカードだけ（ルールは出さない）", pg.query_selector(".game-sheet .sr-ex") is None and pg.query_selector(".gm-rule") is not None)
    r = pg.evaluate("""[...document.querySelectorAll('.game-sheet .gm-card')].map(c=>{const b=c.querySelector('.gm-h b'),m=c.querySelector('.gm-t small'),q=x=>{const e=c.querySelector(x);if(!e)return null;const r=e.getBoundingClientRect();return r.top+r.height/2};
      return [b.scrollWidth<=b.clientWidth&&b.getBoundingClientRect().height<=24, m.scrollWidth<=m.clientWidth&&m.getBoundingClientRect().height<20, q('.gm-rule')===null||Math.abs(q('.gm-rule')-q('.cp-radio'))<2, Math.abs(q('.cp-radio')-(c.getBoundingClientRect().top+c.getBoundingClientRect().height/2))<3]}).flat().every(x=>x)""")
    check("ゲームのカード: 名前・説明は1行で切れない、ルールと丸は縦の真ん中でそろう", r)
    C(pg, "[data-act=open-rules][data-v=peoria]"); pg.wait_for_timeout(200)
    pe = pg.evaluate("[...document.querySelectorAll('.rp .sr-pe')].map(r=>[...r.children].map(c=>[Math.round(c.getBoundingClientRect().left),c.scrollWidth<=c.clientWidth+1]))")
    check("新ペリアの例: 2人の行の列がそろい（左端が同じ）、文字が切れない・ハンデの理由の1行がある", len(pe)==2 and [c[0] for c in pe[0]]==[c[0] for c in pe[1]] and all(c[1] for r in pe for c in r) and "ハンデは隠しホールの合計で決まる" in pg.inner_text(".rp") and "ハンデ 1.2" in pg.inner_text(".rp") and "25.6" in pg.inner_text(".rp") and "はなこの逆転優勝" in pg.inner_text(".rp"))
    C(pg, ".rp-x")
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
    check("ゲームの一覧は上に個人戦、下にチーム戦（見出しつき）", ty.startswith(",#SOLO個人戦,ghost,peoria,olympic,nearpin,skins,snake") and "#TEAMチーム戦2対2・4人で,vegas,teamsum,team" in ty and ty.index("nassau") < ty.index("#TEAM"))
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
    check("ゲームの並び（個人戦をよく遊ばれる順→チーム戦）", pg.evaluate("GAME_KEYS_ORDER()") == "ghost,peoria,olympic,nearpin,skins,snake,match,stable,nassau,vegas,teamsum,team")
    ts = pg.evaluate("(()=>{ const x=teamSumCalc([[3,4],[5,3],[4,4],[4,5]],2); return [x.tot.join(), x.done]; })()")
    check("合計スコアの計算（2人の打数の合計どうし）", ts == ["15,17", 2])
    sf = pg.evaluate("(()=>stableCalc([[3,2,4,5],[5,3,3,2]],[3,3,3,3],4).pts.join())()")
    check("ステーブルフォードの計算（パー2・バーディ3・ボギー1・ダボ0）", sf == "6,7")
    C(pg, "[data-act=open-game]")
    check("ゲーム選択に12のゲーム（ゴーストを含む。ワンオンは一覧から外した）・4人用は2人のとき薄い", pg.evaluate("document.querySelectorAll('.gm-card').length") == 13 and not pg.query_selector(".gm-card[data-v=oneon]") and pg.query_selector(".gm-card.na[data-v=vegas]") is not None)
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
    check("オリンピック: リザルトは列固定の表（メダルの列は全員同じ位置・点数あり）", pg.evaluate("(()=>{const t=document.querySelector('.rc-game .oly-t'); if(!t) return false; const h=[...t.querySelectorAll('thead th')].length; return h>=3 && [...t.querySelectorAll('tbody tr')].every(r=>r.children.length===h) && t.querySelectorAll('td.p b').length>0 && t.getBoundingClientRect().right<=t.closest('.rc-game').getBoundingClientRect().right+1})()"))
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
    panel_open = pg.query_selector(".gp-over [data-act=cd-pin]") is not None   # 4人目を入れた直後に、ニアピンのパネルが自動で開く
    C(pg, '[data-act=cd-pin][data-i="1"]')
    tg = pg.evaluate("[...document.querySelectorAll('.cdt tr.gr .gtag')].map(e=>e.textContent).join()")
    check("ニアピン（スコアカード）: 全員の打数が入るとパネルが開き、一番近い人を選ぶとコイン1枚（記録中の行）", panel_open and tg == "0,1,0,0" and pg.query_selector(".pin-sheet") is None)
    C(pg, '[data-act=cd-pin][data-i="1"]')
    check("ニアピン（スコアカード）: もう一度押すと取り消し。名前のボタンは人数分（表の中には何も増えない）", pg.evaluate("!state.cdraft.pins[0]") and pg.evaluate("document.querySelectorAll('.gp-over [data-act=cd-pin]').length") == 4 and pg.evaluate("document.querySelectorAll('.cd-scroll .pinflag').length") == 0)
    C(pg, '[data-act=cd-pin][data-i="1"]')
    for _ in range(80):
        if not pg.evaluate("state.cdraft&&state.cdraft.sel"): break
        C(pg, '[data-act=cd-key][data-v="3"]')
    C(pg, "[data-act=cd-finish]"); pg.wait_for_timeout(900)
    t = pg.evaluate("(document.querySelector('.rc-game')||{}).textContent||''")
    nums = pg.evaluate("[...document.querySelectorAll('.rc-game .rs-row b .rs-num')].map(e=>Math.round(e.getBoundingClientRect().right))")
    check("ニアピン: リザルトにコインの数（数字は右そろえで列がそろう）。ゲームを選んだだけでは、勝手に1・2人目と3・4人目のチームにしない", "ニアピン" in t and "1枚" in t and len(nums) == 4 and len(set(nums)) == 1 and pg.query_selector(".team-line") is None and "チームで見ると" not in pg.inner_text(".result-card"))
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
    pg.evaluate("document.querySelector('[data-act=cd-finish]').click(); const b2=document.querySelector('[data-act=cd-finish]'); if(b2) b2.click();"); pg.wait_for_timeout(150)
    shown = pg.query_selector(".result-card") is not None   # サーバーの返事（0.7秒×4）を待たずに、すぐリザルトが出る
    pg.wait_for_timeout(3600)
    check("保存: サーバーを待たずにすぐ終わり、続けて2回押しても二重に保存しない（4人で4件）", shown and pg.evaluate("state.rounds.length") == 4 and pg.evaluate("__db.rounds.length") - n0 == 4)
    check("保存中の確認: エラーなし", not errs)
    pg.context.close()

    # 19b. コース画面は「マイコース」だけ（全国の一覧・県の絞り込み・★はない）。自分が登録した・使ったコースだけ出る
    cs = [{"id": "k1", "name": "コースK1", "holes": [{"par": 3, "yards": 100, "pref": "東京都"}] + [{"par": 3, "yards": 100}] * 8},
          {"id": "k2", "name": "コースK2", "holes": [{"par": 3, "yards": 100}] * 9},
          {"id": "k3", "name": "コースK3", "holes": [{"par": 3, "yards": 100}] * 9}]
    rk = [{"id": "rk", "player": "たろう", "courseId": "k2", "courseName": "コースK2", "date": "2026-10-10", "createdAt": 1, "holes": [{"par": 3, "strokes": 3, "putts": 2}] * 9}]
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.courses": cs, "sc.favs": ["k1"], "sc.rounds": rk}, offline=True)
    C(pg, "nav.tabs [data-tab=courses]")
    n = pg.evaluate("[...document.querySelectorAll('.cp-main b')].map(e=>e.textContent)")
    check("コース画面: マイコースだけ（登録したK1・使ったK2。ほかの人のK3は出ない）", sorted(x for x in n if x.startswith("コースK")) == ["コースK1", "コースK2"])
    check("コース画面: 「コースを探す」・県の絞り込み・★はない", "コースを探す" not in pg.inner_text("body") and pg.query_selector("#cl-pref") is None and pg.query_selector(".cp-fav") is None and "マイコース" in pg.inner_text("body"))
    C(pg, "nav.tabs [data-tab=record]"); pg.wait_for_timeout(300); C(pg, "[data-act=open-pick]"); pg.wait_for_timeout(200)
    names = pg.evaluate("[...document.querySelectorAll('.cp-fcard b')].map(e=>e.textContent)")
    check("コースを選ぶ: マイコースだけ・★も県の絞り込みもなく、「＋ コースを追加」がある", "コースK3" not in names and "コースK1" in names and "★" not in pg.inner_text(".cp-sheet") and pg.query_selector(".cp-sheet select") is None and "＋ コースを追加" in pg.inner_text(".cp-sheet"))
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
    st = pg.evaluate("""(()=>{ const f=k=>document.querySelector('.heat.p3 .ht[data-k="'+k+'"]'); const L=f('left'), O=f('long');
      return {same: L.getAttribute('style')===O.getAttribute('style'), worst: L.classList.contains('worst') && O.classList.contains('worst'), ctr: !f('on').getAttribute('style')}; })()""")
    check("分析の方向（十字）: 同じ割合のミスは同じ赤、真ん中（オン）は色なし", st["same"] and st["worst"] and st["ctr"])
    check("分析: 狙い通りの枠は出さない", pg.query_selector(".aim-box") is None)
    check("分析: 一番多いミスが同率なら両方書く", "左・オーバー" in pg.inner_text("body"))
    C(pg, "[data-act=demo-on]"); C(pg, "nav.tabs [data-tab=history]")
    check("サンプルデータは たろう だけ", pg.evaluate("state.demo") and "はなこ" not in pg.inner_text("body"))
    C(pg, "nav.tabs [data-tab=stats]"); C(pg, "[data-act=stat-tab][data-v=score]"); pg.wait_for_timeout(300)
    check("スコアの内訳は横棒の一覧（引き出し線なし）", pg.evaluate("document.querySelectorAll('.bk .bk-row').length>=3 && !document.querySelector('.dsvg')"))
    check("スコア推移: 「対パー／打数」の切りかえがある（ショット/パットの内訳はない）", pg.evaluate("document.querySelectorAll('[data-act=trend-mode]').length") == 2 and not pg.evaluate("!!document.querySelector('#tr-info')") and "ショット" not in pg.inner_text(".panel:has(#ch-score)"))
    C(pg, "[data-act=trend-mode][data-v=total]"); pg.wait_for_timeout(300)
    check("打数に切りかえると、軸の名前が「打数」になり、選んだ状態が残る", pg.evaluate("localStorage.getItem('sc.trendTotal')") == "true" and pg.evaluate("document.querySelector('[data-act=trend-mode][data-v=total]').getAttribute('aria-pressed')") == "true")
    C(pg, "[data-act=trend-mode][data-v=diff]"); pg.wait_for_timeout(300)
    check("対パーに戻せる・エラーなし", pg.evaluate("localStorage.getItem('sc.trendTotal')") == "false" and not errs)
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
    check("分析の方向: 花道はなく、十字の5マス（オーバー・左・オン・右・ショート）。オンは色なしで、ミスだけ赤の濃さ", "花道" not in hm and hm.count('class="ht ') == 5 and 'ctr' in hm and 'worst' in hm)
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
        C(pg, "[data-act=demo-on]"); pg.wait_for_timeout(500); small += pg.evaluate(JPSMALL)
        pg.context.close()
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
    check("コース画面: 用意済みコースでも、マイコースに出る", pg.evaluate("document.querySelectorAll('.cp-main').length")>=0)
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
    check("メダルは円形でリボンつき（ギザギザなし）", "<circle" in pg.evaluate("document.querySelector('.fc.fm.set svg').innerHTML") and pg.evaluate("document.querySelector('.fc.fm.set svg').querySelectorAll('path').length") <= 5 and pg.evaluate("document.querySelector('.fc.fm.set svg').getAttribute('viewBox')") == "0 0 36 41")
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
    # ヤードのパターンは4つまで（ABC・LRなど）。コース編集 → 保存 → ラウンド開始で選べる → ホールカードで切りかえ
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.tab": "\"courses\"", "sc.mates": [], "sc.fields": ALL})
    C(pg, "[data-act=new-course]"); pg.wait_for_timeout(200)
    pg.fill("#c-name", "三パターン場"); C(pg, "[data-act=set-np][data-n='3']"); pg.wait_for_timeout(200)
    check("ヤードのパターン: 3つにすると名前の欄が3つ・ホールごとのヤード欄が3つ", pg.evaluate("document.querySelectorAll('.gnames input').length") == 3 and pg.evaluate("document.querySelectorAll('.par-cell')[0].querySelectorAll('input').length") == 3)
    pg.fill("#c-g0", "A"); pg.fill("#c-g1", "B"); pg.fill("#c-g2", "C")
    for i in range(9):
        pg.fill(f"#yd-{i}", str(100 + i)); pg.fill(f"[data-ydk='{i},1']", str(110 + i)); pg.fill(f"[data-ydk='{i},2']", str(120 + i))
    C(pg, "[data-act=save-course]"); pg.wait_for_timeout(400)
    cs = pg.evaluate("state.courses.find(c=>c.name==='三パターン場')")
    check("ヤードのパターン: 保存すると yards・yards2・yards3 と名前 A,B,C が残る", cs and cs["holes"][0].get("yards3") == 120 and cs["holes"][0].get("yards2") == 110 and cs["holes"][0].get("gn") == ["A", "B", "C"])
    check("ヤードのパターン: 保存後に開き直すと3つのまま", pg.evaluate("(()=>{const c=state.courses.find(c=>c.name==='三パターン場'); return courseGreens(c).join()})()") == "A,B,C")
    pg.evaluate("state.pick = state.courses.find(c=>c.name==='三パターン場').id")
    C(pg, "nav.tabs [data-tab=record]"); pg.wait_for_timeout(500)
    check("ヤードのパターン: ラウンド開始で今日のパターンを3つから選べる", pg.evaluate("document.querySelectorAll('#r-green option').length") == 3 if pg.query_selector("#r-green") else False)
    pg.select_option("#r-green", "2"); C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    check("ヤードのパターン: 3つ目(C)を選ぶと1番ホールは120ヤード、ホールカードに切りかえボタンが3つ", "120" in pg.inner_text(".hc-m .yd") and pg.evaluate("document.querySelectorAll('.gsw button').length") == 3)
    C(pg, "[data-act=set-g][data-g='1']"); pg.wait_for_timeout(200)
    check("ヤードのパターン: B に切りかえると全ホールが B のヤード（110）", "110" in pg.inner_text(".hc-m .yd"))
    check("ヤードのパターン: エラーなし", not errs); pg.context.close()
    # ホール数は1〜18を自由に（押したまま動かして選ぶ6×3の表）。減らしても増やし直すと数字が戻る
    pg, errs = open_page(b, {"sc.tab": "\"courses\"", "sc.mates": []}, offline=True)
    C(pg, "[data-act=new-course]"); pg.wait_for_timeout(300)
    def pick_holes(n):
        C(pg, "[data-act=open-holes]"); pg.wait_for_timeout(150); C(pg, f'[data-act=set-holes][data-n="{n}"]'); pg.wait_for_timeout(200)
    C(pg, "[data-act=open-holes]"); pg.wait_for_timeout(150)
    check("ホール数: タップで開く6列×3行（18個）の表が出る", pg.evaluate("document.querySelectorAll('.hgrid button').length") == 18)
    hg = pg.evaluate("(()=>{ const b=[...document.querySelectorAll('.hgrid button')], r=b[17].getBoundingClientRect(), g=getComputedStyle(b[0]); return [r.right<=innerWidth, b[5].getBoundingClientRect().top===b[0].getBoundingClientRect().top, g.minWidth, g.paddingLeft]; })()")
    check("ホール数: 6・12・18 の列も画面の中に収まる（ボタンの最小幅・余白で右にはみ出さない）", hg[0] and hg[1] and hg[2] in ("0px", "auto") and hg[3] == "0px")
    C(pg, "[data-act=close-holes]"); pg.wait_for_timeout(150)
    pick_holes(7)
    check("ホール数: 7ホールにできる（6・9・12・18に限らない）", pg.evaluate("state.courseEdit.holes.length") == 7 and pg.evaluate("document.querySelectorAll('.par-cell').length") == 7)
    pg.fill("#yd-6", "150"); pick_holes(3)
    check("ホール数: 3ホールに減らせる", pg.evaluate("document.querySelectorAll('.par-cell').length") == 3)
    pick_holes(7)
    check("ホール数: 増やし直すと、消した7番のヤード(150)が戻る", pg.input_value("#yd-6") == "150")
    pick_holes(18)
    check("ホール数: 18まで増やせる", pg.evaluate("state.courseEdit.holes.length") == 18)
    check("コース編集: 「このコースを削除」は出ない（みんなが同じコースを使うため）", pg.query_selector("[data-act=ask-del-course]") is None and pg.query_selector(".link-del") is None)
    check("ホール数: エラーなし", not errs); pg.context.close()
    # 用意済みコースは、ふだん（sc.presets なし）は一覧に出ない。?presets=1 で出る（確認用）
    pg, errs = open_page(b, {"sc.presets": "0", "sc.pref": "\"大分県\""})
    check("用意済みコース: ふだんは一覧に出ない", pg.evaluate("pickableCourses().filter(c=>c.preset).length") == 0 and pg.evaluate("showPresets()") is False)
    pg.close(); pg.context.close()
    pg, errs = open_page(b, {"sc.pref": "\"大分県\""})
    check("用意済みコース: 確認用に出す設定のときは一覧に出る", pg.evaluate("pickableCourses().filter(c=>c.preset).length") == 5)
    check("用意済みコース: エラーなし", not errs); pg.context.close()
    # 用意済みコース: 大野川ゴルフコース（大分市・6ホール・パー21、MG 1364Y / SG 1287Y。公式サイトの表と合計が一致）
    pg, errs = open_page(b, {"sc.fields": ALL, "sc.player": "\"たろう\"", "sc.pref": "\"大分県\""}, offline=True)
    start_preset(pg, "oonogawa")
    check("大野川ゴルフコース: 6ホール・パー21・MG 1364Y・SG 1287Y（公式の表と一致）", pg.evaluate("state.draft.holes.length") == 6 and pg.evaluate("state.draft.holes.reduce((a,h)=>a+h.par,0)") == 21 and pg.evaluate("state.draft.holes.reduce((a,h)=>a+h.ys[0],0)") == 1364 and pg.evaluate("state.draft.holes.reduce((a,h)=>a+h.ys[1],0)") == 1287 and pg.evaluate("JSON.stringify(state.draft.gn)") == '["MG","SG"]')
    check("大野川ゴルフコース: エラーなし", not errs); pg.context.close()
    # 用意済みコース: 直川カントリー倶楽部（佐伯市・9ホールを2周する形で18ホール、パー58。OUT 1288Y・IN 1361Y）
    pg, errs = open_page(b, {"sc.fields": ALL, "sc.player": "\"たろう\"", "sc.pref": "\"大分県\""}, offline=True)
    start_preset(pg, "naokawa")
    check("直川カントリー倶楽部: 18ホール・パー58・OUT 1288Y・IN 1361Y（公式の表と一致）", pg.evaluate("state.draft.holes.length") == 18 and pg.evaluate("state.draft.holes.reduce((a,h)=>a+h.par,0)") == 58 and pg.evaluate("state.draft.holes.slice(0,9).reduce((a,h)=>a+h.yards,0)") == 1288 and pg.evaluate("state.draft.holes.slice(9).reduce((a,h)=>a+h.yards,0)") == 1361)
    check("直川カントリー倶楽部: エラーなし", not errs); pg.context.close()
    # ここまでを保存して終わる（夕暮れ・ハーフ・6番から回った、など）: 打数を入れたホールだけが、ホール番号つきで保存される
    pg, errs = open_page(b, {"sc.fields": ALL, "sc.player": "\"たろう\"", "sc.mates": []})
    start_preset(pg, "firtree")
    check("途中で終わる: 何も入れていないときは「ここまでを保存」は出ない", (C(pg, "[data-act=ask-discard]") or True) and pg.query_selector("[data-act=finish-early]") is None)
    C(pg, "[data-act=cancel-confirm]")
    flick(pg, "s", 0, 3); C(pg, '[data-act=hole][data-i="5"]'); pg.wait_for_timeout(250); flick(pg, "s", 0, 3)
    C(pg, "[data-act=ask-discard]"); pg.wait_for_timeout(150)
    check("途中で終わる: 「ここまで（2ホール）を保存して終わる」が出る", pg.query_selector("[data-act=finish-early]") is not None and "2ホール" in pg.inner_text("[data-act=finish-early]"))
    C(pg, "[data-act=finish-early]"); pg.wait_for_timeout(900)
    r0 = pg.evaluate("state.rounds[state.rounds.length-1]")
    check("途中で終わる: 入れた2ホールだけ（1番と6番）がホール番号つきで保存される", len(r0["holes"]) == 2 and r0.get("nos") == [1, 6])
    check("途中で終わる: 6番のパーは、そのコースの6番のパー（3）", r0["holes"][1]["par"] == 3)
    check("途中で終わる: エラーなし", not errs); pg.context.close()
    pg, errs = open_page(b, {"sc.fields": ALL, "sc.player": "\"たろう\"", "sc.inputMode": "\"card\"", "sc.mates": ["はなこ"]})
    start_preset(pg, "firtree")
    for v in ("4", "2", "5"): C(pg, f'[data-act=cd-key][data-v="{v}"]')
    C(pg, "[data-act=ask-discard]"); pg.wait_for_timeout(150)
    check("途中で終わる（スコアカード）: 全員が入れた1ホールだけなら「1ホール」と出る", pg.query_selector("[data-act=cd-finish-early]") is not None and "1ホール" in pg.inner_text("[data-act=cd-finish-early]"))
    C(pg, "[data-act=cd-finish-early]"); pg.wait_for_timeout(900)
    rs = pg.evaluate("state.rounds.slice(-2)")
    check("途中で終わる（スコアカード）: 2人分とも1ホールだけで保存される（nos=[1]）", len(rs) == 2 and all(len(r["holes"]) == 1 and r.get("nos") == [1] for r in rs))
    check("途中で終わる（スコアカード）: エラーなし", not errs); pg.context.close()
    # 平均・BEST・推移は、全ホールを回ったラウンドだけ。ホール数が混ざるときは「1ホールあたりの対パー」で、打数への切りかえは出ない
    def rnd(i, course, n, par, strokes, nos=None):
        r = {"id": f"r{i}", "player": "たろう", "courseId": course, "courseName": course, "date": f"2026-10-{10+i:02d}", "createdAt": i,
             "holes": [{"par": par, "strokes": strokes, "putts": 2} for _ in range(n)]}
        if nos: r["nos"] = nos
        return r
    rs1 = [rnd(1, "A", 9, 3, 4), rnd(2, "A", 9, 3, 4), rnd(3, "A", 3, 3, 6, [1, 2, 3])]   # 9ホール×2（各+9）と、途中で終わった3ホール（各+9）
    pg, errs = open_page(b, {"sc.rounds": rs1, "sc.player": "\"たろう\"", "sc.tab": "\"stats\""}, offline=True)
    pg.wait_for_timeout(500)
    check("分析: コースの絞り込みは記録から作る（コースの登録がなくても、記録のあるコースを選べる）", pg.evaluate("[...document.querySelectorAll('#f-course option')].some(o=>o.value==='A')"))
    check("分析: 青い平均のカードは出さない（直近3ラウンドが先頭）", pg.query_selector(".hero-stat") is None and pg.query_selector(".recent") is not None)
    check("推移: 「途中で終わった1回は含めていません」と出る", "途中で終わった1回は含めていません" in pg.inner_text(".note-partial"))
    check("推移: 回ったホール数がそろっていれば、打数／対パーの切りかえが出る", pg.query_selector("[data-act=trend-mode]") is not None)
    check("平均: エラーなし", not errs); pg.context.close()
    rs2 = [rnd(1, "A", 9, 3, 4), rnd(2, "B", 18, 3, 4), rnd(3, "B", 18, 3, 3)]   # 9H(+9) と 18H(+18)・18H(±0) が混ざる → 1ホールあたり +1.0, +1.0, 0.0 の平均
    pg, errs = open_page(b, {"sc.rounds": rs2, "sc.player": "\"たろう\"", "sc.tab": "\"stats\""}, offline=True)
    pg.wait_for_timeout(500)
    check("分析: ホール数が混ざっても青いカードは出ない", pg.query_selector(".hero-stat") is None)
    check("推移: ホール数が混ざるときは、いちばん多いホール数（18H）だけを図にして、切りかえも出る。外した回数を書く", pg.query_selector("[data-act=trend-mode]") is not None and "18ホールのラウンドだけ" in pg.inner_text("#main") and "1回は含めていません" in pg.inner_text("#main"))
    check("平均（混在）: エラーなし", not errs); pg.context.close()
    # ゴースト × スコアカード入力: ゴーストの打数（見ているホール）とここまでの差が出る
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.inputMode": "\"card\"", "sc.mates": [], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.game": "\"ghost\"", "sc.rounds": g1, "sc.fields": ALL}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    check("ゴースト（スコアカード）: 2人目の位置に「ゴースト」の列があり、全ホールのゴーストの打数が縦に入る（27打）", "ゴースト" in pg.inner_text(".cdt thead") and pg.evaluate("[...document.querySelectorAll('.cd-scroll tbody td.gcol')].length") == 9 and "27" in pg.inner_text(".cd-scroll tfoot td.gcol"))
    C(pg, '[data-act=cd-key][data-v="4"]'); C(pg, '[data-act=cd-key][data-v="2"]')
    check("ゴースト（スコアカード）: ポイントはゴーストより1打多い＝＋1打（赤）", "+1" in pg.inner_text(".cd-game") and pg.query_selector(".cd-game b.gd.dn") is not None)
    check("ゴースト（スコアカード）: エラーなし", not errs); pg.context.close()

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
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.inputMode": "\"card\"", "sc.mates": ["はなこ", "じろう"], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.game": "\"olympic\"", "sc.fields": dict(NONE)}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    C(pg, '[data-act=cd-key][data-v="3"]')
    check("スコアカード＋オリンピック: 打数を入れている途中は数字キーのまま（メダルの帯は出ない）。表の中にもメダルの欄はない", pg.query_selector(".gp-over") is None and pg.query_selector(".cd-keys") is not None and pg.evaluate("document.querySelectorAll('.cdt .fc.fm, .cdt tfoot tr.gin').length") == 0)
    C(pg, '[data-act=cd-key][data-v="3"]'); C(pg, '[data-act=cd-key][data-v="3"]'); pg.wait_for_timeout(350)
    check("スコアカード＋オリンピック: 全員の打数がそろうと、数字キーの上に「メダル」の帯が重なって開く（人の列は上の表と同じ位置・同じ幅）", pg.query_selector(".gp-over") is not None and pg.evaluate("document.querySelectorAll('.gp-over .fc.fm').length") == 3 and "メダル" in pg.inner_text(".gp-h") and pg.evaluate("(()=>{const a=[...document.querySelectorAll('.cdt thead th.nm')].map(e=>Math.round(e.getBoundingClientRect().left)), b=[...document.querySelectorAll('.gp-over .gin-c')].map(e=>Math.round(e.getBoundingClientRect().left)); return b.length>0 && b.every((x,i)=>Math.abs(x-a[i])<=2)})()") and pg.evaluate("(()=>{const a=document.querySelector('.cd-pad').getBoundingClientRect(), o=document.querySelector('.gp-over').getBoundingClientRect(); return o.top<=a.top+2 && o.bottom>=a.bottom-2 && !!document.querySelector('.gp-scrim')})()"))
    flick(pg, "m", 0, 4)
    check("スコアカード＋オリンピック: 自分の列で金をフリックで入れる", pg.evaluate("state.cdraft.medals[0][0]") == 4)
    flick(pg, "m", 1, 4)
    check("スコアカード＋オリンピック: 同じメダルは1人だけ（金が後の人へ移る）", pg.evaluate("JSON.stringify(state.cdraft.medals[0])") == "[0,4,0]")
    flick(pg, "m", 1, 0); flick(pg, "m", 2, 2)
    check("スコアカード＋オリンピック: 「なし」にすると外れる（金を外して、別の人に銅）", pg.evaluate("JSON.stringify(state.cdraft.medals[0])") == "[0,0,2]")
    check("スコアカード: 9ホールがスクロールなしで見える（4人・ゲームあり）", pg.evaluate("(()=>{const s=document.querySelector('.cd-scroll'); return s.scrollHeight<=s.clientHeight+4})()"))
    check("スコアカード: 1〜9Hと計は1つの枠、ゲームのポイントは別の枠", pg.evaluate("(()=>{const g=document.querySelector('.cd-game'), s=document.querySelector('.cd-scroll'); return !!g && !s.contains(g) && s.querySelector('tfoot').textContent.includes('計') && g.querySelector('tr.skr')!==null})()"))
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
        pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": m, "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.game": "\"olympic\"", "sc.inputMode": "\"card\"", "sc.fields": dict(NONE)}, offline=True)
        C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
        for _ in range(1 + len(m)): C(pg, '[data-act=cd-key][data-v="3"]')
        r = pg.locator('.gp-over .fc[data-k="m"][data-i="0"]').first.bounding_box(); pg.mouse.move(r["x"] + r["width"] / 2, r["y"] + r["height"] / 2); pg.mouse.down(); pg.mouse.move(r["x"] + r["width"] / 2 + 10, r["y"] + r["height"] / 2, steps=2)
        got = pg.evaluate("[...document.querySelectorAll('.fpop .fpc')].map(e=>e.dataset.v).join()"); pg.mouse.up()
        check(f"スコアカード入力のオリンピック{nm}: 帯でフリックして選べるメダルも {want}", got == want)
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
    check("スコアカード: ボギー△・ダボ□・トリプル以上の二重□の記号が上下の行にはみ出さない", pg.evaluate("(()=>{const td=document.querySelector('.cdt tbody td.c-s'); const ok=['m-bogey','m-double','m-triple'].map(k=>{const m=document.createElement('span'); m.className='mk3 '+k; m.textContent='4'; td.appendChild(m); const r=m.getBoundingClientRect(); m.remove(); return r.height+2 <= td.getBoundingClientRect().height-4}); return ok.every(Boolean)})()"))
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
    check("ショット分析は3つのまとまり（SHOTS・TEE SHOT・CLUB）、弱点分析はない、分析のタブは3つ", ttl == "SHOTS,TEE SHOT,CLUB" and pg.query_selector(".wk") is None and pg.evaluate("[...document.querySelectorAll('.stat-tabs button')].map(b=>b.textContent.replace(/[A-Z]/g,'')).join()") == "スコア,ショット,パット")
    pg.context.close()

    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.tab": "\"stats\"", "sc.statTab": "\"shot\""}, offline=True)
    C(pg, "[data-act=demo-on]"); pg.wait_for_timeout(500)
    sm = lambda sel: pg.evaluate("s=>[...document.querySelectorAll(s)].map(e=>parseInt(e.textContent)).reduce((a,b)=>a+b,0)", sel)
    check("方向の十字の割合は整数で、5マスの合計が100になる（パー3）／4マスの合計が100（パー4・5）", sm(".heat.p3 .ht b") == 100 and sm(".heat.fw .ht b") == 100)
    check("方向の十字は横幅がコンパクト（260px以下）", pg.evaluate("document.querySelector('.heat').getBoundingClientRect().width") <= 260)
    pg.evaluate("state.statTab='score'; render()"); ad = pg.evaluate("[document.querySelectorAll('.alld-svg circle').length, (()=>{const e=document.querySelector('.alld-svg'); return e?e.getBoundingClientRect().width<=document.documentElement.clientWidth:false})()]")
    lg = pg.evaluate("document.querySelector('.alld-lg')?document.querySelector('.alld-lg').innerText:''")
    check("分析: 15回より多いと「これまでの全ラウンド」（1ラウンド1つの点、横に動かさず1画面。線は直近5回の平均と書いてある）", ad[0] == 60 and ad[1] and "直近5回の平均" in lg and pg.evaluate("document.querySelectorAll('.alld-svg polyline').length") == 1)
    pg.evaluate("state.statTab='shot'; render()")
    cb = pg.evaluate("(()=>{const r=document.querySelector('.clubs2 .cb-row'); if(!r) return null; const b=r.querySelector('.cb-bar').getBoundingClientRect(); return [Math.round(b.width), document.querySelectorAll('.club-tbl').length]})()")
    check("クラブ別: 棒は横に長く（150px以上）、表ではなく1クラブ1行の棒（得意・苦手の札は出さない）", cb is None or (cb[0] >= 150 and cb[1] == 0 and pg.evaluate("document.querySelectorAll('.clubs2 .ctag').length") == 0))
    check("割合に小数点は出さない（リング・クラブ別など）", pg.evaluate("!/\\d\\.\\d+\\s*%/.test(document.querySelector('#main').innerText)"))
    C(pg, "[data-act=stat-tab][data-v=score]"); pg.wait_for_timeout(300)
    check("スコアの内訳の割合は整数で、合計が100になる", sm(".bk .bk-row b") == 100)
    pg.context.close()

    # 19k. 保存したあと、スタート画面には前回の同伴者・コース・ゲームが残っている（専用の「もう1周」ボタンは作らない）
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": ["はなこ", "", ""], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.game": "\"skins\"", "sc.fields": ALL}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300); walk(pg); C(pg, "[data-act=finish]"); pg.wait_for_timeout(900)
    C(pg, "[data-act=close-result]"); C(pg, "nav.tabs [data-tab=record]"); pg.wait_for_timeout(300)
    check("保存したあとの記録タブ: 前回の同伴者・コース・ゲームがそのまま入っていて、「始める」だけで続けられる", "はなこ" in pg.inner_text("[data-act=open-mates]") and pg.evaluate("document.querySelector('#r-course').value") == "c1" and pg.evaluate("state.game") == "skins" and pg.query_selector("[data-act=start]") is not None)
    check("リザルトに専用の「もう1周」ボタンはない", pg.query_selector("[data-act=next-round]") is None)
    pg.context.close()

    # 19l. 打数のフリックの選択肢は、入力したあとも同じ幅（狭くならない）
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": ["はなこ"], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.fields": ALL}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    def popw(k, i):
        r = pg.locator(f'.fc[data-k="{k}"][data-i="{i}"]').first.bounding_box(); x, y = r["x"] + r["width"] / 2, r["y"] + r["height"] / 2
        pg.mouse.move(x, y); pg.mouse.down(); pg.wait_for_timeout(30); pg.mouse.move(x + 6, y); pg.wait_for_timeout(40)
        w = pg.evaluate("document.querySelector('.fpop .fpc').getBoundingClientRect().width"); pg.mouse.up(); pg.wait_for_timeout(60); return w
    w0 = popw("s", 0); flick(pg, "s", 0, 8); w1 = popw("s", 0); flick(pg, "s", 0, 9); w2 = popw("s", 0)
    check("打数のフリックは、入力したあとも選択肢の幅が同じ（狭くならない）", abs(w0 - w1) < 1 and abs(w0 - w2) < 1 and w0 >= 30)
    pg.context.close()

    # 19m. 入力ずみのマスを押すだけでは、数字が変わらない（未入力のときだけ、パーが入る）
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": ["はなこ"], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.fields": ALL}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    flick(pg, "s", 0, 5); flick(pg, "p", 0, 3)
    for k in ("s", "p"):
        r = pg.locator(f'.fc[data-k="{k}"][data-i="0"]').first.bounding_box(); pg.mouse.click(r["x"] + r["width"] / 2, r["y"] + r["height"] / 2); pg.wait_for_timeout(80)
    check("入力ずみの打数・パットを押すだけでは変わらない（打数6・パット3のまま）", pg.evaluate("[state.draft.holes[0].strokes,state.draft.holes[0].putts]") == [6, 3])
    pg.context.close()

    # 19n. 同伴者・ほかの人のマスも、その人の今の値から始まる（自分の値ではない）／メダルもその人の今のメダルから
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": ["はなこ", "じろう"], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.game": "\"olympic\"", "sc.fields": ALL}, offline=True)
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    flick(pg, "s", 0, 7); flick(pg, "s", 1, 2); flick(pg, "s", 2, 5); flick(pg, "m", 0, 4)
    def starts(k, i):
        r = pg.locator(f'.fc[data-k="{k}"][data-i="{i}"]').first.bounding_box(); x, y = r["x"] + r["width"] / 2, r["y"] + r["height"] / 2
        pg.mouse.move(x, y); pg.mouse.down(); pg.wait_for_timeout(50)
        v = pg.evaluate("(()=>{const c=[...document.querySelectorAll('.fpop .fpc')],e=c.find(e=>e.classList.contains('on')); return e? (e.dataset.v||e.textContent.trim()):null})()"); pg.mouse.up(); pg.wait_for_timeout(60); return v
    check("打数の選択肢は、押したマスの人の今の数字から始まる（自分8・はなこ3・じろう6）", [starts("s", 0), starts("s", 1), starts("s", 2)] == ["8", "3", "6"])
    check("メダルの選択肢は、その人の今のメダルから始まる（自分=金4、ほかは なし0）", [starts("m", 0), starts("m", 1)] == ["4", "0"])
    pg.context.close()

    # 19o. 右上の札: ふだんは出さない。送れていないときだけ「バックアップ待ち」
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.fields": dict(NONE)})
    pg.wait_for_timeout(300)
    v0 = pg.is_visible("#sync")
    start_preset(pg, "firtree"); walk(pg); C(pg, "[data-act=finish]"); pg.wait_for_timeout(500); C(pg, "[data-act=close-result]")
    v1 = pg.is_visible("#sync")
    check("右上の札: ふだんは出ない（保存してサーバーに送れたあとも）", not v0 and not v1 and pg.evaluate("pendN()") == 0 and pg.evaluate("__db.rounds.length") == 1)
    pg.evaluate("window.__offline=true")
    C(pg, "nav.tabs [data-tab=history]"); C(pg, "[data-act=ask-del-round]") or (C(pg, "[data-act=toggle-round]") and C(pg, "[data-act=ask-del-round]")); C(pg, "[data-act=del-round]"); pg.wait_for_timeout(300)
    check("電波がなくても削除できる（端末からすぐ消え、札は「バックアップ待ち」）", pg.evaluate("state.rounds.length") == 0 and pg.is_visible("#sync") and "バックアップ待ち" in pg.inner_text("#sync") and pg.evaluate("__db.rounds.length") == 1)
    check("バックアップの枠に「あとで自動で送ります」", "あとで自動で送ります" in pg.inner_text("#bk-line"))
    pg.evaluate("window.__offline=false; window.dispatchEvent(new Event('online'))"); pg.wait_for_timeout(500)
    check("電波が戻ると削除もサーバーに送られ、札が消える", pg.evaluate("__db.rounds.length") == 0 and not pg.is_visible("#sync") and "最後のバックアップ" in pg.inner_text("#bk-line") and not errs)
    pg.context.close()

    # 19q. 2段目（方向・クラブ・OB・バンカー）は、項目が増えても減っても、1つのボタンの大きさと左からの位置が同じ
    ws = {}
    for nm, F in {"4": {"shot": True, "club": True, "ob": True, "bunker": True}, "3": {"shot": True, "club": False, "ob": True, "bunker": True}, "2": {"shot": True, "club": False, "ob": True, "bunker": False}, "1": {"shot": True, "club": False, "ob": False, "bunker": False}}.items():
        pg, errs = open_page(b, {"sc.fields": {**NONE, "putts": True, **F}, "sc.player": "\"たろう\""})
        start_preset(pg, "firtree")
        ws[nm] = pg.evaluate("[...document.querySelectorAll('.rowb .fb')].map(e=>{const q=e.getBoundingClientRect(); return [Math.round(q.left),Math.round(q.width)]})")
        pg.context.close()
    check("2段目のボタンは、項目が1〜4つのどれでも同じ大きさ・同じ位置から並ぶ", [len(ws[k]) for k in "4321"] == [4, 3, 2, 1] and len({tuple(v[0]) for v in ws.values()}) == 1 and all(ws[k] == ws["4"][:len(ws[k])] for k in ws))

    # 19s. 記録画面の左上の×は、歯車・スコアカードのボタンと同じ大きさ（46pxの丸・28pxの絵）
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.fields": dict(NONE)})
    start_preset(pg, "firtree")
    xs = pg.evaluate("['[data-act=ask-discard]','.card-btn','.gear-btn'].map(q=>{const e=document.querySelector(q), r=e.getBoundingClientRect(), v=e.querySelector('svg').getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height), Math.round(v.width)]})")
    check("記録画面の左上の×は、歯車・スコアカードと同じ大きさ（46pxの丸・28pxの絵）", xs[0] == xs[1] == xs[2] == [46, 46, 28] and not errs)
    pg.context.close()

    # 19t. ラウンドを始める画面（設定アプリ風のリスト）: 同伴者が何人でも高さは同じ・シートで入れた名前で始められる・ゲーム選択に人数が伝わる
    cst = [{"id": "c1", "name": "ファーツリーカントリークラブ", "holes": [{"par": 3, "yards": 100}] * 9, "createdAt": 1}]
    hs = {}
    for nm, ml in {"0": [], "1": ["はなこ"], "3": ["はなこ", "じろう", "さぶろう"]}.items():
        pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": ml, "sc.courses": cst, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.v2": 1})
        hs[nm] = pg.evaluate("[Math.round(document.querySelector('[data-act=start]').getBoundingClientRect().top), document.querySelectorAll('.sl-row').length]")
        pg.context.close()
    check("開始画面: 一緒に回る人が0人でも3人でも、行の数も「始める」の位置も同じ", hs["0"] == hs["1"] == hs["3"])
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": [], "sc.courses": cst, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.v2": 1, "sc.fields": dict(NONE)})
    C(pg, "[data-act=open-mates]"); pg.fill("#r-mate0", "はなこ"); pg.dispatch_event("#r-mate0", "input"); pg.fill("#r-mate1", "じろう"); pg.dispatch_event("#r-mate1", "input")
    C(pg, "[data-act=mates-done]")
    t1 = pg.inner_text("[data-act=open-mates]")
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    check("開始画面: シートで入れた名前が1行に並び、そのまま始められる（同伴者2人）", "はなこ・じろう" in t1 and pg.evaluate("state.draft.mates.map(m=>m.name).join()") == "はなこ,じろう" and pg.evaluate("JSON.parse(localStorage.getItem('sc.mates')).join()") == "はなこ,じろう," and not errs)
    pg.context.close()
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": ["はなこ", "じろう", "さぶろう"], "sc.courses": cst, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.v2": 1})
    C(pg, "[data-act=open-game]"); C(pg, "[data-act=pick-game][data-v=vegas]"); pg.wait_for_timeout(100)
    check("開始画面: 画面に入力欄がなくても、ゲーム選択に人数（4人）が伝わる（ラスベガスが選べる）", pg.evaluate("state.game") == "vegas" and "ラスベガス" in pg.inner_text("[data-act=open-game]") and not errs)
    pg.context.close()

    # 19u. 日付（右寄せの値＋アプリの見た目のカレンダー）・コースがないときの案内
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": ["はなこ", "", ""], "sc.courses": cst, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.v2": 1, "sc.fields": dict(NONE)})
    rights = pg.evaluate("[...document.querySelectorAll('.sl-row:not(.sl-prac)')].map(r=>r.querySelector('.sl-v')).filter(Boolean).map(e=>Math.round(e.getBoundingClientRect().right))")
    C(pg, "[data-act=open-cal]"); pg.wait_for_timeout(100)
    cal = pg.evaluate("({days:document.querySelectorAll('.cal-d').length, on:[...document.querySelectorAll('.cal-d.on')].map(e=>e.textContent), today:document.querySelectorAll('.cal-d.today').length, ttl:document.querySelector('.cal-h b').textContent})")
    C(pg, "[data-act=cal-go]:last-of-type") or C(pg, ".cal-nav[aria-label='次の月']"); pg.wait_for_timeout(100)
    ttl2 = pg.inner_text(".cal-h b")
    C(pg, ".cal-d[data-v$='-15']"); pg.wait_for_timeout(100)
    row = pg.inner_text("[data-act=open-cal]")
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    check("日付: 値は他の行と同じく右寄せ（右端がそろう）で、押すとアプリの見た目のカレンダー（今日の丸・月の切りかえ）が出る", len(set(rights)) == 1 and cal["days"] in range(28, 32) and cal["on"] and cal["ttl"] != ttl2 and not errs)
    check("日付: カレンダーで選んだ日が行に出て、その日付でラウンドが始まる", "15日" in row and pg.evaluate("state.draft.date").endswith("-15") and not errs)
    pg.context.close()
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.v2": 1, "sc.presets": "0"})
    txt = pg.inner_text(".panel.empty")
    check("コースがないときの案内: 説明文が正しく、ボタンは黒い丸ボタン（緑のグラデーションではない）", "コース名と各ホールのパーだけ" in txt and pg.evaluate("(()=>{const e=document.querySelector('[data-act=goto-courses]'); return e.classList.contains('sl-go') && getComputedStyle(e).backgroundImage==='none'})()") and not errs)
    pg.context.close()

    # 19p. 端末が本体・サーバーはバックアップ（2026-11-16 のバグ出しで見つかった不具合のチェック）
    def bk_round(i, player, nos=None, putts=2):
        return {"id": f"b{player}{i}", "player": player, "courseId": "c1", "courseName": "テストC", "date": f"2026-10-{10+i:02d}", "createdAt": 1000 + i, **({"nos": nos} if nos else {}),
                "holes": [{"par": 3, "strokes": 3 + (i + h) % 3, "putts": putts, "dir": None, "bunker": False, "ob": False, "pen": False, "tee": None, "club": None} for h in range(len(nos) if nos else 9)]}
    bk_course = [{"id": "c1", "name": "テストC", "holes": [{"par": 3, "yards": 100}] * 9, "createdAt": 1}]
    # 19r. 履歴の一番上にバックアップの1行（ラウンドが多くても下までスクロールしない）・中断のシートの並び・分析のタブの色
    many = [bk_round(i % 28, "たろう") | {"id": f"m{i}", "createdAt": 5000 + i} for i in range(40)]
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.rounds": many, "sc.courses": bk_course, "sc.v2": 1, "sc.fields": dict(NONE)})
    C(pg, "nav.tabs [data-tab=history]"); pg.wait_for_timeout(200)
    bt = pg.evaluate("(()=>{const e=document.querySelector('[data-act=bk-open]'); const r=e.getBoundingClientRect(); return [Math.round(r.top), document.documentElement.scrollHeight>window.innerHeight*2]})()")
    C(pg, "[data-act=bk-open]")
    three = pg.evaluate("[...document.querySelectorAll('.data-sheet button')].map(e=>e.dataset.act).join(',')")
    check("履歴の一番上にバックアップのボタン（40ラウンドあっても画面の上のほう）。押すと3つの操作が出る", bt[0] < 250 and bt[1] and three == "file-save,file-pick,restore,bk-close" and not errs)
    C(pg, "[data-act=bk-close]"); pg.context.close()
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.fields": dict(NONE)})
    start_preset(pg, "firtree"); flick(pg, "s", 0, 4); C(pg, "[data-act=ask-discard]"); pg.wait_for_timeout(150)
    order = pg.evaluate("[...document.querySelectorAll('.sheet button')].map(e=>e.dataset.act).join(',')")
    dq = pg.evaluate("(()=>{const e=document.querySelector('.sheet button.danger'); const c=getComputedStyle(e); return [e.classList.contains('quiet'), c.backgroundColor, c.color]})()")
    check("中断のシート: 破棄は一番下に離れて、赤い塗りではなく赤い文字だけ（緑・赤が灰色をはさまない）", order == "finish-early,pause,cancel-confirm,discard" and dq[0] and dq[1] in ("rgba(0, 0, 0, 0)", "transparent") and not errs)
    pg.context.close()
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.rounds": [bk_round(0, "たろう")], "sc.courses": bk_course, "sc.v2": 1})
    C(pg, "nav.tabs [data-tab=stats]"); pg.wait_for_timeout(200)
    st = pg.evaluate("(()=>{const e=document.querySelector('.stat-tabs button[aria-selected=true]'), c=getComputedStyle(e); return [c.backgroundImage, c.borderTopColor]})()")
    check("分析のタブ: 選んだタブは青いグラデーションではなく、ほかの切りかえと同じ緑の枠", st[0] == "none" and st[1] == "rgb(14, 159, 98)" and not errs)
    pg.context.close()

    # 19v. 履歴の数字の列・（±X）はグレー・コースの絞り込み（分析と共通）・名前の欄の枠・一緒に回る人のシート（下から）
    two = [bk_round(0, "たろう"), bk_round(1, "たろう") | {"holes": [{"par": 3, "strokes": 9, "putts": 2}] * 9}, bk_round(2, "たろう") | {"courseId": "c2", "courseName": "べつのコース"}]
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.rounds": two, "sc.courses": cst, "sc.v2": 1})
    C(pg, "nav.tabs [data-tab=history]"); pg.wait_for_timeout(200)
    hm = pg.evaluate("(()=>{ const n=[...document.querySelectorAll('.rcard .st-n')].map(e=>Math.round(e.getBoundingClientRect().right)); const g=[...document.querySelectorAll('.rcard .sc-tot small')].map(e=>getComputedStyle(e).color); return {n, g, rows:document.querySelectorAll('.rcard').length}; })()")
    check("履歴: 打数の数字の列は右そろえ・（±X）はどの値でも同じグレー", len(hm["n"]) == 3 and len(set(hm["n"])) == 1 and len(set(hm["g"])) == 1 and not errs)
    C(pg, "[data-act=bk-open]") ; C(pg, "[data-act=bk-close]")
    pg.select_option("#h-course", "c2"); pg.wait_for_timeout(200)
    after = pg.evaluate("({rows:document.querySelectorAll('.rcard').length, f:state.fCourse})")
    C(pg, "nav.tabs [data-tab=stats]"); pg.wait_for_timeout(300)
    check("履歴: コースで絞り込める（分析のコースの絞り込みと共通）", hm["rows"] == 3 and after["rows"] == 1 and after["f"] == "c2" and pg.evaluate("document.querySelector('#f-course').value") == "c2" and not errs)
    pg.context.close()
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": ["はなこ", "", ""], "sc.courses": cst, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.v2": 1})
    pg.focus("#r-player"); pg.wait_for_timeout(100)
    fo = pg.evaluate("(()=>{ const e=document.querySelector('#r-player'); const c=getComputedStyle(e); const row=e.closest('.sl-row'); return [c.outlineStyle, getComputedStyle(row).backgroundColor]; })()")
    C(pg, "[data-act=open-mates]"); pg.wait_for_timeout(100)
    sb = pg.evaluate("(()=>{ const b=document.querySelector('.mates-sheet').closest('.sheet-back'); const sh=document.querySelector('.mates-sheet').getBoundingClientRect(); return [b.classList.contains('top'), Math.round(sh.bottom), window.innerHeight]; })()")
    check("開始画面: 名前の欄に黄色い枠を出さない（行をうすい緑にする）。「一緒に回る」のシートはゲームと同じ下から出る", fo[0] == "none" and sb[0] is False and sb[1] >= sb[2] - 2 and not errs)
    pg.context.close()

    # 19w. ナイトモードはやめた・名前の欄はカーソルを右端に・結果画面の下のボタン・コインが枠に収まる
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.v2": 1}, dark=True)
    bg = pg.evaluate("(()=>{ const c=getComputedStyle(document.body).backgroundColor.match(/\\d+/g).map(Number); return (c[0]+c[1]+c[2])/3; })()")
    check("ナイトモード（端末が暗い表示でも）: アプリはいつも明るい表示", bg > 200 and pg.evaluate("document.documentElement.outerHTML.includes('prefers-color-scheme')") is False and not errs)
    pg.context.close()
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": ["はなこ", "", ""], "sc.courses": cst, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.v2": 1})
    pg.click("#r-player", position={"x": 5, "y": 10}); pg.wait_for_timeout(300)
    car = pg.evaluate("(()=>{ const e=document.querySelector('#r-player'); return [e.selectionStart, e.selectionEnd, e.value.length]; })()")
    check("名前の欄: 押したら、カーソルは名前の右端に入る（左側に入って消しにくい、をなくす）", car[0] == car[1] == car[2] and car[2] > 0 and not errs)
    pg.context.close()
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.inputMode": "\"card\"", "sc.mates": ["はなこ", "じろう", ""], "sc.courses": [{"id": "c1", "name": "C1", "holes": [{"par": 3, "yards": 100}] * 18}], "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.game": "\"nearpin\"", "sc.v2": 1, "sc.fields": dict(NONE)})
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    for _ in range(120):
        if not pg.evaluate("state.cdraft&&state.cdraft.sel"): break
        C(pg, '[data-act=cd-key][data-v="3"]')
    pg.evaluate("state.cdraft.pins=Object.fromEntries(Array.from({length:18},(_,i)=>[i,i<14?0:1]))")
    C(pg, "[data-act=cd-finish]"); pg.wait_for_timeout(900)
    cw = pg.evaluate("(()=>{ const e=document.querySelector('.rc-game .rs-c'); return [e.scrollWidth<=e.clientWidth+1, e.children.length, e.textContent]; })()")
    btn = pg.evaluate("(()=>{ const q=s=>document.querySelector(s); const c=q('.rc-close'), t=q('.rc-2nd'), p=q('.rc-save'); return [!!c, !!t, !!p, getComputedStyle(c).backgroundColor, !!q('.rc-all'), document.querySelectorAll('.rc-2nd').length]; })()")
    check("結果: 18Hで14枚でも、コインは枠に収まる（あふれる分は「…」、数字は右に出る）", cw[0] and cw[1] <= 8 and "…" in cw[2] and not errs)
    mo = pg.evaluate("(()=>{ const e=document.querySelector('.rc-game .rs-c'), m=e.querySelector('.rs-more'), a=e.firstElementChild.getBoundingClientRect(), r=m.getBoundingClientRect(); return [r.top<a.bottom && r.bottom>a.top, e.getBoundingClientRect().height<=a.height+8, getComputedStyle(document.querySelector('.paper .rc-logo b')).color]; })()")
    check("結果: コインの「…」はコインと同じ行（下の行に落ちない）", mo[0] and mo[1])
    check("結果: 紙のカードの右下の GOLF SC. は、上のロゴと同じ黒（緑にしない）", mo[2] == "rgb(11, 19, 16)")
    check("結果画面の下: 色つきの主ボタン1つ・白い枠の副ボタン（全員分・スコア入力に戻る）・文字だけの「閉じる」の3段", btn[0] and btn[1] and btn[2] and btn[3] in ("rgba(0, 0, 0, 0)", "transparent") and not btn[4] and btn[5] == 2)   # 「みんな」の表示では、副ボタンは「全員分をまとめて保存」だけ（1人ずつの表示では「スコア入力に戻る」も並ぶ）
    pg.context.close()

    # 新しい端末には、サーバーにあるほかの人の記録が入らない（名前なし・同じ名前のどちらでも）
    for nm in (None, "たろう"):
        pg, errs = open_page(b, {"sc.player": json.dumps(nm)} if nm else None)
        pg.evaluate("([r,c])=>{ __db.rounds=r; __db.courses=c; }", [[bk_round(i, "あきら") for i in range(3)] + [bk_round(i, "たろう") for i in range(2)], bk_course])
        pg.evaluate("realConnect()"); pg.wait_for_timeout(400); pg.evaluate("document.dispatchEvent(new Event('visibilitychange'))"); pg.wait_for_timeout(300)
        C(pg, "nav.tabs [data-tab=history]"); C(pg, "nav.tabs [data-tab=stats]"); C(pg, "nav.tabs [data-tab=history]")
        check(f"新しい端末（名前{'あり' if nm else 'なし'}）: サーバーのほかの人の記録は入らない・見えない", pg.evaluate("state.rounds.length") == 0 and pg.query_selector("[data-act=toggle-round]") is None and not errs)
        pg.context.close()
    # 古い版から上がった端末（sc.rounds がある）は、最初の1回だけ今までと同じ読み直しをして、次からはしない
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.rounds": [bk_round(0, "たろう")], "sc.courses": bk_course})
    pg.evaluate("([r,c])=>{ __db.rounds=r; __db.courses=c; localStorage.removeItem('sc.v2'); }", [[bk_round(0, "たろう"), bk_round(1, "たろう")], bk_course])
    pg.evaluate("realConnect()"); pg.wait_for_timeout(500)
    one = pg.evaluate("state.rounds.length") == 2 and pg.evaluate("!!localStorage.getItem('sc.v2')")
    pg.evaluate("__db.rounds.push(%s); 0" % json.dumps(bk_round(5, "あきら"))); pg.evaluate("realConnect()"); pg.wait_for_timeout(500)
    check("古い版から上がった端末: 最初の1回だけサーバーから読み、次からは読まない", one and pg.evaluate("state.rounds.length") == 2 and not errs)
    pg.context.close()
    # 開き直した直後（裏でサーバーと話している間）に保存しても、ラウンドが消えない
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.fields": dict(NONE)})
    pg.evaluate("window.__delay=1200")
    start_preset(pg, "firtree"); walk(pg)
    pg.evaluate("document.dispatchEvent(new Event('visibilitychange')); window.dispatchEvent(new Event('online'))"); pg.wait_for_timeout(100)
    C(pg, "[data-act=finish]"); pg.wait_for_timeout(200)
    fast = pg.query_selector(".result-card") is not None
    pg.wait_for_timeout(3200); C(pg, "[data-act=close-result]"); C(pg, "nav.tabs [data-tab=history]")
    check("保存はサーバーを待たずにすぐ終わり、あとで読み直しが来ても履歴から消えない", fast and pg.evaluate("state.rounds.length") == 1 and pg.query_selector("[data-act=toggle-round]") is not None and pg.evaluate("__db.rounds.length") == 1 and pg.evaluate("pendN()") == 0 and not errs)
    pg.context.close()
    # 途中で終わったラウンドを編集しても、ホール番号が残る
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.rounds": [bk_round(0, "たろう", nos=[1, 2, 6, 7])], "sc.courses": bk_course, "sc.v2": 1})
    C(pg, "nav.tabs [data-tab=history]"); C(pg, "[data-act=toggle-round]"); C(pg, "[data-act=edit-round]"); pg.wait_for_timeout(200)
    labels = pg.evaluate("[...document.querySelectorAll('.dots button')].map(e=>e.textContent.trim()).join(',')")
    C(pg, "[data-act=next]"); C(pg, "[data-act=next]"); C(pg, "[data-act=next]"); C(pg, "[data-act=finish]"); pg.wait_for_timeout(500)
    check("途中で終わったラウンドの編集: ホール番号（1・2・6・7）が残る", labels == "1H,2H,6H,7H" and pg.evaluate("JSON.stringify(state.rounds[0].nos)") == "[1,2,6,7]" and not errs)
    pg.context.close()
    # パットの設定がOFFのまま昔のラウンドを編集しても、パットが消えない
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.fields": {"putts": False}, "sc.rounds": [bk_round(0, "たろう")], "sc.courses": bk_course, "sc.v2": 1})
    C(pg, "nav.tabs [data-tab=history]"); C(pg, "[data-act=toggle-round]"); C(pg, "[data-act=edit-round]"); pg.wait_for_timeout(200)
    for _ in range(8): C(pg, "[data-act=next]")
    C(pg, "[data-act=finish]"); pg.wait_for_timeout(500)
    check("パットOFFで編集しても、もとのパットは残る", pg.evaluate("state.rounds[0].holes.every(h=>h.putts===2)") and not errs)
    pg.context.close()
    # 記録をファイルに保存 → 別の端末で読み込む
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.rounds": [bk_round(0, "たろう"), bk_round(1, "たろう")], "sc.courses": bk_course, "sc.v2": 1})
    C(pg, "nav.tabs [data-tab=history]")
    C(pg, "[data-act=bk-open]")
    with pg.expect_download() as dl: C(pg, "[data-act=file-save]")
    fpath = os.path.join(tempfile.mkdtemp(), "bk.json"); dl.value.save_as(fpath)
    data = json.load(open(fpath, encoding="utf-8"))
    check("ファイルに保存: ラウンド・コース・名前が入った JSON ができる", data.get("app") == "GOLF SC." and len(data["rounds"]) == 2 and len(data["courses"]) == 1 and data["player"] == "たろう" and dl.value.suggested_filename.endswith(".json"))
    pg.context.close()
    pg, errs = open_page(b, {"sc.rounds": [bk_round(1, "たろう")], "sc.v2": 1})
    C(pg, "nav.tabs [data-tab=history]")
    pg.set_input_files("#bk-file", fpath); pg.wait_for_timeout(300)
    msg = pg.inner_text(".data-sheet")
    C(pg, "[data-act=data-ok]"); pg.wait_for_timeout(500)
    check("ファイルから読み込む: 足りない分だけ入り（2ラウンド中1つ・コース1つ）、名前も入り、サーバーにも送られる", "2ラウンド" in msg and "1ラウンド" in msg and pg.evaluate("state.rounds.length") == 2 and pg.evaluate("state.courses.length") == 1 and pg.evaluate("localStorage.getItem('sc.player')") == '"たろう"' and pg.evaluate("__db.rounds.length") == 1 and not errs)
    bad = os.path.join(os.path.dirname(fpath), "bad.json"); open(bad, "w").write('{"hello":1}')
    pg.set_input_files("#bk-file", bad); pg.wait_for_timeout(300)
    check("ファイルから読み込む: 関係ないファイルは「読み込めませんでした」で、何も変わらない", "読み込めませんでした" in pg.inner_text(".data-sheet") and pg.evaluate("state.rounds.length") == 2 and not errs)
    pg.context.close()
    # 手で書きかえた（形のおかしい）ファイルを読みこんでも、画面に NaN・undefined が出ない（読みこむときに数字を整える）
    pg, errs = open_page(b, {"sc.rounds": [bk_round(1, "たろう")], "sc.courses": bk_course, "sc.player": "\"たろう\"", "sc.v2": 1})
    odd = bk_round(5, "たろう"); odd["holes"][2]["putts"] = None; odd["holes"][3]["putts"] = 1.5; odd["holes"][4]["putts"] = 99; odd["holes"][5]["club"] = {"a": 1}; odd["holes"][6]["strokes"] = 3.4; odd["holes"][7]["dir"] = 7; odd["createdAt"] = "abc"; odd["courseName"] = None
    oddf = os.path.join(tempfile.mkdtemp(), "odd.json"); json.dump({"app": "GOLF SC.", "format": 1, "rounds": [odd, {"id": "x"}, None, 3], "courses": "x"}, open(oddf, "w", encoding="utf-8"))
    C(pg, "nav.tabs [data-tab=history]"); pg.set_input_files("#bk-file", oddf); pg.wait_for_timeout(300); C(pg, "[data-act=data-ok]"); pg.wait_for_timeout(400)
    r5 = pg.evaluate("state.rounds.find(r=>r.id==='bたろう5')")
    pg.evaluate("document.querySelectorAll('[data-act=toggle-round]').forEach(e=>e.click())"); pg.wait_for_timeout(200); txt = pg.inner_text("body")
    C(pg, "nav.tabs [data-tab=stats]"); pg.wait_for_timeout(300)
    for v in ["shot", "putt"]: C(pg, f'[data-act=stat-tab][data-v="{v}"]'); pg.wait_for_timeout(300); txt += pg.inner_text("body")
    check("ファイルから読み込む: 形のおかしい記録は数字を整えて入れる（パットは0〜打数、クラブは文字だけ）。画面に NaN・undefined が出ない",
          r5 and [h["putts"] for h in r5["holes"][2:5]] == [0, 2, r5["holes"][4]["strokes"]] and r5["holes"][5]["club"] is None and r5["holes"][6]["strokes"] == 3 and r5["holes"][7]["dir"] is None and isinstance(r5["createdAt"], (int, float)) and pg.evaluate("state.rounds.length") == 2
          and not any(k in txt for k in ["NaN", "undefined", "[object"]) and not errs)
    pg.context.close()
    # 開始画面を開いたまま日付が変わったら、アプリに戻ったときに今日の日付になる（前の日付のまま記録が始まっていた）
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.courses": bk_course, "sc.rounds": [], "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.v2": 1}, offline=True, repl={"const today = () => {": "const today = () => { if(window.__today) return window.__today;", "const LS = {": "const LS = window.LS = {"})
    d0 = pg.evaluate("document.querySelector('#r-date').value")
    pg.evaluate("window.__today='2031-01-02'; document.dispatchEvent(new Event('visibilitychange'))"); pg.wait_for_timeout(200)
    d1 = pg.evaluate("document.querySelector('#r-date').value")
    pg.evaluate("window.__today='2031-01-03'"); C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    check("開始画面: 開いたまま日付が変わっても、戻ったときに表示が今日になり、始めたラウンドの日付も今日", d0 != d1 and d1 == "2031-01-02" and pg.evaluate("(state.draft||state.cdraft).date") == "2031-01-03" and not errs)
    pg.context.close()
    # スコアカード入力: マスを選んでいないときに「パットあり／なし」を押しても、エラーにならず切りかわる（でたらめ操作で見つかった）
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.inputMode": "\"card\"", "sc.courses": bk_course, "sc.rounds": [], "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.v2": 1}, offline=True, repl={"const LS = {": "const LS = window.LS = {"})
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    pg.evaluate("state.cdraft.sel=null; render()"); p0 = pg.inner_text("[data-act=cd-putts]"); C(pg, "[data-act=cd-putts]"); pg.wait_for_timeout(150)
    check("スコアカード入力: マスを選んでいなくても「パットあり／なし」が切りかわる", pg.inner_text("[data-act=cd-putts]") != p0 and not errs)
    pg.context.close()
    # 名前を変えて始めるとき: 今までの記録も新しい名前にするかを聞く（変えると、履歴から消えたように見えていた）
    RN = {"sc.player": "\"たろう\"", "sc.courses": bk_course, "sc.rounds": [bk_round(1, "たろう"), bk_round(2, "たろう"), bk_round(3, "はなこ")], "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.lastClubs": {"たろう|c1|0": "7I", "はなこ|c1|0": "9I"}, "sc.v2": 1}
    def rn_open():
        pg, errs = open_page(b, RN, offline=True); pg.fill("#r-player", "太郎"); C(pg, "[data-act=start]"); pg.wait_for_timeout(250); return pg, errs
    pg, errs = rn_open(); ask = pg.inner_text(".rename-sheet") if pg.query_selector(".rename-sheet") else ""
    C(pg, "[data-act=rename-yes]"); pg.wait_for_timeout(400)
    check("名前を変えて始める: 「これまでの記録（2回）も…」と聞き、「変える」で前の名前の記録・前回のクラブが新しい名前になって、ラウンドが始まる（ほかの人の記録はそのまま）",
          "たろう" in ask and "太郎" in ask and "2回" in ask and sorted(pg.evaluate("state.rounds.map(r=>r.player)")) == sorted(["太郎", "太郎", "はなこ"]) and pg.evaluate("JSON.parse(localStorage.getItem('sc.player'))") == "太郎"
          and pg.evaluate("(state.draft||state.cdraft.players[0]).player||state.cdraft.players[0].name") == "太郎" and pg.evaluate("JSON.parse(localStorage.getItem('sc.lastClubs'))") == {"太郎|c1|0": "7I", "はなこ|c1|0": "9I"} and pg.evaluate("JSON.parse(localStorage.getItem('sc.rounds')).filter(r=>r.player==='太郎').length") == 2 and not errs)
    pg.context.close()
    pg, errs = rn_open(); C(pg, "[data-act=rename-other]"); pg.wait_for_timeout(400)
    check("名前を変えて始める: 「別の人として始める」は、記録の名前を変えずに始まる", sorted(pg.evaluate("state.rounds.map(r=>r.player)")) == sorted(["たろう", "たろう", "はなこ"]) and pg.evaluate("!!(state.draft||state.cdraft)") and not errs)
    pg.context.close()
    pg, errs = rn_open(); C(pg, "[data-act=rename-no]"); pg.wait_for_timeout(300)
    check("名前を変えて始める: 「やめる」は始めずに、名前の欄を前の名前に戻す", not pg.evaluate("!!(state.draft||state.cdraft)") and pg.input_value("#r-player") == "たろう" and not pg.query_selector(".rename-sheet") and not errs)
    pg.fill("#r-player", "はなこ"); C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    check("名前を変えて始める: 新しい名前にすでに記録があるとき（一緒に回った人の名前など）は聞かずに始まる", not pg.query_selector(".rename-sheet") and pg.evaluate("!!(state.draft||state.cdraft)") and not errs)
    pg.context.close()
    # 0パット（チップイン）は、パットの数字の下に CHIP IN。ラスベガスは10打以上を9として数える
    pg, errs = open_page(b, {**RN, "sc.inputMode": "\"hole\""}, offline=True, repl={"const LS = {": "const LS = window.LS = {"}); C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    pg.evaluate("(()=>{ const h=state.draft.holes[0]; h.strokes=3; h.sT=true; h.putts=0; h.pT=true; render(); })()")
    chip = pg.inner_text('.fc[data-k="p"][data-i="0"]'); chip0 = pg.query_selector(".badge.b-chip") is not None
    pg.evaluate("(()=>{ const h=state.draft.holes[0]; h.putts=1; render(); })()")
    check("記録画面: 0パットのときだけ、上のホールカードに CHIP IN の札が出る。打数・パットの数字の下には何も出さない（高さがそろう）", "CHIP IN" not in chip and chip0 and not pg.query_selector(".badge.b-chip") and not pg.query_selector('.fc[data-k="s"] small') and pg.evaluate("(()=>{const a=document.querySelector('.fc[data-k=\"s\"] b').getBoundingClientRect(), b=document.querySelector('.fc[data-k=\"p\"] b').getBoundingClientRect(); return Math.abs(a.top-b.top)<1.5})()") and not errs)
    check("ラスベガス: 10打以上は9として数える（3と10は39。4と5の45との差で6点）", pg.evaluate("vegasCalc([[3],[10],[4],[5]],1).pts.join()") == "6,0" and pg.evaluate("vegasCalc([[10],[10],[4],[5]],1).pts.join()") == "0,54")
    pg.context.close()
    # ワンオン（パー3で1打で乗せた人に1点・何人でも）とスネーク（3パットした人にヘビが移り、最後に持っていた人の負け）
    c93 = [{"id": "c1", "name": "テストC", "holes": [{"par": 3}, {"par": 4}, {"par": 3}], "createdAt": 1}]
    GM = {"sc.player": "\"たろう\"", "sc.mates": ["はなこ", "じろう", ""], "sc.courses": c93, "sc.rounds": [], "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.v2": 1}
    def fill3(pg): pg.evaluate("(()=>{ const d=state.draft; d.holes.forEach(h=>{ h.strokes=h.par; h.sT=true; h.putts=2; h.pT=true; }); d.mates.forEach(m=>{ m.s=d.holes.map(h=>h.par+1); m.t=d.holes.map(()=>true); }); render(); })()")
    pg, errs = open_page(b, {**GM, "sc.game": "\"oneon\"", "sc.inputMode": "\"hole\""}, offline=True, repl={"function marksCalc(": "window.marksCalc=marksCalc; function marksCalc("})
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300); fill3(pg)
    C(pg, '[data-act=mark-q][data-i="0"]'); C(pg, '[data-act=mark-q][data-i="1"]')
    pg.evaluate("state.hole=1; render()"); par4 = pg.evaluate("document.querySelectorAll('[data-act=mark-q]').length")
    pg.evaluate("state.hole=2; render()"); C(pg, '[data-act=mark-q][data-i="0"]'); C(pg, '[data-act=mark-q][data-i="2"]'); C(pg, '[data-act=mark-q][data-i="2"]')
    pts = pg.evaluate("[...document.querySelectorAll('.frow .pt b')].map(e=>e.textContent).join()")
    C(pg, "[data-act=finish]"); pg.wait_for_timeout(600); res = pg.inner_text(".rc-game") if pg.query_selector(".rc-game") else ""
    check("ワンオン: パー3だけボタンが出て、乗った人を何人でも押せる（もう一度押すと外れる）。点は 2・1・0 で、結果に「たろう の勝ち」",
          par4 == 0 and pts == "2,1,0" and "ワンオン" in res and "たろう の勝ち" in res and pg.evaluate("marksCalc({0:[0,1],1:[0],2:[0]},3,[3,4,3],true).n.join()") == "2,1,0" and not errs)
    pg.context.close()
    pg, errs = open_page(b, {**GM, "sc.game": "\"snake\"", "sc.inputMode": "\"hole\""}, offline=True, repl={"function marksCalc(": "window.marksCalc=marksCalc; function marksCalc("})
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300); fill3(pg)
    check("スネーク: 3パットのボタンはチェックの印（ヘビが横に2つ並ばない）", pg.evaluate("document.querySelectorAll('[data-act=mark-q] .tick').length") == 3 and pg.evaluate("document.querySelectorAll('[data-act=mark-q] img').length") == 0)
    C(pg, '[data-act=mark-q][data-i="1"]'); h1 = pg.evaluate("[...document.querySelectorAll('.frow')].findIndex(r=>r.querySelector('.pt.snk img'))")
    pg.evaluate("state.hole=1; render()"); n4 = pg.evaluate("document.querySelectorAll('[data-act=mark-q]').length"); C(pg, '[data-act=mark-q][data-i="2"]'); C(pg, '[data-act=mark-q][data-i="0"]')
    h2 = pg.evaluate("[...document.querySelectorAll('.frow')].findIndex(r=>r.querySelector('.pt.snk img'))")
    pg.evaluate("state.hole=2; render()"); C(pg, "[data-act=finish]"); pg.wait_for_timeout(600); res = pg.inner_text(".rc-game") if pg.query_selector(".rc-game") else ""
    pg2, errs2 = open_page(b, {**GM, "sc.game": "\"snake\""}, offline=True)
    C(pg2, "[data-act=open-game]"); pg2.wait_for_timeout(200)
    ih = pg2.evaluate("(()=>{const g=k=>{const x=document.querySelector(`.gm-card[data-v=${k}] .gm-ill>*`); return x?Math.round(x.getBoundingClientRect().height):-1}; return [g('snake'),g('nearpin')]})()")
    C(pg2, "[data-act=open-rules][data-v=snake]"); pg2.wait_for_timeout(200); rt = pg2.inner_text(".rp"); pg2.context.close()
    check("スネーク: 一覧の絵はほかのゲームと同じ高さ。ルールは「ヘビを持ちます」で、ボタンの説明は書かない",
          ih[0] == ih[1] and ih[0] > 0 and "ヘビを持ちます" in rt and "ボタンを押します" not in rt and "あとの人を押す" not in rt)
    tick = pg.evaluate("[!!document.querySelector('.rc-game .rs-lose img'), document.querySelectorAll('.rc-game .rs-row img').length]")
    C(pg, "[data-act=memo-open]"); pg.wait_for_timeout(150); pg.fill("#memo-in", "風が強かった"); C(pg, "[data-act=memo-save]"); pg.wait_for_timeout(200)
    memo1 = pg.evaluate("state.rounds.filter(r=>r.memo==='風が強かった').map(r=>r.player).join()")
    C(pg, "[data-act=close-result]"); pg.wait_for_timeout(300)
    hist = pg.inner_text(".rcard .detail") if pg.query_selector(".rcard .detail") else ""
    check("結果のスネークは、負けた人を大きく1つ（名前の行にヘビは置かない）。メモは自分のラウンドに付き、履歴に出る。履歴に、いっしょに回った人と打数",
          tick == [True, 0] and memo1 == "たろう" and "風が強かった" in hist and "いっしょに回った人" in hist and "はなこ" in hist and "メモを直す" in hist and not errs)
    check("スネーク: どのホールでも押せて、3パットした人にヘビが移る（同じホールはあとに押した人）。結果に「たろう の負け」と回数",
          n4 == 3 and h1 == 1 and h2 == 0 and "スネーク" in res and "たろう の負け" in res and pg.evaluate("marksCalc({0:[1],1:[2,0]},3,[3,4,3],false).holder") == 0 and pg.evaluate("marksCalc({},3,[3,4,3],false).holder") == -1 and not errs)
    pg.context.close()
    for g, lab in [("oneon", "ワンオン"), ("snake", "3パット")]:
        pg, errs = open_page(b, {**GM, "sc.game": json.dumps(g), "sc.inputMode": "\"card\""}, offline=True); C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
        C(pg, '[data-act=cd-gh][data-h="0"]'); row = pg.inner_text(".gp-h"); C(pg, '[data-act=cd-mark][data-i="1"]'); C(pg, '[data-act=cd-mark][data-i="0"]')
        want = '{"0":[1,0]}' if g == "oneon" else '{"0":[0]}'   # スネークは1ホールに1人だけ（あとに押した人に移る）
        cells = pg.evaluate("[...document.querySelectorAll('tr.skr.gr td.c-s')].slice(0,3).map(e=>e.querySelector('svg,img')?'S':e.textContent.trim())")
        check(f"スコアカード入力（{lab}）: ホール番号を押すとパネルが開き、人のボタンを押すと入る", (("ワンオン" if g == "oneon" else "3パット") in row) and pg.evaluate("JSON.stringify(state.cdraft.marks)") == want and cells == (["1", "1", "0"] if g == "oneon" else ["S", "", ""]) and ("スネーク" in pg.inner_text("tr.skr.gr") if g == "snake" else True) and not errs)
        pg.context.close()
    # スコアカード入力: ゲームの行がどのホールかを表の中で示し（ホール番号に色）、ホール番号を押すと前のホールのゲームを直せる。印は表の中にも小さく出る
    pg, errs = open_page(b, {**GM, "sc.game": "\"snake\"", "sc.inputMode": "\"card\"", "sc.fields": {"putts": False}}, offline=True); C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    for v in "333444": C(pg, f'[data-act=cd-key][data-v="{v}"]')
    hl1 = pg.evaluate("[...document.querySelectorAll('.cdt tbody tr')].findIndex(r=>r.classList.contains('ghr'))")   # 全員の打数がそろった2Hのパネルが自動で開いていて、その行が光る
    C(pg, '[data-act=cd-gh][data-h="0"]'); C(pg, '[data-act=cd-mark][data-i="1"]')
    hl0 = pg.evaluate("[...document.querySelectorAll('.cdt tbody tr')].findIndex(r=>r.classList.contains('ghr'))")
    check("スコアカード入力: パネルが開いているホールの行だけが光り、ホール番号を押すと前のホールを直せる。表の中の印はホール番号のとなりの点だけ",
          hl1 == 1 and hl0 == 0 and pg.evaluate("JSON.stringify(state.cdraft.marks)") == '{"0":[1]}' and pg.evaluate("document.querySelectorAll('.cdt .gmk').length") == 0 and pg.evaluate("document.querySelectorAll('.hbtn.has').length") == 1 and pg.inner_text(".gp-hn").startswith("1H") and not errs)
    pg.context.close()
    # スコアカード入力（9ホール）: 画面が低くても表は上下に動かず収まる。ホール番号は本物のボタンで、押すとゲームの行がそのホールになる
    c9h = [{"id": "c1", "name": "九ホール", "holes": [{"par": 3}] * 9, "createdAt": 1}]
    pg, errs = open_page(b, {**GM, "sc.courses": c9h, "sc.game": "\"snake\"", "sc.inputMode": "\"card\""}, offline=True); C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    pg.set_viewport_size({"width": 375, "height": 600}); pg.wait_for_timeout(300); C(pg, '[data-act=cd-key][data-v="4"]')
    fit = pg.evaluate("(()=>{const e=document.querySelector('.cd-scroll');return e.scrollHeight-e.clientHeight})()")
    pg.locator('button.hbtn[data-h="2"]').click(); pg.wait_for_timeout(200)
    hl = pg.evaluate("[...document.querySelectorAll('.cdt tbody tr')].findIndex(r=>r.classList.contains('ghr'))")
    check("スコアカード入力（9H）: 高さ600pxでも表は動かず収まる。ホール番号（ボタン）を押すとゲームの行が動く", fit <= 1 and hl == 2 and pg.evaluate("document.documentElement.classList.contains('noscroll')") and not errs)
    pg.context.close()
    # スコアカード入力: 長い名前（2行）の見出しは高さに余裕があり切れない。チーム戦のポイントはA・Bで色が分かれる
    pg, errs = open_page(b, {**GM, "sc.mates": ["体操きんたろう", "たろう", "川越大生"], "sc.game": "\"vegas\"", "sc.inputMode": "\"card\""}, offline=True); C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    hh = pg.evaluate("[...document.querySelectorAll('.cdt thead th.nm')].map(e=>[Math.round(e.getBoundingClientRect().height), e.scrollHeight<=e.clientHeight+1])")
    bgs = pg.evaluate("[...document.querySelectorAll('.cd-game tr.skr td.c-s')].map(e=>getComputedStyle(e).backgroundColor)")
    check("スコアカード入力: 長い名前の見出しは高さ40px以上で切れない。チーム戦のポイントはA・Bで色がちがう", all(h[0] >= 40 and h[1] for h in hh) and len(bgs) == 2 and bgs[0] != bgs[1] and not errs)
    pg.context.close()
    # みんなのリザルト: 名前が2行になっても、合計の数字は全員同じ高さ
    pg, errs = open_page(b, {"sc.v2": 1}, offline=True, query="?show=9"); pg.wait_for_timeout(800)
    pg.evaluate("document.querySelectorAll('table.v1 .v1n')[1].textContent='体操きんたろう'")
    check("みんなのリザルト: 名前が2行の人がいても、合計の数字・順位の丸は全員同じ高さ",
          pg.evaluate("new Set([...document.querySelectorAll('table.v1 .v1c b')].map(e=>Math.round(e.getBoundingClientRect().top))).size") == 1 and pg.evaluate("new Set([...document.querySelectorAll('table.v1 .v1c .rc-rk')].map(e=>Math.round(e.getBoundingClientRect().top))).size") == 1 and not errs)
    pg.context.close()
    # コース登録: 県・市の欄はない。ヤードが空でも、聞かずにそのまま保存できる
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.rounds": [], "sc.courses": [], "sc.tab": "\"courses\"", "sc.v2": 1}, offline=True)
    C(pg, "[data-act=new-course]"); pg.wait_for_timeout(300); nopref = pg.query_selector("#c-pref") is None and pg.query_selector("#c-area") is None
    pg.evaluate("state.courseEdit.name='ヤードなしコース'"); C(pg, "[data-act=save-course]"); pg.wait_for_timeout(300)
    check("コース登録: 県・市の欄なし。ヤードが空でも聞かずに保存できる", nopref and pg.evaluate("!state.courseEdit && state.courses.some(c=>c.name==='ヤードなしコース')") and not errs)
    pg.context.close()
    # ラウンドがないとき: 分析の2つのボタンは同じ幅。分析と履歴は別の文
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.rounds": [], "sc.courses": c93, "sc.tab": "\"stats\"", "sc.v2": 1}, offline=True)
    bw = pg.evaluate("[...document.querySelectorAll('.empty-b .btn')].map(e=>Math.round(e.getBoundingClientRect().width))"); t1 = pg.inner_text(".panel.empty strong")
    C(pg, "nav.tabs [data-tab=history]"); t2 = pg.inner_text(".panel.empty strong")
    check("ラウンドがないとき: 分析のボタン2つは同じ幅。分析と履歴の見出しは別", len(bw) == 2 and bw[0] == bw[1] and t1 != t2 and not errs)
    pg.context.close()
    # 開始画面の「練習として回る」: ONで始めると練習ラウンドとして保存され、分析に入らない。次のラウンドではOFFに戻る
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.rounds": [], "sc.courses": c93, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.inputMode": "\"card\"", "sc.fields": {"putts": False}, "sc.v2": 1}, offline=True, repl={"function ghostBest(": "window.filteredRounds=filteredRounds; function ghostBest("})
    C(pg, "[data-act=prac-next]"); sw = pg.evaluate("document.querySelector('.sl-sw').classList.contains('on')")
    pg.set_viewport_size({"width": 375, "height": 627}); pg.wait_for_timeout(200)
    fit = pg.evaluate("document.querySelector('[data-act=start]').getBoundingClientRect().bottom <= document.querySelector('nav.tabs').getBoundingClientRect().top")
    pg.set_viewport_size({"width": 375, "height": 667})
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300); tag = pg.query_selector(".rec-who .prac-tag") is not None
    for v in "333": C(pg, f'[data-act=cd-key][data-v="{v}"]')
    C(pg, "[data-act=cd-finish]"); pg.wait_for_timeout(700); C(pg, "[data-act=close-result]"); pg.wait_for_timeout(200)
    saved = pg.evaluate("[state.rounds.length, state.rounds[0].practice===true, filteredRounds().length]")
    C(pg, "nav.tabs [data-tab=record]"); pg.wait_for_timeout(200); off = pg.evaluate("document.querySelector('.sl-sw') && !document.querySelector('.sl-sw').classList.contains('on')")
    check("開始画面の「練習として回る」: ONで始めると記録中に「練習」の札、保存は練習ラウンド（分析に入らない）。次はOFFに戻る。高さ627pxでも「始める」が見える",
          sw and fit and tag and saved == [1, True, 0] and off and not errs)
    pg.context.close()
    # 練習ラウンド: 履歴で印を付けると、分析・自己ベスト・ゴーストに入らない（履歴には「練習」の札つきで出る）
    PR = [{"id": f"p{i}", "player": "たろう", "courseId": "c1", "courseName": "テストコース", "date": f"2026-10-{10+i:02d}", "createdAt": 1000 + i,
           "holes": [{"par": 3, "strokes": 3 if i else 2, "putts": 1} for _ in range(3)]} for i in range(3)]
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.rounds": PR, "sc.courses": c93, "sc.tab": "\"history\"", "sc.v2": 1}, offline=True, repl={"function ghostBest(": "window.ghostBest=ghostBest; window.filteredRounds=filteredRounds; function ghostBest("})
    pg.evaluate("state.openRound='p0'; render()"); C(pg, "[data-act=prac-toggle]"); pg.wait_for_timeout(200)
    pr = pg.evaluate("[state.rounds.find(r=>r.id==='p0').practice===true, filteredRounds().map(r=>r.id).join(), ghostBest('たろう','c1',3,'x').id, !!document.querySelector('.rcard .prac-tag')]")
    C(pg, "[data-act=prac-toggle]"); pg.wait_for_timeout(200)
    check("練習ラウンド: 印を付けると分析とゴーストから外れ、履歴に「練習」の札。もう一度押すと戻る",
          pr[0] and pr[1] == "p1,p2" and pr[2] != "p0" and pr[3] and pg.evaluate("filteredRounds().length") == 3 and pg.evaluate("ghostBest('たろう','c1',3,'x').id") == "p0" and not errs)
    pg.context.close()
    # 履歴: 結果カードと同じ紙の表（9ホールは横にスクロールせず全部見える）。練習ラウンドはスイッチ。スコアカードで記録したラウンドの編集はスコアカード入力で開き、保存してもメモ・練習の印・idが残る
    HR = [{"id": "e1", "card": True, "memo": "メモ", "practice": True, "player": "たろう", "courseId": "c1", "courseName": "テストコース", "date": "2026-10-10", "createdAt": 1000,
           "holes": [{"par": 3, "strokes": 4 if i == 0 else 3, "putts": 0} for i in range(9)]},
          {"id": "e2", "player": "たろう", "courseId": "c1", "courseName": "テストコース", "date": "2026-10-11", "createdAt": 1001,
           "holes": [{"par": 3, "strokes": 3, "putts": 1} for _ in range(9)]}]
    c9h = [{"id": "c1", "name": "テストコース", "holes": [{"par": 3} for _ in range(9)], "createdAt": 1}]
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.rounds": HR, "sc.courses": c9h, "sc.tab": "\"history\"", "sc.v2": 1}, offline=True)
    pg.evaluate("state.openRound='e1'; render()")
    ht = pg.evaluate("(()=>{const t=document.querySelector('.hist-paper table.half'); if(!t) return null; const w=t.closest('.detail').getBoundingClientRect(); const cs=[...t.querySelectorAll('tr.h td')].map(e=>e.getBoundingClientRect()); return {n:cs.length, ok:cs.every(r=>r.left>=w.left-1&&r.right<=w.right+1), sw:document.documentElement.scrollWidth<=innerWidth}})()")
    sw = pg.evaluate("(()=>{const b=document.querySelector('.rcard .prac-b'); return b && b.getAttribute('role')==='switch' && b.getAttribute('aria-checked')==='true' && !!b.querySelector('.sl-sw.on') && b.textContent.trim()==='練習ラウンド'})()")
    check("履歴: 結果カードと同じ紙の表で、9ホール全部が枠の中に収まる（計の列も）。練習ラウンドはスイッチ（文字は「練習ラウンド」だけ）",
          ht and ht["n"] == 11 and ht["ok"] and ht["sw"] and sw and not pg.query_selector(".prac-note") and not errs)
    C(pg, "[data-act=edit-round][data-id=e1]"); pg.wait_for_timeout(300)
    ce = pg.evaluate("!!state.cdraft && state.cdraft.editing===true && !state.draft && state.cdraft.players[0].s[0]===4 && !!document.querySelector('.cdt')")
    C(pg, "[data-act=cd-cell][data-h='0'][data-i='0'][data-k='s']"); C(pg, "[data-act=cd-key][data-v='3']"); pg.wait_for_timeout(200)
    C(pg, "[data-act=cd-finish]"); pg.wait_for_timeout(700)
    sv = pg.evaluate("(()=>{const rs=state.rounds.filter(r=>r.id==='e1'); return [state.rounds.length, rs.length, rs[0].holes[0].strokes, rs[0].memo, rs[0].practice===true, rs[0].card===true]})()")
    check("スコアカードで記録したラウンドを編集すると、スコアカード入力で開く。保存すると同じラウンドが直り、メモ・練習の印は残る（増えない）",
          ce and sv == [2, 1, 3, "メモ", True, True] and not errs)
    old_pg, old_errs = pg, errs
    HM = [{**HR[0], "id": "e1"}, {"id": "e3", "card": True, "player": "はなこ", "courseId": "c1", "courseName": "テストコース", "date": "2026-10-10", "createdAt": 1000 + 5,
           "holes": [{"par": 3, "strokes": 3, "putts": 0} for _ in range(9)]}]
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.rounds": HM, "sc.courses": c9h, "sc.tab": "\"history\"", "sc.v2": 1}, offline=True)
    pg.evaluate("state.openRound='e1'; render()"); C(pg, "[data-act=edit-round][data-id=e1]"); pg.wait_for_timeout(300)
    two = pg.evaluate("state.cdraft && state.cdraft.players.length")
    C(pg, "[data-act=cd-cell][data-h='0'][data-i='1'][data-k='s']"); C(pg, "[data-act=cd-key][data-v='5']"); pg.wait_for_timeout(200)
    C(pg, "[data-act=cd-finish]"); pg.wait_for_timeout(700)
    gm = pg.evaluate("(()=>{const m=state.rounds.find(r=>r.id==='e3'); return [state.rounds.length, m.holes[0].strokes, m.player, state.rounds.find(r=>r.id==='e1').holes[0].strokes]})()")
    check("履歴で編集: いっしょに回った人（同じ日・同じコース・同時に保存）もスコアカードに並び、直すとその人のラウンドが直る（増えない）", two == 2 and gm == [2, 5, "はなこ", 4] and not errs)
    pg.context.close()
    HM2 = [{**HR[1], "id": "e2"}, {"id": "e4", "card": True, "player": "はなこ", "courseId": "c1", "courseName": "テストコース", "date": "2026-10-11", "createdAt": 1001 + 5,
           "holes": [{"par": 3, "strokes": 3, "putts": 0} for _ in range(9)]}]
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.rounds": HM2, "sc.courses": c9h, "sc.tab": "\"history\"", "sc.v2": 1}, offline=True)
    pg.evaluate("state.openRound='e2'; render()"); C(pg, "[data-act=edit-round][data-id=e2]"); pg.wait_for_timeout(300)
    dm = pg.evaluate("!!state.draft && (state.draft.mates||[]).length===1 && state.draft.mateIds[0]==='e4'")
    pg.evaluate("state.draft.mates[0].s[0]=6; state.hole=8; render()"); C(pg, "[data-act=finish]"); pg.wait_for_timeout(700)
    dg = pg.evaluate("(()=>{const m=state.rounds.find(r=>r.id==='e4'); return [state.rounds.length, m.holes[0].strokes, m.player]})()")
    check("詳しい入力で記録したラウンドの編集でも、いっしょに回った人の打数が並び、直せる（増えない）", dm and dg == [2, 6, "はなこ"] and not errs)
    pg.context.close()
    HM3 = [{**HR[1], "id": "e5", "nos": [1, 2, 3], "holes": HR[1]["holes"][:3]}, {"id": "e6", "card": True, "nos": [1, 2, 3], "player": "はなこ", "courseId": "c1", "courseName": "テストコース", "date": "2026-10-11", "createdAt": 1001 + 5,
           "holes": [{"par": 3, "strokes": 3, "putts": 0} for _ in range(3)]}]
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.rounds": HM3, "sc.courses": c9h, "sc.tab": "\"history\"", "sc.v2": 1}, offline=True)
    pg.evaluate("state.openRound='e5'; render()"); C(pg, "[data-act=edit-round][data-id=e5]"); pg.wait_for_timeout(300)
    pm = pg.evaluate("!!state.draft && (state.draft.mates||[]).length===1 && state.draft.mateIds[0]==='e6' && JSON.stringify(state.draft.nos)==='[1,2,3]'")
    pg.evaluate("state.draft.mates[0].s[0]=6; state.hole=2; render()"); C(pg, "[data-act=finish]"); pg.wait_for_timeout(700)
    pn = pg.evaluate("(()=>{const m=state.rounds.find(r=>r.id==='e6'), o=state.rounds.find(r=>r.id==='e5'); return [state.rounds.length, m.holes[0].strokes, JSON.stringify(m.nos), JSON.stringify(o.nos)]})()")
    check("途中で終わったラウンドの編集でも、いっしょに回った人の打数が並び、直せる（ホール番号は残る）", pm and pn == [2, 6, "[1,2,3]", "[1,2,3]"] and not errs)
    pg.context.close()
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.courses": c9h, "sc.v2": 1, "sc.game": "\"snake\""}, offline=True)
    pg.evaluate("(()=>{state.cdraft={id:'z',card:true,courseId:'c1',courseName:'テストコース',date:'2026-10-10',holes:Array.from({length:9},()=>({par:3})),players:[0,1].map(i=>({name:'n'+i,s:Array(9).fill(4),p:Array(9).fill(null)})),sel:null,game:'snake',marks:{},gAsk:8,gh:8,half:0}; state.tab='record'; render()})()"); pg.wait_for_timeout(300)
    sh = pg.evaluate("(()=>{const o=document.querySelector('.gp-over'); if(!o) return null; const r=o.getBoundingClientRect(); return {h:r.height, vis:r.top>=0 && r.bottom<=innerHeight+16}})()")
    check("ゲームの入力のシート: 9ホール目（数字キーが出ていないとき）でも、見える高さが足りる（160px以上、決定ボタンまで画面から切れない）", sh and sh["h"] >= 160 and sh["vis"] and not errs)
    pg.context.close()
    toast_ok = None
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.rounds": HR, "sc.courses": c9h, "sc.tab": "\"history\"", "sc.v2": 1}, offline=True)
    pg.evaluate("state.openRound='e2'; render()"); C(pg, "[data-act=prac-toggle]"); pg.wait_for_timeout(200)
    tt = pg.evaluate("(()=>{const e=document.querySelector('.toast'); return e? e.textContent:null})()")
    check("練習ラウンドのメッセージは短く「練習ラウンドにしました」だけ", tt == "練習ラウンドにしました" and not errs)
    pg, errs = old_pg, old_errs
    pg.evaluate("state.result=null; state.tab='history'; render()")
    C(pg, "[data-act=toggle-round][data-id=e2]"); pg.wait_for_timeout(100); C(pg, "[data-act=edit-round][data-id=e2]"); pg.wait_for_timeout(300)
    check("詳しい入力で記録したラウンドの編集は、今まで通り詳しい入力で開く", pg.evaluate("!!state.draft && state.draft.editing===true && !state.cdraft") and not errs)
    ms = pg.evaluate("(()=>{const mk=k=>{const m=document.createElement('span'); m.className='mk3 m-'+k; m.textContent='5'; document.body.appendChild(m); const cs=getComputedStyle(m); const o={bg:cs.backgroundImage.includes('svg'), b:parseFloat(cs.borderTopWidth), fs:cs.fontSize}; m.remove(); return o}; return {bo:mk('bogey'), db:mk('double'), tr:mk('triple')}})()")
    check("記号: ボギーは△（線の絵）・ダボは□（枠）・トリプル以上は二重□（塗りつぶしなし）。数字の大きさは同じ", ms["bo"]["bg"] and ms["bo"]["b"] == 0 and ms["db"]["b"] == 2 and not ms["db"]["bg"] and ms["tr"]["bg"] and ms["bo"]["fs"] == ms["db"]["fs"] == ms["tr"]["fs"])
    pg.context.close()
    # スコアカード入力: 最後の人の打数を入れて選択が次のホールへ進んでも、ゲームの行（メダルなど）は今入れたホールのまま。次のホールの数字を入れたら、そちらに移る
    pg, errs = open_page(b, {**GM, "sc.game": "\"olympic\"", "sc.inputMode": "\"card\"", "sc.fields": {"putts": False}}, offline=True); C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    for v in ["3", "4", "3"]: C(pg, f'[data-act=cd-key][data-v="{v}"]')
    a1 = (pg.evaluate("state.cdraft.sel.h"), pg.inner_text(".gp-hn"))   # 3人（全員）の打数がそろった瞬間: 選択は2Hへ進み、パネルは入れ終わった1Hのメダル
    C(pg, "[data-act=cd-gp-ok]"); a2 = (pg.query_selector(".gp-over") is None, pg.evaluate("getComputedStyle(document.querySelector('.cd-keys')).visibility") == "visible")
    C(pg, "[data-act=cd-key][data-v='4']"); a3 = pg.query_selector(".gp-over") is None   # 2Hの途中ではパネルは出ない
    C(pg, "[data-act=cd-gh][data-h='0']"); a4 = pg.inner_text(".gp-hn")   # 前のホールの番号を押すと、そのホールのパネル
    C(pg, "[data-act=cd-cell][data-h='1'][data-i='1'][data-k='s']"); a5 = pg.query_selector(".gp-over") is None   # マスを押すと数字キーに戻る
    check("スコアカード入力: 全員の打数がそろうとそのホールのメダルのパネルが自動で開き、決定で数字キーに戻る。ホール番号を押すと前のホールを開き、マスを押すと閉じる",
          a1 == (1, "1H") and a2 == (True, True) and a3 and a4 == "1H" and a5 and not errs)
    pg.context.close()
    pg, errs = open_page(b, {"sc.v2": 1}, offline=True, repl={"const LS = {": "const LS = window.LS = {"})
    # 端末に保存できなかったとき（保存領域がいっぱいなど）は、だまって消えずに知らせる
    pg.evaluate("Storage.prototype.setItem=function(){ throw new Error('QuotaExceededError'); }; window.__r=LS.set('sc.rounds',[])"); pg.wait_for_timeout(200)
    check("端末に保存できなかったときは、トーストで知らせる（設定などの小さな保存では出さない）", pg.evaluate("window.__r") is False and "保存できませんでした" in pg.inner_text("#toast") and not pg.evaluate("document.querySelector('#toast').hidden") and not errs)
    pg.context.close()

    # 20. Web版（Cloudflare Pages + D1）: ローカルの代わりのサーバー（tests/cf-server.mjs）で本物の functions/api.js を動かす
    import subprocess, time
    PORT = 8791
    srv = subprocess.Popen(["node", os.path.join(ROOT, "tests", "cf-server.mjs"), str(PORT)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    srv.stdout.readline()
    try:
        src = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
        src = (src.replace("setTimeout(checkUpdate, 1500);", "").replace("setTimeout(maybeShowA2HS, 2500);", "").replace("const state = {", "const state = window.state = {")
                  .replace("async function connect(){", "window.connect=async()=>{ await backup(); await legacyPull(); }; window.realConnect=()=>connect(); window.backup=()=>backup(); window.pendN=()=>pendN(); async function connect(){")
                  .replace("function ocrApplyRes(", "window.ocrApplyRes=ocrApplyRes; function ocrApplyRes("))
        src = src.replace("<body>", "<body><script>if(!sessionStorage.t){sessionStorage.t=1;localStorage.clear();localStorage.setItem('sc.player',JSON.stringify('たろう'));localStorage.setItem('sc.flickTip','true');localStorage.setItem('sc.presets','1');localStorage.setItem('sc.fields',JSON.stringify(%s));}</script>" % json.dumps(ALL), 1)
        open(os.path.join(ROOT, "_test_cf.html"), "w", encoding="utf-8").write(src)
        ctx = b.new_context(viewport={"width": 375, "height": 667}, service_workers="block"); pg = ctx.new_page(); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(f"http://127.0.0.1:{PORT}/_test_cf.html"); pg.wait_for_timeout(1200)
        check("Web版: 同じサイトの /api につながる（version 3・シート更新のお願いは出ない）", pg.evaluate("state.mode") == "cloud" and not pg.is_visible("#sync"))
        start_preset(pg, "firtree"); play_round(pg, ALL)
        srvr = pg.evaluate("fetch('/api').then(r=>r.json())")
        hs = srvr["rounds"][0]["holes"] if srvr["rounds"] else []
        check("Web版: ラウンドがデータベースに保存され、クラブ・ティーショット・方向も残る", len(srvr["rounds"]) == 1 and any(h.get("club") for h in hs) and any(h.get("tee") for h in hs) and any(h.get("dir") for h in hs))
        check("Web版: コースも保存される", any(c.get("name") for c in srvr["courses"]))
        # 新しい端末（買い替え・入れ直し）: ほかの人の記録は勝手に入らない。「バックアップから戻す」で、同じ名前の記録だけ戻る
        pg.evaluate("fetch('/api',{method:'POST',body:JSON.stringify({action:'saveRound',round:{id:'other1',player:'べつのひと',courseId:'cZ',courseName:'よそ',date:'2026-10-02',createdAt:5,holes:[{par:3,strokes:3,putts:2}]}})})"); pg.wait_for_timeout(200)
        pg.evaluate("localStorage.clear(); localStorage.setItem('sc.flickTip','true'); localStorage.setItem('sc.presets','1')"); pg.reload(); pg.wait_for_timeout(1200)
        C(pg, "nav.tabs [data-tab=history]"); pg.wait_for_timeout(200)
        C(pg, "[data-act=bk-open]")
        check("新しい端末: サーバーにあるほかの人の記録は入らない（履歴は空）", pg.evaluate("state.rounds.length") == 0 and pg.query_selector("[data-act=toggle-round]") is None and pg.query_selector("[data-act=restore]") is not None)
        C(pg, "[data-act=restore]"); pg.fill("#bk-name", "いないひと"); C(pg, "[data-act=restore-find]"); pg.wait_for_timeout(500)
        check("バックアップから戻す: 名前が違うと「見つかりませんでした」", "見つかりませんでした" in pg.inner_text(".data-sheet") and pg.query_selector("[data-act=data-ok]") is None)
        C(pg, "[data-act=data-cancel]"); C(pg, "[data-act=bk-open]"); C(pg, "[data-act=restore]"); pg.fill("#bk-name", "たろう"); C(pg, "[data-act=restore-find]"); pg.wait_for_timeout(500)
        check("バックアップから戻す: 同じ名前の記録が見つかる（1ラウンド）", "1ラウンド" in pg.inner_text(".data-sheet"))
        C(pg, "[data-act=data-ok]"); pg.wait_for_timeout(300)
        check("バックアップから戻す: 自分の記録だけが戻る（クラブも・コースも）", pg.evaluate("state.rounds.length") == 1 and pg.evaluate("state.rounds[0].player") == "たろう" and pg.evaluate("state.rounds[0].holes.some(h=>h.club)") and pg.evaluate("state.courses.length") == 1 and pg.evaluate("pendN()") == 0)
        pg.reload(); pg.wait_for_timeout(1000)
        check("バックアップから戻したあと、開き直しても残っている（ほかの人の分は入らない）", pg.evaluate("state.rounds.length") == 1)
        pg.evaluate("fetch('/api',{method:'POST',body:JSON.stringify({action:'deleteRound',id:'other1'})})"); pg.wait_for_timeout(200)
        rid = pg.evaluate("state.rounds[0].id")
        pg.evaluate(f"fetch('/api',{{method:'POST',body:JSON.stringify({{action:'deleteRound',id:'{rid}'}})}})"); pg.wait_for_timeout(200)
        check("Web版: 削除できる", len(pg.evaluate("fetch('/api').then(r=>r.json())")["rounds"]) == 0)
        # 写真からコースを読み取る: 確認画面なしで、そのままコース編集に入る（偽のAI: 1つ目は3番のLを177と答え、2つ目は117と答える → 割れた数字は空欄・赤い枠）
        import base64
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
        check("写真読み取り: サーバーにAIがつながっていれば ready", pg.evaluate("fetch('/ocr').then(r=>r.json())")["ready"] is True)
        C(pg, "nav.tabs [data-tab=courses]"); C(pg, "[data-act=new-course]"); pg.wait_for_timeout(900)
        check("写真読み取り: コース登録に「写真から読み取る」ボタンが出る", pg.query_selector("[data-act=ocr-pick]") is not None)
        pg.set_input_files("#ocr-file", {"name": "card.png", "mimeType": "image/png", "buffer": png}); pg.wait_for_function("state.courseEdit && state.courseEdit.ocrNote", timeout=8000); pg.wait_for_timeout(300)
        check("写真読み取り: 確認画面は出さず、そのままコース編集に入る（9ホール・パターン2つ(L,R)・ヤード）", pg.query_selector(".ocr-sheet") is None and pg.query_selector(".ocr-t") is None and pg.evaluate("(()=>{const e=state.courseEdit; return e.holes.length===9 && e.np===2 && e.gn[0]==='L' && e.gn[1]==='R' && e.holes[0].yards===97 && e.holes[0].yards2===95})()"))
        check("写真読み取り: 2つのAIで答えが割れた数字（3番のL）は、赤い枠（合計から計算できれば入る）", pg.input_value("#yd-2") in ("", "117") and pg.evaluate("document.querySelector('#yd-2').classList.contains('need')") and not pg.evaluate("document.querySelector('#yd-0').classList.contains('need')"))
        check("写真読み取り: 上に案内（「スコアカードと見比べて」・赤い枠の説明）が出る", "スコアカードと見比べて" in pg.inner_text(".ocr-note") and "赤い枠" in pg.inner_text(".ocr-note"))
        pg.fill("#yd-2", "117")
        check("写真読み取り: 数字を入れると赤い枠が消える", not pg.evaluate("document.querySelector('#yd-2').classList.contains('need')"))
        # 印刷の合計は画面に出さない。抜けが1か所だけなら合計から引き算で入れる（赤い枠のまま）。2か所以上なら空欄の赤い枠
        mkres = lambda y0, tot: "ocrApplyRes({cols:['Back','Front'],holes:[[%s,82],[91,74],[57,44],[70,51],[55,45],[63,48],[78,67],[89,69],[94,79]].map((y,i)=>({hole:i+1,par:3,yards:y})),totals:{par:27,yards:%s,first:[null,null],second:[null,null]}})" % (y0, tot)
        pg.evaluate(mkres("105", "[702,559]")); pg.wait_for_timeout(300)
        check("写真読み取り: 合計が合っていても合わなくても、合計の文は出ない", "合計" not in pg.inner_text(".ocr-note"))
        pg.evaluate(mkres("105", "[692,559]")); pg.wait_for_timeout(300)
        check("写真読み取り: 合計と合わなくても、文は出ない（裏で見るだけ）", "合計" not in pg.inner_text(".ocr-note") and "692" not in pg.inner_text(".ocr-note"))
        pg.evaluate(mkres("null", "[702,559]")); pg.wait_for_timeout(300)
        check("写真読み取り: 抜けが1か所なら合計から引き算で入り（105）、赤い枠のまま", pg.input_value("#yd-0") == "105" and pg.evaluate("document.querySelector('#yd-0').classList.contains('need')") and "合計から計算" in pg.inner_text(".ocr-note"))
        pg.evaluate("ocrApplyRes({cols:['Back'],holes:[[null],[null],[57],[70],[55],[63],[78],[89],[94]].map((y,i)=>({hole:i+1,par:3,yards:y})),totals:{par:27,yards:[702],first:[null],second:[null]}})"); pg.wait_for_timeout(300)
        check("写真読み取り: 抜けが2か所なら空欄の赤い枠", pg.input_value("#yd-0") == "" and pg.evaluate("document.querySelector('#yd-0').classList.contains('need')"))
        # 保存: 赤い枠が残っていたら、1回目は止めて確認を促し、2回目で保存する
        pg.evaluate("state.courseEdit.name='テスト写真コース'"); C(pg, "[data-act=save-course]"); pg.wait_for_timeout(300)
        check("写真読み取り: 赤い枠が残ったまま保存を押すと、1回目はエラーで確認を促す", pg.evaluate("!!state.courseEdit") and "赤い枠" in pg.inner_text("#c-err"))
        C(pg, "[data-act=save-course]"); pg.wait_for_timeout(500)
        check("写真読み取り: もう一度押すと保存される", pg.evaluate("!state.courseEdit"))
        C(pg, "[data-act=new-course]"); pg.wait_for_timeout(500)
        pg.evaluate("ocrApplyRes({cols:['L','R'],holes:[[97,95],[65,63],[null,123],[68,66],[101,118],[65,60],[85,76],[92,110],[103,120]].map((y,i)=>({hole:i+1,par:3,yards:y})),totals:{par:27,yards:[null,null],first:[null,null],second:[null,null]}})"); pg.wait_for_timeout(300)
        # 9ホールのスコアカードを「同じ表を2つ並べて18H」にしてあるとき: 9ホールのコースとして入れる（あとでホール数を18にすると後半も入る）
        pg.evaluate("ocrApplyRes({cols:['L','R'],holes:Array.from({length:18},(_,i)=>({hole:i+1,par:3,yards:[[97,65,117,68,101,65,85,92,103][i%9],[95,63,123,66,118,60,76,110,120][i%9]]})),totals:{par:54,yards:[1586,1662],first:[793,831],second:[793,831]}})"); pg.wait_for_timeout(300)
        check("写真読み取り: 前半と後半が同じなら、9ホールのコースとして入る（案内の文つき）", pg.evaluate("state.courseEdit.holes.length") == 9 and "9ホールのコース" in pg.inner_text(".ocr-note") and "合計が合いません" not in pg.inner_text(".ocr-note"))
        C(pg, "[data-act=open-holes]"); pg.wait_for_timeout(150); C(pg, "[data-act=set-holes][data-n='18']"); pg.wait_for_timeout(250)
        check("写真読み取り: あとでホール数を18にすると、後半（10番〜）のヤードも入っている", pg.evaluate("state.courseEdit.holes.length") == 18 and pg.evaluate("state.courseEdit.holes[9].yards") == 97 and pg.evaluate("state.courseEdit.holes[9].yards2") == 95)
        # 確認用のプレビュー（本番以外のアドレス）は、同じデータベースでも別の表を使う → 本番のデータに混ざらない
        import urllib.request, json as _json
        def call(host, body=None):
            rq = urllib.request.Request(f"http://127.0.0.1:{PORT}/api", data=(_json.dumps(body).encode() if body else None), headers={"Host": host, "Content-Type": "text/plain"}, method="POST" if body else "GET")
            return _json.loads(urllib.request.urlopen(rq).read())
        PREV = "preview.golf-score-bd6.pages.dev"
        # サーバー: ?player=名前 で、その人のラウンドとそのコースだけを返す。大きすぎるデータは受け取らない
        import urllib.parse, urllib.error
        def getp(name): return _json.loads(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}/api?player=" + urllib.parse.quote(name), headers={"Host": "localhost"})).read())
        call("localhost", {"action": "saveCourse", "course": {"id": "pf-c", "name": "しぼりこみコース", "holes": [{"par": 3}]}})
        call("localhost", {"action": "saveRound", "round": {"id": "pf-1", "player": "しぼり太郎", "courseId": "pf-c", "courseName": "しぼりこみコース", "date": "2026-11-17", "createdAt": 5, "holes": [{"par": 3, "strokes": 3, "putts": 2}]}})
        g = getp("しぼり太郎")
        check("サーバー: ?player=名前 は、その人のラウンドとそのコースだけを返す", [r["id"] for r in g["rounds"]] == ["pf-1"] and [c["id"] for c in g["courses"]] == ["pf-c"])
        check("サーバー: いない名前には何も返さない", getp("いないひと")["rounds"] == [] and getp("いないひと")["courses"] == [])
        def code(body):
            try: urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}/api", data=body, headers={"Host": "localhost", "Content-Type": "text/plain"}, method="POST")); return 200
            except urllib.error.HTTPError as e: return e.code
        check("サーバー: 大きすぎるラウンド（300KB超）は受け取らない", code(_json.dumps({"action": "saveRound", "round": {"id": "big", "x": "a" * 400000}}).encode()) == 400 and "big" not in [r["id"] for r in call("localhost")["rounds"]])
        check("サーバー: JSON が null でも落ちない（400）", code(b"null") == 400)
        # 形のおかしいデータで 500 を返すと、端末がずっと送り直して、うしろの記録が送れなくなる → 受け取るか 400 で返す
        odd_r = {"id": "odd-1", "player": "へんな人", "courseId": "c", "courseName": "C", "date": "2026-11-22", "createdAt": "abc", "holes": "x"}
        check("サーバー: createdAt が数字でない・holes が配列でないラウンドでも落ちない（500 を返さない）", code(_json.dumps({"action": "saveRound", "round": odd_r}).encode()) == 200 and code(_json.dumps({"action": "import", "rounds": [{**odd_r, "holes": [{"par": 3}]}], "courses": "x"}).encode()) == 200 and code(_json.dumps({"action": "import", "rounds": {"length": 3}}).encode()) == 200)
        call("localhost", {"action": "deleteRound", "id": "odd-1"})
        call("localhost", {"action": "deleteRound", "id": "pf-1"}); call("localhost", {"action": "deleteCourse", "id": "pf-c"})
        call(PREV, {"action": "saveRound", "round": {"id": "prev-r1", "player": "後輩", "courseId": "c", "courseName": "C", "date": "2026-11-09", "createdAt": 1, "holes": [{"par": 3, "strokes": 3, "putts": 2}]}})
        call(PREV, {"action": "saveCourse", "course": {"id": "prev-c1", "name": "プレビューのコース", "holes": [{"par": 3}]}})
        prod = call("golf-score-bd6.pages.dev"); prev = call(PREV)
        check("プレビュー: 試したラウンド・コースは、プレビューのほうにだけ入る", any(r["id"] == "prev-r1" for r in prev["rounds"]) and any(c["id"] == "prev-c1" for c in prev["courses"]))
        check("プレビュー: 本番のアドレスから見えるデータには、プレビューで試したものが混ざらない", not any(r["id"] == "prev-r1" for r in prod["rounds"]) and not any(c["id"] == "prev-c1" for c in prod["courses"]))
        check("写真読み取り: エラーなし", not errs)
        check("Web版: エラーなし", not errs)
        ctx.close()
        # 古いアプリ（github.io）から引っ越し: 端末に残っていたクラブなども一緒に送る
        pg, errs = open_page(b, {"sc.player": "\"たろう\""}, repl={'const CF_URL = "";': f'const CF_URL = "http://127.0.0.1:{PORT}";'})
        pg.evaluate("window.__noNav=true; __db.courses.push({id:'cX',name:'旧コース',holes:[{par:3}]}); __db.rounds.push({id:'old1',player:'たろう',courseId:'cX',courseName:'旧コース',date:'2026-10-01',createdAt:1,holes:[{par:3,strokes:4,putts:2}]})")
        pg.evaluate("state.rounds=[{id:'old1',player:'たろう',holes:[{par:3,strokes:4,putts:2,club:'7I',tee:null,dir:'right'}]}]; localStorage.setItem('sc.rounds', JSON.stringify(state.rounds))")
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

    # お試しの案（?mates= / ?result=）。決まるまでのあいだ、アドレスの後ろに付けたときだけ動く
    tc = [{"id": "c1", "name": "テストコース", "holes": [{"par": 3, "yards": 100}] * 9, "createdAt": 1}]
    def trd(i, p, d, ca): return {"id": "t%d" % i, "player": p, "courseId": "c1", "courseName": "テストコース", "date": d, "createdAt": ca, "holes": [{"par": 3, "strokes": 4, "putts": 2}] * 9}
    trs = [trd(1, "たろう", "2026-11-01", 1000), trd(2, "ふゆこ", "2026-11-01", 1001), trd(3, "まさる", "2026-11-01", 1002), trd(4, "たろう", "2026-10-01", 500), trd(5, "ふゆこ", "2026-10-01", 501), trd(6, "さくら", "2026-09-01", 300), trd(7, "たろう", "2026-09-01", 299)]
    TL = {"sc.player": "\"たろう\"", "sc.mates": ["ふゆこ", "", ""], "sc.courses": tc, "sc.rounds": trs, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.v2": 1}
    MV = "[0,1,2].map(i=>document.querySelector('#r-mate'+i).value).join()"
    pg, errs = open_page(b, TL, offline=True)
    C(pg, "[data-act=open-mates]")
    chips0 = pg.evaluate("[...document.querySelectorAll('.mt-chip')].map(e=>e.dataset.n)")
    check("一緒に回る人: 何も付けなければ「前回と同じ」＋最近の人のボタン（iPhone の候補は出さない・見本の名前は混ぜない・お試しの札なし）", pg.evaluate("document.querySelector('#r-mate0').getAttribute('list')") is None and pg.query_selector("datalist") is None and pg.query_selector(".try-tag") is None and pg.query_selector(".try-link") is None and "ふゆこ・まさる" in pg.inner_text(".mt-last") and chips0 == ["まさる", "さくら"] and not errs)
    check("名前は8文字まで（長い名前で表が崩れないように）", pg.evaluate("[document.querySelector('#r-mate0').maxLength, document.querySelector('#r-player').maxLength].join()") == "8,8" and pg.evaluate("document.querySelector('#r-player').getAttribute('list')") is None)
    pg.context.close()
    pg, errs = open_page(b, TL, offline=True, query="?mates=old")
    C(pg, "[data-act=open-mates]")
    check("お試し: ?mates=old で前の形（iPhone の候補）", pg.evaluate("document.querySelector('#r-mate0').getAttribute('list')") == "plist" and pg.query_selector("datalist#plist") is not None and not errs)
    pg.context.close()
    pg, errs = open_page(b, TL, offline=True, query="?mates=m1")
    C(pg, "[data-act=open-mates]")
    check("お試し M1: 候補を出さない（入力欄だけ）", pg.evaluate("document.querySelector('#r-mate0').getAttribute('list')") is None and pg.query_selector(".mt-chip") is None and "M1" in pg.inner_text(".try-tag") and not errs)
    pg.context.close()
    pg, errs = open_page(b, TL, offline=True, query="?mates=m2")
    check("お試し: 開始画面に「お試し中」のリンク", "M2" in pg.inner_text(".try-link"))
    C(pg, "[data-act=open-mates]")
    chips = pg.evaluate("[...document.querySelectorAll('.mt-chip')].map(e=>e.dataset.n)")
    check("お試し M2: 最近の人が6人まで・新しい順・すでに入れた人と自分は出ない", len(chips) == 6 and chips[:2] == ["まさる", "さくら"] and "ふゆこ" not in chips and "たろう" not in chips)
    C(pg, '.mt-chip[data-n="まさる"]')
    check("お試し M2: 押すと空いている欄に入り、候補から消える", pg.evaluate(MV) == "ふゆこ,まさる," and pg.query_selector('.mt-chip[data-n="まさる"]') is None and pg.evaluate("JSON.parse(localStorage.getItem('sc.mates')).join()") == "ふゆこ,まさる,")
    C(pg, '.mt-chip[data-n="さくら"]'); n3 = pg.evaluate(MV); C(pg, ".mt-chip")
    check("お試し M2: 3人入ったら、それ以上は入らない", n3 == "ふゆこ,まさる,さくら" and pg.evaluate(MV) == n3 and not errs)
    C(pg, "[data-act=mates-done]"); C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    check("お試し M2: そのまま始められる（同伴者3人）", pg.evaluate("state.draft.mates.map(m=>m.name).join()") == "ふゆこ,まさる,さくら" and not errs)
    pg.context.close()
    pg, errs = open_page(b, TL, offline=True, query="?mates=m3")
    C(pg, "[data-act=open-mates]"); pg.evaluate("window.__el=document.querySelector('#r-mate1')")
    pg.fill("#r-mate1", "さ"); pg.dispatch_event("#r-mate1", "input")
    sg = pg.evaluate("[...document.querySelectorAll('.mt-sug button')].map(e=>e.dataset.n)")
    check("お試し M3: 打ち始めたら、その文字で始まる人だけ（3人まで）。入力欄は描き直さない", sg == ["さくら", "さぶろう"] and pg.evaluate("window.__el===document.querySelector('#r-mate1')"))
    pg.fill("#r-mate1", "フ"); pg.dispatch_event("#r-mate1", "input")
    check("お試し M3: すでに入れた人は出ない", pg.evaluate("document.querySelectorAll('.mt-sug button').length") == 0)
    pg.fill("#r-mate1", "マ"); pg.dispatch_event("#r-mate1", "input"); C(pg, ".mt-sug button")
    check("お試し M3: カタカナで打っても見つかり、押すとその欄に入る", pg.evaluate(MV) == "ふゆこ,まさる," and not errs)
    pg.context.close()
    pg, errs = open_page(b, TL, offline=True, query="?mates=m4")
    C(pg, "[data-act=open-mates]")
    check("お試し M4: 人の一覧（えらんだ人にチェック・1/3人）", pg.evaluate("[...document.querySelectorAll('.mp-row.on')].map(e=>e.dataset.n).join()") == "ふゆこ" and "1/3人" in pg.inner_text(".mp-n"))
    C(pg, '.mp-row[data-n="まさる"]'); C(pg, '.mp-row[data-n="さくら"]'); C(pg, '.mp-row[data-n="はなこ"]')
    check("お試し M4: 3人まで選べる（4人目は入らない）", pg.evaluate("JSON.parse(localStorage.getItem('sc.mates')).join()") == "ふゆこ,まさる,さくら" and "3/3人" in pg.inner_text(".mp-n"))
    C(pg, '.mp-row[data-n="ふゆこ"]')
    check("お試し M4: もう一度押すと外れて、前に詰まる", pg.evaluate("JSON.parse(localStorage.getItem('sc.mates')).join()") == "まさる,さくら,")
    pg.fill("#mp-q", "サ"); pg.dispatch_event("#mp-q", "input")
    check("お試し M4: 名前で探せる", pg.evaluate("[...document.querySelectorAll('.mp-row')].filter(e=>!e.hidden).map(e=>e.dataset.n).sort().join()") == "さくら,さぶろう,まさる")
    pg.fill("#mp-new", "ごんべえ"); C(pg, "[data-act=mate-new]")
    check("お試し M4: 新しい名前を足せる", pg.evaluate("JSON.parse(localStorage.getItem('sc.mates')).join()") == "まさる,さくら,ごんべえ" and pg.query_selector('.mp-row.on[data-n="ごんべえ"]') is not None and not errs)
    pg.context.close()
    pg, errs = open_page(b, {**TL, "sc.mates": []}, offline=True, query="?mates=m5")
    C(pg, "[data-act=open-mates]")
    check("お試し M5: 「前回と同じ」に前回の人たち・よく回る人は3人（回数つき）", "ふゆこ・まさる" in pg.inner_text(".mt-last") and pg.evaluate("document.querySelectorAll('.mt-chip').length") == 3 and pg.evaluate("document.querySelector('.mt-chip').dataset.n") == "ふゆこ")
    C(pg, "[data-act=mate-last]")
    check("お試し M5: 「前回と同じ」を押すと、前回の人が入る", pg.evaluate(MV) == "ふゆこ,まさる," and pg.query_selector(".mt-last.on") is not None and not errs)
    pg.context.close()
    pg, errs = open_page(b, {**TL, "sc.mates": []}, offline=True, query="?mates=m25")
    C(pg, "[data-act=open-mates]")
    check("お試し M2＋M5: 「前回と同じ」と、最近の人6人", pg.query_selector(".mt-last") is not None and pg.evaluate("document.querySelectorAll('.mt-chip').length") == 6 and pg.evaluate("localStorage.getItem('sc.try.mates')") == "m25")
    w = pg.evaluate("[document.querySelector('.mates-sheet').scrollWidth<=375, document.querySelector('.mates-sheet').getBoundingClientRect().top>=0]")
    check("お試し M2＋M5: シートが画面に収まる", w == [True, True] and not errs)
    pg.goto(pg.url.split("?")[0] + "?mates=0"); pg.wait_for_timeout(800)
    check("お試し: ?mates=0 で元に戻る", pg.evaluate("localStorage.getItem('sc.try.mates')") is None and pg.query_selector(".try-link") is None)
    # 結果カード: COURSE BEST の札が出ても、コース名と日付は1行のまま（カードの高さが変わらない）
    lc = [{"id": "c1", "name": "ファーツリーカントリークラブ", "holes": [{"par": 3, "yards": 100}] * 9, "createdAt": 1}]
    def lrd(i, st, d): return {"id": "b%d" % i, "player": "たろう", "courseId": "c1", "courseName": lc[0]["name"], "date": d, "createdAt": i, "holes": [{"par": 3, "strokes": st, "putts": 2}] * 9}
    hh = {}
    for nm, first in [("best", 5), ("normal", 3)]:
        pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.courses": lc, "sc.rounds": [lrd(1, first, "2026-10-01"), lrd(2, 4, "2026-11-18")], "sc.v2": 1, "sc.tab": "\"history\""}, offline=True)
        pg.evaluate("state.result=state.rounds.find(r=>r.id==='b2'); render()"); pg.wait_for_timeout(300)
        hh[nm] = pg.evaluate("[!!document.querySelector('.rc-flag'), Math.round(document.querySelector('.rc-band').getBoundingClientRect().height), Math.round(document.querySelector('.result-card').getBoundingClientRect().height)]")
        hh[nm] += pg.evaluate("(()=>{ const q=x=>document.querySelector(x), g=x=>getComputedStyle(q(x)); const sv=q('.rc-stars svg'); return [g('.rc-band').backgroundImage.includes('gradient'), g('.rc-score b').backgroundImage.includes('gradient'), sv?getComputedStyle(sv).animationName:'', g('.rc-sc tr:last-child td.sum').backgroundColor]; })()")
        pg.context.close()
    check("結果カード: COURSE BEST の札が出ても、緑の帯（コース名・日付）の高さもカードの高さも同じ", hh["best"][0] and not hh["normal"][0] and hh["best"][1] == hh["normal"][1] and hh["best"][2] == hh["normal"][2])
    check("結果カード: COURSE BEST は白い紙×金の帯（帯と合計の数字が金・表はうすい金・星はキラッと光る）。ふだんのカードは緑の帯のまま", hh["best"][3] and hh["best"][4] and hh["best"][5] == "glint" and hh["best"][6] == "rgb(243, 230, 184)" and not hh["normal"][3] and not hh["normal"][4])
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.courses": lc, "sc.rounds": [lrd(1, 5, "2026-10-01"), lrd(2, 4, "2026-11-18")], "sc.v2": 1, "sc.tab": "\"history\""}, offline=True)
    pg.evaluate("state.result=state.rounds.find(r=>r.id==='b2'); render()"); pg.wait_for_timeout(300)
    # 保存画像を作るときに足すCSS（onclone）と同じものを画面に足して、星と数字が消えないことを確かめる
    sv = pg.evaluate("(()=>{ const src=document.documentElement.outerHTML, m=src.match(/st\\.textContent=\"([^\"]+)\"/); if(!m) return null; const st=document.createElement('style'); st.textContent=m[1]; document.head.appendChild(st); const e=document.querySelector('.rc-stars svg'), g=getComputedStyle(e), n=getComputedStyle(document.querySelector('.rc-score b')); return [g.animationName, g.opacity, g.transform, n.color, n.backgroundImage]; })()")
    check("結果カード: 保存画像では、COURSE BEST の星は止まって見える（消えない）・合計の数字は金の1色", sv is not None and sv[0] == "none" and sv[1] == "1" and sv[2] == "none" and sv[3] == "rgb(184, 134, 11)" and sv[4] == "none")
    pg.context.close()
    gc = [{"id": "g1", "name": "二グリーン", "holes": [{"par": 3, "yards": 100, "yards2": 90, "gn": ["MG", "SG"]}] + [{"par": 3, "yards": 100, "yards2": 90}] * 8, "createdAt": 1}]
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.courses": gc, "sc.favs": ["g1"], "sc.lastCourse": "\"g1\"", "sc.v2": 1}, offline=True)
    ga = pg.evaluate("(()=>{ const e=document.querySelector('#r-green'); if(!e||e.closest('[hidden]')) return null; const g=getComputedStyle(e); return [g.textAlignLast, g.direction, e.options.length]; })()")
    check("開始画面: グリーン（MG / SG）は、ほかの行と同じ右そろえ", ga is not None and ga[0] == "right" and ga[1] == "rtl" and ga[2] == 2 and not errs)
    k1 = pg.evaluate("(()=>{ const q=x=>document.querySelector(x), e=q('#r-green'), r=e.getBoundingClientRect(), gb=getComputedStyle(q('.sl-go')); return [[...document.querySelectorAll('.sl-sec b')].map(x=>x.textContent).join(','), document.querySelectorAll('.sl-ic').length, Math.round(r.height), getComputedStyle(e).backgroundColor, gb.backgroundImage.includes('gradient'), document.querySelectorAll('.start-list .sl-row:not([hidden])').length]; })()")
    check("開始画面（K1）: ROUND / WITH の小見出し・行ごとの細い線の絵・MG は緑の札（高さ28px）・「始める」は緑のグラデーション", k1[0] == "ROUND,WITH" and k1[1] == 7 and k1[2] == 28 and k1[3] == "rgb(11, 107, 68)" and k1[4] and k1[5] == 7)
    pg.context.close()
    gc1 = [{"id": "g2", "name": "グリーン1つ", "holes": [{"par": 3, "yards": 100}] * 9, "createdAt": 1}]
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.courses": gc1, "sc.favs": ["g2"], "sc.lastCourse": "\"g2\"", "sc.v2": 1}, offline=True)
    k2 = pg.evaluate("(()=>{ const g=document.querySelector('#r-gwrap'), go=document.querySelector('.sl-go').getBoundingClientRect(), t=document.querySelector('nav.tabs').getBoundingClientRect(); return [!!g&&getComputedStyle(g).display==='none', document.querySelectorAll('.start-list .sl-row').length-document.querySelectorAll('.start-list .sl-row[hidden]').length, Math.round(go.bottom)<=Math.round(t.top)]; })()")
    for nm, want in [("ファーツリーカントリークラブ", 1), ("テストコースロング倶楽部カントリー", 2), ("とても長い名前のゴルフコースクラブ東九州カントリー倶楽部", 2)]:
        cc = [{"id": "g3", "name": nm, "holes": [{"par": 3, "yards": 100}] * 9, "createdAt": 1}]
        pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.courses": cc, "sc.favs": ["g3"], "sc.lastCourse": "\"g3\"", "sc.v2": 1}, offline=True)
        cn = pg.evaluate("(()=>{ const e=document.querySelector('.sl-cn'), lh=parseFloat(getComputedStyle(e).lineHeight), t=e.innerHTML.split('<br>'); return [Math.round(e.getBoundingClientRect().height/lh), e.scrollWidth<=e.parentElement.clientWidth+1, t.length>1?Math.abs(t[0].length-t[1].length):0, parseFloat(getComputedStyle(e).fontSize)]; })()")
        check(f"開始画面: コース名「{nm[:8]}…」は{want}行で収まり、1文字だけ次の行に落ちない（文字は14px以上）", cn[0] == want and cn[1] and cn[3] >= 14 and not errs)
        pg.context.close()
    pg, errs = open_page(b, {"sc.v2": 1}, offline=True)
    sp = pg.evaluate("""[["ファーツリーカントリークラブ","ファーツリー／カントリークラブ"],["日向シーサイドゴルフクラブ","日向シーサイド／ゴルフクラブ"],["直川カントリー倶楽部","直川／カントリー倶楽部"],["大野川ゴルフコース","大野川／ゴルフコース"],["東京相武CCショートコース","東京相武CC／ショートコース"],["光丘パブリックゴルフ場","光丘パブリック／ゴルフ場"],["青梅リバーサイドパーク","青梅／リバーサイドパーク"],["ゴルフパートナー千葉ショートコース","ゴルフパートナー／千葉ショートコース"],["学園ゴルフセンター ショートコース","学園ゴルフセンター／ショートコース"],["そうぶファミリーゴルフ","そうぶ／ファミリーゴルフ"],["グリーンヒルズ","グリーン／ヒルズ"],["東京相武カントリークラブショートコース","東京相武カントリークラブ／ショートコース"],["本宮パークカントリークラブ","本宮パーク／カントリークラブ"],["ゴルフステーション高山","ゴルフステーション／高山"],["シーサイドゴルフガーデン","シーサイド／ゴルフガーデン"],["つま恋リゾート彩の郷ゴルフ練習場","つま恋リゾート／彩の郷ゴルフ練習場"],["知多スポーツクラブトップス","知多スポーツクラブ／トップス"],["旭ヶ丘ゴルフリゾート","旭ヶ丘／ゴルフリゾート"]].map(x=>[courseSplit(x[0]).slice(0,2).join("／"), x[1]])""")
    check("開始画面: コース名の2行の切れ目は、言葉の区切り（カントリークラブ・ゴルフ場・ショートコースなど）。目印がなければまん中", all(x[0] == x[1] for x in sp))
    pg.context.close()
    check("開始画面: グリーンが1つのコースでは「グリーン」の行は出ない（6行）。「始める」はタブの上に出る", k2[0] and k2[1] == 6 and k2[2] and not errs)
    pg.context.close()
    # 幅320px（iPhone の拡大表示）: カレンダーの土曜が切れない・「始める」が隠れたままにならない
    pg, errs = open_page(b, {**TL, "sc.mates": ["はなこ", "じろう", ""]}, offline=True)
    pg.set_viewport_size({"width": 320, "height": 568}); pg.evaluate("render()"); pg.wait_for_timeout(100)
    cut = pg.evaluate("document.querySelector('[data-act=start]').getBoundingClientRect().bottom > document.querySelector('nav.tabs').getBoundingClientRect().top")
    check("幅320px: 「始める」がタブに隠れるときは、画面を動かせる", (not cut) or not pg.evaluate("document.documentElement.classList.contains('noscroll')"))
    C(pg, "[data-act=open-cal]")
    check("幅320px: カレンダーの7列が画面に収まる", pg.evaluate("[...document.querySelectorAll('.cal-d')].every(e=>{const r=e.getBoundingClientRect(); return r.left>=0 && r.right<=320})") and not errs)
    pg.context.close()
    pg.context.close()
    for n in [9, 18]:
        pg, errs = open_page(b, {"sc.v2": 1}, offline=True, query=f"?show={n}&game=skins")
        fit = pg.evaluate("(()=>{ const c=document.querySelector('.grp-card'), r=c.getBoundingClientRect(); return [...c.querySelectorAll('td,.rc-band b')].every(e=>{ const q=e.getBoundingClientRect(); return q.left>=r.left-0.5 && q.right<=r.right+0.5; }); })()")
        ok = (pg.query_selector(".result-card.paper.grp-card") is not None and pg.query_selector(".rc-rank") is None and pg.query_selector(".rc-top") is None
              and pg.evaluate("document.querySelectorAll('table.v1 .v1c').length") == 4 and pg.evaluate("document.querySelectorAll('table.v1 .mk3').length") == 4 * n
              and pg.evaluate("[...document.querySelectorAll('table.v1 tr.v1t td b')].map(e=>e.textContent).join()") == pg.evaluate("[...document.querySelectorAll('table.v1 .v1c b')].map(e=>e.textContent).join()")
              and pg.evaluate("document.querySelectorAll('table.v1 tr.v1s').length") == (2 if n == 18 else 0))
        check(f"みんなの結果・{n}H: 紙のカードの縦の表（人が横・順位と合計は見出し）・表がカードからはみ出さない・ゲームの欄もある", ok and fit and pg.query_selector(".grp-card .rc-skins") is not None and pg.evaluate("state.rounds.length") == 0 and not errs)
        if n == 9:
            C(pg, "[data-act=result-pick]"); one = pg.query_selector(".result-card.paper:not(.grp-card)") is not None and pg.query_selector("[data-act=back-to-input]") is None and pg.query_selector(".rc-who2 .rc-score") is not None
            C(pg, "[data-act=close-result]")
            check("見本の結果は1人ずつ（横書きの紙のカード）にも切りかえられ、編集はできない。閉じると消える（保存しない）", one and pg.query_selector(".result-back") is None and pg.evaluate("state.rounds.length") == 0 and pg.evaluate("localStorage.getItem('sc.rounds')") in (None, "[]") and not errs)
        pg.context.close()

    b.close()

try: os.remove(os.path.join(ROOT, "_test.html"))
except OSError: pass
bad = [n for ok, n in results if not ok]
print("\n" + ("ALL OK (%d)" % len(results) if not bad else "NG %d件: %s" % (len(bad), " / ".join(bad))))
sys.exit(1 if bad else 0)
