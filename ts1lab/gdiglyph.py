#!/usr/bin/env python3
"""Windows の GDI（GetGlyphOutlineW / GGO_NATIVE）から、VWS が .ts1 に入れているのと同じ字形データを作る

VWS は字形を次の手順で作っている（見本 16 字・1,046 点、新サンプルの字で 1 点残らず一致）:
  1. em の高さ 2048（lfHeight = -2048）でフォントを選び、GetGlyphOutlineW(GGO_NATIVE) で輪郭を取る
  2. 各点を整数にする: x = floor(x)、y = floor(-y - ずれ)   （GDI は y 上向き、VWS は y 下向き。ずれ = フォントごとの定数）
  3. 2 次スプライン（制御点が続くもの）は、隣り合う制御点の中点を整数で作って（足してから 0 方向へ切り捨て）
     「制御点 1 + 終点 1」の組に分ける。.ts1 の曲線は必ず 2 点ずつ
  4. 輪郭は逆回りにして書く（始点 = GDI の最後の点）。直線が続く所は 1 つの直線レコードにまとめる
ヘッダの アセント / ディセント は OUTLINETEXTMETRIC の otmAscent / otmDescent、A・B・C は字形の左右の余白と幅。

fontTools で輪郭を作ると（特に OTF = 3 次ベジェのフォントで）点の位置も数も VWS と違ってしまうので、
フォントが PC に入っているときはこちらを使う。Windows 専用。
"""
import ctypes
import math
import struct
import sys
from ctypes import wintypes as W

from ts1core import CELL, Contour, Glyph, PRIM_LINE, PRIM_QSPLINE, fx

EM = 2048
GGO_METRICS, GGO_NATIVE = 0, 2
GDI_ERROR = 0xFFFFFFFF
SHIFTJIS_CHARSET, DEFAULT_CHARSET = 128, 1


class LOGFONTW(ctypes.Structure):
    _fields_ = [("lfHeight", W.LONG), ("lfWidth", W.LONG), ("lfEscapement", W.LONG), ("lfOrientation", W.LONG),
                ("lfWeight", W.LONG), ("lfItalic", W.BYTE), ("lfUnderline", W.BYTE), ("lfStrikeOut", W.BYTE),
                ("lfCharSet", W.BYTE), ("lfOutPrecision", W.BYTE), ("lfClipPrecision", W.BYTE), ("lfQuality", W.BYTE),
                ("lfPitchAndFamily", W.BYTE), ("lfFaceName", W.WCHAR * 32)]


class TEXTMETRICW(ctypes.Structure):
    _fields_ = [(n, W.LONG) for n in ("tmHeight", "tmAscent", "tmDescent", "tmInternalLeading", "tmExternalLeading",
                                      "tmAveCharWidth", "tmMaxCharWidth", "tmWeight", "tmOverhang",
                                      "tmDigitizedAspectX", "tmDigitizedAspectY")] + \
               [("tmFirstChar", W.WCHAR), ("tmLastChar", W.WCHAR), ("tmDefaultChar", W.WCHAR), ("tmBreakChar", W.WCHAR),
                ("tmItalic", W.BYTE), ("tmUnderlined", W.BYTE), ("tmStruckOut", W.BYTE), ("tmPitchAndFamily", W.BYTE),
                ("tmCharSet", W.BYTE)]


class OUTLINETEXTMETRICW(ctypes.Structure):
    _fields_ = [("otmSize", W.UINT), ("tm", TEXTMETRICW), ("otmFiller", W.BYTE), ("panose", W.BYTE * 10),
                ("otmfsSelection", W.UINT), ("otmfsType", W.UINT), ("otmsCharSlopeRise", ctypes.c_int),
                ("otmsCharSlopeRun", ctypes.c_int), ("otmItalicAngle", ctypes.c_int), ("otmEMSquare", W.UINT),
                ("otmAscent", ctypes.c_int), ("otmDescent", ctypes.c_int), ("otmLineGap", W.UINT),
                ("otmsCapEmHeight", W.UINT), ("otmsXHeight", W.UINT), ("otmrcFontBox", W.LONG * 4),
                ("otmMacAscent", ctypes.c_int), ("otmMacDescent", ctypes.c_int), ("otmMacLineGap", W.UINT),
                ("otmusMinimumPPEM", W.UINT)]


class MAT2(ctypes.Structure):
    _fields_ = [("v", ctypes.c_short * 8)]


class GLYPHMETRICS(ctypes.Structure):
    _fields_ = [("gmBlackBoxX", W.UINT), ("gmBlackBoxY", W.UINT), ("ox", W.LONG), ("oy", W.LONG),
                ("cx", ctypes.c_short), ("cy", ctypes.c_short)]


