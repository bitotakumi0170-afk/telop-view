"""VWS と同じ手順でテロップを描く（VWS の書き出し PNG の画素値から突き止めた描き方。2026-11-12）

VWS の描き方（見本 1 件・縁 6px・ニューロダン B 100px で確認）
  1. 字の原点を 8 倍の整数格子に置く。横は「行の先頭字からの距離 × 0.9999」を 1/8px に四捨五入
     （1 字の送りが 100px ではなく 99.99px。7 字目で 1/8px の段差が出る）
  2. 輪郭の点（2048 単位）を 8 倍の格子へ: (int)(座標 × サイズ × 8 / 2048)。C の整数変換 = 0 方向へ切り捨て
     （x は左へ、ベースラインより上の y は下へ寄る）
  3. 2 次ベジェ曲線は、整数のまま半分ずつに割る（中点も 0 方向へ切り捨て）。十分まっすぐなら割るのをやめて直線でつなぐ
  4. 多角形を GDI と同じ規則で塗る（8 倍サイズ・整数座標の点で内外判定・左と上を含み右と下を含まない）
  5. 1 画素 = 8×8 の点を数える → 1 ラインぶんの文字面 m (0..64)
  6. 上下 2 ラインを足す: k[y] = m[y] + m[y+1] (0..128)。字ごとに上端を整数行に丸めて、その上にはみ出す行は切る
  7. 縁 = 文字面 k を 1px 刻みでずらしたコピーの最大値（ずらし量は i² + j² ≤ (縁 + 0.5)² の全整数点）
  8. 色 = 切り捨て(縁色′ + (文字色′ − 縁色′) × k / 128)、アルファ = min(255, 2 × 縁の k)
     内部の色′は 8bit 値に端数が乗っている（R +0.90〜0.95、G +0.81、B +0.10。見本の白と青から逆算）
直線だけの字は画素単位で完全に一致する。6〜8 は VWS の画像から逆算した文字面を使うと全画素で一致する。
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image

from ts1core import COLOR_ORDER, PRIM_LINE, PRIM_QSPLINE, PRIM_CSPLINE

SS = 8                 # 1px を 8×8 に分ける
ADVANCE_SCALE = 0.9999  # 字送りの倍率（行の先頭字の位置を基準）
# 曲線を割るのをやめる条件: 2 階差分 (p0 − 2c + p1) の大きさの 2 乗がこの値以下（8 倍格子の単位）
FLAT_SQ = 72
MAX_DEPTH = 8
# 内部の色′ = 8bit 値 + COLOR_BIAS + COLOR_GAIN × 値 / 255（チャンネルごと。見本の 129 色すべてが再現できる値）
COLOR_BIAS = (0.902, 0.81, 0.10)
COLOR_GAIN = (0.043, 0.0, -0.01)


def _tdiv2(a, b):
    """(a + b) / 2 を C の整数割り算（0 方向へ切り捨て）で"""
    return int((a + b) / 2)


def _subdivide(p0, c, p1, out, depth=0):
    ddx, ddy = p0[0] - 2 * c[0] + p1[0], p0[1] - 2 * c[1] + p1[1]
    if depth >= MAX_DEPTH or ddx * ddx + ddy * ddy <= FLAT_SQ:
        out.append(p1)
        return
    q0 = (_tdiv2(p0[0], c[0]), _tdiv2(p0[1], c[1]))
    q1 = (_tdiv2(c[0], p1[0]), _tdiv2(c[1], p1[1]))
    r = (_tdiv2(q0[0], q1[0]), _tdiv2(q0[1], q1[1]))
    _subdivide(p0, q0, r, out, depth + 1)
    _subdivide(r, q1, p1, out, depth + 1)


def glyph_polys8(glyph, sx8, sy8=None):
    """字形の輪郭 → 8 倍格子の整数の多角形（字の原点からの相対）。sx8, sy8 = 2048 単位 → 8 倍格子の倍率（横・縦）"""
    sy8 = sx8 if sy8 is None else sy8
    P = lambda p: (int(p[0] * sx8), int(p[1] * sy8))
    polys = []
    for c in glyph.contours:
        cur = c.start
        pl = [P(cur)]
        for prim, pts in c.segs:
            if prim == PRIM_LINE:
                for p in pts:
                    pl.append(P(p))
                    cur = p
            elif prim == PRIM_QSPLINE:
                pts = list(pts)
                for i in range(len(pts) - 1):
                    end = pts[i + 1] if i == len(pts) - 2 else ((pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2)
                    _subdivide(P(cur), P(pts[i]), P(end), pl)
                    cur = end
            elif prim == PRIM_CSPLINE:   # 3 次（見本には無い）: 細かく等分して切り捨て
                pts = list(pts)
                for i in range(0, len(pts) - 2, 3):
                    p0, c1, c2, p1 = cur, pts[i], pts[i + 1], pts[i + 2]
                    for k in range(1, 17):
                        t = k / 16
                        x = (1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * c1[0] + 3 * (1 - t) * t * t * c2[0] + t ** 3 * p1[0]
                        y = (1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * c1[1] + 3 * (1 - t) * t * t * c2[1] + t ** 3 * p1[1]
                        pl.append(P((x, y)))
                    cur = p1
        polys.append(pl)
    return polys


def fill_gdi(polys, w, h):
    """整数座標の多角形を GDI と同じ規則で塗る（非ゼロ巻き数）。bool 配列 (h, w)。
    GDI は整数座標の点 (i, j) が内側なら画素 (i, j) を塗る。左・上の辺は含み、右・下の辺は含まない"""
    acc = np.zeros((h, w + 1), np.int32)
    for poly in polys:
        p = np.asarray(poly, float)
        if len(p) < 3:
            continue
        a, b = p, np.roll(p, -1, axis=0)
        y0, y1 = a[:, 1], b[:, 1]
        dirs = np.where(y1 > y0, 1, -1)
        lo, hi = np.minimum(y0, y1), np.maximum(y0, y1)
        r0 = np.ceil(lo).astype(int)        # 行 j は lo <= j < hi のとき辺と交わる
        r1 = np.ceil(hi).astype(int)
        for k in np.nonzero(r1 > r0)[0]:
            rows = np.arange(max(r0[k], 0), min(r1[k], h))
            if rows.size == 0:
                continue
            t = (rows - y0[k]) / (y1[k] - y0[k])
            xs = a[k, 0] + t * (b[k, 0] - a[k, 0])
            cols = np.clip(np.ceil(xs).astype(int), 0, w)   # 点 i は i >= xs のとき辺の右側
            np.add.at(acc, (rows, cols), dirs[k])
    return np.cumsum(acc, axis=1)[:, :w] != 0


def edge_offsets(edge):
    """縁のずらし量（整数点の円板）"""
    r2 = (edge + 0.5) ** 2
    n = int(math.floor(edge + 0.5))
    return [(i, j) for i in range(-n, n + 1) for j in range(-n, n + 1) if i * i + j * j <= r2]


def _dilate(a, offs):
    n = max(max(abs(i), abs(j)) for i, j in offs)
    h, w = a.shape
    p = np.pad(a, n)
    out = np.zeros_like(a)
    for i, j in offs:
        np.maximum(out, p[n + j:n + j + h, n + i:n + i + w], out=out)
    return out


def face_counts(ts1, canvas=(1920, 1080)):
    """文字面の量 k_f (0..128) をキャンバス全体の int 配列で返す"""
    W, H = canvas
    kf = np.zeros((H, W), np.int32)
    first_x = {}
    for ch, g, x, y, sc in ts1.layout():
        if not g.contours:
            continue
        x0 = first_x.setdefault(round(y, 3), x)                 # 行の先頭字
        g8x = int(math.floor((x0 + (x - x0) * ADVANCE_SCALE) * SS + 0.5))
        g8y = int(math.floor(y * SS + 0.5))
        polys = glyph_polys8(g, getattr(sc, "sx", sc) * SS, getattr(sc, "sy", sc) * SS)
        xs = [p[0] for pl in polys for p in pl]
        ys = [p[1] for pl in polys for p in pl]
        # 字ごとの窓（画素単位）
        px0 = (g8x + min(xs)) // SS - 1
        py0 = (g8y + min(ys)) // SS - 2
        px1 = (g8x + max(xs)) // SS + 2
        py1 = (g8y + max(ys)) // SS + 2
        w, h = px1 - px0, py1 - py0
        mask = fill_gdi([[(g8x + a - px0 * SS, g8y + b - py0 * SS) for a, b in pl] for pl in polys], w * SS, h * SS)
        m = mask.reshape(h, SS, w, SS).sum((1, 3)).astype(np.int32)       # 1 ラインぶん (0..64)
        k = m.copy()
        k[:-1] += m[1:]                                                    # 上下 2 ライン
        # 上端: 字の上端（8 倍格子）を整数行に丸め、その 1 行上から描く。それより上にはみ出す行は切る
        top8 = g8y + min(ys)
        first_row = (top8 + SS // 2 - 1) // SS - 1   # = ceil(上端px - 0.5) - 1。端数 .5 ちょうどは切らない（別サンプルの OFRI で確認）
        if first_row - py0 > 0:
            k[:first_row - py0] = 0
        # キャンバスへ（字どうしが重なる所は大きい方）
        cx0, cy0 = max(px0, 0), max(py0, 0)
        cx1, cy1 = min(px1, W), min(py1, H)
        if cx1 > cx0 and cy1 > cy0:
            sub = k[cy0 - py0:cy1 - py0, cx0 - px0:cx1 - px0]
            np.maximum(kf[cy0:cy1, cx0:cx1], sub, out=kf[cy0:cy1, cx0:cx1])
    return kf


def _internal(color):
    return np.array([color[i] + COLOR_BIAS[i] + COLOR_GAIN[i] * color[i] / 255.0 for i in range(3)])


def compose(kf, face_rgb, edge_rgb, edge):
    """k_f → RGBA (uint8)。VWS と同じ整数の丸め"""
    if edge and edge > 0:
        ke = _dilate(kf, edge_offsets(edge))
        base = _internal(edge_rgb)
    else:
        ke = kf
        base = _internal(face_rgb)
    fi = _internal(face_rgb)
    out = np.zeros(kf.shape + (4,), np.uint8)
    rgb = np.floor(base[None, None] + (fi - base)[None, None] * (kf[..., None] / 128.0))
    out[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    out[..., 3] = np.minimum(255, 2 * ke).astype(np.uint8)
    out[ke == 0] = 0
    return out


def render_rgba(ts1, edge=None, canvas=(1920, 1080), color_order=COLOR_ORDER):
    """キャンバス全体の RGBA 配列（VWS の書き出し PNG と同じ値になるように組み立てる）"""
    edge = ts1.edge_width() if edge is None else edge
    kf = face_counts(ts1, canvas)
    return compose(kf, tuple(ts1.face_color(color_order)[:3]), tuple(ts1.edge_color(color_order)[:3]), edge or 0)


def render_image(ts1, **kw):
    return Image.fromarray(render_rgba(ts1, **kw), "RGBA")


def render_layers(ts1, edge=None, canvas=(1920, 1080), color_order=COLOR_ORDER):
    """(文字面 RGBA, 縁 RGBA, (left, top))。文字面を縁の上に重ねると VWS とほぼ同じ絵になる（PSD のレイヤ用）"""
    edge = ts1.edge_width() if edge is None else edge
    kf = face_counts(ts1, canvas)
    ke = _dilate(kf, edge_offsets(edge)) if edge else kf
    ys, xs = np.nonzero(ke)
    if len(ys) == 0:
        return None
    l, t, r, b = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1

    def layer(k, rgb):
        im = Image.new("RGBA", (r - l, b - t), tuple(rgb) + (0,))
        im.putalpha(Image.fromarray(np.minimum(255, 2 * k[t:b, l:r]).astype(np.uint8), "L"))
        return im

    return layer(kf, tuple(ts1.face_color(color_order)[:3])), layer(ke, tuple(ts1.edge_color(color_order)[:3])), (int(l), int(t))
