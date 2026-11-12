#!/usr/bin/env python3
"""VWSフォトショブリッジツール — ドラッグ＆ドロップ用の入口（設定.json を読んで変換する）

  python run.py to-ps  素材.ts1 ...   → 素材_再現.png / 素材_画像レイヤ.psd / 素材_Photoshop用.jsx
  python run.py to-ts1 テロップ.psd ... → テロップ.ts1 / テロップ_確認.png
  python run.py info   素材.ts1 ...   → 文字・色・縁・サイズなどを表示、全項目を 素材_構造.txt に保存
  python run.py diff   A.ts1 B.ts1    → 2 つの違いを項目単位で表示（A_差分.txt に保存）
  python run.py identify 素材.ts1     → 字形を PC の全フォントと照合してフォントを特定、フォント表.json に登録
                                        （to-ps / text / info でも自動で特定する。これは結果を詳しく見たいとき・候補を出したいとき用）
  python run.py font-name 番号 名前    → フォント番号に名前を手で登録（PC に無いフォントを VWS の画面で確かめたとき）
  python run.py text   素材.ts1 [新しい文言] → 書式はそのまま文言だけ打ち替え（素材_打ち替え.ts1 / _確認.png）。文言を省くと入力を求める
"""
import json
import logging
import os
import sys
import traceback

# 古いフォントを読むと fontTools が「'modified' timestamp seems very low」を大量に出すので黙らせる
logging.getLogger("fontTools").setLevel(logging.ERROR)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = os.path.dirname(HERE)  # ts1lab の 1 つ上（設定.json がある場所）

from ts1core import TS1, FontGlyphSource  # noqa: E402


def load_config():
    for p in (os.path.join(ROOT, "設定.json"), os.path.join(HERE, "設定.json")):
        if os.path.exists(p):
            with open(p, encoding="utf-8-sig") as f:
                cfg = json.load(f)
            cfg["_dir"] = os.path.dirname(p)
            return cfg
    return {"_dir": ROOT}


def resolve(cfg, p):
    if not p:
        return None
    return p if os.path.isabs(p) else os.path.join(cfg["_dir"], p)


# ---- フォント表（VWS のフォント番号 ⇄ PC のフォント） --------------------------
def table_path(cfg):
    return os.path.join(cfg["_dir"], "フォント表.json")


# これまでに確定した内蔵フォント番号（Windows の字形と 1 点残らず一致したもの）。フォント表.json に無くても名前が出るように持っておく
KNOWN_FONTS = {
    "1119": {"name": "ニューロダン B", "face": "FOT-ニューロダン Pro B", "postscript": "NewRodinPro-B", "weight": 700,
             "hints": [["ニューロダン", "B"], ["NewRodin", "B"]]},
    "1121": {"name": "ニューロダン EB", "face": "FOT-ニューロダン Pro EB", "postscript": "NewRodinPro-EB", "weight": 800,
             "hints": [["ニューロダン", "EB"], ["NewRodin", "EB"]]},
}


def load_table(cfg):
    p = table_path(cfg)
    tbl = {}
    if os.path.exists(p):
        with open(p, encoding="utf-8-sig") as f:
            tbl = json.load(f)
    for k, v in KNOWN_FONTS.items():
        e = tbl.setdefault(k, {})
        for kk, vv in v.items():
            e.setdefault(kk, vv)
    return tbl


def save_table(cfg, tbl):
    with open(table_path(cfg), "w", encoding="utf-8") as f:
        json.dump(tbl, f, ensure_ascii=False, indent=1)


