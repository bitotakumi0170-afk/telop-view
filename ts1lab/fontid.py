"""TS1 内の字形（アウトライン）から、PC に入っているフォントを特定する

.ts1 にはフォント名が無く番号だけなので、字形そのものを全フォントと照合する。
  1. PC のフォントを走査し、TS1 の文字を全部持つフォントだけ候補にする
  2. 文字ごとに輪郭を塗りつぶして重なり具合（IoU）を計算（大きさ・位置は正規化）
  3. スコア順に並べる。一致フォントなら 0.97 以上になる
特定できたら、VWS の座標系（2048 セル）への倍率・オフセットも実測して対応表に保存する
（Photoshop → TS1 で VWS と同じ字形データを作るため）。
"""
from __future__ import annotations

import numpy as np
from PIL import Image

import fontfind
from render import flatten_contour, fill_nonzero

N = 72   # 照合用ラスタの一辺
SS = 2


def _raster(polys, bbox):
    """bbox (x0, y0, x1, y1) を N×N に引き伸ばして塗りつぶし"""
    x0, y0, x1, y1 = bbox
    w, h = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    m = N * SS
    sc = [[((x - x0) / w * m, (y - y0) / h * m) for x, y in p] for p in polys]
    a = fill_nonzero(sc, m, m)
    return np.asarray(Image.fromarray((a * 255).astype(np.uint8)).resize((N, N), Image.BOX)) / 255.0


def _bbox(polys):
    xs = [x for p in polys for x, _ in p]
    ys = [y for p in polys for _, y in p]
    return (min(xs), min(ys), max(xs), max(ys))


def ts1_glyph_polys(g):
    return [flatten_contour(c, 0, 0, 1.0, steps=4) for c in g.contours]


def font_glyph_polys(gs, name):
    """フォントの字形 → 折れ線（y 下向きに反転、フォント単位）"""
    from fontTools.pens.recordingPen import DecomposingRecordingPen
    pen = DecomposingRecordingPen(gs)
    gs[name].draw(pen)
    polys, cur, last = [], None, None

    def quad(p0, c, p1, k=4):
        return [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * c[0] + t * t * p1[0],
                 (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * c[1] + t * t * p1[1]) for t in [i / k for i in range(1, k + 1)]]

    def cubic(p0, c1, c2, p1, k=6):
        out = []
        for i in range(1, k + 1):
            t = i / k
            u = 1 - t
            out.append((u ** 3 * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t ** 3 * p1[0],
                        u ** 3 * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t ** 3 * p1[1]))
        return out

    for op, args in pen.value:
        if op == "moveTo":
            cur = [args[0]]; last = args[0]
        elif op == "lineTo":
            cur.append(args[0]); last = args[0]
        elif op == "qCurveTo":
            pts = list(args)
            if pts[-1] is None:
                pts = pts[:-1]
                mid = ((pts[-1][0] + pts[0][0]) / 2, (pts[-1][1] + pts[0][1]) / 2)
                if cur is None:
                    cur = [mid]; last = mid
                pts.append(mid)
            for i in range(len(pts) - 1):
                c = pts[i]
                e = pts[i + 1] if i == len(pts) - 2 else ((pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2)
                cur += quad(last, c, e); last = e
        elif op == "curveTo":
            cur += cubic(last, *args); last = args[-1]
        elif op in ("closePath", "endPath"):
            if cur and len(cur) >= 3:
                polys.append([(x, -y) for x, y in cur])
            cur = None
    return polys


def pick_chars(ts1, limit=8):
    """照合に使う文字（輪郭のある字形。漢字・かなを混ぜる）"""
    gl = [g for g in ts1.glyphs if g.contours]
    gl.sort(key=lambda g: -sum(len(p) for _, pts in [(0, c.segs) for c in g.contours] for p in pts))
    return gl[:limit]


def identify(ts1, extra_dirs=(), cache_path=None, progress=None, top=10):
    """[(score, 名前リスト, path, index, fit)] をスコア順に返す"""
    from fontTools.ttLib import TTFont
    glyphs = pick_chars(ts1)
    if not glyphs:
        return []
    target = []
    for g in glyphs:
        polys = ts1_glyph_polys(g)
        bb = _bbox(polys)
        target.append((g, polys, bb, _raster(polys, bb)))
    table = fontfind.scan(extra_dirs, cache_path)
    results = []
    for k, e in enumerate(table):
        if progress and k % 25 == 0:
            progress(k, len(table))
        try:
            f = TTFont(e["path"], fontNumber=e["index"], lazy=True)
            cmap = f.getBestCmap() or {}
            if not all(g.code in cmap for g, *_ in target):
                continue
            gs = f.getGlyphSet()
            ious, pairs = [], []
            for g, polys, bb, ras in target:
                fp = font_glyph_polys(gs, cmap[g.code])
                if not fp:
                    ious.append(0.0)
                    continue
                fb = _bbox(fp)
                r2 = _raster(fp, fb)
                inter = np.minimum(ras, r2).sum()
                union = np.maximum(ras, r2).sum()
                ious.append(inter / union if union else 0.0)
                pairs.append((bb, fb))
            score = float(np.mean(ious))
            results.append((score, e["names"], e["path"], e["index"], pairs))
        except Exception:
            continue
    out = [(score, names, path, index, _fit(pairs)) for score, names, path, index, pairs in results]
    # スコア順。同点（等幅版とプロポーショナル版など）は位置のずれが小さい方を上に
    out.sort(key=lambda r: (-round(r[0], 3), r[4]["rms"] if r[4] else 1e9))
    return out[:top]


def _fit(pairs):
    """フォント単位 → TS1 座標（2048 セル, y 下向き）の倍率とオフセットを最小二乗で求める

    x_ts1 = s * x_font + ox,  y_ts1 = s * y_font(反転済) + oy
    """
    if not pairs:
        return None
    A, B = [], []
    for (tx0, ty0, tx1, ty1), (fx0, fy0, fx1, fy1) in pairs:
        A += [[fx0, 1, 0], [fx1, 1, 0], [fy0, 0, 1], [fy1, 0, 1]]
        B += [tx0, tx1, ty0, ty1]
    sol, *_ = np.linalg.lstsq(np.array(A, float), np.array(B, float), rcond=None)
    res = np.array(A, float) @ sol - np.array(B, float)
    return {"scale": float(sol[0]), "ox": float(sol[1]), "oy": float(sol[2]),
            "rms": float(np.sqrt((res ** 2).mean()))}


def fit_font(ts1, path, index=0):
    """1 つのフォントだけ照合して (一致度, 倍率・オフセット) を返す"""
    from fontTools.ttLib import TTFont
    glyphs = pick_chars(ts1)
    font = TTFont(path, fontNumber=index, lazy=True)
    cmap = font.getBestCmap() or {}
    gs = font.getGlyphSet()
    ious, pairs = [], []
    for g in glyphs:
        if g.code not in cmap:
            continue
        polys = ts1_glyph_polys(g)
        bb = _bbox(polys)
        fp = font_glyph_polys(gs, cmap[g.code])
        if not fp:
            continue
        fb = _bbox(fp)
        a, b = _raster(polys, bb), _raster(fp, fb)
        ious.append(np.minimum(a, b).sum() / max(np.maximum(a, b).sum(), 1e-9))
        pairs.append((bb, fb))
    if not ious:
        return 0.0, None
    return float(np.mean(ious)), _fit(pairs)
