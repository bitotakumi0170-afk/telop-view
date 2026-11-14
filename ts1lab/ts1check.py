#!/usr/bin/env python3
"""納品前の検査: .ts1 が壊れていないか、打ち替えで「変えてよい所」以外が変わっていないかを機械で確かめる

  check_file(data)                 → その .ts1 だけを見て分かること（構造・参照・使用回数・書き直しても同じになるか）
  check_retype(template, out, ...) → 型と打ち替え後を突き合わせる（文字・字形・行の幅・描画範囲のほかは同じはず）

どちらも [(重さ, 説明), ...] を返す。重さは "NG"（納品してはいけない）か "注意"（VWS で一度見てほしい）。空なら問題なし。

検査のもとになっている決まり（実物の .ts1 で確かめたもの。FORMAT.md「リソースの表の決まり」）:
  ・字（MCHR）のグリフ番号は ORMP の添字で、指す先は同じ文字コードの字形（RCHR / RUCH）
  ・装飾（ADCO）の色リソース番号は ORMP の添字で、指す先は CRGB / CGRD / CPTN
  ・リソースの使用回数 = それを指している字・装飾の数（図形から指されているぶんは数えられないので、図形つきは「以上」）
  ・ORMP は 10 個単位で、使っていない位置は空き枠（ORSC）
確認画像は装飾つきの型では近似なので、装飾つきの型ほどこの検査が頼りになる。
"""
import struct

from ts1core import CELL, FIX, TS1, u16, u32

NG, WARN = "NG", "注意"
COLOR_TAGS = ("CRGB", "CGRD", "CPTN")
SCREEN = (1920, 1080)


def _area(contours):
    """輪郭の符号つき面積（2 次曲線のふくらみ込み）"""
    tot = 0.0
    for c in contours:
        cur = c.start
        for _prim, pts in c.segs:
            if len(pts) == 2 and _prim != 1 and _is_q(_prim):
                ctl, e = pts
                tot += (cur[0] * e[1] - cur[1] * e[0]) / 2 + ((ctl[0] - cur[0]) * (e[1] - cur[1]) - (ctl[1] - cur[1]) * (e[0] - cur[0])) / 3
                cur = e
            else:
                for e in pts:
                    tot += (cur[0] * e[1] - cur[1] * e[0]) / 2
                    cur = e
        tot += (cur[0] * c.start[1] - cur[1] * c.start[0]) / 2
    return tot


def _is_q(prim):
    from ts1core import PRIM_QSPLINE
    return prim == PRIM_QSPLINE


def _norm_res(tag, raw):
    """使用回数の欄を除いたリソースの中身（同じリソースかどうかを比べるのに使う）"""
    if tag in COLOR_TAGS and len(raw) >= 12:
        return tag, raw[:10] + b"\x00\x00" + raw[12:]
    return tag, raw


def _refs(t):
    """全部の文字要素から、リソースへの参照を数える → (グリフ番号ごとの数, 色リソース番号ごとの数, 問題の一覧)"""
    gref, cref, bad = {}, {}, []
    res = t.resources
    for n, v in enumerate(t.element_views()):
        where = "" if n == 0 else f"（{n + 1} つめの文字）"
        font = None
        for k, ch in enumerate(v.chars):
            if "ACTP" in ch.attrs:
                font = u32(ch.attrs["ACTP"], 2)
            i = ch.glyph_index
            gref[i] = gref.get(i, 0) + 1
            name = f"{k + 1} 字目「{ch.char or '終端'}」{where}"
            if not (0 <= i < len(res)) or res[i][0] != "RCHR":
                bad.append(f"{name} のグリフ番号 {i} が字形を指していません")
            else:
                g = res[i][1]
                if g.code != ch.code:
                    bad.append(f"{name} のグリフ番号 {i} が別の字の字形（「{g.char or '終端'}」）を指しています")
                elif font is not None and ch.code not in (0, 0x0A, 0x0D) and g.font_id != font:
                    bad.append(f"{name} の字形のフォント番号が {g.font_id} です（この字の書体は {font}）")
            if "ADCO" in ch.attrs:
                try:
                    decos = v._decode_adco(ch.attrs["ADCO"])
                except Exception as ex:
                    bad.append(f"{name} の装飾を読めません（{ex}）")
                    continue
                for d in decos:
                    c = d["color_resource"]
                    cref[c] = cref.get(c, 0) + 1
                    if not (0 <= c < len(res)) or res[c][0] not in COLOR_TAGS:
                        bad.append(f"{name} の装飾の色リソース番号 {c} が色を指していません"
                                   f"（{res[c][0] if 0 <= c < len(res) else '範囲の外'}）")
    # 図形（座布団）の面と縁も、色を番号で指している
    for f in t.figures():
        for d in f["decorations"]:
            c = d["color_resource"]
            cref[c] = cref.get(c, 0) + 1
            if not (0 <= c < len(res)) or res[c][0] not in COLOR_TAGS:
                bad.append(f"図形の装飾の色リソース番号 {c} が色を指していません")
    return gref, cref, bad