def _resolve_entry(cfg, fid, e, ts1=None):
    """フォント表の 1 件について、PC 上のフォントファイルを確定する（無ければヒント語で探して表に書き戻す）。
    ts1 がそのフォントの字形を持っていれば、VWS 座標への倍率・オフセットも実測して保存する"""
    import fontfind
    changed = False
    if not (e.get("file") and os.path.exists(e["file"])):
        e.pop("file", None)
        cache = os.path.join(cfg["_dir"], ".font_cache.json")
        r = fontfind.find(e.get("hints", []), postscript=e.get("postscript"), cache_path=cache)
        if r:
            e["file"], e["index"] = r[0], r[1]
            e["names"] = list(e.get("names", [])) + [n for n in r[2] if n not in e.get("names", [])]
            e["postscript"] = fontfind.postscript_name(r[0], r[1]) or e.get("postscript")
            for k in ("scale", "ox", "oy", "rms", "score"):
                e.pop(k, None)
            changed = True
    if e.get("file") and "scale" not in e and ts1 is not None and str(ts1.font_id) == str(fid):
        try:
            import fontid
            score, fit = fontid.fit_font(ts1, e["file"], int(e.get("index", 0)))
            if fit and score >= 0.95:
                e.update({k: round(v, 6) for k, v in fit.items()})
                e["score"] = round(score, 4)
                changed = True
            elif fit:
                print(f"  ※ 「{e.get('name')}」として見つけたフォントは字形の一致度が {score * 100:.0f}% です（別ウェイト・別版の可能性）")
        except Exception:
            pass
    if changed:
        tbl = load_table(cfg)
        tbl[str(fid)] = e
        save_table(cfg, tbl)
    return e


def font_ids(ts1):
    """.ts1 の中で使われているフォント番号（字形のあるもの。無ければ先頭文字の番号）"""
    ids = []
    for g in ts1.glyphs:
        if g.code and g.contours and g.font_id not in ids:
            ids.append(g.font_id)
    return ids or [ts1.font_id]


def resolve_font(cfg, fid, ts1, force=False):
    """.ts1 のフォント番号 fid が何のフォントかを決めて、フォント表に書き込む。エントリ（dict）を返す（何も分からなければ None）。

    分かり方は 3 通り（上から順に確かめる）:
      1. ファイルの中のフォント表（ODTT）に名前が書いてある
      2. Windows に入っている全フォント・全部の太さを字形データにして、.ts1 の中の字形と 1 点残らず一致するものを探す
         （全部調べても 1 秒ほど。一致は 1 つに決まる = 確定）
      3. フォント表.json / KNOWN_FONTS に名前が登録済み
    2 が見つからなかった結果も「入っているフォントの一覧の指紋」と一緒に覚えておき、フォントを入れ直すまでは探し直さない"""
    fid = int(fid)
    key = str(fid)
    tbl = load_table(cfg)
    e = dict(tbl.get(key, {}))
    before = json.dumps(e, sort_keys=True, ensure_ascii=False)
    names = list(e.get("names", []))
    odtt = next((f for f in getattr(ts1, "fonts", []) if f["font_id"] == fid), None) if ts1 is not None else None
    if odtt:
        e.setdefault("name", odtt["name"])
        e.setdefault("postscript", odtt["postscript"])
        e.setdefault("weight", odtt["weight"])
        e["in_file"] = True          # 名前が .ts1 の中に書いてある
        names += [odtt["name"], odtt["postscript"]]
    try:
        import gdiglyph
        if ts1 is not None and gdiglyph.available() and gdiglyph.ref_glyphs(ts1, fid):
            sig = gdiglyph.installed_signature()
            if force or e.get("exact") is not False or e.get("scan_sig") != sig:
                # 前に確定したフォント（と、そのとき区別できなかった同じ形のフォント）だけを先に確かめ直す。
                # 文言が違えば区別できる字が入っていることがあるので、候補は .ts1 を読むたびに絞られていく
                prev = ([e["face"]] + list(e.get("same_shape", []))) if (e.get("exact") and e.get("face")) else []
                hits = gdiglyph.identify(ts1, fid, only=prev) if (prev and not force) else []
                narrowed = bool(hits)
                if not hits:
                    hits = gdiglyph.identify(ts1, fid, faces=[e.get("face")] + names)     # 全フォントを調べる
                    if prev and force:      # 調べ直しでも、前の候補に入っていなかったものは増やさない
                        keep = [h for h in hits if h["face"] in prev]
                        hits = keep or hits
                if hits:
                    # 同じ字形のフォントが複数あるとき（Pro と ProN など、この文言の字では区別がつかない場合）は、
                    # 名前の手がかり（ファイルの中の名前・登録済みの名前・前に選んだもの）に合うものを優先する
                    known = [n for n in [e.get("face"), e.get("postscript"), e.get("name")] + names if n]

                    def rank(h):
                        return 0 if (h["face"] in known or h.get("postscript") in known) else 1
                    hits.sort(key=rank)
                    h = hits[0]
                    e.update({"face": h["face"], "weight": h["weight"], "italic": h["italic"], "charset": h["charset"],
                              "y_shift": h["y_shift"], "exact": True})
                    if h.get("postscript"):
                        e["postscript"] = h["postscript"]
                    if "name" not in e or e.get("name_auto"):     # 名前が未登録なら書体名を名前にする（候補が絞られたら付け直す）
                        e["name"], e["name_auto"] = h["face"], True
                    names += [h["face"], h.get("full"), h.get("family"), h.get("postscript")]
                    same = []
                    for x in hits[1:]:
                        if x["face"] != h["face"] and x["face"] not in same:
                            same.append(x["face"])
                    if same:
                        e["same_shape"] = same
                    else:
                        e.pop("same_shape", None)
                    e.pop("scan_sig", None)
                else:
                    if e.get("exact"):       # 前は入っていたフォントが無くなった
                        for k in ("y_shift", "italic", "charset"):
                            e.pop(k, None)
                    e["exact"] = False
                    e["scan_sig"] = sig
    except Exception as ex:
        print(f"  ※ Windows のフォントとの照合はできませんでした（{ex}）")
    if not e:
        return None
    e["names"] = [n for i, n in enumerate(names) if n and n not in names[:i]]
    if json.dumps(e, sort_keys=True, ensure_ascii=False) != before:
        tbl[key] = e
        save_table(cfg, tbl)
    return e


