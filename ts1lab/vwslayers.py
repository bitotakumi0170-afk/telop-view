# -*- coding: utf-8 -*-
"""装飾つきのテロップを、VWS と同じく「層を上から順に作り、下から順に重ねる」形で描く。

文字面 + 縁 1 本だけのテロップは vwsrender（VWS の PNG と 1 画素も違わない）。こちらはそれ以外の装飾
（2 重の縁・四角い縁・立体・影・光彩・グラデーション・模様・半透明の層）を扱う。

確かめてあること（VWS の PNG と 1 画素も違わない。FORMAT.md「第 4・5 回で確定したこと」）:
  - 層の不透明度: アルファ × 不透明度 >> 8。不透明度 0 の層は描かれない
  - 太さが負の縁: 文字面の量 k を縁の形で削る（最小値）
  - 種別 4（四角い縁）: 上の層の形を (2 × 太さ + 1) の正方形で広げる
  - 縁は上の層（文字面・ほかの縁）を合わせた形の外側から広がる
  - 追加分 = ぼかし: 幅 2 × 追加 + 1 の箱型の平均（切り上げ）。縦棒の真横の断面で確認
  - 模様つきのグラデーション: 模様の値（0〜255）をグラデーションの位置として色を引く
まだ仮の式（第 6 回の確認用で決める）: 立体（種別 3）・影（種別 16 / 1）・光彩（0x800）・面取りつきの文字面（0x801）、
グラデーションの範囲（字の上端〜下端として描く）、模様の貼り方（縦 150/1080・横 0.267 倍、基準は行の左端・文字の上端）。
"""
import math

import numpy as np

import vwsrender as VR
from ts1core import COLOR_ORDER, decode_color

CANVAS = (1920, 1080)

# 仮の式の数値（第 6 回の確認用で決める。実物の赤・二色に合わせて選んだ値）
PARAMS = {
    "extrude_step": (1, 1),        # 立体（種別 3）: 1 段ごとのずらし (x, y)
    "extrude_len": "w2",           # 立体の段数: "w" = 太さ / "w2" = 太さ 2
    "extrude_blur": 0,             # 立体のぼかし（ぼかさない方が赤・二色とも合う）
    "shadow_shift": "w2//2",       # 影（種別 16）のずらし
    "shadow_blur": "w//2",
    "glow_grow": "w+e-3",          # 光彩: 広げる量（二色の光彩だけの所で、箱型 3 回・半径 7・広げ 2 が一番近い。平均の差 5.1）
    "glow_blur": "e*7//10",
    "glow_passes": 3,              # 光彩のぼかしは箱型を何回重ねるか（1 回だと外側の薄れ方が直線になり合わない）
    "grad_top": 0.928,             # 縦のグラデーションの範囲: ベースライン − 文字サイズ × grad_top 〜 ベースライン + 文字サイズ × grad_bottom
    "grad_bottom": 0.008,          # （金の 73 で 468.3〜608.7。サイズ 150 の 1 例だけ）
}


def _pv_signed(spec, w, w2, e):
    if isinstance(spec, (int, float)):
        return int(spec)
    return int(eval(spec, {}, {"w": w, "w2": w2, "e": e}))


def _pv(spec, w, w2, e):
    if isinstance(spec, (int, float)):
        return int(spec)
    return int(eval(spec, {}, {"w": max(w, 0), "w2": max(w2, 0), "e": max(e, 0)}))


# ---------------------------------------------------------------------------
# 形の操作（文字面の量 k: 0〜128 の int 配列）
# ---------------------------------------------------------------------------
def _square(r):
    return [(i, j) for i in range(-r, r + 1) for j in range(-r, r + 1)]


def dilate_round(k, w):
    return VR._dilate(k.astype(np.int32), VR.edge_offsets(w)).astype(np.int64) if w > 0 else k.copy()


def dilate_square(k, w):
    return VR._dilate(k.astype(np.int32), _square(w)).astype(np.int64) if w > 0 else k.copy()


def erode(k, offs):
    n = max(max(abs(i), abs(j)) for i, j in offs)
    h, w = k.shape
    p = np.pad(k, n)
    out = np.full_like(k, 1 << 30)
    for i, j in offs:
        np.minimum(out, p[n + j:n + j + h, n + i:n + i + w], out=out)
    return out


def shift(a, dx, dy):
    out = np.zeros_like(a)
    h, w = a.shape
    xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
    ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
    out[yd, xd] = a[ys, xs]
    return out


