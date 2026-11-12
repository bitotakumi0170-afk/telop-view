"""VWS が出力した見本画像（透過 PNG）と TS1 の再現描画を照合する

  - 文字面・縁を色で分離し、全体オフセット・縁幅・文字ごとのずれを推定
  - 差分画像（赤 = 見本のみ / 緑 = 再現のみ / 黄 = 一致）を出力
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from ts1core import COLOR_ORDER
from render import render_layers, flatten_contour, fill_nonzero


def _paste(canvas_hw, layer, l, t):
    out = np.zeros(canvas_hw)
    a = np.asarray(layer)[:, :, 3] / 255.0
    h, w = a.shape
    H, W = canvas_hw
    y0, x0 = max(t, 0), max(l, 0)
    y1, x1 = min(t + h, H), min(l + w, W)
    out[y0:y1, x0:x1] = a[y0 - t:y1 - t, x0 - l:x1 - l]
    return out


def split_reference(ref_path, face_rgb, edge_rgb):
    im = Image.open(ref_path).convert("RGBA")
    a = np.asarray(im).astype(float)
    alpha = a[:, :, 3] / 255.0
    if alpha.min() > 0.99:
        raise ValueError("見本画像に透過がありません（背景透過の PNG で書き出してください）")
    rgb = a[:, :, :3]
    F, E = np.array(face_rgb, float), np.array(edge_rgb, float)
    d = F - E
    w = np.clip(((rgb - E) * d).sum(2) / max((d * d).sum(), 1e-6), 0, 1)
    return alpha * w, alpha, im.size  # 文字面カバレッジ, 全体(縁込み)カバレッジ


def detect_colors(ref_path):
    a = np.asarray(Image.open(ref_path).convert("RGBA")).astype(int)
    solid = a[a[:, :, 3] > 250][:, :3]
    if len(solid) == 0:
        return []
    keys, counts = np.unique(solid, axis=0, return_counts=True)
    order = np.argsort(-counts)
    return [(tuple(int(v) for v in keys[i]), int(counts[i])) for i in order[:4]]


def compare(ts1, ref_path, color_order=COLOR_ORDER, diff_path=None, search=True):
    face_rgb, edge_rgb = ts1.face_color(color_order)[:3], ts1.edge_color(color_order)[:3]
    # 見本 PNG の書き出しで R と B が入れ替わっている場合を自動判定
    ref_cols = [c for c, _ in detect_colors(ref_path)]
    near = lambda a, b: sum(abs(x - y) for x, y in zip(a, b)) < 30
    swapped = any(near(c, edge_rgb[::-1]) for c in ref_cols) and not any(near(c, edge_rgb) for c in ref_cols)
    if swapped:
        face_rgb, edge_rgb = face_rgb[::-1], edge_rgb[::-1]
    FR, AR, size = split_reference(ref_path, face_rgb, edge_rgb)
    hw = (size[1], size[0])
    total = FR.sum() + AR.sum()

    def score(off, e):
        face, edge, (l, t) = render_layers(ts1, edge=e, color_order=color_order, offset=off)
        fa, ea = _paste(hw, face, l, t), _paste(hw, edge, l, t)
        return np.abs(fa - FR).sum() + np.abs(ea - AR).sum(), fa, ea

    e0 = ts1.edge_width() or 6
    best = None
    grid_off = [(dx, dy) for dx in np.arange(-0.5, 0.51, 0.125) for dy in np.arange(-0.75, 0.51, 0.125)] if search else [(0, 0)]
    for off in grid_off:  # まず位置
        s, _, _ = score(off, e0)
        if best is None or s < best[0]:
            best = (s, off, e0)
    for e in ([e0 - 0.5, e0 - 0.25, e0 + 0.25, e0 + 0.5] if search else []):  # 次に縁幅
        s, _, _ = score(best[1], e)
        if s < best[0]:
            best = (s, best[1], e)
    s, off, e = best
    _, fa, ea = score(off, e)

    # 文字ごとのずれ（隣接文字を除いた窓で 1 文字ずつ照合）
    per_char = []
    items = ts1.layout()
    ss = 4
    for ch, g, x, y, sc in items:
        if not g.contours:
            continue
        l_, t_, r_, b_ = g.bbox()
        x0, x1 = int(x + l_ * sc) - 2, int(x + r_ * sc) + 3
        y0, y1 = int(y - t_ * sc) - 3, int(y - b_ * sc) + 3
        ref = FR[y0:y1, x0:x1]
        bb = None
        for dx in np.arange(-3, 3.01, 0.25):
            for dy in np.arange(-2, 2.01, 0.25):
                polys = [flatten_contour(c, (x + off[0] + dx - x0) * ss, (y + off[1] + dy - y0) * ss, sc * ss)
                         for c in g.contours]
                m = fill_nonzero(polys, (x1 - x0) * ss, (y1 - y0) * ss)
                m = np.asarray(Image.fromarray((m * 255).astype(np.uint8)).resize((x1 - x0, y1 - y0), Image.BOX)) / 255.0
                err = np.abs(m - ref).sum()
                if bb is None or err < bb[0]:
                    bb = (err, dx, dy)
        per_char.append({"char": ch.char, "x": round(x, 2), "dx": bb[1], "dy": bb[2]})

    if diff_path:
        r = (np.maximum(FR, AR * 0.5) * 255).astype(np.uint8)
        gch = (np.maximum(fa, ea * 0.5) * 255).astype(np.uint8)
        Image.fromarray(np.stack([r, gch, np.zeros_like(r)], 2)).save(diff_path)

    return {
        "error_percent": round(100 * s / total, 2),
        "best_offset_px": [float(off[0]), float(off[1])],
        "best_edge_px": float(e),
        "file_edge_px": ts1.edge_width(),
        "colors_in_reference": detect_colors(ref_path),
        "colors_in_file": {"face": "#%02X%02X%02X" % tuple(ts1.face_color(color_order)[:3]),
                           "edge": "#%02X%02X%02X" % tuple(ts1.edge_color(color_order)[:3])},
        "reference_rb_swapped": swapped,
        "per_char": per_char,
    }
