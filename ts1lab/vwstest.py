#!/usr/bin/env python3
"""VWS 確認用の .ts1 一式を作る / VWS の書き出し PNG と照合する

  python vwstest.py make  見本.ts1 [出力フォルダ] [1行ものの見本.ts1]
        … 見本をもとに、条件を 1 つずつ変えた .ts1 と「予想の見た目」PNG を作る
  python vwstest.py check フォルダ
        … 「○○.png」か「[○○].png」（VWS の書き出し）があれば、同じ名前の .ts1 と画素単位で照合する

VWS で各 .ts1 を開いて PNG で書き出し、同じフォルダに置いてから check する。
PNG は 全画面（1920×1080）でも、描画範囲だけ切り抜いたものでもよい（切り抜きは OFRI の範囲 + 右下 1px として位置を合わせる）。

これまでに VWS で確かめたこと（第 1 回: 打ち替え・縁 3px・縁 10px・サイズ 60・位置・色）:
  縁 3/10px・サイズ 60・位置・色は 1 画素も違わず一致。打ち替えは「曲線レコードは 2 点ずつ」でないと字が崩れると分かった。
第 2 回（この make が作る一式）で確かめること:
  打ち替えの字形（直した版）/ 縁 1・2・5・14・20px（縁の円の半径の式）/ 文字が縁より暗い色 / 字間あり / 1 行もの（縁なし）とその打ち替え
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

import run  # noqa: E402
import vwsrender  # noqa: E402
from ts1core import TS1, build_text  # noqa: E402
from psdbridge import ts1_to_png  # noqa: E402

NEW_TEXT = "文字を打ち替えてテストしています"
NEW_TEXT_1LINE = "打ち替えテスト"


def _describe(t2):
    i = t2.info()
    edge = "なし" if not i["edge_width"] else f"{i['edge_width']}px"
    return (f"    文言「{i['text']}」 文字色 {i['face_color']} 縁色 {i['edge_color']} 縁 {edge} "
            f"サイズ {i['char_size'][0]:g} 位置 x={i['position_x']:g} y={i['baseline_y']:g} フォント番号 {i['font_id']}")


def make(src, outdir, src_1line=None):
    cfg = run.load_config()
    with open(src, "rb") as f:
        t = TS1(f.read())
    e = run.font_for_id(cfg, t.font_id, t)
    font = run.glyph_source(cfg, t.font_id, e, t)
    os.makedirs(outdir, exist_ok=True)

    def retext(tt, text, fnt, spacing=0.0, size=None):
        chars, glyphs = build_text(tt, text, font=fnt, spacing=[spacing] * (len(text) + 1), size=size, prefer_template=True)
        return dict(chars=chars, glyphs=glyphs)

    cases = [
        ("11_打ち替え", "文言だけ変更（新しい字は Windows のフォントから VWS と同じ方法で作成）", t, retext(t, NEW_TEXT, font) if font else None),
        ("12_縁1px", "縁の太さだけ 1px に", t, dict(edge_width=1)),
        ("13_縁2px", "縁の太さだけ 2px に", t, dict(edge_width=2)),
        ("14_縁5px", "縁の太さだけ 5px に", t, dict(edge_width=5)),
        ("15_縁14px", "縁の太さだけ 14px に", t, dict(edge_width=14)),
        ("16_縁20px", "縁の太さだけ 20px に", t, dict(edge_width=20)),
        ("17_暗い文字", "色だけ 文字 濃いグレー #141414・縁 白 #FFFFFF（文字が縁より暗いときの色の丸め）",
         t, dict(face_color=(20, 20, 20, 255), edge_color=(255, 255, 255, 255))),
        ("18_字間20", "先頭 12 字だけにして字間を 20px に", t, retext(t, t.text[:12], font, spacing=20.0)),
    ]
    if src_1line:
        with open(src_1line, "rb") as f:
            t1 = TS1(f.read())
        e1 = run.font_for_id(cfg, t1.font_id, t1)
        font1 = run.glyph_source(cfg, t1.font_id, e1, t1)
        cases += [
            ("19_1行もの_そのまま", "1 行もの（縁なし）の見本をそのまま。書き出しの見た目を知りたい", t1, {}),
            ("20_1行もの_打ち替え", "1 行もの（縁なし）の文言だけ変更", t1, retext(t1, NEW_TEXT_1LINE, font1) if font1 else None),
        ]
    lines = ["VWS 確認用の一式・第 2 回（元: %s 「%s」）" % (os.path.basename(src), t.text), "",
             "やり方:",
             "  1. 下の .ts1 を 1 つずつ VWS（テスト環境）で開く",
             "  2. 開けたら PNG で書き出す（前回と同じやり方で大丈夫です。名前は [11_打ち替え].png のままで構いません）",
             "  3. 書き出した PNG をまとめて送ってください（こちらで照合します）",
             "  ※ 開けないもの・字が崩れるものがあれば、その番号を教えてください", ""]
    for name, desc, base, kw in cases:
        if kw is None:
            lines.append(f"{name}: フォントが PC に無いため作れませんでした")
            continue
        out = base.rebuild(**kw) if kw else base.data
        with open(os.path.join(outdir, name + ".ts1"), "wb") as f:
            f.write(out)
        t2 = TS1(out)
        ts1_to_png(t2, os.path.join(outdir, name + "_予想.png"))
        lines.append(f"{name}.ts1 … {desc}")
        lines.append(_describe(t2))
        print(f"  {name}.ts1 / {name}_予想.png  … {desc}")
    with open(os.path.join(outdir, "やり方.txt"), "w", encoding="utf-8-sig") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  → {outdir}（やり方.txt を見てください）")


def _find_png(folder, stem):
    for cand in (stem + ".png", f"[{stem}].png"):
        p = os.path.join(folder, cand)
        if os.path.exists(p):
            return p
    return None


def compare(t, png_path):
    """.ts1 を VWS と同じ描き方で描いて PNG と比べる → (違う画素数, 色のある画素数, 最大差, 切り抜き原点 or None, 差分画像)"""
    ref = np.asarray(Image.open(png_path).convert("RGBA")).astype(int)
    H, W = ref.shape[:2]
    full = vwsrender.render_rgba(t).astype(int)
    if (W, H) == (full.shape[1], full.shape[0]):
        cands = [(0, 0)]
    else:
        # 切り抜き: VWS は描画範囲の左上から (幅 + 1, 高さ + 1) で書き出す。念のため ±2px も試す
        l, tp = (int(v) for v in vwsrender.drawn_bbox(t)[:2])
        cands = [(l + dx, tp + dy) for dx in (0, -1, 1, -2, 2) for dy in (0, -1, 1, -2, 2)]
    best = None
    for ox, oy in cands:
        if ox < 0 or oy < 0 or ox + W > full.shape[1] or oy + H > full.shape[0]:
            continue
        out = full[oy:oy + H, ox:ox + W]
        vis = (ref[..., 3] > 0) | (out[..., 3] > 0)
        d = np.abs(out - ref).max(-1) * vis
        n = int((d > 0).sum())
        if best is None or n < best[0]:
            best = (n, int(vis.sum()), int(d.max()), (ox, oy), d)
        if n == 0:
            break
    return best


def check(folder):
    found = 0
    for fn in sorted(os.listdir(folder)):
        if not fn.lower().endswith(".ts1"):
            continue
        stem = os.path.splitext(fn)[0]
        png = _find_png(folder, stem)
        if not png:
            print(f"{fn}: VWS の書き出し（{stem}.png）がまだありません")
            continue
        found += 1
        with open(os.path.join(folder, fn), "rb") as f:
            t = TS1(f.read())
        r = compare(t, png)
        if r is None:
            print(f"{fn}: 画像の大きさが合いません")
            continue
        n, tot, mx, org, d = r
        verdict = "完全一致" if n == 0 else f"違う画素 {n}（最大差 {mx}/255）"
        print(f"{fn}: 色のある {tot} 画素 → {verdict}  一致率 {100 * (1 - n / max(tot, 1)):.3f}%  書き出しの左上 {org}")
        if n:
            Image.fromarray(np.clip(d * 8, 0, 255).astype(np.uint8)).save(os.path.join(folder, stem + "_差.png"))
    if not found:
        print("照合できる組がありません。VWS の書き出し PNG を .ts1 と同じ名前で置いてください")


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ("make", "check"):
        print(__doc__)
        return 1
    if sys.argv[1] == "make":
        src = sys.argv[2]
        out = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(os.path.abspath(src)), "VWS確認用")
        make(src, out, sys.argv[4] if len(sys.argv) > 4 else None)
    else:
        check(sys.argv[2])
    return 0


if __name__ == "__main__":
    sys.exit(main())