def check_file(data, rebuild=True):
    """.ts1 単体の検査 → [(重さ, 説明)]"""
    out = []
    try:
        from structure import dump
        dump(data)
    except Exception as ex:
        return [(NG, f"構造を最後まで読めません（{ex}）")]
    try:
        t = TS1(data)
    except Exception as ex:
        return [(NG, f"文字の要素を読めません（{ex}）")]
    res = t.resources
    # 中身を読めない図形（読める図形からの参照は _refs が数える）
    simple = {f["index"] for f in t.figures() if f["simple"]}
    figures = [e for i, e in enumerate(t.elements) if e[0] not in ("CSML", "CSSL") and i not in simple]
    gref, cref, bad = _refs(t)
    out += [(NG, m) for m in bad]
    # 使用回数
    for i, (tag, r) in enumerate(res):
        if tag == "RCHR":
            use, ref, name = r.usage, gref.get(i, 0), f"字形「{r.char or '終端'}」（番号 {i}）"
        elif tag in COLOR_TAGS:
            use, ref, name = u16(r, 10), cref.get(i, 0), f"色 {tag}（番号 {i}）"
        elif tag == "ORSC":
            if gref.get(i) or cref.get(i):
                out.append((NG, f"空き枠（番号 {i}）が字か装飾から指されています"))
            continue
        else:
            continue
        if use == ref:
            continue
        if figures and tag in COLOR_TAGS and use > ref:
            continue          # 図形から指されているぶん（図形の中身は読めないので数えられない）
        out.append((NG, f"{name} の使用回数が {use} ですが、指している所は {ref} か所です"))
    if len(res) % 10:
        out.append((WARN, f"リソースの数が {len(res)} 個です（VWS が作るファイルは 10 個単位）"))
    # 字形のヘッダ
    for i, (tag, g) in enumerate(res):
        if tag != "RCHR" or not g.header_raw or len(g.header_raw) < 44:
            continue
        h = g.header_raw
        if u16(h, 0) != len(g.contours):
            out.append((NG, f"字形「{g.char or '終端'}」のヘッダの輪郭数 {u16(h, 0)} が、輪郭 {len(g.contours)} 個と合いません"))
        if u32(h, 32) != g.code or u32(h, 28) != g.font_id:
            out.append((NG, f"字形「{g.char or '終端'}」のヘッダの文字コード・フォント番号が、リソースの値と合いません"))
    # 1 行もの: 行の幅
    if t.kind == "CSSL":
        try:
            w = t.line_width()
            if abs(w - t.box[0]) > 1.5 / FIX:
                out.append((NG, f"行の幅が {t.box[0]:g}px と書かれていますが、字送りの合計は {w:g}px です"))
        except Exception as ex:
            out.append((NG, f"字の並びを計算できません（{ex}）"))
    # 書き直しても同じになるか（読み落としている所があれば、ここで差が出る）
    if rebuild:
        try:
            if t.rebuild() != data:
                out.append((NG, "読んでそのまま書き直すと、元と違うファイルになります（読めていない所があります）"))
        except Exception as ex:
            out.append((NG, f"書き直せません（{ex}）"))
    return out