def font_status(e):
    """フォント表のエントリ → (名前, どこまで確かか) の説明"""
    if not e:
        return "不明", "この PC のフォントに一致するものが無く、名前も登録されていません"
    name = e.get("name") or e.get("face") or e.get("postscript") or "不明"
    if e.get("exact"):
        if e.get("same_shape"):
            return name, (f"候補が {len(e['same_shape']) + 1} つ:「{e.get('face')}」「{'」「'.join(e['same_shape'])}」。"
                          "この文言の字ではどれも 1 点残らず同じ形で、区別できません（別の文言の .ts1 を読むと絞られます）")
        return name, f"確定: Windows の「{e.get('face')}」の字形と 1 点残らず一致"
    if e.get("in_file"):
        return name, "名前は .ts1 の中に書かれています。この PC には入っていません"
    if e.get("score"):
        return name, f"候補: 形の一致 {e['score'] * 100:.1f}%（完全一致するフォントはこの PC にありません）"
    return name, "名前は登録済み。この PC には入っていません（字形の照合はできていません）"


def describe_fonts(cfg, ts1, force=False):
    """.ts1 のフォントを全部特定して表示する。{番号: エントリ} を返す"""
    out = {}
    for fid in font_ids(ts1):
        e = resolve_font(cfg, fid, ts1, force=force)
        name, how = font_status(e)
        print(f"  フォント: {name}（VWS 番号 {fid}。{how}）")
        out[fid] = e
    return out


def font_for_id(cfg, font_id, ts1=None):
    """VWS フォント番号 → フォント表のエントリ（file が入っていれば PC にフォントあり）。分からなければ None。
    ts1 を渡すと、その場でフォントを特定してフォント表に書き込む"""
    if ts1 is not None:
        resolve_font(cfg, font_id, ts1)
    e = load_table(cfg).get(str(font_id))
    return _resolve_entry(cfg, font_id, e, ts1) if e else None


def font_for_ps(cfg, ps_name, ts1=None):
    """Photoshop のフォント (PostScript 名) → フォント表の (VWS 番号, エントリ)"""
    import fontfind
    if not ps_name:
        return None
    for fid, e in load_table(cfg).items():
        names = [e.get("postscript"), e.get("face"), (e.get("substitute") or {}).get("postscript"),
                 (e.get("substitute") or {}).get("face")] + list(e.get("names", []))
        hit = any(n and fontfind.norm(n) == fontfind.norm(ps_name) for n in names)
        hit = hit or any(fontfind.hint_match(ps_name, h) for h in e.get("hints", []))
        if hit:
            return int(fid), _resolve_entry(cfg, fid, e, ts1)
    return None


