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
  python run.py catalog ストックのフォルダ → 型の一覧を画像つきで見られる 型カタログ.html を作る（画像は カタログ画像 のフォルダ）
  python run.py check ストックのフォルダ → そのフォルダの .ts1 を全部検査する（読めるか・壊れていないか・打ち替えられるか）。検査結果.csv を作る
  python run.py batch ストックのフォルダ → そのフォルダの .ts1 の一覧（打ち替え一覧.csv）を作る。Excel で「新しい文言」を書き込む
  python run.py batch 打ち替え一覧.csv → 一覧どおりにまとめて打ち替え（打ち替え結果_日時 のフォルダに .ts1 と確認 PNG、結果.csv）
  python run.py texture 素材.ts1      → 文字面に貼る模様の画像を取り出す（素材_模様<番号>_面<k>.png。値はそのまま 0〜255 の濃淡）
  python run.py texture-in 素材.ts1 画像.png [番号] [面]
                                      → 模様を画像で差し替える（素材_模様差し替え.ts1）。番号・面は texture の出力名のもの（省くと最初の模様の面 0）
  python run.py gradient 素材.ts1 番号 色1,色2,... [位置1,位置2,...]
                                      → グラデーションの色（と位置 0〜255）を差し替える（素材_グラデ差し替え.ts1）。色は #RRGGBB か #RRGGBBAA
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
    # 以下は VWS が表示したフォント名から（2026-11-13。字形は B のまま番号だけ変えた .ts1 を VWS で開いて確認）
    "1113": {"name": "ニューロダン L", "face": "FOT-ニューロダン Pro L", "postscript": "NewRodinPro-L", "weight": 300,
             "hints": [["ニューロダン", "L"], ["NewRodin", "L"]]},
    "1115": {"name": "ニューロダン M", "face": "FOT-ニューロダン Pro M", "postscript": "NewRodinPro-M", "weight": 500,
             "hints": [["ニューロダン", "M"], ["NewRodin", "M"]]},
    # 1117 も VWS の表示は「ニューロダンB」（1119 と同じ名前。どの Windows のフォントに当たるかは、字形つきの .ts1 が無く未確認）
    "1117": {"name": "ニューロダン B（番号 1117）", "hints": [["ニューロダン", "B"], ["NewRodin", "B"]]},
    "1123": {"name": "ニューロダン UB", "face": "FOT-ニューロダン Pro UB", "postscript": "NewRodinPro-UB", "weight": 900,
             "hints": [["ニューロダン", "UB"], ["NewRodin", "UB"]]},
    # 以下は推定（2026-11-13）。.ts1 の字形が OpenType 版の輪郭（向き・点の打ち方）で、形は同じ書体の TrueType 版
    # （FTT-ロダンNTLG UB / FTT-ロダンわんぱく UB）と 2048 分の 3 以内で重なる。番号の並びも合う
    # （ニューロダンは L 1113・M 1115・DB?1117・B 1119・EB 1121・UB 1123 と 2 つおき。同じ並びなら
    #   ロダンNTLG が 1443〜1453、すぐ次のロダンわんぱくが 1455〜1465 で、UB はどちらも最後）。VWS の表示名は未確認
    "1453": {"name": "ロダンNTLG UB", "face": "FOT-ロダンNTLG Pro UB", "postscript": "RodinNTLGPro-UB", "weight": 900,
             "hints": [["ロダンNTLG", "UB"], ["RodinNTLG", "UB"]]},
    "1465": {"name": "ロダンわんぱく UB", "face": "FOT-ロダンわんぱく Pro UB", "postscript": "RodinWanpakuPro-UB", "weight": 900,
             "hints": [["ロダンわんぱく", "UB"], ["RodinWanpaku", "UB"]]},
}
# VWS で開くと「不明な書体」と出た番号（この番号のフォントは VWS に無い）: 1114, 1116, 1118, 1120, 1122, 1124
VWS_UNKNOWN_IDS = {1114, 1116, 1118, 1120, 1122, 1124}


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
    if fid in VWS_UNKNOWN_IDS and not e:
        e["vws_unknown"] = True
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
                known_face = KNOWN_FONTS.get(key, {}).get("face")
                if hits and known_face and known_face not in [h_["face"] for h_ in hits]:
                    # 番号から分かっているフォントと、字形が一致したフォントが違う（字形だけ別のフォントにした .ts1 など）。
                    # フォント表は書き換えず、この .ts1 についてだけ食い違いを返す
                    out = dict(e)
                    out.update({"name": KNOWN_FONTS[key]["name"], "mismatch": hits[0]["face"], "exact": False})
                    return out
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
                    try:
                        with gdiglyph.GdiFont(h["face"], h["weight"], h["charset"], h["italic"]) as f_:
                            e["format"] = f_.outline_format()
                    except Exception:
                        pass
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
                    e.pop("sibling", None)
                else:
                    if e.get("exact"):       # 前は入っていたフォントが無くなった
                        for k in ("y_shift", "italic", "charset"):
                            e.pop(k, None)
                    e["exact"] = False
                    e["scan_sig"] = sig
                    _find_sibling(e, ts1, fid)
            elif "sibling" not in e:
                _find_sibling(e, ts1, fid)          # 前に探したときは、この探し方がまだ無かった
            elif e.get("sibling") and (e["sibling"].get("others") or len(gdiglyph.ref_glyphs(ts1, fid)) > e["sibling"].get("chars", 0)
                                       or (not e["sibling"].get("ascii_ok")
                                           and any(0x20 < g.code < 0x7F for g in gdiglyph.ref_glyphs(ts1, fid)))):
                # 候補が複数残っているか、前より字の多い .ts1 → 前の候補だけを確かめ直す（絞れる・縦位置が確かになる）
                old = e["sibling"]
                if not _find_sibling(e, ts1, fid, only=[old["face"]] + list(old.get("others", []))):
                    e["sibling"] = old
                elif old.get("ascii_ok"):
                    e["sibling"]["ascii_ok"] = True
    except Exception as ex:
        print(f"  ※ Windows のフォントとの照合はできませんでした（{ex}）")
    if not e:
        return None
    e["names"] = [n for i, n in enumerate(names) if n and n not in names[:i]]
    if json.dumps(e, sort_keys=True, ensure_ascii=False) != before:
        tbl[key] = e
        save_table(cfg, tbl)
    return e


