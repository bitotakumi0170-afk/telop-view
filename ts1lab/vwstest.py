#!/usr/bin/env python3
"""VWS 確認用の .ts1 一式を作る / VWS の書き出し PNG と照合する

  python vwstest.py make  見本.ts1 [出力フォルダ] [1行ものの見本.ts1]
        … 見本をもとに、条件を 1 つずつ変えた .ts1 と「予想の見た目」PNG を作る
  python vwstest.py extra 見本.ts1 [出力フォルダ]
        … 追加の確認用（半角入り・2 行・横 85%・サイズ 30・空白入り・ニューロダン EB）と、フォント番号調べ用の .ts1 を作る
  python vwstest.py probe 見本.ts1 [出力フォルダ]
        … 第 3 回の確認用（字送りの 1/256px 調べ・半角のあと・縁 4/7/8/9/12px・縦位置の端数・ニューロダン EB）を作る
  python vwstest.py origins フォルダ
        … 字ごとに「VWS の PNG と合う原点」を求め、式で出した位置との差を 1/8px 単位で出す（字送り調べ 31〜34 の読み取り用）
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


def make_extra(src, outdir):
    """追加の確認用。まだ VWS で確かめていない並べ方・描き方を 1 つずつ入れる"""
    import struct
    import gdiglyph
    from ts1core import fx
    cfg = run.load_config()
    with open(src, "rb") as f:
        t = TS1(f.read())
    e = run.font_for_id(cfg, t.font_id, t)
    font = run.glyph_source(cfg, t.font_id, e, t)
    if font is None:
        print("  フォントが PC に無いため作れません")
        return
    os.makedirs(outdir, exist_ok=True)

    def retext(text, fnt=font, size=None, font_id=None, prefer=True, mag=None):
        chars, glyphs = build_text(t, text, font=fnt, spacing=[0.0] * (len(text) + 1), size=size, font_id=font_id,
                                   prefer_template=prefer)
        if mag is not None:      # 文字の倍率 %（横, 縦）
            chars[0].attrs["ACMG"] = struct.pack("<Hii", 1, fx(mag[0]), fx(mag[1]))
        return dict(chars=chars, glyphs=glyphs)

    cases = [
        ("21_半角入り", "半角の英数字が入った文言（字ごとに送り幅が違うときの並び）", retext("ABCabc123でテストする")),
        ("22_2行", "改行して 2 行にしたもの", retext("1行目のテストです\n2行目です")),
        ("23_横85%", "文字の横幅だけ 85% に", retext(t.text, mag=(85, 100))),
        ("24_サイズ30", "文字サイズだけ 30 に（小さい字での曲線・切り方）", retext(t.text, size=(30, 30))),
        ("25_空白入り", "半角の空白と全角の空白が入った文言", retext("空白 あり　全角")),
    ]
    # ニューロダン EB（番号 1121）で同じ文言
    eb = run.load_table(cfg).get("1121", {})
    try:
        if eb.get("face"):
            src_eb = gdiglyph.GdiGlyphSource(eb["face"], int(eb.get("y_shift", 183)), int(eb.get("weight", 0)))
            cases.append(("26_EB", "フォントだけ ニューロダン EB（番号 1121）に。VWS のフォント名の表示も EB になるはず",
                          retext(t.text, fnt=src_eb, font_id=1121, prefer=False)))
    except Exception as ex:
        print(f"  ※ ニューロダン EB が無いので 26 は作りません（{ex}）")
    lines = ["VWS 確認用・追加分（元: %s 「%s」）" % (os.path.basename(src), t.text), "",
             "第 2 回（11〜20）と同じやり方で、VWS で開いて PNG を書き出してください。余裕があれば、で大丈夫です。",
             "開けないもの・字が崩れるもの・並びがおかしいものがあれば、その番号を教えてください。", ""]
    for name, desc, kw in cases:
        out = t.rebuild(**kw)
        with open(os.path.join(outdir, name + ".ts1"), "wb") as f:
            f.write(out)
        t2 = TS1(out)
        ts1_to_png(t2, os.path.join(outdir, name + "_予想.png"))
        lines += [f"{name}.ts1 … {desc}", _describe(t2)]
        print(f"  {name}.ts1 / {name}_予想.png  … {desc}")

    # フォント番号調べ: 字形はニューロダン B のまま、フォント番号だけ変える。VWS は番号からフォント名を表示するので、
    # 開いたときに表示されるフォント名を教えてもらえば 番号 → フォント の表が増える
    probe = os.path.join(outdir, "フォント番号調べ")
    os.makedirs(probe, exist_ok=True)
    nums = [1113, 1114, 1115, 1116, 1117, 1118, 1120, 1122, 1123, 1124]
    plines = ["フォント番号調べ（やらなくても大丈夫です）", "",
              "見た目は全部同じ（ニューロダン B の字形）で、中のフォント番号だけ変えてあります。",
              "VWS で開いて文字を選んだとき、フォント名の欄に何と出るかを教えてください（PNG は不要です）。",
              "分かった番号はフォント表に登録して、次から名前が自動で出るようにします。",
              "参考: 1119 = ニューロダン B、1121 = ニューロダン EB は確認済みです。", ""]
    for n in nums:
        kw = retext(t.text, font_id=n, prefer=False)
        with open(os.path.join(probe, f"番号{n}.ts1"), "wb") as f:
            f.write(t.rebuild(**kw))
        plines.append(f"番号{n}.ts1 … フォント名: ")
    with open(os.path.join(probe, "やり方.txt"), "w", encoding="utf-8-sig") as f:
        f.write("\n".join(plines) + "\n")
    with open(os.path.join(outdir, "やり方.txt"), "w", encoding="utf-8-sig") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  フォント番号調べ: {len(nums)} 本 → {probe}")
    print(f"  → {outdir}（やり方.txt を見てください）")


PROBE_RULER = "いいいいいいいいいいいい"
PROBE_LINES = [                    # (行の文言, 何を見るか)
    ("いでいい", "で（右の余白 8/2048）"),
    ("いグいい", "グ（右の余白 2/2048）"),
    ("いガいい", "ガ（右の余白 8/2048。で と同じ余白で別の字）"),
    ("いひいい", "ひ（右の余白 12/2048。濁点の無い字）"),
    ("いがいい", "が（右の余白 22/2048）"),
    ("いCでいい", "半角 1 字のあとの で"),
    ("いabcB3でいい", "半角 5 字のあとの で（21_半角入りと同じ「3 のあと」）"),
]


def make_probe(src, outdir):
    """第 3 回の確認用。

    字送り調べ（31・32・33）の考え方:
      VWS は字を 1/256px の整数で並べ、原点を 1/8px（= 32/256）へ切り捨てて描く。だから「原点が 32 の倍数より 1 つ手前（余り 31）」に
      なるよう枠の位置を 1/256px 単位で決めておくと、字送りが式より 1/256px でも長い字のあとは、うしろの字が 1/8px 右へ動いて見える。
      余り 0 にしておくと、逆に 1/256px 短い字のあとが 1/8px 左へ動く。1 行目（「い」だけの長い行。い の字送りは 100px ちょうどと確認済み）が
      枠の中の左端を決めるので、2 行目以降はそれぞれ独立に 1 字ずつ調べられる。
      きっかけ: 21_半角入り の「で」だけ、式より 1/256px 左に描かれていた（「で」の字送りが 1/256px 長いと考えると 249 字すべて合う）
    """
    import gdiglyph
    cfg = run.load_config()
    with open(src, "rb") as f:
        t = TS1(f.read())
    e = run.font_for_id(cfg, t.font_id, t)
    font = run.glyph_source(cfg, t.font_id, e, t)
    if font is None:
        print("  フォントが PC に無いため作れません")
        return
    os.makedirs(outdir, exist_ok=True)

    def retext(text, fnt=font, font_id=None, prefer=True):
        chars, glyphs = build_text(t, text, font=fnt, spacing=[0.0] * (len(text) + 1), font_id=font_id, prefer_template=prefer)
        return dict(chars=chars, glyphs=glyphs)

    def aligned(text, rem):
        """枠の中の左端（1/256px）を 32 で割った余りが rem になるように、枠の x を 1/256px 単位でずらして作る"""
        t2 = TS1(t.rebuild(**retext(text)))
        x0, bw = int(round(t2.pos[0] * 256)), int(round(t2.box[0] * 256))
        start = x0 + (bw - max(t2.line_widths_fx())) // 2
        x0 += (rem - start) % 32
        out = t2.rebuild(pos=(x0 / 256.0, t2.pos[1]))
        t3 = TS1(out)
        assert t3.layout_fx()[0][2] % 32 == rem, (t3.layout_fx()[0][2], rem)
        return out

    multi = "\n".join([PROBE_RULER] + [ln for ln, _ in PROBE_LINES])
    cases = [
        ("31_字送り調べA", "8 行。字送りが式より長い字があると、その行のうしろの字が 1/8px 右へ動く（左端の余り 31）", aligned(multi, 31)),
        ("32_字送り調べB", "同じ 8 行。字送りが式より短い字があると 1/8px 左へ動く（左端の余り 0）", aligned(multi, 0)),
        ("33_で2行", "31 の 1・2 行目だけ（8 行がうまく開けなかったときの控え）", aligned(PROBE_RULER + "\n" + PROBE_LINES[0][0], 31)),
        ("34_半角のあと", "21_半角入り の「で」を「い」に替えたもの（字のせいか、半角のあとという場所のせいかを分ける）",
         t.rebuild(**retext("ABCabc123いテストする"))),
    ]
    for w in (4, 7, 8, 9, 12):
        cases.append((f"{31 + len(cases)}_縁{w}px", f"縁の太さだけ {w}px に（縁の形の表にまだ無い太さ）", t.rebuild(edge_width=w)))
    cases.append((f"{31 + len(cases)}_縦の端数", "位置だけ 20/256px（約 0.08px）下へ。縦の位置の丸め方（切り捨てか四捨五入か）が分かる",
                  t.rebuild(pos=(t.pos[0], t.pos[1] + 20 / 256.0))))
    eb = run.load_table(cfg).get("1121", {})
    try:
        if eb.get("face"):
            src_eb = gdiglyph.GdiGlyphSource(eb["face"], int(eb.get("y_shift", 183)), int(eb.get("weight", 0)))
            cases.append((f"{31 + len(cases)}_EB", "フォントだけ ニューロダン EB（番号 1121）に（前回の 26_EB と同じもの。書き出しが無かったので）",
                          t.rebuild(**retext(t.text, fnt=src_eb, font_id=1121, prefer=False))))
    except Exception as ex:
        print(f"  ※ ニューロダン EB が無いので EB は作りません（{ex}）")
    lines = ["VWS 確認用の一式・第 3 回（元: %s 「%s」）" % (os.path.basename(src), t.text), "",
             "これまでと同じやり方で、VWS（テスト環境）で 1 つずつ開いて PNG で書き出してください。",
             "開けないもの・字が崩れるもの・並びがおかしいものがあれば、その番号を教えてください。", "",
             "31・32 は 8 行あります（行ごとに別の字を調べます）。もし 8 行がうまく開けなければ、33 だけでも助かります。",
             "31 の行: 1 行目 " + PROBE_RULER] + [f"         {i + 2} 行目 {ln} … {d}" for i, (ln, d) in enumerate(PROBE_LINES)] + [""]
    for name, desc, out in cases:
        with open(os.path.join(outdir, name + ".ts1"), "wb") as f:
            f.write(out)
        t2 = TS1(out)
        ts1_to_png(t2, os.path.join(outdir, name + "_予想.png"))
        lines += [f"{name}.ts1 … {desc}", _describe(t2)]
        print(f"  {name}.ts1 / {name}_予想.png  … {desc}")
    with open(os.path.join(outdir, "やり方.txt"), "w", encoding="utf-8-sig") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  → {outdir}（やり方.txt を見てください）")


def _find_png(folder, stem):
    """「○○.png」「[○○].png」のほか、後ろに書き足した名前（「[○○]_ニューロダンB.png」など）も探す"""
    for cand in (stem + ".png", f"[{stem}].png"):
        p = os.path.join(folder, cand)
        if os.path.exists(p):
            return p
    for fn in sorted(os.listdir(folder)):
        if fn.lower().endswith(".png") and not fn.endswith("_予想.png") and not fn.endswith("_差.png")                 and (fn.startswith(f"[{stem}]") or fn.startswith(stem + "_") and fn[len(stem) + 1:len(stem) + 3] not in ("予想", "差.")):
            return os.path.join(folder, fn)
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


def diagnose(t, png_path, org):
    """合わなかったときの見当をつける: 文字面だけなら合うか / 縁のずらし量の円はどこまでか"""
    ref = np.asarray(Image.open(png_path).convert("RGBA")).astype(int)
    H, W = ref.shape[:2]
    ox, oy = org
    kfull = vwsrender.face_counts(t)
    al = np.zeros(kfull.shape, int)
    al[oy:oy + H, ox:ox + W] = ref[..., 3]
    edge = int(t.edge_width() or 0)
    notes = []
    if not edge:
        mine = np.minimum(255, 2 * kfull)
        notes.append(f"縁なし: 文字面のアルファの違い {int((mine != al).sum())} 画素")
        return notes
    # 文字面（縁が不透明な所の色）だけ比べる
    F = np.array(t.face_color()[:3]); E = np.array(t.edge_color()[:3])
    ch = int(np.argmax(np.abs(F - E)))
    full = np.zeros(kfull.shape + (4,), int)
    full[oy:oy + H, ox:ox + W] = ref
    a = np.minimum(255, 2 * kfull)
    mine = (E[ch] * 256 + (F[ch] - E[ch]) * (a + 1)) >> 8
    inner = (al == 255) & (kfull > 0)
    notes.append(f"文字面の色（縁が不透明な所）の違い {int((mine[inner] != full[..., ch][inner]).sum())} / {int(inner.sum())} 画素")
    # 縁: ずらしたコピーが VWS のアルファを超えないずらし量だけが「許される」
    R = int(edge * 1.3) + 4
    ys, xs = np.nonzero(kfull)
    y0, y1, x0, x1 = max(ys.min() - R - 2, 0), ys.max() + R + 3, max(xs.min() - R - 2, 0), xs.max() + R + 3
    kf = kfull[y0:y1, x0:x1]
    hi = np.where(al[y0:y1, x0:x1] == 255, 128, (al[y0:y1, x0:x1] + 1) // 2)
    ok_r2, ng_r2 = [], []
    acc = np.zeros_like(kf)
    for j in range(-R, R + 1):
        for i in range(-R, R + 1):
            sh = np.zeros_like(kf)
            ys0, ys1 = max(0, j), kf.shape[0] + min(0, j)
            xs0, xs1 = max(0, i), kf.shape[1] + min(0, i)
            sh[ys0:ys1, xs0:xs1] = kf[ys0 - j:ys1 - j, xs0 - i:xs1 - i]
            if (sh <= hi).all():
                ok_r2.append(i * i + j * j)
                np.maximum(acc, sh, out=acc)
            else:
                ng_r2.append(i * i + j * j)
    if ok_r2:
        top = max(ok_r2)
        nxt = min([v for v in ng_r2 if v > top], default=None)
        holes = sorted({v for v in ng_r2 if v <= top})
        bad = int((np.minimum(255, 2 * acc) != al[y0:y1, x0:x1]).sum())
        cur = max(i * i + j * j for i, j in vwsrender.edge_offsets(edge))
        notes.append(f"縁 {edge}px: ずらし量の円は i² + j² ≤ {top} まで入る（次の {nxt} は入らない）。今の式では ≤ {cur}。"
                     f"その円で作ったアルファと VWS の違い {bad} 画素" + (f"。円の中に入らない値がある: {holes[:6]}" if holes else ""))
    else:
        notes.append("縁: ずらし量を逆算できませんでした（文字面の位置が合っていない可能性）")
    return notes


def _ref_k(t, png):
    """VWS の PNG → 文字面の量 k（0〜128。分からない画素は −1）を全画面の配列で返す。
    縁つきは「縁が不透明な所の色」から、縁なしはアルファから逆算する（重ね方の式の逆）"""
    best = compare(t, png)
    ox, oy = best[3]
    ref = np.asarray(Image.open(png).convert("RGBA")).astype(int)
    H, W = ref.shape[:2]
    full = np.zeros((1080, 1920, 4), int)
    full[oy:oy + H, ox:ox + W] = ref
    K = np.full((1080, 1920), -1, int)
    if t.edge_width():
        F = np.array(t.face_color()[:3])
        E = np.array(t.edge_color()[:3])
        ch = int(np.argmax(np.abs(F - E)))
        lut = {}
        for k in range(129):
            a = min(255, 2 * k)
            c = int(E[ch]) if a == 0 else int(E[ch] + (((F[ch] - E[ch]) * (a + 1)) >> 8))
            lut.setdefault(c, []).append(k)
        op = full[..., 3] == 255
        for c, ks in lut.items():
            if len(ks) == 1:
                K[op & (full[..., ch] == c)] = ks[0]
    else:
        a = full[..., 3]
        even = (a % 2 == 0) & (a < 255)
        K[even] = (a // 2)[even]
        K[a == 255] = 128
    return K


def _kmap(g, g8x, g8y, sc):
    """字 1 つを 8 倍格子の原点 (g8x, g8y) に置いたときの文字面の量 k（枠で切る前）→ (k, 左の列, 上の行)"""
    SS = vwsrender.SS
    polys = vwsrender.glyph_polys8(g, getattr(sc, "sx", sc) * SS, getattr(sc, "sy", sc) * SS, g8x % SS, g8y % SS)
    xs = [p[0] for pl in polys for p in pl]
    ys = [p[1] for pl in polys for p in pl]
    px0, py0 = (g8x + min(xs)) // SS - 2, (g8y + min(ys)) // SS - 3
    px1, py1 = (g8x + max(xs)) // SS + 3, (g8y + max(ys)) // SS + 3
    w, h = px1 - px0, py1 - py0
    m = vwsrender.fill_gdi([[(g8x + a - px0 * SS, g8y + b - py0 * SS) for a, b in pl] for pl in polys], w * SS, h * SS)
    m = m.reshape(h, SS, w, SS).sum((1, 3)).astype(np.int32)
    k = m.copy()
    k[:-1] += m[1:]
    return k, px0, py0


def origins(folder):
    """字ごとに、原点を 1/8px ずつ動かして VWS の PNG（から逆算した文字面の量）と 1 画素も違わない位置を探す。
    枠の切り方（上端 2 行・左端 2 列）と、隣の字に重なる列は比べない。
    出力は行ごとに「字 余り→ずれ」: 余り = 式で出した x（1/256px）を 32 で割った余り、ずれ = VWS の原点 − 式の原点（1/8px 単位。0 なら式どおり）。
    字送りが式より 1/256px 長い字があると、その字のうしろで 余り 31 の字が +1 になる（短いと 余り 0 の字が −1）"""
    import math
    SS = vwsrender.SS
    for fn in sorted(os.listdir(folder)):
        if not fn.endswith(".ts1"):
            continue
        stem = fn[:-4]
        png = _find_png(folder, stem)
        if not png:
            continue
        with open(os.path.join(folder, fn), "rb") as f:
            t = TS1(f.read())
        K = _ref_k(t, png)
        lay = t.layout_fx()
        rows = {}
        for idx, (c, g, x, y, sc) in enumerate(lay):
            if not g.contours:
                rows.setdefault(y, []).append(f"{g.char}(空)")
                continue
            sx = float(getattr(sc, "sx", sc))
            neigh = []
            for j in (idx - 1, idx + 1):
                if 0 <= j < len(lay) and lay[j][1].contours and lay[j][3] == y:
                    gg, xx, sxx = lay[j][1], lay[j][2] / 256.0, float(getattr(lay[j][4], "sx", lay[j][4]))
                    neigh.append((xx + gg.bbox()[0] * sxx, xx + gg.bbox()[2] * sxx))
            b8x, b8y = x * SS // 256, int(math.floor(y * SS))
            res = []
            for dy in (0, -1, 1):
                for dx in range(-3, 4):
                    k, px0, py0 = _kmap(g, b8x + dx, b8y + dy, sc)
                    h, w = k.shape
                    sub = K[py0:py0 + h, px0:px0 + w]
                    if sub.shape != k.shape:
                        continue
                    sel = (sub >= 0) & (k > 0)
                    ys_, xs_ = np.nonzero(k > 0)
                    sel[:ys_.min() + 2] = False
                    sel[:, :xs_.min() + 2] = False
                    for nl, nr in neigh:
                        c0, c1 = int(math.floor(nl)) - 1 - px0, int(math.ceil(nr)) + 1 - px0
                        sel[:, max(c0, 0):max(c1, 0)] = False
                    res.append((int((k[sel] != sub[sel]).sum()), -int(sel.sum()), dx, dy))
            good = [(dx, dy) for bad, nsel, dx, dy in res if bad == 0 and -nsel >= 30]
            if len(good) == 1:
                dx, dy = good[0]
                mark = f"{dx:+d}" if dx else "0"
                if dy:
                    mark += f"(縦{dy:+d})"
            elif good:
                mark = "?" + "/".join(f"{dx:+d}" for dx, dy in good)
            else:
                bad, nsel, dx, dy = min(res) if res else (0, 0, 0, 0)
                mark = f"×(最も近い {dx:+d} 縦{dy:+d} で {bad} 画素違う)"
            rows.setdefault(y, []).append(f"{g.char} {x % 32}→{mark}")
        print(f"{fn}:")
        for y in sorted(rows):
            print(f"    y={y:g}:  " + "   ".join(rows[y]))


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
            try:
                for note in diagnose(t, png, org):
                    print("      見当:", note)
            except Exception as ex:
                print("      見当: 出せませんでした", ex)
    if not found:
        print("照合できる組がありません。VWS の書き出し PNG を .ts1 と同じ名前で置いてください")


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ("make", "extra", "probe", "origins", "check"):
        print(__doc__)
        return 1
    if sys.argv[1] == "extra":
        src = sys.argv[2]
        out = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(os.path.abspath(src)), "VWS確認用_追加")
        make_extra(src, out)
        return 0
    if sys.argv[1] == "origins":
        origins(sys.argv[2])
        return 0
    if sys.argv[1] == "probe":
        src = sys.argv[2]
        out = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(os.path.abspath(src)), "VWS確認用_第3回")
        make_probe(src, out)
        return 0
    if sys.argv[1] == "make":
        src = sys.argv[2]
        out = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(os.path.abspath(src)), "VWS確認用")
        make(src, out, sys.argv[4] if len(sys.argv) > 4 else None)
    else:
        check(sys.argv[2])
    return 0


if __name__ == "__main__":
    sys.exit(main())
