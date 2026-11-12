"""TS1 のグリフ・アウトラインを numpy + Pillow で描画する（文字面と縁を別レイヤで出力）"""
from __future__ import annotations

import numpy as np
from PIL import Image

from ts1core import COLOR_ORDER, PRIM_LINE, PRIM_QSPLINE, PRIM_CSPLINE


def _quad(p0, c, p1, steps):
    t = np.linspace(0, 1, steps + 1)[1:, None]
    p0, c, p1 = map(np.asarray, (p0, c, p1))
    return ((1 - t) ** 2 * p0 + 2 * (1 - t) * t * c + t ** 2 * p1).tolist()


def _cubic(p0, c1, c2, p1, steps):
    t = np.linspace(0, 1, steps + 1)[1:, None]
    p0, c1, c2, p1 = map(np.asarray, (p0, c1, c2, p1))
    return ((1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * c1 + 3 * (1 - t) * t ** 2 * c2 + t ** 3 * p1).tolist()


def flatten_contour(c, tx, ty, sc, steps=6):
    """輪郭を折れ線（ピクセル座標）へ。tx,ty = 原点位置、sc = 2048単位→px"""
    T = lambda p: (tx + p[0] * sc, ty + p[1] * sc)
    cur = T(c.start)
    out = [cur]
    for prim, pts in c.segs:
        P = [T(p) for p in pts]
        if prim == PRIM_LINE:
            out += P
            cur = P[-1]
        elif prim == PRIM_QSPLINE:
            for i in range(len(P) - 1):
                ctrl = P[i]
                end = P[i + 1] if i == len(P) - 2 else ((P[i][0] + P[i + 1][0]) / 2, (P[i][1] + P[i + 1][1]) / 2)
                out += _quad(cur, ctrl, end, steps)
                cur = end
        elif prim == PRIM_CSPLINE:
            for i in range(0, len(P) - 2, 3):
                out += _cubic(cur, P[i], P[i + 1], P[i + 2], steps)
                cur = P[i + 2]
    return out


def fill_nonzero(polys, w, h):
    """非ゼロ巻き数で塗りつぶし（画素中心サンプリング）。bool 配列 (h, w)"""
    acc = np.zeros((h, w + 1), np.int32)
    for poly in polys:
        p = np.asarray(poly, float)
        if len(p) < 3:
            continue
        a, b = p, np.roll(p, -1, axis=0)
        y0, y1 = a[:, 1], b[:, 1]
        dirs = np.where(y1 > y0, 1, -1)
        lo, hi = np.minimum(y0, y1), np.maximum(y0, y1)
        r0 = np.ceil(lo - 0.5).astype(int)
        r1 = np.ceil(hi - 0.5).astype(int)  # 半開区間 [r0, r1)
        for k in np.nonzero(r1 > r0)[0]:
            rows = np.arange(max(r0[k], 0), min(r1[k], h))
            if rows.size == 0:
                continue
            yc = rows + 0.5
            t = (yc - y0[k]) / (y1[k] - y0[k])
            xs = a[k, 0] + t * (b[k, 0] - a[k, 0])
            cols = np.clip(np.ceil(xs - 0.5).astype(int), 0, w)
            np.add.at(acc, (rows, cols), dirs[k])
    return np.cumsum(acc, axis=1)[:, :w] != 0


# VWS の描き方（見本の VWS 出力 PNG の画素値から特定。2026-11-12）
#   1. 文字面を画素の被覆率で塗る（位置は数式上の位置 + RENDER_OFFSET）
#   2. 縁 = 文字面を 1px 刻みでずらしたコピーの重ね（最大値）。ずらし量は i^2 + j^2 <= (縁 + 0.5)^2 の全整数点
#      （縁 6px なら (±6,2) (±5,4) (±4,5) (±2,6) の階段。丸い縁ではなく、斜めが少し太い。「回」の角の画素値で確認）
#   3. 文字面・縁とも上下 2 ラインの平均（out[y] = (in[y] + in[y+1]) / 2。縦の辺だけ 2 画素にまたがる）
# このモデルと VWS 出力の差は premultiplied 平均差 0.94%（丸い縁 + フィルタなしの旧モデルは 2.74%）
RENDER_OFFSET = (-0.0625, 0.09375)
# 旧モデル（丸い縁・縦フィルタなし）で合わせたときのオフセット。縦フィルタの -0.5px を含んだ見かけの値
RENDER_OFFSET_LEGACY = (-0.125, -0.375)


def edge_offsets(edge):
    """VWS の縁のずらし量（整数点の円板）"""
    r2 = (edge + 0.5) ** 2
    n = int(np.floor(edge + 0.5))
    return [(i, j) for i in range(-n, n + 1) for j in range(-n, n + 1) if i * i + j * j <= r2]


def coverage_layers(ts1, edge=None, ss=8, offset=RENDER_OFFSET, vfilter=True):
    """(文字面の被覆率, 縁込みの被覆率, (left, top)) を float 配列 (0..1) で返す"""
    edge = ts1.edge_width() if edge is None else edge
    edge = edge or 0
    bb = ts1.ink_bbox(edge=edge + 3)
    if bb is None:
        return None
    left, top = int(np.floor(bb[0])), int(np.floor(bb[1]))
    right, bottom = int(np.ceil(bb[2])), int(np.ceil(bb[3]))
    W, H = right - left, bottom - top
    polys = []
    for ch, g, x, y, sc in ts1.layout():
        for c in g.contours:
            polys.append(flatten_contour(c, (x + offset[0] - left) * ss, (y + offset[1] - top) * ss, sc * ss))
    cf = fill_nonzero(polys, W * ss, H * ss).reshape(H, ss, W, ss).mean((1, 3))
    ce = cf
    if edge > 0:
        offs = edge_offsets(edge)
        n = max(max(abs(i), abs(j)) for i, j in offs)
        p = np.pad(cf, n)
        ce = np.zeros_like(cf)
        for i, j in offs:
            np.maximum(ce, p[n + j:n + j + H, n + i:n + i + W], out=ce)
    if vfilter:
        def vavg(c):
            o = c.copy()
            o[:-1] = (c[:-1] + c[1:]) / 2
            o[-1] = c[-1] / 2
            return o
        cf, ce = vavg(cf), vavg(ce)
    return cf, ce, (left, top)


def render_layers(ts1, edge=None, ss=8, canvas=(1920, 1080), colors=None, color_order=COLOR_ORDER,
                  offset=RENDER_OFFSET, vfilter=True):
    """(face RGBA, edge RGBA, (left, top)) を返す。画像はテロップ範囲だけ切り出し。
    edge を face の下に重ねると VWS の出力と同じ絵になる"""
    r = coverage_layers(ts1, edge=edge, ss=ss, offset=offset, vfilter=vfilter)
    if r is None:
        return None
    cf, ce, lt = r
    face_rgb = tuple(ts1.face_color(color_order)[:3])
    edge_rgb = tuple(ts1.edge_color(color_order)[:3])

    def colorize(cov, rgb):
        im = Image.new("RGBA", (cov.shape[1], cov.shape[0]), rgb + (0,))
        # VWS は被覆率を 256 倍して 255 で頭打ちにしている（0.875 → 224、0.375 → 96）
        im.putalpha(Image.fromarray(np.clip(np.round(cov * 256), 0, 255).astype(np.uint8), "L"))
        return im

    return colorize(cf, face_rgb), colorize(ce, edge_rgb), lt


def render_preview(ts1, canvas=(1920, 1080), bg=(40, 60, 90), **kw):
    face, edge, (l, t) = render_layers(ts1, **kw)
    im = Image.new("RGBA", canvas, bg + (255,))
    im.alpha_composite(edge, (l, t))
    im.alpha_composite(face, (l, t))
    return im


def to_svg(ts1, canvas=(1920, 1080), edge=None, color_order=COLOR_ORDER):
    """編集可能なベクター（SVG）として書き出す。縁は stroke（paint-order）で表現"""
    edge = ts1.edge_width() if edge is None else edge
    fc = "#%02x%02x%02x" % tuple(ts1.face_color(color_order)[:3])
    ec = "#%02x%02x%02x" % tuple(ts1.edge_color(color_order)[:3])
    paths = []
    for ch, g, x, y, sc in ts1.layout():
        if not g.contours:
            continue
        d = []
        for c in g.contours:
            d.append("M%d %d" % c.start)
            cur = c.start
            for prim, pts in c.segs:
                if prim == PRIM_LINE:
                    d += ["L%d %d" % p for p in pts]
                    cur = pts[-1]
                elif prim == PRIM_QSPLINE:
                    for i in range(len(pts) - 1):
                        ctrl = pts[i]
                        end = pts[i + 1] if i == len(pts) - 2 else ((pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2)
                        d.append("Q%g %g %g %g" % (ctrl[0], ctrl[1], end[0], end[1]))
                        cur = end
            d.append("Z")
        paths.append(f'<path transform="translate({x:.2f} {y:.2f}) scale({sc:.6f})" d="{" ".join(d)}"><title>{ch.char}</title></path>')
    sw = (edge or 0) * 2
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas[0]}" height="{canvas[1]}" viewBox="0 0 {canvas[0]} {canvas[1]}">\n'
            f'<g fill="{fc}" stroke="{ec}" stroke-width="{sw / (ts1.char_size[0] / 2048):.1f}" stroke-linejoin="round" paint-order="stroke" fill-rule="nonzero">\n'
            + "\n".join(paths) + "\n</g>\n</svg>\n")
