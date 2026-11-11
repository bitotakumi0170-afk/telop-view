#!/usr/bin/env python3
"""ts1tool — VWS テロップ (.ts1) ⇄ Photoshop 実験ツール

  python ts1tool.py info     in.ts1                     # 中身を表示（--history で編集履歴も）
  python ts1tool.py render   in.ts1 -o preview.png      # プレビュー画像（--svg でベクターも）
  python ts1tool.py to-psd   in.ts1 -o out.psd [--jsx out.jsx --ps-font KozGoPr6N-Heavy]
  python ts1tool.py from-psd in.psd -t template.ts1 -o out.ts1 [--font font.ttf]
  python ts1tool.py text     template.ts1 "新しい文言" -o out.ts1 [--font font.ttf]
  python ts1tool.py selftest in.ts1                     # 解析→再構築のバイト一致などを検証
  python ts1tool.py compare  in.ts1 vws見本.png --diff d.png  # VWS の見本画像と照合
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ts1core import TS1, FontGlyphSource, build_text  # noqa: E402


def load(p):
    with open(p, "rb") as f:
        return TS1(f.read())


def cmd_info(a):
    t = load(a.ts1)
    print(json.dumps(t.info(a.color_order), ensure_ascii=False, indent=1))
    if a.history:
        for h in t.history:
            ts = datetime.datetime.fromtimestamp(h.time, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            print(f"{h.id:5d} {ts} {h.user} | {h.action}")


def cmd_render(a):
    from render import render_preview, to_svg
    t = load(a.ts1)
    im = render_preview(t, color_order=a.color_order, edge=a.edge)
    im.save(a.out)
    print("保存:", a.out)
    if a.svg:
        with open(a.svg, "w", encoding="utf-8") as f:
            f.write(to_svg(t, color_order=a.color_order, edge=a.edge))
        print("保存:", a.svg)


def cmd_to_psd(a):
    from psdbridge import ts1_to_psd, ts1_to_jsx
    t = load(a.ts1)
    ts1_to_psd(t, a.out, color_order=a.color_order, edge=a.edge)
    print("保存:", a.out)
    if a.jsx:
        import os as _os
        name = _os.path.splitext(_os.path.basename(a.jsx))[0]
        hints = [h.split(",") for h in (a.font_hint or [])]
        ts1_to_jsx(t, a.jsx, name=name, ps_font=a.ps_font, font_hints=hints,
                   color_order=a.color_order, edge=a.edge)
        print("保存:", a.jsx)


def cmd_from_psd(a):
    from psdbridge import psd_to_ts1
    tmpl = load(a.template)
    out, info = psd_to_ts1(a.psd, tmpl, font_path=a.font, layer_name=a.layer,
                           font_id=a.font_id, color_order=a.color_order, clear_history=a.clear_history)
    with open(a.out, "wb") as f:
        f.write(out)
    print("PSD から読み取り:", json.dumps({k: v for k, v in info.items()}, ensure_ascii=False, default=str))
    print("保存:", a.out)
    print(json.dumps(TS1(out).info(a.color_order), ensure_ascii=False))


def cmd_text(a):
    tmpl = load(a.template)
    font = FontGlyphSource(a.font) if a.font else None
    text = a.text.replace("\\n", "\n")
    sp = None
    if a.spacing is not None:
        sp = [a.spacing] * (len(text) + 1)
    size = (a.size, a.size) if a.size else None
    chars, glyphs = build_text(tmpl, text, font=font, spacing=sp, size=size, font_id=a.font_id)
    pos = (a.x, a.y) if a.x is not None and a.y is not None else None
    out = tmpl.rebuild(chars=chars, glyphs=glyphs, pos=pos, clear_history=a.clear_history)
    with open(a.out, "wb") as f:
        f.write(out)
    print("保存:", a.out)
    print(json.dumps(TS1(out).info(a.color_order), ensure_ascii=False))


def cmd_compare(a):
    from calibrate import compare
    t = load(a.ts1)
    r = compare(t, a.ref, color_order=a.color_order, diff_path=a.diff)
    print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
    if a.diff:
        print("差分画像:", a.diff, "（赤 = 見本のみ / 緑 = 再現のみ / 黄 = 一致）")


def cmd_selftest(a):
    with open(a.ts1, "rb") as f:
        d = f.read()
    t = TS1(d)
    ok = True

    def check(name, cond):
        nonlocal ok
        ok &= bool(cond)
        print(("OK  " if cond else "NG  ") + name)

    check("解析→そのまま再構築でバイト一致", t.rebuild() == d)
    check("全グリフの輪郭データが再エンコードで一致",
          all(g.rchr_raw[len(g.rchr_raw) - len(g.encode_contours()):] == g.encode_contours() for g in t.glyphs))
    chars, glyphs = build_text(t, t.text, spacing=t.spacings())
    t2 = TS1(t.rebuild(chars=chars, glyphs=glyphs))
    check("同じ文言で組み直し → 文字列が一致", t2.text == t.text)
    check("同じ文言で組み直し → 字間が一致", t2.spacings()[:len(t.text)] == t.spacings()[:len(t.text)])
    bb = t.ink_bbox()
    check(f"推定レイアウトの描画範囲 {tuple(round(v) for v in bb)} が OFRI {t.ofri_bbox} と ±3px 以内",
          all(abs(x - y) <= 3 for x, y in zip(bb, t.ofri_bbox)))
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--color-order", choices=["RGBA", "BGRA"], default="RGBA",
                    help="CRGB のバイト順（既定 RGBA: E7 2F 12 = 赤）")
    ap.add_argument("--edge", type=float, default=None, help="縁の太さ px（既定: ファイルから推定）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info"); p.add_argument("ts1"); p.add_argument("--history", action="store_true")
    p.set_defaults(f=cmd_info)

    p = sub.add_parser("render"); p.add_argument("ts1"); p.add_argument("-o", "--out", required=True)
    p.add_argument("--svg"); p.set_defaults(f=cmd_render)

    p = sub.add_parser("to-psd"); p.add_argument("ts1"); p.add_argument("-o", "--out", required=True)
    p.add_argument("--jsx"); p.add_argument("--ps-font", help="Photoshop のフォント PostScript 名")
    p.add_argument("--font-hint", action="append", help="フォント名に含まれる語（カンマ区切りで AND、複数指定可）例: ウィン,L7")
    p.set_defaults(f=cmd_to_psd)

    p = sub.add_parser("selftest"); p.add_argument("ts1"); p.set_defaults(f=cmd_selftest)

    p = sub.add_parser("compare", help="VWS の見本画像（透過 PNG）と照合")
    p.add_argument("ts1"); p.add_argument("ref"); p.add_argument("--diff", help="差分画像の出力先")
    p.set_defaults(f=cmd_compare)

    for name, f in (("from-psd", cmd_from_psd), ("text", cmd_text)):
        p = sub.add_parser(name)
        if name == "from-psd":
            p.add_argument("psd"); p.add_argument("-t", "--template", required=True)
            p.add_argument("--layer", help="テキストレイヤ名（既定: 最初のテキストレイヤ）")
        else:
            p.add_argument("template"); p.add_argument("text", help="改行は \\n")
            p.add_argument("--size", type=float); p.add_argument("--spacing", type=float)
            p.add_argument("--x", type=float); p.add_argument("--y", type=float)
        p.add_argument("-o", "--out", required=True)
        p.add_argument("--font", help="グリフを取り出す TTF/OTF（省略時はテンプレート内の字形のみ使用可）")
        p.add_argument("--font-id", type=lambda s: int(s, 0), help="VWS フォント番号（既定: テンプレートと同じ）")
        p.add_argument("--clear-history", action="store_true", help="編集履歴(OMR0)を空にする")
        p.set_defaults(f=f)

    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
