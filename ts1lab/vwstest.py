#!/usr/bin/env python3
"""VWS 確認用の .ts1 一式を作る / VWS の書き出し PNG と照合する

  python vwstest.py make  見本.ts1 [出力フォルダ]   … 見本をもとに、条件を 1 つずつ変えた .ts1 と「予想の見た目」PNG を作る
  python vwstest.py check フォルダ                  … 同じ名前の「○○.png」（VWS の書き出し）があれば .ts1 と照合する

VWS で各 .ts1 を開いて PNG で書き出し、.ts1 と同じ名前（01_打ち替え.png など）で同じフォルダに置いてから check する。
未確認の点（縁幅を変えたときの縁の形、サイズ・位置・色の反映、文字ごとの位置の癖）をこれで確かめる。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

import run  # noqa: E402
from ts1core import TS1, FontGlyphSource, build_text  # noqa: E402
from psdbridge import ts1_to_png  # noqa: E402

NEW_TEXT = "文字を打ち替えてテストしています"


def make(src, outdir):
    cfg = run.load_config()
    with open(src, "rb") as f:
        t = TS1(f.read())
    e = run.font_for_id(cfg, t.font_id, t)
    font = FontGlyphSource(e["file"], int(e.get("index", 0)), fit=run._fit_of(e)) if e and e.get("file") else None
    n = len(t.text)
    zero = lambda k: [0.0] * (k + 1)
    os.makedirs(outdir, exist_ok=True)

    def retext(text, size=None):
        chars, glyphs = build_text(t, text, font=font, spacing=zero(len(text)), size=size, prefer_template=True)
        return dict(chars=chars, glyphs=glyphs)

    cases = [
        ("01_打ち替え", "文言だけ変更（新しい字はフォントから作成）", retext(NEW_TEXT) if font else None),
        ("02_縁3px", "縁の太さだけ 3px に", dict(edge_width=3)),
        ("03_縁10px", "縁の太さだけ 10px に", dict(edge_width=10)),
        ("04_サイズ60", "文字サイズだけ 60 に（字間は 0）", retext(t.text, size=(60, 60))),
        ("05_位置変更", "位置だけ 左へ 100px・上へ 400px", dict(pos=(t.pos[0] - 100, t.pos[1] - 400))),
        ("06_色変更", "色だけ 文字 黄 #FFE600・縁 黒 #000000", dict(face_color=(255, 230, 0, 255), edge_color=(0, 0, 0, 255))),
    ]
    lines = ["VWS 確認用の一式（元: %s 「%s」）" % (os.path.basename(src), t.text), "",
             "やり方:",
             "  1. 下の .ts1 を 1 つずつ VWS（テスト環境）で開く",
             "  2. 開けたら PNG で書き出し、.ts1 と同じ名前で このフォルダに置く（例: 01_打ち替え.png）",
             "  3. 「VWSの書き出しと照合する.bat」にこのフォルダをドロップ（または python vwstest.py check このフォルダ）",
             "  ※ 開けないもの・フォント名が違うものがあれば、その番号を教えてください", ""]
    for name, desc, kw in cases:
        if kw is None:
            lines.append(f"{name}: フォントが PC に無いため作れませんでした")
            continue
        out = t.rebuild(**kw)
        with open(os.path.join(outdir, name + ".ts1"), "wb") as f:
            f.write(out)
        t2 = TS1(out)
        ts1_to_png(t2, os.path.join(outdir, name + "_予想.png"))
        i = t2.info()
        lines.append(f"{name}.ts1 … {desc}")
        lines.append(f"    文言「{i['text']}」 文字色 {i['face_color']} 縁色 {i['edge_color']} 縁 {i['edge_width']}px "
                     f"サイズ {i['char_size'][0]:g} 位置 x={i['position_x']:g} y={i['baseline_y']:g} フォント番号 {i['font_id']}")
        print(f"  {name}.ts1 / {name}_予想.png  … {desc}")
    with open(os.path.join(outdir, "やり方.txt"), "w", encoding="utf-8-sig") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  → {outdir}（やり方.txt を見てください）")


def _premult(x):
    y = x.astype(float).copy()
    y[..., :3] *= y[..., 3:4] / 255
    return y


def check(folder):
    found = 0
    for fn in sorted(os.listdir(folder)):
        if not fn.lower().endswith(".ts1"):
            continue
        base = os.path.join(folder, os.path.splitext(fn)[0])
        png = base + ".png"
        if not os.path.exists(png):
            print(f"{fn}: VWS の書き出し（{os.path.basename(png)}）がまだありません")
            continue
        found += 1
        with open(os.path.join(folder, fn), "rb") as f:
            t = TS1(f.read())
        tmp = base + "_予想.png"
        ts1_to_png(t, tmp)
        a = np.asarray(Image.open(png).convert("RGBA")).astype(float)
        b = np.asarray(Image.open(tmp).convert("RGBA")).astype(float)
        if a.shape != b.shape:
            print(f"{fn}: 画像の大きさが違います（VWS {a.shape[1]}x{a.shape[0]} / 予想 {b.shape[1]}x{b.shape[0]}）")
            continue
        ink = (a[..., 3] > 0) | (b[..., 3] > 0)
        d = np.abs(_premult(a) - _premult(b)).max(-1)
        bb = lambda m: tuple(int(v) for v in (np.nonzero(m.any(0))[0][[0, -1]].tolist() + np.nonzero(m.any(1))[0][[0, -1]].tolist())) if m.any() else None
        op = a[..., 3] > 250
        cols, cnt = np.unique(a[op][:, :3].astype(int), axis=0, return_counts=True)
        top = [tuple(int(v) for v in cols[k]) for k in np.argsort(-cnt)[:2]]
        print(f"{fn}: 平均差 {d[ink].mean() / 255 * 100:.2f}%  完全一致 {100 * (d[ink] == 0).mean():.1f}%  差32超 {100 * (d[ink] > 32).mean():.2f}%")
        print(f"    範囲(左,右,上,下) VWS {bb(a[..., 3] > 128)} / 予想 {bb(b[..., 3] > 128)}   VWS の主な色 {top}")
        Image.fromarray(np.clip(d * 3, 0, 255).astype(np.uint8)).save(base + "_差.png")
    if not found:
        print("照合できる組がありません。VWS の書き出し PNG を .ts1 と同じ名前で置いてください")


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ("make", "check"):
        print(__doc__)
        return 1
    if sys.argv[1] == "make":
        src = sys.argv[2]
        out = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(os.path.abspath(src)), "VWS確認用")
        make(src, out)
    else:
        check(sys.argv[2])
    return 0


if __name__ == "__main__":
    sys.exit(main())