def _fit_of(e):
    return {k: e[k] for k in ("scale", "ox", "oy")} if e and "scale" in e else None


def _baseline_fit(e):
    """Photoshop のテキストの縦位置合わせに使う値。フォントファイルから実測した値が無ければ、縦位置のずれ y_shift から作る。
    PC に無いフォントで代わりのフォントを使っているときは、代わりのフォントで実測した値"""
    f = _fit_of(e)
    if f is None and e and e.get("y_shift") is not None:
        f = {"oy": -(float(e["y_shift"]) + 0.26)}
    if f is None and e and (e.get("substitute") or {}).get("oy") is not None:
        f = {"oy": float(e["substitute"]["oy"])}
    return f


def _family_key(name):
    """書体名から版の違い（Pro / ProN / Std / StdN / Pr5 / Pr6N など）を除いたもの。兄弟フォントを探すのに使う"""
    import re
    return re.sub(r"\s+", " ", re.sub(r"\s*\b(Pr6N|Pr5N|Pr6|Pr5|ProN|Pro|StdN|Std)\b", "", name, flags=re.I)).strip().lower()


def substitute_font(cfg, fid, e, ts1=None):
    """PC に入っていないフォントの代わりに、Photoshop の編集用テキストで使う兄弟フォント（版だけ違う同じ書体）を探す。
    見つかれば {"face", "postscript", "oy"} をフォント表の substitute に書いて返す。字の形は近いが点までは一致しない
    （見た目の正確さは PSD の画像レイヤが受け持つ。編集用テキストの組みと位置を近づけるためのもの）"""
    if not e or e.get("exact"):
        return None
    try:
        import gdiglyph
        if not gdiglyph.available():
            return None
        keys = {_family_key(n) for n in [e.get("name")] + list(e.get("names", [])) if n}
        hit = None
        for face, w, it, cs in gdiglyph.list_styles():
            if _family_key(face) in keys:
                with gdiglyph.GdiFont(face, w, cs, it) as f:
                    nm = f.names()
                hit = {"face": face, "postscript": nm.get("postscript")}
                break
        if not hit or not hit["postscript"]:
            return None
        if ts1 is not None:      # 縦位置のずれを、代わりのフォントの輪郭を重ねて実測する
            try:
                import fontfind
                import fontid
                r = fontfind.find([], postscript=hit["postscript"], cache_path=os.path.join(cfg["_dir"], ".font_cache.json"))
                if r:
                    score, fit = fontid.fit_font(ts1, r[0], r[1])
                    if fit and score >= 0.85:
                        hit["oy"] = round(fit["oy"], 3)
                        hit["score"] = round(score, 4)
            except Exception:
                pass
        if e.get("substitute") != hit:
            e["substitute"] = hit
            tbl = load_table(cfg)
            tbl[str(fid)] = {**tbl.get(str(fid), {}), "substitute": hit}
            save_table(cfg, tbl)
        return hit
    except Exception:
        return None