def _find_sibling(e, ts1, fid, only=None):
    """1 点残らず一致するフォントが PC に無いとき: 同じ書体の別形式（OpenType 版 ↔ TrueType 版）を探して e["sibling"] に入れる。
    見つからなければ e["sibling"] = None（探した印）。候補が複数あるとき（この文言の字では区別できない）は others に残し、
    別の文言の .ts1 を読むたびに絞る"""
    import fontfind
    import gdiglyph
    hits = gdiglyph.find_siblings(ts1, fid, faces=only)
    if not hits:
        if only is None:
            e["sibling"] = None
        return False
    hints = e.get("hints", [])

    def rank(h):
        named = any(fontfind.hint_match(n, hh) for hh in hints for n in (h["face"], h.get("postscript") or "") if n)
        return (0 if named else 1, -h["header_ok"], h["dev"])
    hits.sort(key=rank)
    h = hits[0]
    sib = {k: h[k] for k in ("face", "weight", "italic", "charset", "y_shift", "flip", "dev", "format", "ts1_format")}
    sib["postscript"] = h.get("postscript")
    sib["chars"] = len(gdiglyph.ref_glyphs(ts1, fid))
    # 半角の英数字も一致を確かめたか（照合した字の中に半角の字があった）
    if any(0x20 < g.code < 0x7F for g in gdiglyph.ref_glyphs(ts1, fid)):
        sib["ascii_ok"] = True
    others = [x["face"] for x in hits[1:] if x["face"] != h["face"]]
    if others:
        sib["others"] = others
    e["sibling"] = sib
    return True


def font_status(e):
    """フォント表のエントリ → (名前, どこまで確かか) の説明"""
    if not e:
        return "不明", "この PC のフォントに一致するものが無く、名前も登録されていません"
    if e.get("mismatch"):
        return e.get("name") or "不明", (f"番号ではこのフォントですが、中の字形は「{e['mismatch']}」です（字形だけ別のフォント）。"
                                         "VWS の表示は番号のフォント名、見た目は字形のほうになります")
    if e.get("vws_unknown"):
        return "不明", "VWS で開くと「不明な書体」と表示される番号です（VWS にこの番号のフォントがありません）"
    name = e.get("name") or e.get("face") or e.get("postscript") or "不明"
    if e.get("exact"):
        if e.get("same_shape"):
            return name, (f"候補が {len(e['same_shape']) + 1} つ:「{e.get('face')}」「{'」「'.join(e['same_shape'])}」。"
                          "この文言の字ではどれも 1 点残らず同じ形で、区別できません（別の文言の .ts1 を読むと絞られます）")
        return name, f"確定: Windows の「{e.get('face')}」の字形と 1 点残らず一致" + (f"（{e['format']} 版）" if e.get("format") else "")
    if e.get("sibling"):
        sb = e["sibling"]
        more = f"（ほかの候補:「{'」「'.join(sb['others'])}」。この文言の字では区別できません）" if sb.get("others") else ""
        return name, (f"ほぼ確定: .ts1 の字形は {sb.get('ts1_format', '別の形式')} 版の作り。"
                      f"この PC にあるのは同じ書体の {sb.get('format', '別の形式')} 版「{sb['face']}」です{more}。"
                      f"輪郭の点の打ち方は違いますが、形の差は 2048 分の {sb['dev']} 以内。新しい字はここから作ります")
    if e.get("in_file"):
        return name, "名前は .ts1 の中に書かれています。この PC には入っていません"
    if e.get("score"):
        return name, f"候補: 形の一致 {e['score'] * 100:.1f}%（完全一致するフォントはこの PC にありません）"
    if name == "不明":
        # 照合はしたが一致なし（scan_sig だけがある）。以前は「名前は登録済み」と出ていたが、名前は入っていない
        return name, ("この PC の全フォントと字形を照合しましたが、一致するものがありません（PC に入っていないフォントです）。"
                      "VWS の画面でフォント名を確かめて「python run.py font-name 番号 名前」で登録できます")
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
    if f is None and e and e.get("sibling"):
        f = {"oy": -(float(e["sibling"]["y_shift"]) + 0.26)}
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
    if e.get("sibling") and e["sibling"].get("postscript"):      # 同じ書体の別形式が PC にある → それを使う
        sb = e["sibling"]
        return {"face": sb["face"], "postscript": sb["postscript"], "oy": -(float(sb["y_shift"]) + 0.26)}
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
            sb = e.get("sibling")
            if sb and not e.get("exact"):
                src = gdiglyph.GdiGlyphSource(sb["face"], int(sb["y_shift"]), int(sb.get("weight", 0)),
                                              int(sb.get("charset", gdiglyph.SHIFTJIS_CHARSET)), bool(sb.get("italic")),
                                              flip=bool(sb.get("flip")))
                # 半角の英数字（U+0020〜007E）は、形式が違うと字幅も形も違うことがある（OpenType 版 Pro は欧文が
                # プロポーショナル、TrueType 版 FTT は半角の固定幅）。その .ts1 に半角の字があって一致を確かめたときだけ使う
                if not sb.get("ascii_ok"):
                    src.no_halfwidth = f"{sb.get('format', '別形式')} 版の「{sb['face']}」"
                if not quiet:
                    print(f"  字形の作り方: Windows の「{sb['face']}」（同じ書体の別形式）から作成。"
                          f"元からある字とは点の打ち方が違いますが、形の差は 2048 分の {sb['dev']} 以内です")
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
        if e and e.get("sibling"):
            sb = e["sibling"]
            print(f"  ☆ VWS フォント番号 {fid} ≒ 「{e.get('name')}」。.ts1 の字形は {sb.get('ts1_format')} 版の作りで、"
                  f"この PC にあるのは同じ書体の {sb.get('format')} 版「{sb['face']}」（PostScript 名: {sb.get('postscript')}）です。"
                  f"形の差は 2048 分の {sb['dev']} 以内")
            if e.get("face") and e["face"] != sb["face"]:
                print(f"     VWS の PC にあるのは「{e['face']}」と思われます（推定）。これをこの PC に入れると 1 点残らず一致するはずです")
            if sb.get("others"):
                print(f"     ほかの候補:「{'」「'.join(sb['others'])}」（この文言の字では区別できません。別の文言の .ts1 を読むと絞られます）")
            print("     新しい字はこのフォントから作ります（元からある字は .ts1 の中の字形をそのまま使います）")
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
    from psdbridge import texture_images
    tex = []
    for i, k, lname, im in texture_images(t):
        png = f"{name}_模様{i}_面{k}.png"
        im.save(os.path.join(os.path.dirname(base) or ".", png))
        tex.append((png, lname))
    ts1_to_jsx(t, base + "_Photoshop用.jsx", name=name, ps_font=ps_font,
               font_hints=hints, cell_ratio=ratio, font_fit=fit,
               ref_png=name + "_再現.png", textures=tex)
    print(f"  → {name}_再現.png / {name}_画像レイヤ.psd / {name}_Photoshop用.jsx"
          + (f" / 模様 {len(tex)} 枚（PSD の「模様」グループに入ります。編集すると .ts1 に戻ります）" if tex else ""))


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