class ABC(ctypes.Structure):
    _fields_ = [("a", ctypes.c_int), ("b", W.UINT), ("c", ctypes.c_int)]


def _gdi():
    g = ctypes.windll.gdi32
    g.CreateFontIndirectW.restype = W.HFONT
    g.CreateFontIndirectW.argtypes = [ctypes.c_void_p]
    g.SelectObject.restype = W.HGDIOBJ
    g.SelectObject.argtypes = [W.HDC, W.HGDIOBJ]
    g.CreateCompatibleDC.restype = W.HDC
    g.CreateCompatibleDC.argtypes = [W.HDC]
    g.DeleteObject.argtypes = [W.HGDIOBJ]
    g.DeleteDC.argtypes = [W.HDC]
    g.GetTextFaceW.argtypes = [W.HDC, ctypes.c_int, ctypes.c_wchar_p]
    g.GetGlyphOutlineW.argtypes = [W.HDC, W.UINT, W.UINT, ctypes.POINTER(GLYPHMETRICS), W.DWORD, ctypes.c_void_p,
                                   ctypes.POINTER(MAT2)]
    g.GetGlyphOutlineW.restype = W.DWORD
    g.GetOutlineTextMetricsW.argtypes = [W.HDC, W.UINT, ctypes.c_void_p]
    g.GetCharABCWidthsW.argtypes = [W.HDC, W.UINT, W.UINT, ctypes.POINTER(ABC)]
    g.GetGlyphIndicesW.argtypes = [W.HDC, ctypes.c_wchar_p, ctypes.c_int, ctypes.POINTER(W.WORD), W.DWORD]
    g.GetGlyphIndicesW.restype = W.DWORD
    return g


def available():
    return sys.platform == "win32"


class GdiFont:
    """em 2048 で選んだフォント。with で使うか、close() を呼ぶ"""

    def __init__(self, face, weight=0, charset=SHIFTJIS_CHARSET, italic=False):
        if not available():
            raise OSError("GDI は Windows でしか使えません")
        self.g = _gdi()
        self.dc = self.g.CreateCompatibleDC(None)
        lf = LOGFONTW()
        lf.lfHeight = -EM
        lf.lfWeight = int(weight or 0)
        lf.lfCharSet = charset
        lf.lfItalic = 1 if italic else 0
        lf.lfFaceName = face[:31]
        self.hf = self.g.CreateFontIndirectW(ctypes.byref(lf))
        self.old = self.g.SelectObject(self.dc, self.hf)
        buf = ctypes.create_unicode_buffer(64)
        self.g.GetTextFaceW(self.dc, 64, buf)
        self.face = buf.value          # 実際に選ばれた書体名（無いフォントを頼むと別の書体になる）
        self.requested = face
        n = self.g.GetOutlineTextMetricsW(self.dc, 0, None)
        raw = ctypes.create_string_buffer(max(n, ctypes.sizeof(OUTLINETEXTMETRICW)))
        self.g.GetOutlineTextMetricsW(self.dc, n, raw)
        self.otm = OUTLINETEXTMETRICW.from_buffer_copy(raw.raw[:ctypes.sizeof(OUTLINETEXTMETRICW)])
        self.ascent, self.descent = self.otm.otmAscent, -self.otm.otmDescent
        self.mat = MAT2()
        self.mat.v[:] = [0, 1, 0, 0, 0, 0, 0, 1]

    def close(self):
        if self.dc:
            self.g.SelectObject(self.dc, self.old)
            self.g.DeleteObject(self.hf)
            self.g.DeleteDC(self.dc)
            self.dc = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def matched(self):
        """頼んだ書体が実際に選ばれたか"""
        return self.face == self.requested

    def has(self, code):
        s = chr(code)
        if len(s.encode("utf-16-le")) != 2:
            return False
        idx = (W.WORD * 1)()
        r = self.g.GetGlyphIndicesW(self.dc, s, 1, idx, 1)   # GGI_MARK_NONEXISTING_GLYPHS
        return r != GDI_ERROR and idx[0] != 0xFFFF

    def native(self, code):
        """GGO_NATIVE の生データを [(始点, [(種類, [点...]), ...]), ...]（実数・y 上向き）で返す"""
        gm = GLYPHMETRICS()
        n = self.g.GetGlyphOutlineW(self.dc, code, GGO_NATIVE, ctypes.byref(gm), 0, None, ctypes.byref(self.mat))
        if n == GDI_ERROR:
            raise KeyError(f"字形を取れません: U+{code:04X}")
        raw = b""
        if n:
            buf = ctypes.create_string_buffer(n)
            self.g.GetGlyphOutlineW(self.dc, code, GGO_NATIVE, ctypes.byref(gm), n, buf, ctypes.byref(self.mat))
            raw = buf.raw
        out, p = [], 0
        while p < len(raw):
            cb, _typ, sx, sy = struct.unpack_from("<IIii", raw, p)
            q, qe = p + 16, p + cb
            segs = []
            while q < qe:
                prim, k = struct.unpack_from("<HH", raw, q)
                q += 4
                pts = [tuple(v / 65536 for v in struct.unpack_from("<ii", raw, q + 8 * i)) for i in range(k)]
                q += 8 * k
                segs.append((prim, pts))
            out.append(((sx / 65536, sy / 65536), segs))
            p = qe
        return out, gm

    def blackbox(self, code):
        """GGO_METRICS の黒枠の幅・高さ（ヘッダの w, h に入る値）"""
        gm = GLYPHMETRICS()
        n = self.g.GetGlyphOutlineW(self.dc, code, GGO_METRICS, ctypes.byref(gm), 0, None, ctypes.byref(self.mat))
        if n == GDI_ERROR:
            return None
        return gm.gmBlackBoxX, gm.gmBlackBoxY

    def abc(self, code):
        a = ABC()
        if self.g.GetCharABCWidthsW(self.dc, code, code, ctypes.byref(a)):
            return a.a, a.b, a.c
        return None