def glyph_source(cfg, fid, e, ts1=None, quiet=False):
    """新しい字形を作る元を返す。
    Windows にそのフォントが入っていれば GDI から作る（VWS が作る字形データとバイト単位で同じになる）。
    入っていなければフォントファイルから作る（近似。点の位置が VWS と少し違う）。どちらも無ければ None。
    ts1 = そのフォントの字形を持つ .ts1（その場で照合して、1 点残らず一致するときだけ GDI を使う）"""
    if not e:
        return None
    try:
        import gdiglyph
        if gdiglyph.available():
            if ts1 is not None and gdiglyph.ref_glyphs(ts1, int(fid)):
                e = resolve_font(cfg, fid, ts1) or e
            if e.get("exact") and e.get("face") and e.get("y_shift") is not None:
                src = gdiglyph.GdiGlyphSource(e["face"], int(e["y_shift"]), int(e.get("weight", 0)),
                                              int(e.get("charset", gdiglyph.SHIFTJIS_CHARSET)), bool(e.get("italic")))
                if not quiet:
                    print(f"  字形の作り方: Windows の「{e['face']}」から VWS と同じ方法で作成")
                return src
    except Exception as ex:
        if not quiet:
            print(f"  ※ Windows からの字形作成は使えませんでした（{ex}）")
    if e.get("file"):
        if not quiet:
            print("  ※ 字形はフォントファイルから作ります（VWS の字形と点の位置が少し違います）")
        return FontGlyphSource(e["file"], int(e.get("index", 0)), fit=_fit_of(e))
    return None


def find_font(cfg, ps_name=None):
    """設定のフォントファイル → PostScript 名 → 名前のヒント語 の順で探す"""
    fp = resolve(cfg, cfg.get("font_file"))
    if fp and os.path.exists(fp):
        return fp, int(cfg.get("font_index", 0))
    import fontfind
    cache = os.path.join(cfg["_dir"], ".font_cache.json")
    r = fontfind.find(cfg.get("font_name_hints", []), postscript=ps_name or cfg.get("photoshop_font"),
                      cache_path=cache)
    if r:
        print(f"  フォント: {r[2][0] if r[2] else ''}  ({r[0]})")
        return r[0], r[1]
    return None, 0


def identify(cfg, path):
    """.ts1 のフォントを特定してフォント表に登録する。
    まず Windows の全フォントと字形を 1 点残らず照合する（1 秒ほど）。一致が無いときだけ、形の近さで候補を出す（数分）"""
    with open(path, "rb") as f:
        t = TS1(f.read())
    print(f"  文言: {t.text}")
    fonts = describe_fonts(cfg, t, force=True)
    for fid, e in fonts.items():
        if e and e.get("exact"):
            mark = "☆ 候補" if e.get("same_shape") else "★"
            print(f"  {mark} VWS フォント番号 {fid} = 「{e.get('name')}」（PostScript 名: {e.get('postscript')}、太さ {e.get('weight')}）")
            continue
        if e and e.get("in_file"):
            print(f"  ★ VWS フォント番号 {fid} = 「{e.get('name')}」（.ts1 の中のフォント表より。PostScript 名: {e.get('postscript')}）")
            print("     この PC には入っていないので、新しい字は作れません（元の .ts1 にある字だけで組めます）")
            continue
        _identify_by_shape(cfg, t, fid)


def _identify_by_shape(cfg, t, fid):
    """完全一致するフォントが PC に無いとき: 形の近さで候補を出す（別の版・別の太さが入っている場合の手がかり）"""
    import fontid
    import fontfind
    if fid != t.font_id:
        print(f"  ※ フォント番号 {fid}: 完全一致するフォントがこの PC にありません")
        return
    print(f"  フォント番号 {fid}: 完全一致するフォントがこの PC に無いので、形の近いフォントを探します…（数分かかります）")
    cache = os.path.join(cfg["_dir"], ".font_cache.json")
    if os.path.exists(cache):
        os.remove(cache)  # 毎回最新のフォント一覧で照合
    res = fontid.identify(t, cache_path=cache,
                          progress=lambda k, n: print(f"\r    {k}/{n}", end="", flush=True))
    print("\r" + " " * 20)
    if not res:
        print("  照合できるフォントがありませんでした")
        return
    print("  形の一致  フォント")
    for score, names, fp, idx, fit in res[:8]:
        print(f"   {score * 100:5.1f}%  {' / '.join(names[:3])}   [{os.path.basename(fp)}]")
    score, names, fp, idx, fit = res[0]
    tbl = load_table(cfg)
    old = tbl.get(str(fid), {})
    if score >= 0.95:
        ps = fontfind.postscript_name(fp, idx)
        jp = [n for n in names if any(ord(ch) > 0x2E80 for ch in n)]
        name = old.get("name") or (jp or names or [ps])[0]
        tbl[str(fid)] = {**old, "name": name, "names": names, "postscript": ps, "file": fp, "index": idx,
                         "score": round(score, 4), **({k: round(v, 6) for k, v in fit.items()} if fit else {})}
        save_table(cfg, tbl)
        print(f"\n  ☆ 候補: VWS フォント番号 {fid} ≒ 「{name}」（形の一致 {score * 100:.1f}%。点までは一致しないので、版か太さが違う可能性があります）")
        print("     フォント表.json に候補として登録しました")
    else:
        print(f"\n  ※ このフォントはこの PC に入っていません（一番近いものでも {score * 100:.1f}%）")
        print("     VWS の画面でフォント名を確かめたら、次のように登録できます:")
        print(f"       python run.py font-name {fid} フォント名")
        print("     そのフォントを PC に入れてからもう一度実行すれば、自動で確定します")