def retype_bytes(cfg, t, text, quiet=False):
    """打ち替えの本体（ファイルは書かない）→ (新しい .ts1 のバイト列, 注意の一覧)。
    書式（位置・色・縁・サイズ・フォント番号・装飾）はそのまま。元の .ts1 にある文字は VWS の字形をそのまま使い、
    無い文字だけ PC のフォントから作る。続けられない問題は RuntimeError"""
    from ts1core import build_text, u32
    say = (lambda *a: None) if quiet else print
    notes = []
    text = text.replace("\\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
    # 色や大きさが途中で変わる型: 「あい|１２|うえ」のように | で区切ると、区切りごとに雛形のまとまりの書式を当てる
    runs = t.style_runs()
    segments = None
    if len(runs) > 1 and "|" in text:
        segments = text.split("|")
        now = "|".join("".join(t.chars[i].char for i in run) for run in runs)
        if len(segments) != len(runs) or any(not s_ for s_ in segments):
            raise RuntimeError(f"この型は書式のまとまりが {len(runs)} つです（元の文言の区切り: {now}）。"
                               f"| で {len(runs)} つに区切ってください（今は {len(segments)} つ）")
        text = "".join(segments)
    if not text.strip():
        raise RuntimeError("文言が空です")
    if t.kind == "CSSL" and "\n" in text:
        raise RuntimeError("この型は枠なしの 1 行ものです。改行は入れられません（この型が 2 行を持てるか確かめられていないため）")
    e = font_for_id(cfg, t.font_id, t)
    font = None
    have = {g.code for g in t.glyphs}
    need = sorted({c for c in text if c != "\n" and ord(c) not in have})
    if e:
        name_, how_ = font_status(e)
        say(f"  フォント: {name_}（VWS 番号 {t.font_id}。{how_}）")
        font = glyph_source(cfg, t.font_id, e, t, quiet=quiet)
    if font is not None:
        if need:
            # フォントに無い字を黙って作らない（GDI は無い字に「豆腐」や空の字形を返す。そのまま納品物に入ってしまう）
            wide = [c for c in need if ord(c) > 0xFFFF]
            if wide:
                raise RuntimeError(f"この字は打てません: {''.join(wide)}（「𠮷」のような、文字コードが 4 バイトになる字。"
                                   "VWS がこの種類の字をどう持つかが分かっていません。VWS で打った .ts1 があれば調べられます）")
            lack = [c for c in need if hasattr(font, "has") and not font.has(ord(c))]
            if lack:
                raise RuntimeError(f"この書体に無い字です: {''.join(lack)}（別の字に置き換えるか、その字を持つ型を使ってください）")
            half = [c for c in need if 0x20 <= ord(c) <= 0x7E]
            if half and getattr(font, "no_halfwidth", False):
                shown = "".join(half).replace(" ", "（半角の空白）")
                raise RuntimeError(
                    f"半角の英数字・記号は作れません: {shown}。この型の書体は、PC にあるのが別形式（{font.no_halfwidth}）で、"
                    "半角の英数字だけは字幅も形も違います（例: 半角の 0 の幅が 1413 と 1024）。"
                    "全角（０１２ＡＢＣ）で書くか、VWS と同じ形式のフォントを PC に入れてください")
            say(f"  元の .ts1 に無い {len(need)} 文字はフォントから作ります: {''.join(need)}")
            notes.append(f"フォントから作った字: {''.join(need)}")
    elif need:
        raise RuntimeError(f"元の .ts1 に無い文字があり、フォント番号 {t.font_id} のフォントがこの PC に入っていません: {''.join(need)}"
                           "（そのフォントを PC に入れるか、元の .ts1 にある字だけで組んでください）")
    # 普通の型は字間 0 で組み直す。装飾つきの型は、字間も「何文字目か」で元の値を引き継ぐ（二色の数字の −30・−68 など）
    spacing = [0.0] * (len(text) + 1) if t.is_plain() else None
    chars, glyphs = build_text(t, text, font=font, spacing=spacing, prefer_template=True, segments=segments)
    out = t.rebuild(chars=chars, glyphs=glyphs, clear_history=cfg.get("clear_history", False))
    t2 = TS1(out)
    # ---- 納品前の検査: 壊れていないか、文字のほかの所が型と変わっていないか。NG なら .ts1 を出さない
    import ts1check
    found = ts1check.check_retype(t.data, out, text, clear_history=cfg.get("clear_history", False))
    bad = [m for lv, m in found if lv == ts1check.NG]
    if bad:
        raise RuntimeError("納品前の検査で止めました（この .ts1 は出していません）: " + " / ".join(bad[:4])
                           + (f" ほか {len(bad) - 4} 件" if len(bad) > 4 else ""))
    notes += [m for lv, m in found if lv == ts1check.WARN]
    width = max(len(line) for line in text.split("\n")) * t2.char_size[0]
    if t2.kind == "CSML" and width > t2.box[0]:      # 1 行もの（CSSL）は枠が無い（行の幅が文言に合わせて変わる）
        notes.append(f"文字の幅 {width:g}px が枠の幅 {t2.box[0]:g}px を超えています（VWS で左右がはみ出すかもしれません）")
    # ---- 画面からのはみ出し（1 行ものは文言が長いと左右に伸びる）
    try:
        if len(t.elements) == 1:
            notes += ts1check.screen_overflow(t2.ofri_bbox, t.ofri_bbox)
        else:                # 要素が複数: 描画範囲（OFRI）は書き直していないので、主の文字の字形の範囲で見る
            import vwsrender
            notes += ts1check.screen_overflow(vwsrender.real_bbox(t2), vwsrender.real_bbox(t))
    except Exception:
        pass
    # ---- 普通の型で、型のほうが字ごとに字間を詰めてある（手詰め）とき: 新しい文言には持ち越さない
    if t.is_plain():
        sp = [v for k, v in enumerate(t.spacings()[1:], start=1) if t.chars[k].code]
        if sp and any(abs(v - sp[0]) > 1e-6 for v in sp):
            notes.append(f"型は字ごとに字間を変えてあります（{min(sp):g}〜{max(sp):g}px の手詰め）。打ち替えた文言は字間 0 で組んでいます")
    # 装飾つきの型の注意
    if not t.is_plain():
        per = t.decorations_per_char()
        sig = [tuple((d["kind"], d["color_resource"]) for d in ds) for ds in per]
        visible = [k for k, ch in enumerate(t.chars) if ch.code]
        switch = [k for k in visible[1:] if sig[k] != sig[k - 1]]
        mags = t.magnifications()
        mag_switch = [k for k in visible[1:] if mags[k] != mags[k - 1]]
        n_old, n_new = len(visible), len([c for c in text if c != "\n"])
        if len(runs) > 1 and segments is None and n_old != n_new:
            now = "|".join("".join(t.chars[i].char for i in run) for run in runs)
            notes.append(f"色や大きさが途中で変わる型です（元の区切り: {now}）。区切りを書かなかったので、変わり目は元と同じ「何文字目か」になります。"
                         f"文字数が違う文言（{n_old} → {n_new} 文字）では狙いとずれるので、「{now}」のように | で区切って書いてください")
        if t.h_condense() != 1.0:
            notes.append("字を横に縮める型です（金・縞と同じ設定）。縮める倍率は金・縞の書体で測った値なので、"
                         "ほかの書体の型では打ち替えたあと VWS で字の位置を確かめてください")
        notes.append("装飾つきの型です。確認画像の装飾は近似なので、最初の 1 回は VWS で見てください")
    return out, notes


def retype(cfg, path, text=None):
    """.ts1 の書式（位置・色・縁・サイズ・フォント番号）のまま文言だけ打ち替える。
    元の .ts1 にある文字は VWS の字形をそのまま使い、無い文字だけ PC のフォントから作る"""
    from psdbridge import ts1_to_png
    with open(path, "rb") as f:
        t = TS1(f.read())
    print(f"  今の文言: {t.text}")
    if text is None:
        text = input("  新しい文言（改行は \\n と入力）: ").strip()
    if not text:
        print("  文言が空なので中止しました")
        return
    out, notes = retype_bytes(cfg, t, text)
    base = os.path.splitext(path)[0] + "_打ち替え"
    with open(base + ".ts1", "wb") as f:
        f.write(out)
    t2 = TS1(out)
    ts1_to_png(t2, base + "_確認.png")
    for n in notes:
        if not n.startswith("フォントから作った字"):
            print(f"  ※ {n}")
    print(f"  新しい文言: {t2.text}")
    print(f"  → {os.path.basename(base)}.ts1 / {os.path.basename(base)}_確認.png")


# ---------------------------------------------------------------------------
# ストックから一覧どおりにまとめて打ち替える
# ---------------------------------------------------------------------------
LIST_NAME = "打ち替え一覧.csv"
LIST_COLS = ["型", "新しい文言", "出力名", "今の文言（参考）", "型のメモ（参考）"]


def _read_list(path):
    import csv
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp932"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    rows = list(csv.reader(io_StringIO(text)))
    return rows


def io_StringIO(text):
    import io as _io
    return _io.StringIO(text, newline="")


def _stock_files(folder):
    out = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith("打ち替え結果_")]
        for fn in sorted(files):
            if fn.lower().endswith(".ts1") and "_打ち替え" not in fn and not fn.endswith("_VWS用.ts1"):
                out.append(os.path.join(root, fn))
    return out