def _explicit_style(t, ch):
    """その字が自分で持っている書式（字間 ACSP を除く）。装飾の色は番号ではなく中身で比べる"""
    sig = []
    for tag in ch.attr_order:
        if tag == "ACSP":
            continue
        raw = ch.attrs[tag]
        if tag == "ADCO":
            a = bytearray(raw)
            colors = []
            for d in t._decode_adco(raw):
                c = d["color_resource"]
                colors.append(_norm_res(*t.resources[c]) if 0 <= c < len(t.resources) and t.resources[c][0] != "RCHR" else ("?", c))
                struct.pack_into("<I", a, d["offset"] + 4, 0)
            sig.append((tag, bytes(a), tuple(colors)))
        else:
            sig.append((tag, raw))
    return tuple(sig), ch.pre_raw[18:22]


def local_trace(template, out, clear_history=False):
    """この PC で作業した記録が .ts1 に入っていないか → [(重さ, 説明)]。
    ユーザーの決まり（2026-11-14）: 型の編集履歴は残す。こちらの作業の記録（日時・ユーザー名・端末名）は足さない。
      ・編集履歴（OMR0）が型と 1 バイトも違わない（clear_history のときは 0 件になっている）
      ・保存時刻（SWRK の saved）が型のまま
      ・この PC のユーザー名・コンピュータ名が、型より多く出てこない"""
    import getpass
    import os
    import socket
    from structure import dump
    msgs = []
    f0, f1 = dump(template), dump(out)
    h0 = [f.raw for f in f0 if "OMR0" in f.path]
    h1 = [f.raw for f in f1 if "OMR0" in f.path]
    if clear_history:
        if len(h1) > 2:
            msgs.append((NG, "編集履歴を消す設定なのに、履歴が残っています"))
    elif h0 != h1:
        msgs.append((NG, f"編集履歴が型と違います（型 {len(h0) - 2} 件 → {len(h1) - 2} 件、または中身が違う）。こちらの作業の記録は足さない決まりです"))
    s0 = next((f.raw for f in f0 if f.path == "SWRK" and f.name == "saved"), None)
    s1 = next((f.raw for f in f1 if f.path == "SWRK" and f.name == "saved"), None)
    if s0 != s1:
        msgs.append((NG, "保存時刻が型と違います（こちらの作業の日時は書かない決まりです）"))
    names = set()
    for get in (getpass.getuser, socket.gethostname, lambda: os.environ.get("COMPUTERNAME", ""), lambda: os.environ.get("USERNAME", "")):
        try:
            n = get()
        except Exception:
            n = ""
        if n and len(n) >= 3:
            names.add(n.lower())

    def count(b):
        low = b.lower()
        tot = 0
        for n in names:
            for enc in ("ascii", "utf-16-le", "cp932"):
                try:
                    tot += low.count(n.encode(enc))
                except UnicodeEncodeError:
                    pass
        return tot
    if count(out) > count(template):
        msgs.append((NG, "この PC のユーザー名かコンピュータ名が .ts1 に入っています"))
    return msgs


def check_figure_resize(before, after, index):
    """図形（座布団）の幅を変える前後を突き合わせる → [(重さ, 説明)]。
    変わってよいのは、その図形の左位置（とトランジションの x）・幅だけ"""
    from structure import dump
    msgs = check_file(after)
    if len(before) != len(after):
        return msgs + [(NG, "図形の幅を変えたらファイルの長さが変わりました")]
    f0 = {f"{f.path}.{f.name}": f for f in dump(before)}
    f1 = {f"{f.path}.{f.name}": f for f in dump(after)}
    path = f"SWRK/SLYR[0]/CGRP[{index}]"
    allowed = {path + ".x", path + "/MFIG[0].w"} | {f"{path}/XTRS[{n}].{nm}" for n in range(8) for nm in ("x0", "x1")}
    for key in f0:
        if key in allowed:
            continue
        b = f1.get(key)
        if b is None or f0[key].raw != b.raw:
            msgs.append((NG, f"図形の幅のほかの所が変わっています: {key}"))
    msgs += local_trace(before, after)
    fa = next((f for f in TS1(after).figures() if f["index"] == index), None)
    if not fa or not fa["simple"] or fa["w"] <= 0:
        msgs.append((NG, "幅を変えたあとの図形を読めません"))
    return msgs