def set_font_name(cfg, fid, name):
    """フォント番号に名前を手で登録する"""
    tbl = load_table(cfg)
    e = tbl.setdefault(str(int(fid, 0) if isinstance(fid, str) else int(fid)), {})
    e["name"] = name
    e.pop("name_auto", None)
    e.setdefault("hints", [[name]])
    e.pop("scan_sig", None)      # 次に .ts1 を読んだとき、この名前を手がかりにもう一度探す
    save_table(cfg, tbl)
    print(f"  VWS フォント番号 {fid} = 「{name}」を フォント表.json に登録しました")


def to_ps(cfg, path):
    from psdbridge import ts1_to_png, ts1_to_psd, ts1_to_jsx
    base = os.path.splitext(path)[0]
    name = os.path.basename(base)
    with open(path, "rb") as f:
        t = TS1(f.read())
    info = t.info()
    print(f"  文言: {info['text']}")
    print(f"  文字色 {info['face_color']} / 縁色 {info['edge_color']} / 縁 {str(info['edge_width']) + 'px' if info['edge_width'] else 'なし'} / "
          f"サイズ {info['char_size'][0]:g} / VWS フォント番号 {info['font_id']}")
    ratio, ps_font, hints = 1.0, cfg.get("photoshop_font"), list(cfg.get("font_name_hints", []))
    describe_fonts(cfg, t)
    e = font_for_id(cfg, t.font_id, t)
    sub = substitute_font(cfg, t.font_id, e, t)
    fit = _baseline_fit(e)
    if e:
        ps_font = e.get("postscript") or ps_font
        hints = list(e.get("hints", [])) + hints
        if sub:
            ps_font = sub["postscript"]
            print(f"  ※ このフォントは PC に無いので、編集用テキストは「{sub['face']}」で組みます（同じ書体の別の版。見た目の画像レイヤは正確です）")
        if e.get("file"):
            try:
                ratio = FontGlyphSource(e["file"], int(e.get("index", 0)), fit=_fit_of(e)).cell_ratio
            except Exception:
                pass
    else:
        print(f"  ※ フォント番号 {t.font_id} のフォントが分かりません。Photoshop 側は設定.json のフォントで組みます")
        print(f"     VWS の画面でフォント名を確かめて「python run.py font-name {t.font_id} フォント名」で登録できます")
        fp, fi = find_font(cfg)
        if fp:
            try:
                ratio = FontGlyphSource(fp, fi).cell_ratio
            except Exception:
                pass
    ts1_to_png(t, base + "_再現.png")
    ts1_to_psd(t, base + "_画像レイヤ.psd")
    ts1_to_jsx(t, base + "_Photoshop用.jsx", name=name, ps_font=ps_font,
               font_hints=hints, cell_ratio=ratio, font_fit=fit,
               ref_png=name + "_再現.png")
    print(f"  → {name}_再現.png / {name}_画像レイヤ.psd / {name}_Photoshop用.jsx")