def _type_memo(t):
    memo = []
    memo.append("1 行もの（枠なし）" if t.kind == "CSSL" else "枠つき（改行できる）")
    if not t.is_plain():
        kinds = sorted({d["role_ja"].split("（")[0] for ds in t.decorations_per_char() for d in ds})
        memo.append("装飾つき: " + "・".join(kinds))
        runs = t.style_runs()
        if len(runs) > 1:
            memo.append("色や大きさが途中で変わる。区切りを | で書く（元: " + "|".join("".join(t.chars[i].char for i in run) for run in runs) + "）")
    return " / ".join(memo)


CATALOG_HTML = "型カタログ.html"
CATALOG_IMG = "カタログ画像"


def _thumb(im, max_w=560, max_h=200):
    """透過の RGBA 画像 → 字のある所だけ切り出し、暗い市松の上に重ねた縮小画像（無ければ None）"""
    from PIL import Image
    bb = im.getbbox()
    if not bb:
        return None
    m = 12
    box = (max(bb[0] - m, 0), max(bb[1] - m, 0), min(bb[2] + m, im.width), min(bb[3] + m, im.height))
    c = im.crop(box)
    w, h = c.size
    bg = Image.new("RGBA", (w, h), (58, 62, 70, 255))
    q = 16
    dark = Image.new("RGBA", (q, q), (46, 50, 58, 255))
    for y in range(0, h, q):
        for x in range((y // q % 2) * q, w, 2 * q):
            bg.paste(dark, (x, y))
    bg.alpha_composite(c)
    bg.thumbnail((max_w, max_h), Image.LANCZOS)
    return bg.convert("RGB")


def _vws_png_near(path):
    """その .ts1 の VWS の書き出し PNG が隣にあれば返す（「名前.png」「[名前].png」「[名前．ts1].png」）"""
    import unicodedata
    d, stem = os.path.dirname(path), os.path.splitext(os.path.basename(path))[0]
    want = {stem + ".png", f"[{stem}].png", f"[{stem}．ts1].png", f"[{stem}.ts1].png"}
    try:
        for fn in os.listdir(d):
            if unicodedata.normalize("NFC", fn) in want:
                return os.path.join(d, fn)
    except OSError:
        pass
    return None


def catalog(cfg, folder):
    """ストックの .ts1 を、画像・文言・使い方のメモつきの 1 枚の HTML にする（Photoshop も VWS も使わない）。
    画像は、隣に VWS の書き出し PNG があればそれ、無ければこの道具の再現（装飾つきは近似）"""
    import datetime
    import hashlib
    import html
    from PIL import Image
    from psdbridge import ts1_to_png
    files = _stock_files(folder)
    if not files:
        raise RuntimeError(f"このフォルダに .ts1 がありません: {folder}")
    img_dir = os.path.join(folder, CATALOG_IMG)
    os.makedirs(img_dir, exist_ok=True)
    cards, n_ng = [], 0
    for path in files:
        rel = os.path.relpath(path, folder)
        key = hashlib.md5(rel.encode("utf-8")).hexdigest()[:10]
        thumb_rel = f"{CATALOG_IMG}/{key}.png"
        thumb = os.path.join(img_dir, key + ".png")
        try:
            data = open(path, "rb").read()
            t = TS1(data)
        except Exception as ex:
            n_ng += 1
            cards.append({"rel": rel, "error": str(ex)})
            print(f"  NG  {rel}: 読めません（{ex}）")
            continue
        vws_png = _vws_png_near(path)
        src_time = max(os.path.getmtime(path), os.path.getmtime(vws_png) if vws_png else 0)
        how = "VWS の書き出し" if vws_png else ("再現" if t.is_plain() else "再現（装飾は近似）")
        if not (os.path.exists(thumb) and os.path.getmtime(thumb) >= src_time):
            try:
                if vws_png:
                    im = Image.open(vws_png).convert("RGBA")
                else:
                    tmp = os.path.join(img_dir, key + "_full.png")
                    ts1_to_png(t, tmp)
                    im = Image.open(tmp).convert("RGBA")
                    im.load()
                    os.remove(tmp)
                th = _thumb(im)
                if th is None:
                    raise RuntimeError("画像に何も描かれていません")
                th.save(thumb)
            except Exception as ex:
                thumb_rel = None
                how = f"画像を作れませんでした（{ex}）"
        # 新しい字を打てるか（その書体が PC にあるか）
        e = font_for_id(cfg, t.font_id, t)
        font_name = font_status(e)[0] if e else "不明"
        can_new, no_half = False, False
        try:
            src_ = glyph_source(cfg, t.font_id, e, t, quiet=True) if e else None
            can_new = src_ is not None
            no_half = bool(getattr(src_, "no_halfwidth", False))
        except Exception:
            can_new = False
        import ts1check
        found = ts1check.check_file(data)
        runs = t.style_runs()
        split = "|".join("".join(t.chars[i].char for i in r) for r in runs) if len(runs) > 1 else ""
        chars = "".join(sorted({ch.char for ch in t.chars if ch.code and ch.code not in (0x0A, 0x0D)}))
        cards.append({
            "rel": rel, "thumb": thumb_rel, "how": how, "text": t.text.replace("\r", "\n"),
            "memo": _type_memo(t).split(" / 色や大きさが途中で変わる")[0], "split": split, "size": f"{t.char_size[0]:g}",
            "multi": len(t.elements) > 1,
            "font": f"{font_name}（番号 {t.font_id}）", "can_new": can_new, "chars": chars,
            "condense": t.h_condense() != 1.0, "no_half": no_half,
            "check_ng": " / ".join(m for lv, m in found if lv == ts1check.NG),
        })
        print(f"  OK  {rel}（{how}）")
    E = html.escape
    out = ["<!doctype html>", '<html lang="ja"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           "<title>型カタログ</title>", "<style>",
           ":root{color-scheme:dark}",
           "body{margin:0;padding:20px 16px 48px;background:#1d2026;color:#e8eaee;font-family:'Yu Gothic UI','Meiryo',sans-serif;line-height:1.6}",
           "h1{font-size:20px;margin:0 0 4px}",
           ".lead{color:#a9afbb;font-size:13px;margin:0 0 14px}",
           "#q{width:100%;max-width:480px;padding:8px 10px;border-radius:6px;border:1px solid #454b58;background:#262a32;color:#e8eaee;font-size:14px}",
           ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px;margin-top:16px}",
           ".card{background:#262a32;border:1px solid #363b47;border-radius:8px;padding:12px;min-width:0}",
           ".pic{background:#3a3e46;border-radius:6px;min-height:72px;display:flex;align-items:center;justify-content:center;overflow:hidden}",
           ".pic img{max-width:100%;height:auto;display:block}",
           ".name{font-weight:700;font-size:14px;margin:10px 0 2px;word-break:break-all}",
           ".txt{font-size:15px;margin:2px 0 8px;white-space:pre-wrap;word-break:break-all}",
           "dl{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;margin:0;font-size:12.5px;color:#c3c8d2}",
           "dt{color:#8f96a3;white-space:nowrap}dd{margin:0;word-break:break-all}",
           ".tag{display:inline-block;font-size:11.5px;padding:1px 7px;border-radius:10px;margin:0 4px 4px 0;background:#39404d;color:#d5d9e1}",
           ".ok{background:#1f4a35;color:#bfe8d0}.ng{background:#5a2f2f;color:#f2c9c9}.warn{background:#554620;color:#f1dfae}",
           "code{background:#1d2026;border:1px solid #363b47;border-radius:4px;padding:0 5px;font-family:Consolas,'MS Gothic',monospace;font-size:12.5px}",
           ".hide{display:none}",
           "</style></head><body>",
           "<h1>型カタログ</h1>",
           f'<p class="lead">ストックの .ts1 {len(files)} 本（{datetime.datetime.now():%Y-%m-%d %H:%M} 作成）。'
           "使う型の「型の名前」を 打ち替え一覧.csv の「型」に書き、「新しい文言」を入れて、5 の bat に入れると .ts1 ができます。</p>",
           '<input id="q" type="search" placeholder="型の名前・文言・メモで絞り込み" aria-label="絞り込み">',
           '<div class="grid">']
    for c in cards:
        if "error" in c:
            out.append(f'<div class="card" data-k="{E(c["rel"])}"><div class="name">{E(c["rel"])}</div>'
                       f'<span class="tag ng">読めません</span><div class="txt">{E(c["error"])}</div></div>')
            continue
        tags = []
        tags.append('<span class="tag ok">新しい字も打てる</span>' if c["can_new"]
                    else '<span class="tag ng">元にある字だけ（書体が PC に無い）</span>')
        if c["split"]:
            tags.append('<span class="tag warn">途中で色や大きさが変わる</span>')
        if c["condense"]:
            tags.append('<span class="tag">字を横に縮める型</span>')
        if c["no_half"]:
            tags.append('<span class="tag warn">半角の英数字は足せない（全角で書く）</span>')
        if c["check_ng"]:
            tags.append(f'<span class="tag ng" title="{E(c["check_ng"])}">検査 NG（打ち替えに使わない）</span>')
        if c["multi"]:
            tags.append('<span class="tag warn">ほかの文字や図形も入っている（替わるのは最初の文字だけ）</span>')
        pic = (f'<img src="{E(c["thumb"])}" alt="{E(c["text"])}" loading="lazy">' if c["thumb"] else "画像なし")
        rows = [("文字サイズ", E(c["size"])), ("書体", E(c["font"])), ("型", E(c["memo"])), ("画像", E(c["how"]))]
        if c["split"]:
            rows.insert(0, ("区切り", f'<code>{E(c["split"])}</code> のように <code>|</code> で区切って書く'))
        if not c["can_new"]:
            rows.append(("使える字", E(c["chars"])))
        k = E(" ".join([c["rel"], c["text"], c["memo"], c["font"]]))
        out.append(f'<div class="card" data-k="{k}"><div class="pic">{pic}</div>'
                   f'<div class="name">{E(c["rel"])}</div><div class="txt">{E(c["text"])}</div>'
                   f'<div>{"".join(tags)}</div><dl>' + "".join(f"<dt>{a}</dt><dd>{b}</dd>" for a, b in rows) + "</dl></div>")
    out += ["</div>", "<script>",
            "const q=document.getElementById('q'),cs=[...document.querySelectorAll('.card')];",
            "q.addEventListener('input',()=>{const w=q.value.trim().toLowerCase().split(/\\s+/).filter(Boolean);"
            "cs.forEach(c=>{const k=c.dataset.k.toLowerCase();c.classList.toggle('hide',!w.every(x=>k.includes(x)))})});",
            "</script></body></html>"]
    dst = os.path.join(folder, CATALOG_HTML)
    with open(dst, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    # いらなくなった画像を消す（型が減ったとき）
    keep = {os.path.basename(c["thumb"]) for c in cards if c.get("thumb")}
    for fn in os.listdir(img_dir):
        if fn.endswith(".png") and fn not in keep:
            os.remove(os.path.join(img_dir, fn))
    print(f"\n  型 {len(files) - n_ng} 本のカタログを作りました → {dst}")
    print("  ブラウザで開いて型を選び、打ち替え一覧.csv に「型」と「新しい文言」を書いて 5 の bat に入れてください")


CHECK_NAME = "検査結果.csv"


def stock_check(cfg, target):
    """ストックの .ts1 を検査する: 読めるか・壊れていないか・書き直しても同じか・新しい字を打てるか。
    フォルダなら中の .ts1 を全部（検査結果.csv も作る）、.ts1 ならその 1 本"""
    import csv
    import ts1check
    files = _stock_files(target) if os.path.isdir(target) else [target]
    if not files:
        raise RuntimeError(f"このフォルダに .ts1 がありません: {target}")
    base = target if os.path.isdir(target) else os.path.dirname(os.path.abspath(target))
    rows = [["型", "結果", "打ち替え", "書体", "新しい字", "要素", "検査の内容"]]
    n = {"OK": 0, "注意": 0, "NG": 0}
    for path in files:
        rel = os.path.relpath(path, base)
        data = open(path, "rb").read()
        found = ts1check.check_file(data)
        level = "NG" if any(lv == ts1check.NG for lv, _ in found) else ("注意" if found else "OK")
        font = retype_ok = can_new = elems = ""
        try:
            t = TS1(data)
            elems = "・".join({"CSML": "文字（枠つき）", "CSSL": "文字（1 行）", "CGRP": "図形"}.get(tg, tg) for tg, _o, _s in t.elements)
            e = font_for_id(cfg, t.font_id, t)
            name_, how_ = font_status(e) if e else ("不明", "")
            font = f"{name_}（番号 {t.font_id}）" + ("・確定" if e and e.get("exact") else "・ほぼ確定" if e and e.get("sibling") else "")
            try:
                can_new = "打てる" if e and glyph_source(cfg, t.font_id, e, t, quiet=True) is not None else "元にある字だけ"
            except Exception:
                can_new = "元にある字だけ"
            # 同じ文言で打ち替えてみる（打ち替えの道が通るか。装飾つきの型は元とバイト単位で同じになるはず）
            runs = t.style_runs()
            same = "|".join("".join(t.chars[i].char for i in rr) for rr in runs) if len(runs) > 1 else \
                "".join("\n" if ch.code in (0x0A, 0x0D) else ch.char for ch in t.chars if ch.code)
            try:
                out, _notes = retype_bytes(cfg, t, same, quiet=True)
                retype_ok = "できる（同じ文言で元と同じ）" if out == data else "できる"
            except Exception as ex:
                retype_ok = f"できない: {ex}"
                level = "NG" if level == "OK" else level
        except Exception as ex:
            retype_ok = f"読めない: {ex}"
        n[level] += 1
        detail = ts1check.summary(found) if found else ""
        rows.append([rel, level, retype_ok, font, can_new, elems, detail])
        print(f"  {level:<3} {rel}  打ち替え: {retype_ok[:40]} / {font} / 新しい字: {can_new}" + (f"\n      {detail[:300]}" if detail else ""))
    if os.path.isdir(target):
        dst = os.path.join(target, CHECK_NAME)
        with open(dst, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(rows)
        print(f"\n  {len(files)} 本を検査しました（OK {n['OK']} / 注意 {n['注意']} / NG {n['NG']}）→ {dst}")
    return n["NG"]


def stock_list(cfg, folder):
    """ストックのフォルダの .ts1 を一覧にして 打ち替え一覧.csv を作る（Excel で開ける UTF-8 BOM 付き）"""
    import csv
    files = _stock_files(folder)
    if not files:
        raise RuntimeError(f"このフォルダに .ts1 がありません: {folder}")
    dst = os.path.join(folder, LIST_NAME)
    if os.path.exists(dst):
        dst = os.path.join(folder, "打ち替え一覧_ひな形.csv")
    rows = [LIST_COLS]
    for p in files:
        try:
            t = TS1(open(p, "rb").read())
            rows.append([os.path.relpath(p, folder), "", "", t.text.replace("\n", "\\n").replace("\r", "\\n"), _type_memo(t)])
        except Exception as ex:
            rows.append([os.path.relpath(p, folder), "", "", "", f"読めません: {ex}"])
    with open(dst, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows)
    print(f"  型 {len(files)} 本の一覧を作りました → {dst}")
    print("  Excel で開いて「新しい文言」の列に書き込み、保存してから、この一覧を同じ bat に入れてください")
    print("  （1 つの型を何回使ってもかまいません。行をコピーして文言だけ変えてください。改行は \\n か、セルの中の改行で）")


def batch(cfg, path):
    """一覧どおりにまとめて打ち替える。フォルダを渡されたら一覧を作る"""
    import csv
    import datetime
    import re
    from psdbridge import ts1_to_png
    if os.path.isdir(path):
        stock_list(cfg, path)
        return
    folder = os.path.dirname(os.path.abspath(path))
    rows = _read_list(path)
    if not rows:
        raise RuntimeError("一覧が空です")
    head = [c.strip() for c in rows[0]]
    col = {name: (head.index(name) if name in head else None) for name in LIST_COLS}
    if col["型"] is None or col["新しい文言"] is None:
        raise RuntimeError(f"一覧の 1 行目に「型」と「新しい文言」の列が必要です（今: {head}）")
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    out_dir = os.path.join(folder, f"打ち替え結果_{stamp}")
    os.makedirs(out_dir, exist_ok=True)
    cache, used, result = {}, set(), [["行", "型", "新しい文言", "結果", "出力", "検査", "注意"]]
    ok = ng = 0
    for i, r in enumerate(rows[1:], start=2):
        get = lambda name: (r[col[name]].strip() if col[name] is not None and col[name] < len(r) else "")
        typ, text, name = get("型"), get("新しい文言"), get("出力名")
        if not typ and not text:
            continue
        if not text:
            continue                              # 文言が無い行は飛ばす（一覧のひな形のまま）
        src = typ if os.path.isabs(typ) else os.path.join(folder, typ)
        if not os.path.exists(src) and not src.lower().endswith(".ts1"):
            src += ".ts1"
        label = f"{i} 行目（{os.path.basename(typ)} → {text.replace(chr(10), ' ')}）"
        try:
            if not os.path.exists(src):
                raise RuntimeError(f"型の .ts1 が見つかりません: {typ}")
            if src not in cache:
                cache[src] = open(src, "rb").read()
            t = TS1(cache[src])
            out, notes = retype_bytes(cfg, t, text, quiet=True)
            stem = name or f"{os.path.splitext(os.path.basename(src))[0]}_{text.replace(chr(10), ' ')[:16]}"
            stem = re.sub(r'[\\/:*?"<>|\n\r\t]', "_", stem).strip() or "テロップ"
            base, k = stem, 2
            while base in used:
                base, k = f"{stem}_{k}", k + 1
            used.add(base)
            with open(os.path.join(out_dir, base + ".ts1"), "wb") as f:
                f.write(out)
            ts1_to_png(TS1(out), os.path.join(out_dir, base + "_確認.png"))
            ok += 1
            warn = [n for n in notes if not n.startswith("フォントから作った字")]
            print(f"  OK  {label} → {base}.ts1" + (f"\n      ※ " + "\n      ※ ".join(warn) if warn else ""))
            result.append([i, typ, text, "OK", base + ".ts1", "通過", " / ".join(notes)])
        except Exception as ex:
            ng += 1
            print(f"  NG  {label}: {ex}")
            result.append([i, typ, text, "NG", "", "止めた" if "納品前の検査" in str(ex) else "", str(ex)])
    with open(os.path.join(out_dir, "結果.csv"), "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(result)
    print(f"\n  できた {ok} 本 / できなかった {ng} 本 → {out_dir}")
    print("  どの .ts1 も納品前の検査（壊れていないか・文字のほかの所が型と同じか）を通っています")
    print("  確認 PNG で文言と見た目を見てから納品してください（装飾つきの型は、最初の 1 回は VWS でも確かめてください）")


def _color_text(d):
    if d.get("gradient"):
        g = d["gradient"]
        stops = " → ".join(("#%02X%02X%02X" % c[:3]) + (f"（α{c[3]}）" if c[3] != 255 else "") + f"@{pos}" for pos, c in g["stops"])
        return f"グラデーション {stops}" + ("（模様の画像つき）" if g["image"] else "")
    if d.get("resource_type") == "CPTN":
        return "模様の画像"
    return f"色 {d['color_hex']}" + (f"（α{d['color'][3]}）" if d.get("color") and d["color"][3] != 255 else "")


def texture_out(cfg, path):
    """模様の画像を PNG に取り出す。値は .ts1 の中の 0〜255 そのまま（濃淡画像）"""
    import wpf
    with open(path, "rb") as f:
        t = TS1(f.read())
    tex = wpf.textures_of(t)
    if not tex:
        print("  模様の画像はありません")
        return
    stem = os.path.splitext(path)[0]
    for i, tag, tx in tex:
        imgs = tx.images()
        users = [f"{k + 1} 字目" for k, ds in enumerate(t.decorations_per_char()) if any(d["color_resource"] == i for d in ds)]
        print(f"  模様 {i}（{'グラデーション' if tag == 'CGRD' else '模様'}・種類 {tx.kind}）{tx.width}×{tx.height} 面 {len(imgs)} 枚"
              + (f"　使っている字: {users[0]}〜{users[-1]}" if users else ""))
        for k, im in enumerate(imgs):
            out = f"{stem}_模様{i}_面{k}.png"
            im.save(out)
            print(f"    → {os.path.basename(out)}")


def texture_in(cfg, path, png, index=None, plane=0):
    """模様を画像で差し替える。ほかのバイトは変えない（色・字形・装飾はそのまま）"""
    import numpy as np
    from PIL import Image
    import wpf
    with open(path, "rb") as f:
        data = f.read()
    t = TS1(data)
    tex = {i: (tag, tx) for i, tag, tx in wpf.textures_of(t)}
    if not tex:
        raise ValueError("このファイルには模様の画像がありません")
    index = min(tex) if index is None else int(index)
    tag, tx = tex[index]
    planes = tx.planes()
    im = Image.open(png).convert("L")
    if im.size != (tx.width, tx.height):
        print(f"  画像の大きさ {im.size[0]}×{im.size[1]} を {tx.width}×{tx.height} に合わせます")
        im = im.resize((tx.width, tx.height), Image.LANCZOS)
    planes[int(plane)] = np.asarray(im, np.uint8)
    new_raw = tx.replaced(planes)
    res = list(t.resources)
    res[index] = (tag, new_raw)
    out = t.rebuild_resources(res)
    dst = os.path.splitext(path)[0] + "_模様差し替え.ts1"
    with open(dst, "wb") as f:
        f.write(out)
    print(f"  模様 {index} の面 {plane} を差し替えました → {os.path.basename(dst)}")


def gradient_set(cfg, path, index, colors, positions=None):
    from ts1core import replace_gradient
    with open(path, "rb") as f:
        t = TS1(f.read())
    index = int(index)
    tag, raw = t.resources[index]
    if tag != "CGRD":
        raise ValueError(f"リソース {index} はグラデーションではありません（{tag}）")
    cols = []
    for c in colors.split(","):
        c = c.strip().lstrip("#")
        cols.append(tuple(int(c[k:k + 2], 16) for k in range(0, len(c), 2)) + ((255,) if len(c) == 6 else ()))
    pos = [int(v) for v in positions.split(",")] if positions else None
    res = list(t.resources)
    res[index] = (tag, replace_gradient(raw, cols, pos))
    out = t.rebuild_resources(res)
    dst = os.path.splitext(path)[0] + "_グラデ差し替え.ts1"
    with open(dst, "wb") as f:
        f.write(out)
    print(f"  グラデーション {index} を {len(cols)} 色にしました → {os.path.basename(dst)}")


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
        if d.get("gradient"):
            stops = " → ".join("#%02X%02X%02X" % c[:3] for _, c in d["gradient"]["stops"])
            col = f"グラデーション {stops}" + ("（模様の画像つき）" if d["gradient"]["image"] else "")
        elif d.get("resource_type") == "CPTN":
            col = "模様の画像"
        else:
            col = f"色 {d['color_hex']}"
        op = d.get("opacity256", 256) * 100 / 256
        print(f"  装飾{d['index']}: {d['role_ja']} {col} 不透明度 {op:g}% 太さ {d.get('width_px', d['width'])}px"
              + (f" + 追加 {d['extra']}px" if d.get("extra") else "") + f"（リソース {d['color_resource']}）")
    print(f"  字間: {i['spacings'][:len(i['text'])]}")
    # 字ごとに装飾が切り替わるファイル（属性は直前の字から引き継ぐ）
    per = t.decorations_per_char()
    sig = [tuple((d["kind"], d["color_resource"], d["opacity256"]) for d in ds) for ds in per]
    switch = [k for k in range(1, len(sig)) if sig[k] != sig[k - 1] and t.chars[k].code]
    for k in switch:
        print(f"  {k + 1} 字目（{t.chars[k].char}）から装飾が変わる:")
        for d in per[k]:
            print(f"      {d['role_ja']} " + _color_text(d) + f" 不透明度 {d['opacity256'] * 100 / 256:g}% 太さ {d['width_px']}px")
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
    if cmd == "texture-in":
        if len(files) < 2:
            print("使い方: python run.py texture-in 素材.ts1 画像.png [番号] [面]")
            return 1
        texture_in(cfg, files[0], files[1], files[2] if len(files) > 2 else None, files[3] if len(files) > 3 else 0)
        return 0
    if cmd == "gradient":
        if len(files) < 3:
            print("使い方: python run.py gradient 素材.ts1 番号 色1,色2,... [位置1,位置2,...]")
            return 1
        gradient_set(cfg, files[0], files[1], files[2], files[3] if len(files) > 3 else None)
        return 0
    if cmd == "check":
        try:
            return 1 if sum(stock_check(cfg, p) for p in files) else 0
        except Exception as e:
            print("  エラー:", e)
            traceback.print_exc()
            return 1
    if cmd == "catalog":
        try:
            catalog(cfg, files[0])
        except Exception as e:
            print("  エラー:", e)
            traceback.print_exc()
            return 1
        return 0
    if cmd == "batch":
        try:
            batch(cfg, files[0])
        except Exception as e:
            print("  エラー:", e)
            traceback.print_exc()
            return 1
        return 0
    if cmd == "text":
        try:
            retype(cfg, files[0], files[1] if len(files) > 1 else None)
        except Exception as e:
            print("  エラー:", e)
            traceback.print_exc()
            return 1
        return 0
    fn = {"to-ps": to_ps, "to-ts1": to_ts1, "info": info, "identify": identify, "texture": texture_out}[cmd]
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