def check_retype(template, out, text=None, clear_history=False, plain_spacing=None, element=0):
    """型（template）と打ち替え後（out）を突き合わせる → [(重さ, 説明)]。text = 入れたはずの文言（| の区切りは除いたもの）。
    element = 打ち替えた文字要素が何番目か（0 始まり。ほかの要素は 1 バイトも変わらないはず）"""
    from structure import dump
    msgs = check_file(out)
    if any(lv == NG and "読めません" in m for lv, m in msgs):
        return msgs
    t0, t1 = TS1(template, element), TS1(out, element)
    # ---- 文言
    if text is not None:
        want = text.replace("\r\n", "\n").replace("\r", "\n")
        got = "".join("\n" if ch.code in (0x0A, 0x0D) else ch.char for ch in t1.chars if ch.code)
        if got != want:
            msgs.append((NG, f"入っている文言が「{got}」です（入れたはずの文言は「{want}」）"))
    if not t1.chars or t1.chars[-1].code != 0:
        msgs.append((NG, "字の並びの最後に終端がありません"))
    msgs += local_trace(template, out, clear_history)
    # ---- 項目ごとの突き合わせ（ORMP の中身と、主の文字要素の字の並びは別に見る）
    f0 = {f"{f.path}.{f.name}": f for f in dump(template)}
    f1 = {f"{f.path}.{f.name}": f for f in dump(out)}
    import re as _re
    mains = [f.path for f in dump(template) if f.name == "(tag+size)" and _re.fullmatch(r"SWRK/SLYR\[0\]/CSML\[\d+\]", f.path)]
    main = mains[element]
    single = t0.kind == "CSSL"
    allowed = {"TSRC.(tag+size)", "SWRK.(tag+size)", "SWRK/ORMP.(tag+size)", "SWRK/ORMP.count",
               main.rsplit("/", 1)[0] + ".(tag+size)", main + ".(tag+size)", main + "/chars.ESET",
               "SWRK/OFRI.left", "SWRK/OFRI.top", "SWRK/OFRI.right", "SWRK/OFRI.bottom"}
    if single:           # 1 行もの: 中心を保ったまま、行の幅と左位置が文言に合わせて変わる
        allowed |= {main + ".x", main + "/CSTR[0].line_w"}
        allowed |= {f"{main}/XTRS[{k}].{n}" for k in range(8) for n in ("x0", "x1")}

    def skip(k):
        return "/ORMP/" in k or k.startswith(main + "/MCHR[") or (clear_history and "OMR0" in k)

    diffs = []
    for k in list(f0) + [k for k in f1 if k not in f0]:
        if skip(k) or k in allowed:
            continue
        a, b = f0.get(k), f1.get(k)
        if a is None or b is None or a.raw != b.raw:
            diffs.append(f"{k}（{(a or b).note or '説明なし'}）: {a.text() if a else '無い'} → {b.text() if b else '無い'}")
    for d in diffs[:8]:
        msgs.append((NG, "文字のほかの所が型と違います: " + d))
    if len(diffs) > 8:
        msgs.append((NG, f"ほかにも型と違う所が {len(diffs) - 8} か所あります"))
    # ---- 位置
    if single:
        c0, c1 = t0.pos[0] + t0.box[0] / 2, t1.pos[0] + t1.box[0] / 2
        if abs(c0 - c1) > 2 / FIX or t0.pos[1] != t1.pos[1]:
            msgs.append((NG, f"文字の中心が動いています（{c0:g}, {t0.pos[1]:g}）→（{c1:g}, {t1.pos[1]:g}）"))
    elif t0.pos != t1.pos or t0.box != t1.box:
        msgs.append((NG, f"文字枠が動いています {t0.pos} {t0.box} → {t1.pos} {t1.box}"))
    # ---- 色・グラデーション・模様: 並びも中身も型と同じ（使用回数の欄は除く）
    c0 = [_norm_res(tg, r) for tg, r in t0.resources if tg in COLOR_TAGS or tg not in ("RCHR", "ORSC")]
    c1 = [_norm_res(tg, r) for tg, r in t1.resources if tg in COLOR_TAGS or tg not in ("RCHR", "ORSC")]
    if c0 != c1:
        msgs.append((NG, f"色・グラデーション・模様のリソースが型と違います（型 {len(c0)} 個 → {len(c1)} 個、または中身が違う）"))
    if len(t0.elements) > 1:      # 2 つめの文字・図形は番号でリソースを指している → 番号が動いてはいけない
        moved = [i for i, (tg, r) in enumerate(t0.resources)
                 if tg in COLOR_TAGS and (i >= len(t1.resources) or _norm_res(*t1.resources[i]) != _norm_res(tg, r))]
        if moved:
            msgs.append((NG, f"色のリソースの番号が動いています（番号 {moved[:5]}）。図形や 2 つめの文字が別の色を指してしまいます"))
    if len(t0.elements) > 1:      # 図形から指されているぶん（使用回数 − 文字からの参照）は、型と同じはず
        _g0, cr0, _b0 = _refs(t0)
        _g1, cr1, _b1 = _refs(t1)
        for i, (tg, r) in enumerate(t0.resources):
            if tg in COLOR_TAGS and i < len(t1.resources) and t1.resources[i][0] == tg:
                x0, x1 = u16(r, 10) - cr0.get(i, 0), u16(t1.resources[i][1], 10) - cr1.get(i, 0)
                if x0 != x1:
                    msgs.append((NG, f"色 {tg}（番号 {i}）の使用回数が合いません（文字のほかから指されている数が 型 {x0} → {x1}）"))
    # ---- 字形: 型にある字は 1 バイトも変えない。新しい字は型の字と同じ作りか
    g0 = {(g.code, g.font_id): g for g in t0.glyphs}
    signs = [(_area(g.contours) > 0) for g in t0.glyphs if g.contours]
    sign = (sum(signs) * 2 >= len(signs)) if signs else None
    new = []
    for g in t1.glyphs:
        o = g0.get((g.code, g.font_id))
        if o is not None:
            if o.header_raw != g.header_raw or o.encode_contours() != g.encode_contours():
                msgs.append((NG, f"型にある字「{g.char or '終端'}」の字形が変わっています"))
            continue
        new.append(g)
        name = f"新しい字「{g.char}」"
        fonts = {x.font_id for x in t0.glyphs}
        if g.font_id not in fonts:
            msgs.append((NG, f"{name} のフォント番号 {g.font_id} は型に無い番号です"))
        ref = next((x for x in t0.glyphs if x.font_id == g.font_id and x.contours), None)
        if ref is not None and (g.ascent, g.descent) != (ref.ascent, ref.descent):
            msgs.append((NG, f"{name} のアセント / ディセントが型の字と違います（{g.ascent}/{g.descent} と {ref.ascent}/{ref.descent}）"))
        if g.contours:
            if sign is not None and (_area(g.contours) > 0) != sign:
                msgs.append((WARN, f"{name} の輪郭の向きが型の字と逆です"))
            xs = [p[0] for c in g.contours for p in [c.start] + [q for _, ps in c.segs for q in ps]]
            ys = [p[1] for c in g.contours for p in [c.start] + [q for _, ps in c.segs for q in ps]]
            if min(xs) < -CELL or max(xs) > 2 * CELL or min(ys) < -2 * CELL or max(ys) > CELL:
                msgs.append((NG, f"{name} の字形がマス目から大きく外れています"))
        elif g.code not in (0x20, 0x3000, 0x0A, 0x0D) and not (0xE000 <= g.code <= 0xF8FF):
            msgs.append((NG, f"{name} の字形が空です（そのフォントに無い字かもしれません）"))
        if g.header_raw and len(g.header_raw) >= 56:
            a, b, c = struct.unpack_from("<iii", g.header_raw, 44)
            if not (0 < a + b + c <= 2 * CELL):
                msgs.append((NG, f"{name} の字幅が {a + b + c} です（マス目は {CELL}）"))
    # ---- 書式のまとまり
    r0, r1 = t0.style_runs(), t1.style_runs()
    if len(r0) != len(r1):
        msgs.append((WARN if len(r1) < len(r0) else NG,
                     f"書式のまとまりの数が型と違います（型 {len(r0)} → {len(r1)}）。色や大きさの変わり目を確かめてください"))
    else:
        for j, (a, b) in enumerate(zip(r0, r1)):
            if _explicit_style(t0, t0.chars[a[0]]) != _explicit_style(t1, t1.chars[b[0]]):
                msgs.append((NG, f"{j + 1} つめのまとまりの書式（装飾・大きさ・書体など）が型と違います"))
    # 終端の字: 型と同じ書式を持っているか（VWS は終端にも字間や倍率を書く）
    e0, e1 = t0.chars[-1], t1.chars[-1]
    if e0.code == 0 and e1.code == 0:
        if e0.pre_raw != e1.pre_raw:
            msgs.append((WARN, "終端の字の前置きの値が型と違います"))
        if [(tg, e0.attrs[tg]) for tg in e0.attr_order if tg != "ADCO"] != [(tg, e1.attrs[tg]) for tg in e1.attr_order if tg != "ADCO"]:
            msgs.append((WARN, "終端の字の書式が型と違います"))
    return msgs


