"""VWS と同じ手順でテロップを描く（VWS の書き出し PNG の画素値から突き止めた描き方。2026-11-12）

VWS の描き方（見本と、確認用の第 1 回・第 2 回・追加分を VWS で開いた書き出しで確認。ニューロダン B / EB）
  1. 字を 1/256px の整数で並べ（layout_fx）、原点を 8 倍の格子へ切り捨てる。字送りは A・B・C を別々に 1/256px へ
     切り捨てて足すので、全角でも少しずつ短くなり、7〜8 字目あたりで 1/8px の段差が出る
  2. 輪郭の点（2048 単位）を 8 倍の格子へ: (int)(原点の画素内の位置 + 座標 × サイズ × 8 / 2048)。C の整数変換 = 0 方向へ切り捨て
     （x は左へ、ベースラインより上の y は下へ寄る。原点が画素の境目なら (int)(座標 × 倍率) と同じ）
  3. 2 次ベジェ曲線は、整数のまま半分ずつに割る（中点 (a + b) / 2 も 0 方向へ切り捨て）。
     弦の中点 p0 + (p1 − p0) / 2 と制御点の差 h が hx² + hy² ≤ 17 になったら割るのをやめて直線でつなぐ
  4. 多角形を GDI と同じ規則で塗る（8 倍サイズ・整数座標の点で内外判定・左と上を含み右と下を含まない）
  5. 1 画素 = 8×8 の点を数える → 1 ラインぶんの文字面 m (0..64)
  6. 上下 2 ラインを足す: k[y] = m[y] + m[y+1] (0..128)。字ごとの絵には枠があり、枠の外は描かない。
     枠は輪郭データの点の範囲を実数のまま置いて決まる（上: floor(上端 − 0.5) の行から。左: 左端を切り捨てた列から）
  7. 縁 = 文字面 k を 1px 刻みでずらしたコピーの最大値（ずらし量の形は EDGE_ROWS の実測値。縁 1・2・3・5・6・10・14・20px）
  8. 縁の層（縁色・Ae）の上に文字面の層（文字色・Af）を重ねる。整数の式は compose の説明
見本と確認用 31 本の 32 枚中 28 枚で、VWS の PNG と 1 画素も違わない（残りは縁 1px の色 1/255 と、
原点がちょうど 1/8px の境目に乗った字が 1/8px 左に出る 3 枚）。
影（装飾の種別 1）は未対応。表に無い縁幅は円 (縁 + 0.62)² で描く（4・7・8・9・12px は VWS と一致を確認）。
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image

import struct

from ts1core import COLOR_ORDER, PRIM_LINE, PRIM_QSPLINE, PRIM_CSPLINE, Scale

SS = 8                 # 1px を 8×8 に分ける
FX = 256               # VWS は横の位置を 1/256px の整数（24.8 固定小数点）で持つ
# 曲線を割るのをやめる条件（8 倍格子の整数）:
#   h = p0 + (p1 − p0) / 2 − c   … 弦の中点と制御点の差。割り算は C の整数割り算（0 方向へ切り捨て）
#   hx² + hy² ≤ FLAT_SQ なら割らずに直線でつなぐ
# 見本の 16 字・曲線 309 本（割る/割らないが画像から確かめられる 702 か所）すべてがこの式どおり。
# 17 は割らない・18 は割る、まで確認済み（しきい値は 17 以上 18 未満のどこか）。
# 「p0 − 2c + p1」（2 階差分）で判定すると奇数のときの丸めが合わず、十数本の曲線で割り方が食い違う。
FLAT_SQ = 17
MAX_DEPTH = 8


def _tdiv2(a, b):
    """(a + b) / 2 を C の整数割り算（0 方向へ切り捨て）で"""
    return int((a + b) / 2)


def _subdivide(p0, c, p1, out, depth=0):
    hx = p0[0] + int((p1[0] - p0[0]) / 2) - c[0]
    hy = p0[1] + int((p1[1] - p0[1]) / 2) - c[1]
    if depth >= MAX_DEPTH or hx * hx + hy * hy <= FLAT_SQ:
        out.append(p1)
        return
    q0 = (_tdiv2(p0[0], c[0]), _tdiv2(p0[1], c[1]))
    q1 = (_tdiv2(c[0], p1[0]), _tdiv2(c[1], p1[1]))
    r = (_tdiv2(q0[0], q1[0]), _tdiv2(q0[1], q1[1]))
    _subdivide(p0, q0, r, out, depth + 1)
    _subdivide(r, q1, p1, out, depth + 1)


def glyph_polys8(glyph, sx8, sy8=None, fx=0, fy=0, shear=0.0):
    """字形の輪郭 → 8 倍格子の整数の多角形（字の原点からの相対）。sx8, sy8 = 2048 単位 → 8 倍格子の倍率（横・縦）
    fx, fy = 字の原点（8 倍格子）が画素の中のどこにあるか（0〜7）。VWS は「画素の中の位置 + 座標 × 倍率」を足してから
    切り捨てる（0 方向）ので、原点が画素の境目でないとき（y = 607.5 など）は、原点のすぐ上・すぐ左の点が 1/8px ずれる
    （サイズ 150・ベースライン .5 の 1 行もので確認: 「打」「験」の下端がこれで合う）"""
    sy8 = sx8 if sy8 is None else sy8
    # 点も、曲線を割るときの中点も「原点のある画素の左上」を基準にした座標で整数にする（中点の切り捨ては符号で向きが変わるため）。
    # 最後に字の原点からの相対に戻す
    # 斜体: 拡大したあとの座標で x − y × tan(角度)（y は下向き正なので、上の点ほど右へ）。
    P = lambda p: (int(fx + p[0] * sx8 - p[1] * sy8 * shear), int(fy + p[1] * sy8))
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
        polys.append([(a - fx, b - fy) for a, b in pl])
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


# 縁のずらし量の形（行 j = 0, 1, 2, … ごとの最大の |i|。上下左右に折り返して使う）。VWS の書き出し PNG から逆算した実測値。
# どの縁幅も、この形で VWS の PNG のアルファと 1 画素も違わない（縁 1・2px は色の重ね方の違いが少し残る。compose 参照）。
# 円 i² + j² ≤ T とは一致しない（例: 20px は (5, 20) が入って (20, 5) が入らない）。式は見つかっていない
EDGE_ROWS = {
    1: [1, 1],
    2: [2, 2, 1],
    3: [3, 3, 2, 1],
    5: [5, 5, 5, 4, 4, 2],
    6: [6, 6, 6, 5, 5, 4, 2],
    10: [10, 10, 10, 10, 9, 9, 8, 8, 7, 5, 3],
    14: [14, 14, 14, 14, 14, 13, 13, 12, 12, 11, 10, 9, 8, 6, 4],
    20: [20, 20, 20, 20, 20, 19, 19, 19, 19, 18, 18, 17, 16, 16, 15, 14, 13, 11, 10, 8, 5],
}
EDGE_FALLBACK_C = 0.62   # 表に無い縁幅: 円 i² + j² ≤ (縁 + 0.62)²（10・14px と同じ形になる値。4・7〜9px などは 1 段ずれる可能性あり）


def edge_measured(edge):
    """その縁幅の形が VWS で実測済みか"""
    return int(round(edge)) in EDGE_ROWS


def edge_offsets(edge):
    """縁のずらし量（整数点の集合）"""
    e = int(round(edge))
    rows = EDGE_ROWS.get(e)
    if rows is None:
        r2 = (e + EDGE_FALLBACK_C) ** 2
        n = int(math.floor(math.sqrt(r2)))
        return [(i, j) for i in range(-n, n + 1) for j in range(-n, n + 1) if i * i + j * j <= r2]
    pts = set()
    for j, m in enumerate(rows):
        for i in range(0, m + 1):
            for si in (1, -1):
                for sj in (1, -1):
                    pts.add((si * i, sj * j))
    return sorted(pts)


def _dilate(a, offs):
    n = max(max(abs(i), abs(j)) for i, j in offs)
    h, w = a.shape
    p = np.pad(a, n)
    out = np.zeros_like(a)
    for i, j in offs:
        np.maximum(out, p[n + j:n + j + h, n + i:n + i + w], out=out)
    return out


def advance_fx(g, size_px):
    """1 字の字送り（1/256px の整数）。中身は ts1core.Glyph.advance_fx"""
    return g.advance_fx(size_px)


def layout_fx(ts1, newline_code=0x0D):
    """VWS と同じ整数の計算で字を並べる。中身は ts1core.TS1.layout_fx"""
    return ts1.layout_fx(newline_code)


def _char_extras(ts1):
    """id(Char) → (tan(斜体の角度), ベースラインを下げる量 px)"""
    sl = ts1.slants() if hasattr(ts1, "slants") else [0.0] * len(ts1.chars)
    bs = ts1.baseline_shifts() if hasattr(ts1, "baseline_shifts") else [0.0] * len(ts1.chars)
    return {id(ch): (math.tan(math.radians(a)) if a else 0.0, d) for ch, a, d in zip(ts1.chars, sl, bs)}


def _sheared_left(g, sx, sy, shear):
    """傾けたあとの輪郭データの点（制御点も含む）の左端 px（字の原点から）"""
    pts = [p for c in g.contours for p in [c.start] + [q for _, qs in c.segs for q in qs]]
    return min(p[0] * sx - p[1] * sy * shear for p in pts) if pts else 0.0


def face_counts(ts1, canvas=(1920, 1080)):
    """文字面の量 k_f (0..128) をキャンバス全体の int 配列で返す"""
    W, H = canvas
    kf = np.zeros((H, W), np.int32)
    extra = _char_extras(ts1)
    smooth3 = bool(getattr(ts1, "h_condense", lambda: 1.0)() != 1.0)
    for ch, g, x_fx, y, sc in layout_fx(ts1):
        if not g.contours:
            continue
        shear, dy = extra[id(ch)]
        y = y + dy                                              # 字ごとのベースラインの下げ（MCHR +30）
        g8x = x_fx * SS // FX                                   # 1/256px の整数 → 8 倍格子へ切り捨て
        g8y = int(math.floor(y * SS))                           # 縦も切り捨て（横と同じ。40_縦の端数で確認）
        polys = glyph_polys8(g, getattr(sc, "sx", sc) * SS, getattr(sc, "sy", sc) * SS, g8x % SS, g8y % SS, shear)
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
        if smooth3:
            # 横に縮める型（金・縞）: 縦が 3 ラインの平均 (m[y−1] + 2 m[y] + m[y+1]) / 2 になる = 2 ライン足しをもう 1 回。
            # アルファは k[y−1] + k[y]。ここでは 0〜128 の量に直す（奇数のとき切り上げ → アルファが 1 だけ大きくなることがある）
            k2 = k.copy()
            k2[1:] += k[:-1]
            k = (k2 + 1) // 2
        # 字ごとの絵には枠があり、枠の外は描かれない。枠は輪郭データの点（制御点も含む）の範囲を実数のまま置いて決まる
        # （描いた多角形からではない。曲線レコードを壊した確認用ファイルで、読まれない点も範囲に入ることを確認）
        #   上: floor(上端の px − 0.5) の行から（2 ライン足しのぶん 1 行広い。上端 − 0.5 がちょうど整数ならその行から。
        #       41_EB の「の」= 上端 954.5px で確認）
        #   左: 左端の px を切り捨てた列から。輪郭の点は 0 方向へ切り捨てるので、左端がちょうど画素の境目を少し越えた字は
        #       1/8px だけ手前の画素にはみ出すが、そこは枠の外で描かれない（245 字で確認: はみ出した 16 字は全部描かれず、ほかは全部描かれる）
        sx_, sy_ = getattr(sc, "sx", sc), getattr(sc, "sy", sc)
        top_px = y - g.bbox()[1] * sy_
        first_row = int(math.floor(top_px - 0.5))
        if first_row - py0 > 0:
            k[:first_row - py0] = 0
        left_px = x_fx / FX + (_sheared_left(g, sx_, sy_, shear) if shear else g.bbox()[0] * sx_)
        first_col = int(math.floor(left_px))
        if first_col - px0 > 0:
            k[:, :first_col - px0] = 0
        # キャンバスへ（字どうしが重なる所は大きい方）
        cx0, cy0 = max(px0, 0), max(py0, 0)
        cx1, cy1 = min(px1, W), min(py1, H)
        if cx1 > cx0 and cy1 > cy0:
            sub = k[cy0 - py0:cy1 - py0, cx0 - px0:cx1 - px0]
            np.maximum(kf[cy0:cy1, cx0:cx1], sub, out=kf[cy0:cy1, cx0:cx1])
    return kf


def real_bbox(ts1):
    """字形の範囲（輪郭データの点。制御点も含む）を実数のまま画面に置いたときの外接範囲 (L, T, R, B)。無ければ None"""
    L = T = float("inf")
    R = B = float("-inf")
    extra = _char_extras(ts1)
    for ch, g, x_fx, y, sc in layout_fx(ts1):
        if not g.contours:
            continue
        sx, sy = getattr(sc, "sx", sc), getattr(sc, "sy", sc)
        shear, dy = extra[id(ch)]
        y = y + dy
        l, top, r, btm = g.bbox()          # y は上向き正
        xx = x_fx / FX
        if shear:
            pts = [p for c in g.contours for p in [c.start] + [q for _, qs in c.segs for q in qs]]
            xs = [p[0] * sx - p[1] * sy * shear for p in pts]
            L, R = min(L, xx + min(xs)), max(R, xx + max(xs))
        else:
            L, R = min(L, xx + l * sx), max(R, xx + r * sx)
        T, B = min(T, y - top * sy), max(B, y - btm * sy)
    return None if L == float("inf") else (L, T, R, B)


def drawn_bbox(ts1, edge=None, canvas=(1920, 1080)):
    """OFRI に入る描画範囲 (left, top, right, bottom)。描くものが無ければ None。
    VWS は描いた結果からではなく、字形の範囲の実数値 (L, T, R, B) から決めている:
        left   = floor(L + 1/8) − 縁          … 横は 1/8px ぶん右へ寄せてから切り捨て
        top    = floor(T − 0.5) − 縁          … 縦は四捨五入。上は 2 ライン足しのぶん 1 行広い（字ごとの枠の上端と同じ式）
        right  = floor(R + 1/8) + 1 + 縁
        bottom = floor(B + 0.5) + 1 + 縁
    VWS が保存した 3 種類（見本・縁なしの 1 行もの・別サンプル）と、確認用を VWS で開いたときの書き出し範囲
    （縁 3・10px、サイズ 60、字形を壊した版）がすべてこの式どおり。
    影があるとき（別サンプル 1 件だけ）: 右へ 影の広がり、下へ 影の広がり + 1"""
    edge = ts1.edge_width() if edge is None else edge
    edge = int(edge or 0)
    rb = real_bbox(ts1)
    if rb is None:
        return None
    L, T, R, B = rb
    l = int(math.floor(L + 0.125)) - edge
    t = int(math.floor(T - 0.5)) - edge
    r = int(math.floor(R + 0.125)) + 1 + edge
    b = int(math.floor(B + 0.5)) + 1 + edge
    sh = ts1.shadow() if hasattr(ts1, "shadow") else None
    if sh:
        r += int(sh["reach"])
        b += int(sh["reach"]) + 1
    return (l, t, r, b)


def compose(kf, face_rgb, edge_rgb, edge):
    """k_f → RGBA (uint8)。VWS と同じ整数の計算（縁の層の上に文字面の層を重ねる）:
        文字面の不透明度 Af = min(255, 2 × 文字面の k)、縁の不透明度 Ae = min(255, 2 × 縁の k)（縁なしなら 0）
        アルファ A = Ae + ((255 − Ae) × (Af + 1) >> 8)                         … 全画像で 1 画素も外れない
        色: Af = 0 なら縁色。それ以外は w = (Af × 255 + 1) // A として 縁色 + ((文字色 − 縁色) × (w + 1) >> 8)
            （>> は負の数でも切り捨て。縁が不透明なら w = Af。縁が半透明の所に文字面が重なる画素だけ、1 段ずれることがある）
    見本・確認用の全部（白/青、黄/黒、暗い灰/白）で、縁が不透明な所の色は 1 画素も違わない"""
    fi = np.array(face_rgb[:3], np.int64)[None, None]
    af = np.minimum(255, 2 * kf).astype(np.int64)
    if edge and edge > 0:
        ke = _dilate(kf, edge_offsets(edge))
        ae = np.minimum(255, 2 * ke).astype(np.int64)
        base = np.array(edge_rgb[:3], np.int64)[None, None]
    else:
        ae = np.zeros_like(af)
        base = fi
    A = ae + (((255 - ae) * (af + 1)) >> 8)
    w = (af * 255 + 1) // np.maximum(A, 1)
    rgb = base + (((fi - base) * (w + 1)[..., None]) >> 8)
    rgb = np.where((af == 0)[..., None], base, rgb)
    out = np.zeros(kf.shape + (4,), np.uint8)
    out[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    out[..., 3] = np.clip(A, 0, 255).astype(np.uint8)
    out[A == 0] = 0
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