def box_blur(a, r):
    """幅 2r + 1 の箱型の平均（縦横とも）。切り上げ（白い縁 + 追加 5 の断面で確認）"""
    if r <= 0:
        return a.copy()
    n = 2 * r + 1
    c = np.cumsum(np.pad(a, ((0, 0), (r + 1, r))), axis=1)
    h = c[:, n:] - c[:, :-n]
    c = np.cumsum(np.pad(h, ((r + 1, r), (0, 0))), axis=0)
    v = c[n:] - c[:-n]
    return -((-v) // (n * n))


def alpha_of(k):
    return np.minimum(255, 2 * k).astype(np.int64)


# ---------------------------------------------------------------------------
# 色（単色・グラデーション・模様）
# ---------------------------------------------------------------------------
def _gradient_lut(stops):
    """[(位置 0〜255, (r, g, b, a))] → 256 段の RGBA の表（位置の間は直線）"""
    stops = sorted(stops, key=lambda s: s[0])
    lut = np.zeros((256, 4), np.int64)
    for v in range(256):
        if v <= stops[0][0]:
            lut[v] = stops[0][1]
            continue
        if v >= stops[-1][0]:
            lut[v] = stops[-1][1]
            continue
        for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
            if p0 <= v <= p1:
                t = (v - p0) / max(p1 - p0, 1)
                lut[v] = [int(round(a + (b - a) * t)) for a, b in zip(c0, c1)]
                break
    return lut


def layer_color(ts1, d, mask, info, order=COLOR_ORDER):
    """層の色 → (rgb (H, W, 3), 色のアルファ (H, W))。info = 行の範囲など"""
    H, W = mask.shape
    tag, raw = ts1.resources[d["color_resource"]] if d["color_resource"] < len(ts1.resources) else (None, None)
    if tag == "CRGB":
        c = decode_color(raw, order)
        rgb = np.broadcast_to(np.array(c[:3], np.int64), (H, W, 3))
        return rgb, np.full((H, W), c[3], np.int64)
    if tag == "CGRD":
        g = d.get("gradient") or {}
        lut = _gradient_lut(g.get("stops") or [(0, (255, 255, 255, 255)), (255, (255, 255, 255, 255))])
        if g.get("image"):
            pos = texture_positions(ts1, d["color_resource"], (H, W), info)
        else:
            # 縦: ベースラインからの範囲で 0 → 255（金の 73: 行全体で同じ範囲。仮の数値は PARAMS）
            size, base = info["size"], info["baseline"]
            top = base - size * PARAMS["grad_top"]
            bottom = base + size * PARAMS["grad_bottom"]
            ys = np.arange(H, dtype=float)[:, None]
            xs = np.arange(W, dtype=float)[None, :]
            if d.get("flags", 0) == 0x100:
                # 赤の光沢（層の見出し +8 = 0x100）: 字ごとの正方形（ベースラインから文字サイズぶん上まで、横は文字サイズ × 横倍率）の
                # 左上の角 → 右下の角へ 0 → 255。斜体と一緒に傾く。光沢だけの PNG（56）の 3 段の色分けと 93% 一致（衝は 99.8%）
                sh = info.get("shear", 0.0)
                org = info["origins"]
                ox = np.array([o for o, cw, ch in org]) if org else np.zeros(1)
                xu0 = xs + (base - ys) * sh
                idx = np.clip(np.searchsorted(ox, xu0, side="right") - 1, 0, len(ox) - 1)
                cw = np.array([c for o, c, h in org])[idx] if org else np.full(idx.shape, size)
                chh = np.array([h for o, c, h in org])[idx] if org else np.full(idx.shape, size)
                xu = xs - ox[idx] - (base - ys) * sh
                proj = (xu / cw + (ys - (base - chh)) / chh) / 2
                pos = np.clip(np.floor(proj * 255 + 0.5), 0, 255).astype(np.int64)
            else:
                pos = np.clip(np.floor((ys - top) * 255 / max(bottom - top, 1) + 0.5), 0, 255).astype(np.int64)
                pos = np.broadcast_to(pos, (H, W))
        c = lut[pos]
        return c[..., :3], c[..., 3]
    if tag == "CPTN":
        pos = texture_positions(ts1, d["color_resource"], (H, W), info)
        v = np.stack([pos] * 3, -1)
        return v, np.full((H, W), 255, np.int64)
    return np.full((H, W, 3), 255, np.int64), np.full((H, W), 255, np.int64)


def texture_positions(ts1, index, shape, info):
    """模様の値を画面の座標に貼る（仮。二色の 72 の方眼から: 縦は 文字の上端から 150/1080 倍、横は 0.267 倍、
    基準は行の左端）。値は 0〜255"""
    import wpf
    H, W = shape
    tex = None
    for i, tag, tx in wpf.textures_of(ts1):
        if i == index:
            tex = tx.planes()[0]
            break
    if tex is None:
        return np.full((H, W), 255, np.int64)
    size = info["size"]
    sy = (150.0 / 135.0) * size / tex.shape[0]
    sx = sy * 1.92
    ys = ((np.arange(H) - info["em_top"]) / sy).astype(np.int64) % tex.shape[0]
    xs = ((np.arange(W) - info["left"]) / sx).astype(np.int64) % tex.shape[1]
    return tex[ys[:, None], xs[None, :]].astype(np.int64)


# ---------------------------------------------------------------------------
# 層の形
# ---------------------------------------------------------------------------
def layer_shape(d, F, U):
    """層 1 つの形 → (重ねる前の量 k, ぼかしたアルファ)。F = 文字面の量、U = 上の層を合わせた量（縁の土台）"""
    kind, w, w2, e = d["kind"], d["width_px"], d.get("width2_px", d["width_px"]), d["extra"]
    base = U if U.any() else F
    if kind in (0, 0x801):
        k = F if w >= 0 or kind == 0x801 else erode(F, VR.edge_offsets(-w))
    elif kind == 9:
        k = dilate_round(base, w) if w > 0 else (erode(F, VR.edge_offsets(-w)) if w < 0 else base.copy())
    elif kind == 4:
        k = dilate_square(base, w) if w > 0 else base.copy()
    elif kind == 3:
        # 仮: 右下へ 1 段ずつずらしたものを重ねる（立体）
        k = base.copy()
        sx, sy = PARAMS["extrude_step"]
        n = _pv(PARAMS["extrude_len"], w, w2, e)
        for i in range(1, n + 1):
            np.maximum(k, shift(base, i * sx, i * sy), out=k)
        e = max(e, _pv(PARAMS["extrude_blur"], w, w2, e))
    elif kind in (16, 1):
        # 仮: 右下へずらしてぼかす（影）
        s = _pv(PARAMS["shadow_shift"], w, w2, e)
        k = shift(base, s, s)
        e = max(e, _pv(PARAMS["shadow_blur"], w, w2, e))
    elif kind == 0x800:
        # 仮: 光彩 = 上の層を広げてぼかす
        g = _pv_signed(PARAMS["glow_grow"], w, w2, e)
        k = dilate_round(base, g) if g > 0 else (erode(base, VR.edge_offsets(-g)) if g < 0 else base.copy())
        a = alpha_of(k)
        r = _pv(PARAMS["glow_blur"], w, w2, e)
        for _ in range(PARAMS["glow_passes"] if r > 0 else 0):
            a = box_blur(a, r)
        return k, np.minimum(a, 255)
    else:
        k = base.copy()
    a = alpha_of(k)
    if e > 0:
        a = box_blur(a, e)
    return k, np.minimum(a, 255)


# ---------------------------------------------------------------------------
# 重ね方（vwsrender.compose を一般にしたもの。文字面 + 縁 1 本なら compose と同じ値）
# ---------------------------------------------------------------------------
def over(lower_rgb, lower_a, up_rgb, up_a):
    A = lower_a + (((255 - lower_a) * (up_a + 1)) >> 8)
    w = (up_a * 255 + 1) // np.maximum(A, 1)
    rgb = lower_rgb + (((up_rgb - lower_rgb) * (w + 1)[..., None]) >> 8)
    rgb = np.where((up_a == 0)[..., None], lower_rgb, rgb)
    return rgb, A


def _line_info(ts1, chars_ids, F):
    ys, xs = np.nonzero(F)
    size = ts1.char_size[1]
    base_y = ts1.pos[1]
    sl = ts1.slants() if hasattr(ts1, "slants") else [0.0]
    origins = sorted((x / 256.0, float(getattr(sc, "sx", sc)) * 2048, float(getattr(sc, "sy", sc)) * 2048)
                     for ch, g, x, y, sc in ts1.layout_fx() if id(ch) in chars_ids and g.contours)
    return {
        "origins": origins,
        "baseline": base_y,
        "shear": math.tan(math.radians(sl[0])) if sl and sl[0] else 0.0,
        "ink_top": int(ys.min()) if len(ys) else 0,
        "ink_bottom": int(ys.max()) + 1 if len(ys) else 1,
        "left": ts1.pos[0],
        "em_top": base_y - size,
        "size": size,
    }


def face_counts_for(ts1, keep_ids, canvas=CANVAS):
    """一部の字だけの文字面の量（字ごとに装飾が違うテロップ用）"""
    return VR.face_counts(_FilteredTS1(ts1, keep_ids), canvas).astype(np.int64)


class _FilteredTS1:
    """layout_fx の結果から一部の字だけを返す薄い包み"""

    def __init__(self, t, keep_ids):
        self._t, self._keep = t, keep_ids

    def __getattr__(self, name):
        return getattr(self._t, name)

    def layout_fx(self, newline_code=0x0D):
        return [l for l in self._t.layout_fx(newline_code) if id(l[0]) in self._keep]


def render_layers(ts1, canvas=CANVAS, order=COLOR_ORDER):
    """→ (重ねた RGBA uint8, [(層の名前, その層の RGBA uint8)])。層は上から順。
    文字の要素が 2 つ以上あるとき（図形つき）は、要素ごとに描いて並びの順に重ねる（図形の要素はまだ描けない）"""
    views = ts1.element_views() if hasattr(ts1, "element_views") else [ts1]
    if len(views) <= 1:
        return _render_one(ts1, canvas, order)
    W, H = canvas
    rgb = np.zeros((H, W, 3), np.int64)
    A = np.zeros((H, W), np.int64)
    layers = []
    for k, v in enumerate(views):
        img, ls = _render_one(v, canvas, order)
        rgb, A = over(rgb, A, img[..., :3].astype(np.int64), img[..., 3].astype(np.int64))
        layers += [(f"文字{k + 1} {n}", L) for n, L in ls]
    out = np.zeros((H, W, 4), np.uint8)
    out[..., :3] = np.clip(rgb, 0, 255)
    out[..., 3] = np.clip(A, 0, 255)
    out[A == 0] = 0
    return out, layers


def _render_one(ts1, canvas=CANVAS, order=COLOR_ORDER):
    W, H = canvas
    per = ts1.decorations_per_char(order)
    groups = {}
    for ch, ds in zip(ts1.chars, per):
        if not ch.code:
            continue
        sig = tuple((d["kind"], d["color_resource"], d["opacity256"], d["width_px"], d["extra"]) for d in ds)
        groups.setdefault(sig, (ds, set()))[1].add(id(ch))
    n_layers = max((len(ds) for ds, _ in groups.values()), default=0)
    layer_rgb = [np.zeros((H, W, 3), np.int64) for _ in range(n_layers)]
    layer_a = [np.zeros((H, W), np.int64) for _ in range(n_layers)]
    names = [""] * n_layers
    for sig, (ds, ids) in groups.items():
        F = face_counts_for(ts1, ids, canvas)
        info = _line_info(ts1, ids, F)
        U = np.zeros_like(F)
        for li, d in enumerate(ds):
            if li >= n_layers:
                break
            k, a = layer_shape(d, F, U)
            U = np.maximum(U, k)
            a = (a * d["opacity256"]) >> 8
            rgb, ca = layer_color(ts1, d, a > 0, info, order)
            a = (a * ca) // 255
            sel = a > layer_a[li]
            layer_rgb[li] = np.where(sel[..., None], rgb, layer_rgb[li])
            layer_a[li] = np.where(sel, a, layer_a[li])
            names[li] = names[li] or d["role_ja"]
    rgb = np.zeros((H, W, 3), np.int64)
    A = np.zeros((H, W), np.int64)
    for li in reversed(range(n_layers)):           # 下の層から重ねる
        rgb, A = over(rgb, A, layer_rgb[li], layer_a[li])
    out = np.zeros((H, W, 4), np.uint8)
    out[..., :3] = np.clip(rgb, 0, 255)
    out[..., 3] = np.clip(A, 0, 255)
    out[A == 0] = 0
    layers = []
    for li in range(n_layers):
        L = np.zeros((H, W, 4), np.uint8)
        L[..., :3] = np.clip(layer_rgb[li], 0, 255)
        L[..., 3] = np.clip(layer_a[li], 0, 255)
        layers.append((f"{li}: {names[li]}", L))
    return out, layers
