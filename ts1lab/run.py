#!/usr/bin/env python3
"""VWSフォトショブリッジツール — ドラッグ＆ドロップ用の入口（設定.json を読んで変換する）

  python run.py to-ps  素材.ts1 ...   → 素材_再現.png / 素材_画像レイヤ.psd / 素材_Photoshop用.jsx
  python run.py to-ts1 テロップ.psd ... → テロップ.ts1 / テロップ_確認.png
  python run.py info   素材.ts1 ...   → 文字・色・縁・サイズなどを表示、全項目を 素材_構造.txt に保存
  python run.py diff   A.ts1 B.ts1    → 2 つの違いを項目単位で表示（A_差分.txt に保存）
  python run.py identify 素材.ts1     → 字形を PC の全フォントと照合してフォントを特定、フォント表.json に登録
"""
import json
import os
import sys
import traceback

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


def load_table(cfg):
    p = table_path(cfg)
    if os.path.exists(p):
        with open(p, encoding="utf-8-sig") as f:
            return json.load(f)
    return {}


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
            e["file"], e["index"], e["names"] = r[0], r[1], r[2]
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


def font_for_id(cfg, font_id, ts1=None):
    """VWS フォント番号 → フォント表のエントリ（file が入っていれば PC にフォントあり）。未登録なら None"""
    e = load_table(cfg).get(str(font_id))
    return _resolve_entry(cfg, font_id, e, ts1) if e else None


def font_for_ps(cfg, ps_name, ts1=None):
    """Photoshop のフォント (PostScript 名) → フォント表の (VWS 番号, エントリ)"""
    import fontfind
    if not ps_name:
        return None
    for fid, e in load_table(cfg).items():
        names = [e.get("postscript")] + list(e.get("names", []))
        hit = any(n and fontfind.norm(n) == fontfind.norm(ps_name) for n in names)
        hit = hit or any(fontfind.hint_match(ps_name, h) for h in e.get("hints", []))
        if hit:
            return int(fid), _resolve_entry(cfg, fid, e, ts1)
    return None


def _fit_of(e):
    return {k: e[k] for k in ("scale", "ox", "oy")} if e and "scale" in e else None


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
    """TS1 の字形を PC の全フォントと照合してフォントを特定し、フォント表に登録する"""
    import fontid
    import fontfind
    with open(path, "rb") as f:
        t = TS1(f.read())
    fid = t.font_id
    print(f"  文言: {t.text}")
    print(f"  VWS フォント番号: {fid}")
    print("  PC のフォントと字形を照合中…（フォントが多いと数分かかります）")
    cache = os.path.join(cfg["_dir"], ".font_cache.json")
    if os.path.exists(cache):
        os.remove(cache)  # 毎回最新のフォント一覧で照合
    res = fontid.identify(t, cache_path=cache,
                          progress=lambda k, n: print(f"\r    {k}/{n}", end="", flush=True))
    print("\r" + " " * 20)
    if not res:
        print("  照合できるフォントがありませんでした")
        return
    print("  一致度  フォント")
    for score, names, fp, idx, fit in res[:8]:
        print(f"   {score * 100:5.1f}%  {' / '.join(names[:3])}   [{os.path.basename(fp)}]")
    score, names, fp, idx, fit = res[0]
    if score >= 0.95:
        ps = fontfind.postscript_name(fp, idx)
        jp = [n for n in names if any(ord(ch) > 0x2E80 for ch in n)]
        name = (jp or names or [ps])[0]
        tbl = load_table(cfg)
        old = tbl.get(str(fid), {})
        tbl[str(fid)] = {"name": old.get("name") or name, "names": names, "postscript": ps, "file": fp, "index": idx,
                         "score": round(score, 4), **({k: round(v, 6) for k, v in fit.items()} if fit else {}),
                         **({"hints": old["hints"]} if "hints" in old else {})}
        save_table(cfg, tbl)
        print(f"\n  ★ 特定: VWS フォント番号 {fid} = 「{name}」 (PostScript: {ps})")
        print("     フォント表.json に登録しました。以後の変換でこのフォントを使います")
    else:
        print(f"\n  ※ 一致するフォントはこの PC に入っていません（最高 {score * 100:.1f}%。95% 以上で一致とみなします）")
        print("     このフォントが入っている PC（VWS 端末など）で実行してください")