def _tdiv2(a, b):
    return int((a + b) / 2)


def convert(native, y_shift):
    """GDI の輪郭 → VWS の輪郭（Contour のリスト）"""
    def T(p):
        return (math.floor(p[0]), math.floor(-p[1] - y_shift))

    res = []
    for st, segs in native:
        items, cur = [], T(st)
        for prim, ps in segs:
            ip = [T(p) for p in ps]
            if prim == 1:
                for p in ip:
                    items.append((None, cur))
                    cur = p
            else:
                for i in range(len(ip) - 1):
                    c = ip[i]
                    e = ip[i + 1] if i == len(ip) - 2 else (_tdiv2(ip[i][0], ip[i + 1][0]), _tdiv2(ip[i][1], ip[i + 1][1]))
                    items.append((c, cur))
                    cur = e
        out = []
        for c, a in reversed(items):
            if c is None:
                if out and out[-1][0] == PRIM_LINE:
                    out[-1][1].append(a)
                else:
                    out.append((PRIM_LINE, [a]))
            else:
                out.append((PRIM_QSPLINE, [c, a]))
        res.append(Contour(cur, out))
    return res


class GdiGlyphSource:
    """FontGlyphSource と同じ使い方で、GDI から VWS と同じ字形を作る

    y_shift = VWS の縦位置の基準と、フォントの本来のベースラインとの差（2048 単位。フォントごとの定数）。
    分からないフォントは measure_shift() で、そのフォントの字形を持つ .ts1 から求める
    """

    def __init__(self, face, y_shift, weight=0, charset=SHIFTJIS_CHARSET):
        self.font = GdiFont(face, weight, charset)
        if not self.font.matched():
            got = self.font.face
            self.font.close()
            raise KeyError(f"フォント「{face}」が Windows に入っていません（代わりに「{got}」が選ばれました）")
        self.y_shift = y_shift
        self.ascent, self.descent = self.font.ascent, self.font.descent
        self.cell_ratio = None

    def has(self, code):
        return self.font.has(code)

    def glyph(self, code, font_id, usage=1) -> Glyph:
        if code == 0:
            return Glyph(0, font_id, usage, [], self.ascent, self.descent)
        native, _gm = self.font.native(code)
        g = Glyph(code, font_id, usage, convert(native, self.y_shift), self.ascent, self.descent)
        g.header_raw = build_header(g, self.font.abc(code), self.font.blackbox(code))
        return g


def build_header(g, abc=None, box=None):
    """字形ヘッダ（60 バイト）。範囲は輪郭の点（整数にしたあと）から、幅・高さは GDI の黒枠（GGO_METRICS）、
    A・B・C は GetCharABCWidths の値。GDI の値が無ければ範囲から作る"""
    xs, ys = [], []
    for c in g.contours:
        xs.append(c.start[0])
        ys.append(c.start[1])
        for _, pts in c.segs:
            for x, y in pts:
                xs.append(x)
                ys.append(y)
    if xs:
        l, r, top, btm = min(xs), max(xs), -min(ys), -max(ys)
        w, h = r - l, top - btm
    else:
        l = r = top = btm = w = h = 0
    if abc is None:
        abc = (l, r - l, CELL - r) if xs else (0, CELL, 0)
    if box is not None and xs:
        w, h = box
    return (struct.pack("<HH", len(g.contours), 1)
            + struct.pack("<iiii", fx(l), fx(top), fx(r), fx(btm))
            + struct.pack("<HHHH", CELL, CELL, w & 0xFFFF, h & 0xFFFF)
            + struct.pack("<II", g.font_id, g.code)
            + struct.pack("<ii", g.ascent, -g.descent)
            + struct.pack("<iii", *abc)
            + b"\x00" * 4)