def to_ts1(cfg, path):
    from psdbridge import psd_to_ts1, read_psd_text, ts1_to_png
    base = os.path.splitext(path)[0]
    tmpl_path = resolve(cfg, cfg.get("template", "テンプレート.ts1"))
    # PSD と同じ名前の .ts1 が隣にあれば（= その .ts1 から作った PSD）、それを雛形にする。
    # 型（枠つき / 1 行もの）・縁の有無・イン/アウトの設定などが元のまま戻る
    if os.path.exists(base + ".ts1"):
        tmpl_path = base + ".ts1"
        print(f"  雛形: 隣にある {os.path.basename(tmpl_path)}（元の .ts1）を使います")
    with open(tmpl_path, "rb") as f:
        tmpl = TS1(f.read())
    info = read_psd_text(path)
    print(f"  文言: {info['text']}  フォント: {info['font']}  サイズ: {info['size']:g}px")
    for w in info.get("warnings", []):
        print(f"  ※ {w}")
    font_id, fit, fp, fi = cfg.get("vws_font_id"), None, None, 0
    for _fid in font_ids(tmpl):
        resolve_font(cfg, _fid, tmpl)
    hit = font_for_ps(cfg, info["font"], tmpl)
    gsrc = None
    if hit:
        font_id, e = hit
        fp, fi, fit = e.get("file"), int(e.get("index", 0)), _baseline_fit(e)
        gsrc = glyph_source(cfg, font_id, e, tmpl)
        print(f"  VWS フォント番号 {font_id}「{e.get('name')}」（フォント表より）")
    else:
        print(f"  ※ 「{info['font']}」の VWS フォント番号は未登録です。番号はテンプレートと同じ {font_id or tmpl.font_id} を書き込みます")
        print("     （VWS では字形はそのまま表示されますが、フォント名はその番号のフォントになります）")
        fp, fi = find_font(cfg, info["font"])
    if not fp:
        print("  ※ フォントファイルが見つからないため、テンプレート内の字形だけで組みます")
    out, info = psd_to_ts1(path, tmpl, font_path=fp, font_index=fi, font_fit=fit,
                           font_id=font_id, clear_history=cfg.get("clear_history", False),
                           prefer_template=bool(hit), glyph_font=gsrc)
    # 「○○.ts1」にすると to-ps の元にした .ts1 を上書きしてしまうので、必ず別名にする
    with open(base + "_VWS用.ts1", "wb") as f:
        f.write(out)
    t = TS1(out)
    ts1_to_png(t, base + "_確認.png")
    i = t.info()
    print(f"  文字色 {i['face_color']} / 縁色 {i['edge_color']} / 縁 {str(i['edge_width']) + 'px' if i['edge_width'] else 'なし'} / サイズ {i['char_size'][0]:g}"
          f" / VWS フォント番号 {t.font_id}")
    print(f"  → {os.path.basename(base)}_VWS用.ts1 / {os.path.basename(base)}_確認.png")


def retype(cfg, path, text=None):
    """.ts1 の書式（位置・色・縁・サイズ・フォント番号）のまま文言だけ打ち替える。
    元の .ts1 にある文字は VWS の字形をそのまま使い、無い文字だけ PC のフォントから作る"""
    from ts1core import build_text
    from psdbridge import ts1_to_png
    with open(path, "rb") as f:
        t = TS1(f.read())
    print(f"  今の文言: {t.text}")
    if text is None:
        text = input("  新しい文言（改行は \\n と入力）: ").strip()
    text = text.replace("\\n", "\n")
    if not text:
        print("  文言が空なので中止しました")
        return
    if t.kind == "CSSL" and "\n" in text:
        raise RuntimeError("この .ts1 は枠なしの 1 行ものです。改行は入れられません（この型が 2 行を持てるか確かめられていないため）")
    e = font_for_id(cfg, t.font_id, t)
    font = None
    have = {g.code for g in t.glyphs}
    need = sorted({c for c in text if c != "\n" and ord(c) not in have})
    if e:
        name_, how_ = font_status(e)
        print(f"  フォント: {name_}（VWS 番号 {t.font_id}。{how_}）")
        font = glyph_source(cfg, t.font_id, e, t)
    if font is not None:
        if need:
            print(f"  元の .ts1 に無い {len(need)} 文字はフォントから作ります: {''.join(need)}")
    elif need:
        raise RuntimeError(f"元の .ts1 に無い文字があり、フォント番号 {t.font_id} のフォントがこの PC に入っていません: {''.join(need)}"
                           "（そのフォントを PC に入れるか、元の .ts1 にある字だけで組んでください）")
    chars, glyphs = build_text(t, text, font=font, spacing=[0.0] * (len(text) + 1), prefer_template=True)
    out = t.rebuild(chars=chars, glyphs=glyphs, clear_history=cfg.get("clear_history", False))
    base = os.path.splitext(path)[0] + "_打ち替え"
    with open(base + ".ts1", "wb") as f:
        f.write(out)
    t2 = TS1(out)
    ts1_to_png(t2, base + "_確認.png")
    width = max(len(line) for line in text.split("\n")) * t2.char_size[0]
    if t2.kind == "CSML" and width > t2.box[0]:      # 1 行もの（CSSL）は枠が無い（行の幅が文言に合わせて変わる）
        print(f"  ※ 文字の幅 {width:g}px が枠の幅 {t2.box[0]:g}px を超えています（VWS で左右がはみ出す可能性があります）")
    print(f"  新しい文言: {t2.text}")
    print(f"  → {os.path.basename(base)}.ts1 / {os.path.basename(base)}_確認.png")