def to_ps(cfg, path):
    from psdbridge import ts1_to_png, ts1_to_psd, ts1_to_jsx
    base = os.path.splitext(path)[0]
    name = os.path.basename(base)
    with open(path, "rb") as f:
        t = TS1(f.read())
    info = t.info()
    print(f"  文言: {info['text']}")
    print(f"  文字色 {info['face_color']} / 縁色 {info['edge_color']} / 縁 {info['edge_width']}px / "
          f"サイズ {info['char_size'][0]:g} / VWS フォント番号 {info['font_id']}")
    ratio, ps_font, hints = 1.0, cfg.get("photoshop_font"), list(cfg.get("font_name_hints", []))
    e = font_for_id(cfg, t.font_id, t)
    if e:
        ps_font = e.get("postscript") or ps_font
        hints = list(e.get("hints", [])) + hints
        print(f"  フォント: {e.get('name')}（フォント表: VWS 番号 {t.font_id}）")
        if e.get("file"):
            try:
                ratio = FontGlyphSource(e["file"], int(e.get("index", 0)), fit=_fit_of(e)).cell_ratio
            except Exception:
                pass
    else:
        print(f"  ※ フォント番号 {t.font_id} は未登録です。「フォントを特定する」に .ts1 をドロップすると登録できます")
        fp, fi = find_font(cfg)
        if fp:
            try:
                ratio = FontGlyphSource(fp, fi).cell_ratio
            except Exception:
                pass
    ts1_to_png(t, base + "_再現.png")
    ts1_to_psd(t, base + "_画像レイヤ.psd")
    ts1_to_jsx(t, base + "_Photoshop用.jsx", name=name, ps_font=ps_font,
               font_hints=hints, cell_ratio=ratio,
               ref_png=name + "_再現.png")
    print(f"  → {name}_再現.png / {name}_画像レイヤ.psd / {name}_Photoshop用.jsx")


def to_ts1(cfg, path):
    from psdbridge import psd_to_ts1, read_psd_text, ts1_to_png
    base = os.path.splitext(path)[0]
    tmpl_path = resolve(cfg, cfg.get("template", "テンプレート.ts1"))
    with open(tmpl_path, "rb") as f:
        tmpl = TS1(f.read())
    info = read_psd_text(path)
    print(f"  文言: {info['text']}  フォント: {info['font']}  サイズ: {info['size']:g}px")
    font_id, fit, fp, fi = cfg.get("vws_font_id"), None, None, 0
    hit = font_for_ps(cfg, info["font"], tmpl)
    if hit:
        font_id, e = hit
        fp, fi, fit = e.get("file"), int(e.get("index", 0)), _fit_of(e)
        print(f"  VWS フォント番号 {font_id}「{e.get('name')}」（フォント表より）")
    else:
        print(f"  ※ 「{info['font']}」の VWS フォント番号は未登録です。番号はテンプレートと同じ {font_id or tmpl.font_id} を書き込みます")
        print("     （VWS では字形はそのまま表示されますが、フォント名はその番号のフォントになります）")
        fp, fi = find_font(cfg, info["font"])
    if not fp:
        print("  ※ フォントファイルが見つからないため、テンプレート内の字形だけで組みます")
    out, info = psd_to_ts1(path, tmpl, font_path=fp, font_index=fi, font_fit=fit,
                           font_id=font_id, clear_history=cfg.get("clear_history", False))
    with open(base + ".ts1", "wb") as f:
        f.write(out)
    t = TS1(out)
    ts1_to_png(t, base + "_確認.png")
    i = t.info()
    print(f"  文字色 {i['face_color']} / 縁色 {i['edge_color']} / 縁 {i['edge_width']}px / サイズ {i['char_size'][0]:g}")
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