def _points(contours):
    return [[c.start] + [p for _, ps in c.segs for p in ps] for c in contours]


def _shape(contours):
    return [[(pr, len(ps)) for pr, ps in c.segs] for c in contours]


def compare(ts1, face, y_shift, weight=0, charset=SHIFTJIS_CHARSET, font_id=None):
    """ts1 の中の字形と、GDI から作った字形を比べる → (字の数, 完全に一致した字の数, 違う点の数, 点の総数)"""
    with GdiFont(face, weight, charset) as f:
        if not f.matched():
            return None
        n = ok = bad = tot = 0
        for g in ts1.glyphs:
            if not g.code or not g.contours or (font_id is not None and g.font_id != font_id):
                continue
            n += 1
            try:
                native, _ = f.native(g.code)
            except KeyError:
                bad += 1
                continue
            cv = convert(native, y_shift)
            a, b = _points(g.contours), _points(cv)
            pts = sum(len(x) for x in a)
            tot += pts
            if _shape(g.contours) != _shape(cv):
                bad += pts
                continue
            d = sum(p != q for x, y in zip(a, b) for p, q in zip(x, y))
            bad += d
            ok += d == 0
        return n, ok, bad, tot


def measure_shift(ts1, face, weight=0, charset=SHIFTJIS_CHARSET, font_id=None):
    """ts1 の字形に合う y_shift（整数）を探す。全字が完全に一致する値が無ければ None"""
    with GdiFont(face, weight, charset) as f:
        if not f.matched():
            return None
        for g in ts1.glyphs:
            if not g.code or not g.contours or (font_id is not None and g.font_id != font_id):
                continue
            try:
                native, _ = f.native(g.code)
            except KeyError:
                return None
            if len(native) != len(g.contours):
                return None
            # 最初の輪郭の始点（= GDI の最後の点）から候補を出す
            cv = convert(native, 0)
            guess = cv[0].start[1] - g.contours[0].start[1]
            for s in (guess, guess - 1, guess + 1):
                r = compare(ts1, face, s, weight, charset, font_id)
                if r and r[0] and r[1] == r[0]:
                    return s
            return None
    return None


def list_faces(charset=DEFAULT_CHARSET):
    """入っているフォントの書体名（縦書き用の @ つきは除く）"""
    g = _gdi()
    user = ctypes.windll.user32
    user.GetDC.restype = W.HDC
    user.GetDC.argtypes = [W.HWND]
    user.ReleaseDC.argtypes = [W.HWND, W.HDC]
    names = set()
    PROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.POINTER(LOGFONTW), ctypes.c_void_p, W.DWORD, W.LPARAM)

    def cb(lf, _tm, _typ, _lp):
        nm = lf.contents.lfFaceName
        if nm and not nm.startswith("@"):
            names.add(nm)
        return 1

    proc = PROC(cb)
    g.EnumFontFamiliesExW.argtypes = [W.HDC, ctypes.POINTER(LOGFONTW), PROC, W.LPARAM, W.DWORD]
    dc = user.GetDC(None)
    lf = LOGFONTW()
    lf.lfCharSet = charset
    g.EnumFontFamiliesExW(dc, ctypes.byref(lf), proc, 0, 0)
    user.ReleaseDC(None, dc)
    return sorted(names)


def identify(ts1, font_id=None, faces=None, progress=None):
    """ts1 の字形と完全に一致するフォントを、入っているフォントの中から探す → [(書体名, y_shift), ...]"""
    hits = []
    faces = faces or list_faces()
    for i, face in enumerate(faces):
        if progress:
            progress(i, len(faces), face)
        try:
            s = measure_shift(ts1, face, font_id=font_id)
        except Exception:
            s = None
        if s is not None:
            hits.append((face, s))
    return hits


if __name__ == "__main__":
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ts1core import TS1
    t = TS1(open(sys.argv[1], "rb").read())
    fids = sorted({g.font_id for g in t.glyphs if g.code and g.contours})
    for fid in fids:
        print(f"フォント番号 {fid} (0x{fid:X}):")
        for face, s in identify(t, fid, faces=sys.argv[2:] or None):
            print(f"   一致: {face}  ずれ {s}  {compare(t, face, s, font_id=fid)}")