def info(cfg, path):
    from structure import dump, format_dump
    with open(path, "rb") as f:
        data = f.read()
    t = TS1(data)
    i = t.info()
    print(f"  文言: {i['text']}")
    print(f"  位置: x={i['position_x']:g} ベースライン y={i['baseline_y']:g} / 枠 {i['box_width']:g}×{i['box_height']:g}")
    print(f"  文字サイズ {i['char_size'][0]:g}×{i['char_size'][1]:g} / 行間 {i['line_spacing']:g} / VWS フォント番号 {i['font_id']}")
    describe_fonts(cfg, t)
    for d in i["decorations"]:
        print(f"  装飾{d['index']}: {d['role_ja']} 色 {d['color_hex']} 不透明度 {d['opacity']}% 太さ {d['width']}px")
    print(f"  字間: {i['spacings'][:len(i['text'])]}")
    out = os.path.splitext(path)[0] + "_構造.txt"
    with open(out, "w", encoding="utf-8-sig") as f:
        f.write(format_dump(dump(data)) + "\n")
    print(f"  → {os.path.basename(out)}（全項目の一覧）")


def diff_pair(cfg, a, b):
    from structure import diff
    with open(a, "rb") as f:
        da = f.read()
    with open(b, "rb") as f:
        db = f.read()
    lines = [f"A: {os.path.basename(a)}  「{TS1(da).text}」", f"B: {os.path.basename(b)}  「{TS1(db).text}」", ""]
    rows = diff(da, db)
    for k, x, y, note in rows:
        lines += [k, f"    A: {x}", f"    B: {y}"] + ([f"    ({note})"] if note else [])
    lines.append(f"\n{len(rows)} 項目が異なります（編集履歴・グリフは除外）")
    out = os.path.splitext(a)[0] + "_差分.txt"
    with open(out, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"→ {out}")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    cmd, files = sys.argv[1], sys.argv[2:]
    cfg = load_config()
    if cmd == "diff":
        if len(files) != 2:
            print(".ts1 を 2 つ指定してください")
            return 1
        diff_pair(cfg, files[0], files[1])
        return 0
    if cmd == "font-name":
        if len(files) != 2:
            print("使い方: python run.py font-name 番号 フォント名")
            return 1
        set_font_name(cfg, files[0], files[1])
        return 0
    if cmd == "text":
        try:
            retype(cfg, files[0], files[1] if len(files) > 1 else None)
        except Exception as e:
            print("  エラー:", e)
            traceback.print_exc()
            return 1
        return 0
    fn = {"to-ps": to_ps, "to-ts1": to_ts1, "info": info, "identify": identify}[cmd]
    ng = 0
    for p in files:
        print(os.path.basename(p))
        try:
            fn(cfg, p)
        except Exception as e:
            ng += 1
            print("  エラー:", e)
            traceback.print_exc()
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