def screen_overflow(bbox, old_bbox=None, screen=SCREEN, safe=0.9):
    """描画範囲 bbox =（左, 上, 右, 下）が画面の外・安全枠（画面の 90%）の外に出ていないか → [説明]。
    old_bbox（型の描画範囲）がすでに出ている辺は言わない（その型はもともとそういう作り）"""
    if not bbox:
        return []
    l, tp, r, b = bbox
    w, h = screen
    out = []
    old = old_bbox if old_bbox else (0, 0, w, h)
    over = []
    if l < 0 and old[0] >= 0:
        over.append(f"左に {-l:g}px")
    if r > w and old[2] <= w:
        over.append(f"右に {r - w:g}px")
    if tp < 0 and old[1] >= 0:
        over.append(f"上に {-tp:g}px")
    if b > h and old[3] <= h:
        over.append(f"下に {b - h:g}px")
    if over:
        out.append(f"画面（{w}×{h}）の外にはみ出します: {'・'.join(over)}（描画範囲 左 {l:g} 〜 右 {r:g}）")
        return out
    mx, my = w * (1 - safe) / 2, h * (1 - safe) / 2
    edge = []
    if l < mx <= old[0]:
        edge.append(f"左（{l:g}px）")
    if r > w - mx >= old[2]:
        edge.append(f"右（{r:g}px）")
    if edge:
        out.append(f"画面の端に近づいています（安全枠 {mx:g}〜{w - mx:g}px の外）: {'・'.join(edge)}")
    return out


def summary(msgs):
    """検査結果を 1 行に（結果の一覧の欄に書く用）"""
    if not msgs:
        return "OK"
    ng = [m for lv, m in msgs if lv == NG]
    wn = [m for lv, m in msgs if lv == WARN]
    return ("NG: " + " / ".join(ng) if ng else "") + (" " if ng and wn else "") + ("注意: " + " / ".join(wn) if wn else "")


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        with open(p, "rb") as f:
            r = check_file(f.read())
        print(p, summary(r))
