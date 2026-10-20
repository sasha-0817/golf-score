"""GOLF SC. の自動テスト（ブラウザで実際に操作して確かめる）

使い方:  python3 tests/run.py
必要なもの: Python と Playwright（pip install playwright && playwright install chromium）
ネットにつながらなくても動く（スプレッドシートは偽物のサーバーに置きかえる）。
全部 OK なら最後に「ALL OK」、1つでもダメなら NG を表示して終了コード1。
"""
import json, os, sys, tempfile, random
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
    if(o&&o.method==="POST"){ const b=JSON.parse(o.body), db=__db;
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

def open_page(b, ls=None, dark=False, offline=False):
    src = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    src = (src.replace("setTimeout(checkUpdate, 1500);", "").replace("setTimeout(maybeShowA2HS, 2500);", "")
              .replace("const state = {", "const state = window.state = {")
              .replace("function skinsCalc(", "window.skinsCalc=skinsCalc; function skinsCalc(")
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

def play_round(pg, fields):
    n = pg.evaluate("state.draft.holes.length")
    for i in range(n):
        par = pg.evaluate(f"state.draft.holes[{i}].par")
        if fields["shot"]:
            C(pg, "[data-act=tee][data-v=fw]" if par >= 4 else "[data-act=dir][data-v=right]"); pg.wait_for_timeout(1100)
            if pg.query_selector(".club-sheet"): C(pg, ".club-sheet [data-act=club]:nth-child(7)")
        elif fields["club"]: C(pg, "[data-act=club-quick]:nth-child(6)")
        if i % 3 == 0 and fields["ob"]: C(pg, "[data-act=tb][data-k=ob]")
        if i % 4 == 1 and fields["bunker"]: C(pg, "[data-act=tog][data-k=bunker]")
        C(pg, '[data-act=st][data-d="1"]')
        if fields["putts"] and i % 2: C(pg, '[data-act=pt][data-d="-1"]')
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
    for _ in range(20): C(pg, '[data-act=st][data-d="1"]')
    check("打数の上限15", pg.evaluate("state.draft.holes[0].strokes") == 15)
    for _ in range(20): C(pg, '[data-act=st][data-d="-1"]')
    check("打数の下限1・パットは打数-1まで", pg.evaluate("[state.draft.holes[0].strokes,state.draft.holes[0].putts]") == [1, 0])
    for _ in range(4): C(pg, "[data-act=next]")
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
    card_fill(pg, putts_for={0, 1})
    C(pg, "[data-act=cd-finish]"); pg.wait_for_timeout(900)
    rs = pg.evaluate("__db.rounds.map(r=>({p:r.player,s:r.holes.reduce((a,h)=>a+h.strokes,0),pt:r.holes.reduce((a,h)=>a+h.putts,0),n:r.holes.length}))")
    check("スコアカード: 4人分が別々のラウンドで保存", sorted(x["p"] for x in rs) == sorted(["たろう", "はなこ", "じろう", "さぶろう"]) and all(x["n"] == 9 for x in rs))
    check("スコアカード: パットを書かなかった人は putts 0（記録なし）", {x["p"]: x["pt"] > 0 for x in rs} == {"たろう": True, "はなこ": True, "じろう": False, "さぶろう": False})
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
    check("同伴者は人ごとの列（自分＋2人）・1画面に収まる", pg.evaluate("document.querySelectorAll('.pboard .pcol.mate').length") == 2 and pg.query_selector(".pcol.me") is not None and pg.evaluate("document.documentElement.scrollHeight") <= 667)
    C(pg, '.pcol.mate [data-act=mt][data-i="1"][data-d="1"]'); C(pg, '.pcol.mate [data-act=mt][data-i="1"][data-d="1"]')
    check("同伴者の列の ∧ で打数が増える", pg.evaluate("state.draft.mates[1].s[0]") == 5)
    C(pg, '[data-act=mt-open][data-i="0"]'); C(pg, '[data-act=mt-key][data-v="4"]')
    check("同伴者の数字キー: 押すと次の人へ進む", pg.evaluate("state.mateI") == 1 and pg.query_selector(".mate-sheet") is not None)
    C(pg, '[data-act=mt-key][data-v="5"]')
    check("同伴者の数字キー: 最後の人で閉じる・打数が入る", pg.query_selector(".mate-sheet") is None and pg.evaluate("[state.draft.mates[0].s[0],state.draft.mates[1].s[0]].join()") == "4,5")
    C(pg, "[data-act=next]")
    check("次のホールへはそのまま進む（同伴者を聞かない）", pg.query_selector(".mate-sheet") is None and pg.evaluate("state.hole") == 1)
    for _ in range(12):
        if pg.evaluate("state.hole") >= 8: break
        C(pg, "[data-act=next]")
        if pg.query_selector(".mate-sheet"): C(pg, "[data-act=mt-ok]")
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

    # 9. 同伴者がいてもティーショットの図が小さくならない（1人のときと同じ大きさ）
    hs = {}
    for nm, m in (("1人", []), ("4人", ["はなこ", "じろう", "さぶろう"])):
        pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": m, "sc.courses": c1, "sc.rounds": old, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.fields": ALL}, offline=True)
        C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
        hs[nm] = pg.evaluate("document.querySelector('svg.approach').getBoundingClientRect().height")
        if m: check("4人: 自分だけの記録を緑の囲みに入れる・1画面に収まる", pg.query_selector(".me-zone svg.approach") is not None and pg.evaluate("document.documentElement.scrollHeight") <= 667)
        pg.context.close()
    check("4人でもティーショットの図が1人のときと同じ大きさ", hs["4人"] >= hs["1人"] - 2)

    # 10. スキンズ: 計算・ラウンド前に選ぶ・記録中のコイン・みんなのリザルト・スコアカードでも
    pg, errs = open_page(b, {"sc.player": "\"たろう\"", "sc.mates": ["はなこ", "", ""], "sc.courses": c1, "sc.favs": ["c1"], "sc.lastCourse": "\"c1\"", "sc.fields": ALL, "sc.skinCarry": True}, offline=True)
    r = pg.evaluate("(()=>{ const x=skinsCalc([[3,4,2],[3,3,3]],3,false); return [x.won.join(), x.carry, x.pot]; })()")
    check("スキンズの計算（持ち越しなし: 同点のホールは誰ももらえない）", r == ["1,1", 0, 1])
    r = pg.evaluate("(()=>{ const x=skinsCalc([[3,4,2],[3,3,3]],3); return [x.won.join(), x.carry, x.pot]; })()")
    check("スキンズの計算（同点は持ち越し・取った人が持ち越し分も獲得）", r == ["1,2", 0, 1])
    r = pg.evaluate("(()=>{ const x=skinsCalc([[3,3],[3,3]],2); return [x.won.join(), x.carry]; })()")
    check("スキンズの計算（最後まで同点なら持ち越しが残る）", r == ["0,0", 2])
    C(pg, "[data-act=open-game]"); C(pg, "[data-act=game-help]")
    check("ゲームの選択画面にルールの解説（例つき）", pg.query_selector(".skins-rules .sr-ex") is not None)
    C(pg, '[data-act=pick-game][data-v=skins]')
    check("選んだゲームは緑の枠と丸・持ち越しのスイッチ", pg.query_selector(".gm-card.on[data-v=skins]") is not None and pg.query_selector('.gm-opt [aria-checked="true"]') is not None)
    C(pg, ".game-sheet [data-act=close-game]")
    C(pg, "[data-act=start]"); pg.wait_for_timeout(300)
    check("スキンズで始まる・記録中にコインの数とこのホールの枚数", pg.evaluate("state.draft.game") == "skins" and pg.query_selector(".pot") is not None and pg.evaluate("document.querySelectorAll('.skc').length") == 2)
    check("スキンズでも図が小さくならない・1画面", pg.evaluate("document.querySelector('svg.approach').getBoundingClientRect().height") >= hs["1人"] - 2 and pg.evaluate("document.documentElement.scrollHeight") <= 667)
    C(pg, "[data-act=next]"); C(pg, '[data-act=mt][data-i="0"][data-d="-1"]'); C(pg, "[data-act=next]")
    check("持ち越し込みで2枚獲得", pg.evaluate("document.querySelectorAll('.skc')[1].textContent") == "2")
    for _ in range(12):
        if pg.evaluate("state.hole") >= 8: break
        C(pg, "[data-act=next]")
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

    b.close()

try: os.remove(os.path.join(ROOT, "_test.html"))
except OSError: pass
bad = [n for ok, n in results if not ok]
print("\n" + ("ALL OK (%d)" % len(results) if not bad else "NG %d件: %s" % (len(bad), " / ".join(bad))))
sys.exit(1 if bad else 0)
