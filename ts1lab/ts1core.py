"""VWS テロップファイル (.ts1) の解析・生成コア（実験用・非公式）

サンプル1件からのリバースエンジニアリングに基づく。フォーマットの詳細は FORMAT.md を参照。

基本ルール
  - チャンク = 4文字タグ + u32 サイズ(ヘッダ8バイト込み) + ペイロード（リトルエンディアン）
    例外: WVCT のサイズはヘッダ8バイトを含まない / ESET は "ESET" + u32 0 + u16 件数 の10バイト固定
          EMTR は "EMTR" + u32 文字コード + 12バイト の20バイト固定
  - 座標・サイズの多くは 24.8 固定小数点 (i32 / 256)
  - 文字列は  u8 長さ(0 のときは続く u16 が長さ) + FF FE + UTF-16LE + 00 00
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

FIX = 256.0  # 24.8 固定小数点


def fx(v: float) -> int:
    return int(round(v * FIX))


def u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def i32(b, o):
    return struct.unpack_from("<i", b, o)[0]


def chunk_at(b, o, tag=None):
    t = bytes(b[o:o + 4])
    if tag is not None and t != tag:
        raise ValueError(f"0x{o:x}: {tag!r} を期待したが {t!r}")
    return t, u32(b, o + 4)


def read_str(b, o):
    """VWS 文字列を読む。(文字列, 次オフセット)"""
    n = b[o]
    o += 1
    if n == 0:
        n = u16(b, o)
        o += 2
    s = bytes(b[o:o + n])
    if s[:2] != b"\xff\xfe":
        raise ValueError(f"0x{o:x}: 文字列マーカー FF FE がない")
    return s[2:].decode("utf-16le").rstrip("\x00"), o + n


def write_str(s: str) -> bytes:
    body = b"\xff\xfe" + s.encode("utf-16le") + b"\x00\x00"
    if len(body) < 256:
        return bytes([len(body)]) + body
    return b"\x00" + struct.pack("<H", len(body)) + body


# ----------------------------------------------------------------------------
# グリフ (RCHR / WVCT)
# ----------------------------------------------------------------------------
PRIM_LINE = 1   # TT_PRIM_LINE      : 直線（n 点）
PRIM_QSPLINE = 2  # TT_PRIM_QSPLINE : 2次Bスプライン（最後以外は制御点、制御点間は暗黙の中点）
PRIM_CSPLINE = 3  # TT_PRIM_CSPLINE : 3次ベジェ（未確認）

CELL = 2048  # グリフセル（tmAscent + tmDescent = 2048）


@dataclass
class Contour:
    start: tuple
    segs: list  # [(prim, [(x, y), ...]), ...]  座標は y 下向き、ベースライン = 0


@dataclass
class Glyph:
    code: int               # Unicode（0 = 終端用の空グリフ）
    font_id: int            # VWS 内部フォント番号（サンプルは 0x45F）
    usage: int              # この文字が本文で使われている回数
    contours: list
    ascent: int = 1802
    descent: int = 246
    header_raw: bytes | None = None  # 解析元のヘッダ（再現性のため保持）
    rchr_raw: bytes | None = None    # 解析元の RCHR 丸ごと
    tag: bytes = b"RCHR"             # チャンク名。RCHR か RUCH（中身の並びは同じ。RUCH は MCHR に USCU が付くファイルで使われる）

    @property
    def char(self):
        return chr(self.code) if self.code else ""

    # -- メトリクス ---------------------------------------------------------
    def bbox(self):
        """(left, top, right, bottom)  y は上向き正（ファイル座標の符号反転）"""
        xs, ys = [], []
        for c in self.contours:
            xs.append(c.start[0]); ys.append(c.start[1])
            for _, pts in c.segs:
                for x, y in pts:
                    xs.append(x); ys.append(y)
        if not xs:
            return (0, 0, 0, 0)
        return (min(xs), -min(ys), max(xs), -max(ys))

    def advance_units(self):
        """送り幅（2048 単位）= ABC 幅の合計 A + B + C。全角は 2048、専用空白 U+E73E は 512"""
        h = self.header_raw
        if h and len(h) >= 56:
            a_, b_, c_ = struct.unpack_from("<iii", h, 44)
            if a_ + b_ + c_ > 0:
                return a_ + b_ + c_
        return CELL

    def advance_fx_condensed(self, size_px, f):
        """横に縮める型での字送り（1/256px の整数）= floor((A + B + C) × k × f)。A・B・C を別々に切り捨てない
        （金の 8 字: 1 字あたり 34468 = floor(38400 × 0.897626)。行の幅 274464 とちょうど合う）"""
        h = self.header_raw
        a_, b_, c_ = struct.unpack_from("<iii", h, 44) if h and len(h) >= 56 else (0, 0, 0)
        if a_ + b_ + c_ <= 0:
            a_, b_, c_ = 0, CELL, 0
        return int(math.floor((a_ + b_ + c_) * size_px * FIX / CELL * f))

    def advance_fx(self, size_px):
        """字送り（1/256px の整数）。VWS は字形ヘッダの A（左の余白）・B（字の幅）・C（右の余白）を
        別々に 1/256px へ切り捨ててから足す。全角（A + B + C = 2048）でも端数の出方で 0〜2/256px 短くなる
        （VWS が保存した 1 行ものの「行の幅」と 1/256px まで一致。書き出し PNG でも 249 字中 248 字の位置が合う）"""
        h = self.header_raw
        a_, b_, c_ = struct.unpack_from("<iii", h, 44) if h and len(h) >= 56 else (0, 0, 0)
        if a_ + b_ + c_ <= 0:
            a_, b_, c_ = 0, CELL, 0
        k = size_px * FIX / CELL
        return int(math.floor(a_ * k)) + int(math.floor(b_ * k)) + int(math.floor(c_ * k))

    def build_header(self) -> bytes:
        l, t, r, btm = self.bbox()
        if not self.contours:
            w = h = 0
            a, bb, c = 0, CELL, 0
        else:
            w, h = r - l, t - btm
            a, bb, c = l, r - l, CELL - r
        return (struct.pack("<HH", len(self.contours), 1)
                + struct.pack("<iiii", fx(l), fx(t), fx(r), fx(btm))
                + struct.pack("<HHHH", CELL, CELL, w & 0xFFFF, h & 0xFFFF)
                + struct.pack("<II", self.font_id, self.code)
                + struct.pack("<ii", self.ascent, -self.descent)
                + struct.pack("<iii", a, bb, c)
                + b"\x00" * 4)

    # -- エンコード ---------------------------------------------------------
    def encode_contours(self) -> bytes:
        out = bytearray()
        for c in self.contours:
            body = bytearray()
            for prim, pts in c.segs:
                body += struct.pack("<HH", prim, len(pts))
                for x, y in pts:
                    body += struct.pack("<ii", x, y)
            out += struct.pack("<IIii", 1, len(body), c.start[0], c.start[1]) + body
        return bytes(out)

    def encode(self, keep_header=True) -> bytes:
        if keep_header and self.rchr_raw is not None:
            return self.rchr_raw
        data = self.encode_contours()
        hdr = self.header_raw if (keep_header and self.header_raw) else self.build_header()
        wv = b"WVCT" + struct.pack("<II", 4 + len(hdr) + len(data), len(data)) + hdr + data
        head = struct.pack("<HHHI", 1, self.usage, 0, self.font_id) + b"\x00" * 4 + struct.pack("<I", self.code)
        body = head + wv
        if self.tag == b"RUCH":      # RUCH は WVCT のあとに「コード数 1 + 文字コード」が付く
            body += struct.pack("<II", 1, self.code)
        return self.tag + struct.pack("<I", 8 + len(body)) + body


def parse_rchr(b, o) -> Glyph:
    tag, size = chunk_at(b, o)
    assert tag in (b"RCHR", b"RUCH"), f"0x{o:x}: グリフのチャンクではない {tag!r}"
    usage = u16(b, o + 10)
    font_id = u32(b, o + 14)
    code = u32(b, o + 22)
    w = o + 26
    _, wsize = chunk_at(b, w, b"WVCT")
    dlen = u32(b, w + 8)
    hdr_start = w + 12
    wend = w + 8 + wsize             # RCHR では WVCT の終わり = チャンクの終わり。RUCH はそのあとに 8 バイト付く
    if tag == b"RUCH":
        assert bytes(b[wend:o + size]) == struct.pack("<II", 1, code), f"RUCH 0x{o:x} の末尾が想定と違う"
    else:
        assert wend == o + size, f"RCHR 0x{o:x} の長さ不一致"
    data_start = wend - dlen
    hdr = bytes(b[hdr_start:data_start])
    asc = i32(hdr, 36)
    desc = -i32(hdr, 40)
    contours = []
    p, end = data_start, wend
    while p < end:
        typ, cb, x, y = struct.unpack_from("<IIii", b, p)
        assert typ == 1, f"0x{p:x}: 輪郭ヘッダ異常 {typ}"
        p += 16
        q, qe = p, p + cb
        segs = []
        while q < qe:
            prim, n = struct.unpack_from("<HH", b, q)
            q += 4
            pts = [struct.unpack_from("<ii", b, q + 8 * k) for k in range(n)]
            q += 8 * n
            segs.append((prim, pts))
        contours.append(Contour((x, y), segs))
        p = qe
    return Glyph(code, font_id, usage, contours, asc, desc, hdr, bytes(b[o:o + size]), bytes(tag))


# ----------------------------------------------------------------------------
# 文字 (MCHR / DAAR / EMTR)
# ----------------------------------------------------------------------------
ATTR_TAGS = [b"ADCO", b"AFIN", b"ACTP", b"ACSZ", b"AFLF", b"ASLP", b"AROT", b"ALSP", b"ACMG", b"ACSP"]


@dataclass
class Char:
    code: int            # 0 = 終端
    glyph_index: int     # ORMP 内のグリフ番号
    attrs: dict = field(default_factory=dict)  # tag(str) -> 生ペイロード(bytes, 先頭 01 00 込み)
    attr_order: list = field(default_factory=list)
    pre_raw: bytes = b"\x00" * 22
    uscu: bool = False     # EMTR のあとに USCU（文字コードの控え）を付けるか。1 行もの（CSSL）のファイルにある

    @property
    def char(self):
        return chr(self.code) if self.code else ""

    def spacing(self):
        """ACSP（字間 px）。無ければ None"""
        p = self.attrs.get("ACSP")
        return None if p is None else i32(p, 2) / FIX

    def encode(self) -> bytes:
        body = struct.pack("<HH", 1, self.glyph_index) + self.pre_raw
        if self.attr_order:
            kids = b"".join(t.encode() + struct.pack("<I", 8 + len(self.attrs[t])) + self.attrs[t]
                            for t in self.attr_order)
            body += b"DAAR" + struct.pack("<I", 12 + len(kids)) + struct.pack("<HH", 1, len(self.attr_order)) + kids
        body += b"EMTR" + struct.pack("<I", self.code) + b"\x00" * 12
        if self.uscu:
            body += b"USCU" + struct.pack("<IHIII", 22, 1, 1, 1, self.code)
        return b"MCHR" + struct.pack("<I", 8 + len(body)) + body


def parse_mchr(b, o):
    _, size = chunk_at(b, o, b"MCHR")
    gi = u16(b, o + 10)
    pre = bytes(b[o + 12:o + 34])
    p = o + 34
    attrs, order = {}, []
    if b[p:p + 4] == b"DAAR":
        _, dsz = chunk_at(b, p)
        n = u16(b, p + 10)
        q = p + 12
        for _ in range(n):
            t, s = chunk_at(b, q)
            attrs[t.decode()] = bytes(b[q + 8:q + s])
            order.append(t.decode())
            q += s
        p += dsz
    chunk_at(b, p, b"EMTR")
    code = u32(b, p + 4)
    p += 20
    uscu = False
    if p < o + size and b[p:p + 4] == b"USCU":
        # USCU: 版 1, 1, 1, 文字コード（EMTR と同じ値）。意味は未確認
        assert bytes(b[p:p + 22]) == b"USCU" + struct.pack("<IHIII", 22, 1, 1, 1, code), f"USCU 0x{p:x} が想定と違う"
        uscu = True
        p += 22
    assert p == o + size, f"MCHR 0x{o:x} の長さ不一致"
    return Char(code, gi, attrs, order, pre, uscu), size


# ----------------------------------------------------------------------------
# ファイル全体
# ----------------------------------------------------------------------------
@dataclass
class HistoryEntry:
    id: int
    time: int
    user: str
    action: str


# CRGB の色のバイト順。B G R A（Windows の DIB/RGBQUAD と同じ）。
# 見本は E7 2F 12 FF = 青 #122FE7。2026-11-12 ユーザー確認「テロップは青エッジ」で確定
# （それまで R G B A = 赤と解釈し、VWS の PNG が青いのは書き出し時の入れ替えと誤って推定していた）
COLOR_ORDER = "BGRA"


def decode_gradient(raw, order=COLOR_ORDER):
    """CGRD（グラデーション）→ {"stops": [(位置 0..255, (r, g, b, a))...], "image": 模様の画像（WPF1）があるか}

    CGRD = タグ, 長さ, u16 版, u16 使用回数, 6 バイト（0）, u8 色の数 n, 色 4 バイト × n（CRGB と同じ並び）, 位置 u8 × n,
           [WPF1 画像（模様。圧縮されていて、まだ展開できていない）], 末尾（0 が並び、04 … 05 … と続く。未解明）"""
    n = raw[18]
    cols = []
    for i in range(n):
        c = raw[19 + 4 * i:23 + 4 * i]
        cols.append((c[0], c[1], c[2], c[3]) if order == "RGBA" else (c[2], c[1], c[0], c[3]))
    pos = list(raw[19 + 4 * n:19 + 5 * n])
    return {"stops": list(zip(pos, cols)), "image": raw.find(b"WPF1") >= 0}


def replace_gradient(raw, colors, positions=None, order=COLOR_ORDER):
    """CGRD の色（と位置）を差し替えた生バイト列を返す。色の数が変わってもよい（長さの欄を直す）。
    colors = [(r, g, b, a), ...]。positions を省くと 0〜255 を等分"""
    n_old = raw[18]
    n = len(colors)
    if positions is None:
        positions = [round(255 * k / (n - 1)) if n > 1 else 0 for k in range(n)]
    assert len(positions) == n
    body = bytearray()
    for c in colors:
        r, g, b_, a = (tuple(c) + (255,))[:4]
        body += bytes((r, g, b_, a)) if order == "RGBA" else bytes((b_, g, r, a))
    body += bytes(int(v) & 255 for v in positions)
    out = bytearray(raw[:18]) + bytes((n,)) + body + raw[19 + 5 * n_old:]
    struct.pack_into("<I", out, 4, len(out))
    return bytes(out)


def decode_color(raw, order=COLOR_ORDER):
    """CRGB チャンク → (r, g, b, a)"""
    c = raw[14:18]
    if order == "RGBA":
        return (c[0], c[1], c[2], c[3])
    return (c[2], c[1], c[0], c[3])


def encode_color(rgba, order=COLOR_ORDER):
    r, g, b, a = (list(rgba) + [255])[:4]
    return bytes([r, g, b, a]) if order == "RGBA" else bytes([b, g, r, a])


class Scale(float):
    """2048 単位 → px の倍率。float としては縦の倍率。横と縦が違うとき（長体・平体）は .sx / .sy"""
    def __new__(cls, sx, sy=None):
        sy = sx if sy is None else sy
        o = float.__new__(cls, sy)
        o.sx, o.sy = float(sx), float(sy)
        return o


class TS1:
    """サンプル構造に沿ったパーサ。未解明部分は生バイトのまま保持し、再構築時に再利用する。"""

    def __init__(self, data: bytes):
        self.data = bytes(data)
        b = self.data
        chunk_at(b, 0, b"TSRC")
        assert u32(b, 4) == len(b), "TSRC サイズがファイル長と一致しない"
        self.off = {"TSRC": 0}
        self.off["SWRK"] = 0x10
        chunk_at(b, 0x10, b"SWRK")
        p = 0x10 + 8 + 10
        # SWRK 直下を順に読む
        while p < len(b):
            t = bytes(b[p:p + 4])
            if t == b"ESET":
                self.off.setdefault("ESET_top", p)
                p += 10
                continue
            _, s = chunk_at(b, p)
            self.off[t.decode()] = p
            if t == b"SLYR":
                break
            p += s
        self._parse_ofri()
        self._parse_fonts()
        self._parse_history()
        self._parse_ormp()
        self._parse_layer()

    # -- OFRI: 描画範囲 -----------------------------------------------------
    def _parse_ofri(self):
        o = self.off["OFRI"]
        self.ofri_bbox_off = o + 0x24
        self.ofri_bbox = tuple(i32(self.data, self.ofri_bbox_off + 4 * k) / FIX for k in range(4))

    # -- ODTT: フォント表（内蔵番号でないフォントを使うときだけある） ------------
    def _parse_fonts(self):
        """ODTT = u16 ver, u16 0, u16 件数, [u32 フォント番号, u32 太さ(700 など), 8B (+1: 0x12 ピッチと系統?, +3: 0x80 = 日本語の文字セット),
        文字列 書体名, 文字列 PostScript 名]…。見本（内蔵番号 1119）には無い。番号の決め方は不明（名前の CRC32/FNV/MD5 などではない）"""
        self.fonts = []
        o = self.off.get("ODTT")
        if o is None:
            return
        b = self.data
        _, s_ = chunk_at(b, o)
        n = u16(b, o + 12)
        p = o + 14
        for _ in range(n):
            fid, weight = struct.unpack_from("<II", b, p)
            flags = bytes(b[p + 8:p + 16])
            name, p = read_str(b, p + 16)
            ps, p = read_str(b, p)
            self.fonts.append({"font_id": fid, "weight": weight, "flags": flags.hex(), "charset": flags[3], "name": name, "postscript": ps})
        assert p == o + s_, "ODTT の長さ不一致"

    def font_name(self, font_id=None):
        fid = self.font_id if font_id is None else font_id
        for f in getattr(self, "fonts", []):
            if f["font_id"] == fid:
                return f
        return None

    # -- OMR0: 編集履歴 -----------------------------------------------------
    def _parse_history(self):
        b = self.data
        o = self.off["OMR0"]
        _, s = chunk_at(b, o)
        n = u16(b, o + 12)
        p = o + 14
        self.history = []
        for _ in range(n):
            eid, ts = struct.unpack_from("<II", b, p)
            p += 8
            user, p = read_str(b, p)
            act, p = read_str(b, p)
            self.history.append(HistoryEntry(eid, ts, user, act))
        assert p == o + s, "OMR0 の長さ不一致"

    # -- ORMP: リソース（グリフ・色・その他） ------------------------------
    def _parse_ormp(self):
        b = self.data
        o = self.off["ORMP"]
        _, s = chunk_at(b, o)
        n = u16(b, o + 10)
        p = o + 12
        self.resources = []  # [(tag, obj_or_raw)]
        for _ in range(n):
            t, cs = chunk_at(b, p)
            if t in (b"RCHR", b"RUCH"):
                self.resources.append(("RCHR", parse_rchr(b, p)))
            else:
                self.resources.append((t.decode(), bytes(b[p:p + cs])))
            p += cs
        assert p == o + s, "ORMP の長さ不一致"
        self.ormp_head = bytes(b[o + 8:o + 10])

    @property
    def glyphs(self):
        return [r for t, r in self.resources if t == "RCHR"]

    def glyph_at(self, index):
        """MCHR のグリフ番号 → Glyph。番号は ORMP の添字（リソース全体の通し番号）。
        グリフだけが先頭に並ぶファイルではグリフの並び順と同じだが、字ごとに装飾を変えたファイルでは
        色・グラデーションがグリフの間に挟まる（装飾サンプル「二色」）"""
        if 0 <= index < len(self.resources) and self.resources[index][0] == "RCHR":
            return self.resources[index][1]
        return self.glyphs[index]

    def glyphs_in_order(self):
        """グリフが ORMP の先頭に切れ目なく並んでいるか（rebuild はこの形だけ対応）"""
        n = len(self.glyphs)
        return all(t == "RCHR" for t, _ in self.resources[:n])

    def colors(self, order=COLOR_ORDER):
        """CRGB リソースの色を (r, g, b, a) で返す（ORMP 内の順）"""
        out = []
        for t, raw in self.resources:
            if t == "CRGB":
                out.append(decode_color(raw, order))
        return out

    def decorations(self, order=COLOR_ORDER):
        """先頭文字の ADCO（装飾）を読み、文字面・縁などの色と太さを返す

        ADCO = u16 ver, u8 件数, u8 追加見出しの有無, [追加見出し 16 バイト], (50 バイト + 拡張 32 バイト × ext) × 件数
          追加見出し: 2 つめのバイトが 1 のときだけある（装飾サンプル「赤」。中身は 01 00 … 00。意味は未確認）
          +0  u16 種別   (0 = 文字面, 9 = 縁, 1 = 影。ほかに 3・4・0x10・0x800・0x801 → FORMAT.md「装飾の種別」)
          +2  u16 ext    (1 のとき 1 件が 32 バイト長い。1 行もの（CSSL）の文字面で 1。中身は 0)
          +4  u32 色リソース番号（ORMP の添字 → CRGB 単色 / CGRD グラデーション・模様つき / CPTN 模様）
          +8  u32 不明（模様つきの文字面で 0x30、「赤」の光沢で 0x100）
          +28 u16 重ね方? (4 が普通。上に重ねる半透明の文字面は 5、「赤」の文字面は 6)
          +30 u16 追加分 px
          +32 u16 不透明度（256 = 100%。128 = 50%）。以前「+33 u8 有効」と読んでいたのは、この上位バイト
          +34 i32 太さ px（縁 6。光彩（0x800）は負の値）
          +38 i32 太さ2 px
        1 件目が一番上。下へ順に重ねる（「二色」: 模様つきグラデーション 50% → 単色の文字面 → … → 光彩）
        self の先頭文字の ADCO だけを読む（字ごとに装飾が違うファイルは decorations_of(ch) を使う）
        """
        return self._decode_adco(self.base_attr("ADCO"), order)

    def decorations_per_char(self, order=COLOR_ORDER):
        """各文字の装飾の一覧。ADCO が無い文字は直前の文字のものを引き継ぐ
        （装飾サンプル「二色」で確認: 3 字目で黄色の装飾に変わり、ADCO の無い 4〜6 字目も黄色）"""
        cur, out = [], []
        for ch in self.chars:
            if "ADCO" in ch.attrs:
                cur = self._decode_adco(ch.attrs["ADCO"], order)
            out.append(cur)
        return out

    def _decode_adco(self, p, order=COLOR_ORDER):
        if not p:
            return []
        n = p[2]
        out = []
        roles = {0: ("face", "文字面"), 9: ("edge", "縁"), 1: ("shadow", "影"),
                 0x800: ("glow", "光彩（外へぼかす）"), 0x801: ("face", "文字面（内側に帯。面取り?）"),
                 3: ("other", "立体（右下へ伸びる）"), 4: ("other", "四角い縁"), 0x10: ("other", "影（右下へずらしてぼかす）")}
        e = 20 if p[3] else 4
        for k in range(n):
            if k:
                e += 50 + 32 * u16(p, e + 2)     # 直前の 1 件の長さ
            if e + 42 > len(p):
                break
            kind, res = u16(p, e), u32(p, e + 4)
            t, raw = self.resources[res] if res < len(self.resources) else (None, None)
            color = decode_color(raw, order) if t == "CRGB" else None
            role, role_ja = roles.get(kind, ("other", f"種別 {kind}"))
            out.append({
                "resource_type": t,                              # CRGB = 単色 / CGRD = グラデーション / CPTN = 模様
                "gradient": decode_gradient(raw, order) if t == "CGRD" else None,
                "opacity256": u16(p, e + 32),                    # 256 = 100%
                "width_px": i32(p, e + 34),                      # 符号つき（光彩は負）
                "width2_px": i32(p, e + 38),                     # 太さ 2（立体・影で太さと違う値になる）
                "flags": u32(p, e + 8),
                "index": k,
                "kind": kind,
                "role": role,
                "role_ja": role_ja,
                "color_resource": res,
                "color": color,
                "color_hex": ("#%02X%02X%02X" % color[:3]) if color else None,
                "opacity": round(color[3] / 255 * 100, 1) if color else None,
                "order": u16(p, e + 28),
                "extra": u16(p, e + 30),        # 太さへの追加分（別サンプル: 縁 3 + 追加 3 = 6px 広がる）
                "enabled": bool(p[e + 33]),     # 0 = 無効（別サンプルの 1 件目の文字面）
                "width": u32(p, e + 34),
                "width2": u32(p, e + 38),
                "reach": u32(p, e + 34) + u16(p, e + 30),   # 外側へ広がる量 px
                "offset": e,
            })
        return out

    def face_color(self, order=COLOR_ORDER):
        ds = self.decorations(order)
        for d in ds:   # 有効な文字面を優先（無効な文字面が先に並ぶことがある）
            if d["role"] == "face" and d["color"] and d["enabled"]:
                return d["color"]
        for d in ds:
            if d["role"] == "face" and d["color"]:
                return d["color"]
        c = self.colors(order)
        return c[0] if c else (255, 255, 255, 255)

    def edge_color(self, order=COLOR_ORDER):
        for d in self.decorations(order):
            if d["role"] == "edge" and d["color"]:
                return d["color"]
        c = self.colors(order)
        return c[1] if len(c) > 1 else (0, 0, 0, 255)

    # -- SLYR / CSML: レイヤ・文字列 ---------------------------------------
    def _parse_layer(self):
        b = self.data
        o = self.off["SLYR"]
        _, s = chunk_at(b, o)
        assert o + s == len(b), "SLYR がファイル末尾まで続いていない（未対応の構造）"
        self.layer_name, p = read_str(b, o + 8)
        p += 2
        assert b[p:p + 4] == b"ESET"
        p += 10
        # 要素の並び（文字 CSML / CSSL のほか、図形のまとまり CGRP など）。最初の文字要素を「主」として読む
        nelem = u16(b, p - 2)
        self.elements = []           # [(タグ, 位置, 長さ)]
        q = p
        for _ in range(nelem):
            t_, s_ = chunk_at(b, q)
            self.elements.append((t_.decode("latin1"), q, s_))
            q += s_
        assert q == len(b), "SLYR の要素の並びがファイル末尾と合わない"
        texts = [el for el in self.elements if el[0] in ("CSML", "CSSL")]
        if not texts:
            raise ValueError(f"0x{p:x}: 文字の要素（CSML / CSSL）がありません: {[el[0] for el in self.elements]}")
        c = texts[0][1]
        c_end = c + texts[0][2]
        self.csml_end = c_end
        ctag, _ = chunk_at(b, c)
        self.kind = ctag.decode()      # CSML = 文字枠つき（複数行） / CSSL = 1 行もの（枠なし）
        self.off["CSML"] = c
        self.pos = (i32(b, c + 10) / FIX, i32(b, c + 14) / FIX)  # (x, ベースライン y)
        # MCHR 列の直前の ESET を探す
        q = b.find(b"CSTR", c)
        self.off["CSTR"] = q
        self.cstr_n = u16(b, q + 4)
        if self.cstr_n >= 8:
            self.box = (i32(b, q + 30) / FIX, i32(b, q + 34) / FIX)  # 文字枠 (幅, 高さ)
        else:                          # CSSL: 値 2 個 + 行の幅 + 0
            self.box = (i32(b, q + 14) / FIX, 0.0)
        e = b.find(b"ESET", q)
        self.off["ESET_chars"] = e
        nchars = u16(b, e + 8)
        p = e + 10
        self.chars = []
        for _ in range(nchars):
            ch, sz = parse_mchr(b, p)
            self.chars.append(ch)
            p += sz
        assert p == c_end, "MCHR 列のあとに未知のデータ"
        self.xtrs = []  # トランジション（イン/アウト）の座標オフセット
        p = c
        while True:
            p = b.find(b"XTRS", p, e)
            if p < 0:
                break
            self.xtrs.append(p)
            p += 4

    # -- 便利プロパティ ----------------------------------------------------
    @property
    def text(self):
        return "".join(ch.char for ch in self.chars)

    def base_attr(self, tag):
        for ch in self.chars:
            if tag in ch.attrs:
                return ch.attrs[tag]
        return None

    @property
    def char_size(self):
        p = self.base_attr("ACSZ")
        return (i32(p, 2) / FIX, i32(p, 6) / FIX) if p else (100.0, 100.0)

    @property
    def line_spacing(self):
        p = self.base_attr("ALSP")
        return i32(p, 2) / FIX if p else 0.0

    @property
    def font_id(self):
        p = self.base_attr("ACTP")
        return u32(p, 2) if p else None

    def edge_width(self):
        """縁の太さ px（ADCO の縁エントリ）。サンプルは 6（見本画像で確認済）"""
        for d in self.decorations():
            if d["role"] == "edge":
                return d["reach"] if d["enabled"] else 0
        return None

    def shadow(self):
        """影（種別 1）の装飾。無ければ None。別サンプルでは縁の外に右下へ約 8px（太さ 4 + 追加 4）広がる（描画は未検証）"""
        for d in self.decorations():
            if d["role"] == "shadow" and d["enabled"]:
                return d
        return None

    def spacings(self):
        """各文字の字間 (px)。DAAR が無い文字は直前の値を引き継ぐ（推定）"""
        cur, out = 0.0, []
        for ch in self.chars:
            v = ch.spacing()
            if v is not None:
                cur = v
            out.append(cur)
        return out

    # -- レイアウト ----------------------------------------------------------
    def magnifications(self):
        """各文字の倍率 (横 %, 縦 %)。ACMG が無い文字は直前の値を引き継ぐ（別サンプルで確認: 「さん」だけ 85%）"""
        cur, out = (100.0, 100.0), []
        for ch in self.chars:
            p = ch.attrs.get("ACMG")
            if p is not None:
                cur = (i32(p, 2) / FIX, i32(p, 6) / FIX)
            out.append(cur)
        return out

    # 横に縮める型（SCLS の 1 バイト目が 0x10）での横の倍率。金（サイズ 150・8 字）と縞（サイズ 230・3 字）の行の幅と、
    # 金の文字面の PNG（91611 画素中 58 画素の差）から 0.897622〜0.897630。値はファイルの中には無い
    # （フォント 1453 だけで確かめた。フォントごとに違う値かどうかは未確認）
    CONDENSE = 0.897626

    def scls_mode(self):
        """SCLS の 1 バイト目（0・2・4・6・0x10 を確認。0x10 = 字を横に縮める型）"""
        b = self.data
        q = b.find(b"SCLS", self.off["CSML"], self.off["ESET_chars"])
        return b[q + 8] if q >= 0 else 0

    def h_condense(self):
        """字の横の倍率（普通は 1.0。横に縮める型は CONDENSE）。字形の横幅と字送りの両方に掛かる。字間は縮めない"""
        return self.CONDENSE if self.scls_mode() & 0x10 else 1.0

    def slants(self):
        """各文字の斜体の角度（度。ASLP の 1 つめ）。無い文字は直前の値を引き継ぐ。
        VWS は「縦横に拡大した座標」を x − y × tan(角度) で傾ける（赤・二色の文字面の PNG と 1 画素も違わない）"""
        cur, out = 0.0, []
        for ch in self.chars:
            p = ch.attrs.get("ASLP")
            if p is not None:
                cur = i32(p, 2) / FIX
            out.append(cur)
        return out

    def baseline_shifts(self):
        """各文字のベースラインを下げる量 px（MCHR の +30、24.8 固定小数点。下向きが正）。
        二色の大きい数字（14.0）が 14px 下に描かれる。引き継ぎは無い（字ごとの値）"""
        return [i32(ch.pre_raw, 18) / FIX for ch in self.chars]

    def layout_fx(self, newline_code=0x0D):
        """VWS と同じ整数の計算で字を並べる → [(Char, Glyph, x 原点（1/256px の整数）, ベースライン y, scale)]
        scale は 2048 単位 → px の倍率（Scale: float。横と縦が違うときは .sx / .sy を見る）

        確認したルール（VWS の書き出し PNG と画素単位で照合。18 本・249 字のうち 248 字の位置がこの計算どおり）
          - 字送り = Glyph.advance_fx（A・B・C を別々に 1/256px へ切り捨てて足す）。文字サイズ × 横倍率で決まる
          - ACMG（倍率 %）は字形も送りも縮める（左・ベースライン基準）。指定が無い文字は直前の値を引き継ぐ
          - ACSP（字間）は「その文字の直前」の間隔に効く（行頭の ACSP は無視）。指定が無い文字は直前の値を引き継ぐ
          - 改行は U+000D（空の字形を持つ）。行送り = 文字サイズ + ALSP（行間）
          - 行の幅 = 字送りの合計 + 2 字目以降の字間
          - 枠つき（CSML）: 一番長い行を CSML の x から幅 CSTR.width の枠の中央に置き（余りの半分は切り捨て）、
            各行は左端をそろえる（行ごとの中央揃えではない）。CSML の y は最終行のベースライン
          - 1 行もの（CSSL）: x がそのまま行の左端
        """
        size_w, size_h = self.char_size
        sp = self.spacings()
        mg = self.magnifications()
        glyphs = self.glyphs
        hc = self.h_condense()
        lines, cur = [], []
        for ch, s_, m_ in zip(self.chars, sp, mg):
            if ch.code == 0:
                continue
            if ch.code in (0x0A, newline_code):
                lines.append(cur); cur = []
                continue
            g = self.glyph_at(ch.glyph_index)
            adv = g.advance_fx(size_w * m_[0] / 100) if hc == 1.0 else g.advance_fx_condensed(size_w * m_[0] / 100, hc)
            cur.append((ch, g, int(math.floor(s_ * FIX)), m_, adv))
        lines.append(cur)
        x0 = int(round(self.pos[0] * FIX))
        y0 = self.pos[1]
        bw = int(round(self.box[0] * FIX))
        pitch = size_h + self.line_spacing
        widths = [sum(c[4] for c in line) + sum(c[2] for c in line[1:]) if line else 0 for line in lines]
        start = x0 if self.kind == "CSSL" else x0 + (bw - max(widths)) // 2
        out = []
        for li, line in enumerate(lines):
            x = start
            y = y0 - (len(lines) - 1 - li) * pitch
            for k, (ch, g, s_fx, m_, adv) in enumerate(line):
                if k:
                    x += s_fx
                out.append((ch, g, x, y, Scale(size_w / CELL * m_[0] / 100 * hc, size_h / CELL * m_[1] / 100)))
                x += adv
        return out

    def layout(self, newline_code=0x0D):
        """各文字の配置を返す: [(Char, Glyph, x原点 px, ベースラインy, scale)]。layout_fx の x を px にしたもの"""
        return [(ch, g, x / FIX, y, sc) for ch, g, x, y, sc in self.layout_fx(newline_code)]

    def line_widths_fx(self, newline_code=0x0D):
        """行ごとの幅（1/256px の整数）= 字送りの合計 + 2 字目以降の字間"""
        out, cur, n = [], 0, 0
        size_w = self.char_size[0]
        hc = self.h_condense()
        for ch, s_, m_ in zip(self.chars, self.spacings(), self.magnifications()):
            if ch.code == 0:
                continue
            if ch.code in (0x0A, newline_code):
                out.append(cur); cur, n = 0, 0
                continue
            g_ = self.glyph_at(ch.glyph_index)
            cur += (g_.advance_fx(size_w * m_[0] / 100) if hc == 1.0 else g_.advance_fx_condensed(size_w * m_[0] / 100, hc)) \
                + (int(math.floor(s_ * FIX)) if n else 0)
            n += 1
        out.append(cur)
        return out

    # -- 情報 ---------------------------------------------------------------
    def info(self, color_order=COLOR_ORDER):
        return {
            "text": self.text,
            "layer_name": self.layer_name,
            "position_x": self.pos[0],
            "baseline_y": self.pos[1],
            "box_width": self.box[0],
            "box_height": self.box[1],
            "char_size": self.char_size,
            "line_spacing": self.line_spacing,
            "font_id": self.font_id,
            "fonts": getattr(self, "fonts", []),
            "face_color": "#%02X%02X%02X" % self.face_color(color_order)[:3],
            "edge_color": "#%02X%02X%02X" % self.edge_color(color_order)[:3],
            "edge_width": self.edge_width(),
            "decorations": [{k: v for k, v in d.items() if k not in ("offset", "color")}
                            for d in self.decorations(color_order)],
            "colors_raw": ["#%02X%02X%02X%02X" % c for c in self.colors(color_order)],
            "spacings": self.spacings(),
            "ofri_bbox": self.ofri_bbox,
            "glyph_count": len(self.glyphs),
            "history_count": len(self.history),
        }

    # -- 再構築 -------------------------------------------------------------
    def line_width(self):
        """1 行もの（CSSL）の CSTR に入る行の幅 (px) = 字送りの合計 + 2 字目以降の字間（1/256px の整数で足したもの）
        （VWS が保存した 5 本で一致: 150 × 3 + 1 + 4 = 455 から、字送りの切り捨てで 4/256 減って 454.984375）"""
        w = self.line_widths_fx()
        return max(w) / FIX if w else 0.0

    def rebuild(self, chars=None, glyphs=None, pos=None, face_color=None, edge_color=None,
                clear_history=False, keep_glyph_header=True, edge_width=None, color_order=COLOR_ORDER, _line_w=None):
        """テンプレート（self）の構造を保ったまま、文字列・グリフ・位置・色を差し替えて書き出す。
        pos = (枠の左, ベースライン)。1 行もの（CSSL）では「元の行の幅のまま左をここに置いたときの中心」を保って、
        新しい文言の幅に合わせて左位置と行の幅を書き直す"""
        b = self.data
        keep_slots = not self.glyphs_in_order()     # グリフの間に色が挟まるファイル: リソースの番号を動かさない
        args = dict(chars=chars, glyphs=glyphs, face_color=face_color, edge_color=edge_color, clear_history=clear_history,
                    keep_glyph_header=keep_glyph_header, edge_width=edge_width, color_order=color_order)
        pos_in = pos
        changed = chars is not None or glyphs is not None or pos is not None or edge_width is not None
        chars = self.chars if chars is None else chars
        glyphs = self.glyphs if glyphs is None else glyphs
        old_ng = len(self.glyphs)
        others = [(t, r) for t, r in self.resources if t != "RCHR"]
        shift = len(glyphs) - old_ng  # 色リソース番号のずれ
        if keep_slots:
            return self._rebuild_keep_slots(chars, glyphs, pos, pos_in, changed, args, clear_history, keep_glyph_header,
                                            face_color, edge_color, edge_width, color_order, _line_w)

        # ---- ORMP
        ormp = bytearray()
        for g in glyphs:
            ormp += g.encode(keep_header=keep_glyph_header)
        # 色: ADCO の参照先（旧番号）ごとに差し替え
        new_color = self.color_targets(face_color, edge_color, color_order)
        for j, (t, raw) in enumerate(others):
            res_old = old_ng + j
            if t == "CRGB" and res_old in new_color:
                raw = raw[:14] + new_color[res_old]
            ormp += raw
        o = self.off["ORMP"]
        n_res = len(glyphs) + len(others)
        ormp_chunk = b"ORMP" + struct.pack("<I", 12 + len(ormp)) + self.ormp_head + struct.pack("<H", n_res) + ormp

        # ---- OMR0
        om = self.off["OMR0"]
        omr0 = b[om:om + u32(b, om + 4)]
        if clear_history:
            body = omr0[8:12] + struct.pack("<H", 0)
            omr0 = b"OMR0" + struct.pack("<I", 8 + len(body)) + body

        # ---- CSML 本体（MCHR 列の前まで）
        c = self.off["CSML"]
        e = self.off["ESET_chars"]
        csml_head = bytearray(b[c:e])
        if _line_w is not None:
            struct.pack_into("<i", csml_head, self.off["CSTR"] - c + 14, fx(_line_w))
        dx = dy = 0.0
        if pos is not None:
            dx, dy = pos[0] - self.pos[0], pos[1] - self.pos[1]
            struct.pack_into("<ii", csml_head, 10, fx(pos[0]), fx(pos[1]))
            for xo in self.xtrs:
                r = xo - c
                # 位置を持つトランジション（種別 2）だけ平行移動
                if csml_head[r + 32] == 2:
                    for k in (34, 38, 46, 50):
                        v = i32(csml_head, r + k)
                        struct.pack_into("<i", csml_head, r + k, v + fx(dx if k in (34, 46) else dy))

        decos = self.decorations()
        mchr = bytearray()
        for ch in chars:
            if "ADCO" in ch.attrs and (shift or edge_width is not None):
                a = bytearray(ch.attrs["ADCO"])
                for d in decos:
                    e0 = d["offset"]
                    if shift:  # 色リソース番号をグリフ数の増減に合わせてずらす
                        struct.pack_into("<I", a, e0 + 4, u32(a, e0 + 4) + shift)
                    if edge_width is not None and d["role"] == "edge":
                        struct.pack_into("<II", a, e0 + 34, int(round(edge_width)), int(round(edge_width)))
                ch = Char(ch.code, ch.glyph_index, {**ch.attrs, "ADCO": bytes(a)}, ch.attr_order, ch.pre_raw, ch.uscu)
            mchr += ch.encode()
        eset = b"ESET" + b"\x00" * 4 + struct.pack("<H", len(chars))
        csml_body = bytes(csml_head[8:]) + eset + bytes(mchr)
        csml = self.kind.encode() + struct.pack("<I", 8 + len(csml_body)) + csml_body

        out = self._assemble(omr0, ormp_chunk, csml)
        return self._finish_rebuild(out, changed, args, pos_in, edge_width, _line_w)

    def _assemble(self, omr0, ormp_chunk, csml):
        """OMR0・ORMP・主の文字要素を差し替えて、ファイル全体を組み直す（長さの欄を全部書き直す）"""
        b = self.data
        om, o, c = self.off["OMR0"], self.off["ORMP"], self.off["CSML"]
        s = self.off["SLYR"]
        slyr_body = b[s + 8:c] + csml + b[self.csml_end:]       # 主の要素のうしろ（図形・2 つめの文字）はそのまま
        slyr = b"SLYR" + struct.pack("<I", 8 + len(slyr_body)) + slyr_body

        # ---- SWRK
        sw = 0x10
        between = b[om + u32(b, om + 4):o]          # OMR0 と ORMP の間（ODTT = フォント表があればここ）
        mid = b[sw + 8:om] + omr0 + between + ormp_chunk + b[o + u32(b, o + 4):s]
        swrk_body = mid + slyr
        swrk = b"SWRK" + struct.pack("<I", 8 + len(swrk_body)) + swrk_body
        tsrc_body = b[8:sw] + swrk
        return bytearray(b"TSRC" + struct.pack("<I", 8 + len(tsrc_body)) + tsrc_body)

    def rebuild_resources(self, resources):
        """ORMP のリソースだけ差し替えて組み直す（数と並びは同じ。中身の長さは変わってよい）。ほかのバイトは変えない"""
        assert len(resources) == len(self.resources), "リソースの数を変えることはできません"
        b = self.data
        ormp = bytearray()
        for t, r in resources:
            ormp += r.encode(keep_header=True) if t == "RCHR" else r
        ormp_chunk = b"ORMP" + struct.pack("<I", 12 + len(ormp)) + self.ormp_head + struct.pack("<H", len(resources)) + ormp
        om = self.off["OMR0"]
        omr0 = b[om:om + u32(b, om + 4)]
        c = self.off["CSML"]
        csml = b[c:self.csml_end]
        return bytes(self._assemble(omr0, ormp_chunk, csml))

    def color_targets(self, face_color=None, edge_color=None, order=COLOR_ORDER):
        """色の差し替え先 → {ORMP の添字: 新しい色のバイト列}。
        文字面は「不透明度 100% の単色の文字面」のうち最初の 1 つ、縁は「不透明度 100% の単色で太さのある縁」のうち最初の 1 つだけ。
        色が今と同じなら差し替えない（縁が何重もあるテロップで、全部の縁が同じ色になるのを防ぐ）"""
        out = {}
        for want, role in ((face_color, "face"), (edge_color, "edge")):
            if want is None:
                continue
            for d in self.decorations(order):
                if d["role"] != role or d["resource_type"] != "CRGB" or d["opacity256"] < 256:
                    continue
                if role == "edge" and d["width_px"] <= 0:
                    continue
                if d["color"] and tuple(d["color"][:3]) == tuple(want[:3]) and (len(want) < 4 or d["color"][3] == want[3]):
                    break
                out[d["color_resource"]] = encode_color(want, order)
                break
        return out

    def element_views(self):
        """層の中の文字の要素（CSML / CSSL）ごとに、その要素だけを持つ TS1 を返す（図形などの要素は除く）。
        リソース（字形・色）は共通。描画で 2 つめ以降の文字を描くのに使う"""
        b = self.data
        s = self.off["SLYR"]
        name, p = read_str(b, s + 8)
        p += 2                                   # u16
        eset = p
        out = []
        for tag, o, size in self.elements:
            if tag not in ("CSML", "CSSL"):
                continue
            if o == self.off["CSML"]:
                out.append(self)
                continue
            slyr_body = b[s + 8:eset] + b[eset:eset + 8] + struct.pack("<H", 1) + b[o:o + size]
            slyr = b"SLYR" + struct.pack("<I", 8 + len(slyr_body)) + slyr_body
            swrk_body = b[0x18:s] + slyr
            swrk = b"SWRK" + struct.pack("<I", 8 + len(swrk_body)) + swrk_body
            tsrc_body = b[8:0x10] + swrk
            out.append(TS1(b"TSRC" + struct.pack("<I", 8 + len(tsrc_body)) + tsrc_body))
        return out

    def is_plain(self):
        """文字面 + 縁（+ 影）だけの、描画範囲の式が分かっている装飾か（字詰め AFIN = 1 や、光彩・立体などが無い）"""
        afin = self.base_attr("AFIN")
        if afin is not None and u32(afin, 2):
            return False
        return len(self.elements) == 1 and all(d["kind"] in (0, 9, 1) and d["resource_type"] == "CRGB"
                                               for ds in self.decorations_per_char() for d in ds)

    def _finish_rebuild(self, out, changed, args, pos_in, edge_width, _line_w):
        # ---- 1 行もの: 文言が変わったら行の幅と左位置を書き直す（中心は動かさない）
        #      横に縮める型は式と保存値が 1/256px 違うことがある（縞）ので、その差は変えたうちに入れない
        tol = 1.5 / FIX if self.h_condense() != 1.0 else 1e-6
        if self.kind == "CSSL" and _line_w is None and changed:
            tmp = TS1(bytes(out))
            w = tmp.line_width()
            if abs(w - self.box[0]) > tol:
                left, base = pos_in if pos_in is not None else self.pos
                return self.rebuild(pos=(left + self.box[0] / 2 - w / 2, base), _line_w=w, **args)

        # ---- OFRI 描画範囲の更新
        if changed:
            tmp = TS1(bytes(out))
            bb = None
            try:
                import vwsrender
                if self.is_plain():
                    # VWS と同じ式で範囲を出す（VWS が保存した 3 種類のファイルの OFRI と 4 辺とも一致）
                    bb = vwsrender.drawn_bbox(tmp)
                elif len(self.elements) == 1:
                    # 光彩・立体などの広がりは式が未解明。元のファイルの「字形の範囲 → OFRI」の余白をそのまま新しい字形の範囲に付ける
                    old_r, new_r = vwsrender.real_bbox(self), vwsrender.real_bbox(tmp)
                    if old_r and new_r:
                        old_o = [i32(self.data, self.ofri_bbox_off + 4 * k) / FIX for k in range(4)]
                        bb = tuple(new_r[k] + (old_o[k] - old_r[k]) for k in range(4))
                else:
                    return bytes(out)      # 要素が複数: OFRI は全部の要素の範囲なので触らない（VWS は開いたときに計算し直す）
            except Exception:
                bb = None
            if bb is None:
                bb = tmp.ink_bbox(edge=(edge_width or self.edge_width() or 6) + 1)
            if bb:
                struct.pack_into("<iiii", out, self.ofri_bbox_off, *(fx(round(v)) for v in bb))
        return bytes(out)

    def _rebuild_keep_slots(self, chars, glyphs, pos, pos_in, changed, args, clear_history, keep_glyph_header,
                            face_color, edge_color, edge_width, color_order, _line_w):
        """リソースの番号を動かさない組み直し。chars の glyph_index は「glyphs の中の何番目か」ではなく、
        ここで決める ORMP の添字に付け替える（呼び出し側は glyphs の並びの番号、または元の ORMP の添字のどちらでもよい:
        chars / glyphs を渡さなかったときは元のまま）"""
        b = self.data
        res = list(self.resources)
        if args["glyphs"] is not None or args["chars"] is not None:
            # 新しいグリフ → 同じ字（文字コードとフォント）が元にあればその位置のまま。無い字は、この文字要素だけが使っていて
            # 要らなくなった位置へ入れ、足りなければ末尾へ足す。余った位置は空き枠（ORSC）にする。
            # ほかの要素（2 つめの文字など）が使っているグリフの位置には触らない
            same = args["glyphs"] is None                 # グリフは元のまま（chars の番号は ORMP の添字）
            if not same:
                import re as _re
                mine = sorted({ch.glyph_index for ch in self.chars})
                protected = set()
                for t_, o_, s_ in self.elements:
                    if o_ == self.off["CSML"]:
                        continue
                    for m in _re.finditer(b"MCHR", b[o_:o_ + s_]):
                        q = o_ + m.start()
                        if 30 <= u32(b, q + 4) <= s_ and u16(b, q + 8) == 1:
                            protected.add(u16(b, q + 10))
                by_code = {(res[i][1].code, res[i][1].font_id): i for i in mine}
                new_index, claimed = {}, set()
                for k, g in enumerate(glyphs):
                    i = by_code.get((g.code, g.font_id))
                    if i is not None and i not in claimed:
                        new_index[k] = i; claimed.add(i)
                        if i not in protected:
                            res[i] = ("RCHR", g)
                free = [i for i in mine if i not in claimed and i not in protected]
                for k, g in enumerate(glyphs):
                    if k in new_index:
                        continue
                    if free:
                        i = free.pop(0); res[i] = ("RCHR", g)
                    else:
                        res.append(("RCHR", g)); i = len(res) - 1
                    new_index[k] = i
                for i in free:
                    res[i] = ("ORSC", b"ORSC" + struct.pack("<IHI", 14, 1, 0))
                chars = [Char(ch.code, new_index[ch.glyph_index], ch.attrs, ch.attr_order, ch.pre_raw, ch.uscu) for ch in chars]
        new_color = self.color_targets(face_color, edge_color, color_order)
        ormp = bytearray()
        for i, (t, r) in enumerate(res):
            if t == "RCHR":
                ormp += r.encode(keep_header=keep_glyph_header)
            elif t == "CRGB" and i in new_color:
                ormp += r[:14] + new_color[i]
            else:
                ormp += r
        ormp_chunk = b"ORMP" + struct.pack("<I", 12 + len(ormp)) + self.ormp_head + struct.pack("<H", len(res)) + ormp

        om = self.off["OMR0"]
        omr0 = b[om:om + u32(b, om + 4)]
        if clear_history:
            body = omr0[8:12] + struct.pack("<H", 0)
            omr0 = b"OMR0" + struct.pack("<I", 8 + len(body)) + body

        c = self.off["CSML"]
        e = self.off["ESET_chars"]
        csml_head = bytearray(b[c:e])
        if _line_w is not None:
            struct.pack_into("<i", csml_head, self.off["CSTR"] - c + 14, fx(_line_w))
        if pos is not None:
            dx, dy = pos[0] - self.pos[0], pos[1] - self.pos[1]
            struct.pack_into("<ii", csml_head, 10, fx(pos[0]), fx(pos[1]))
            for xo in self.xtrs:
                r = xo - c
                if csml_head[r + 32] == 2:
                    for k in (34, 38, 46, 50):
                        v = i32(csml_head, r + k)
                        struct.pack_into("<i", csml_head, r + k, v + fx(dx if k in (34, 46) else dy))
        mchr = bytearray()
        for ch in chars:
            if "ADCO" in ch.attrs and edge_width is not None:
                a = bytearray(ch.attrs["ADCO"])
                for d in self._decode_adco(ch.attrs["ADCO"]):
                    if d["role"] == "edge" and d["opacity256"] >= 256 and d["width_px"] > 0:
                        struct.pack_into("<ii", a, d["offset"] + 34, int(round(edge_width)), int(round(edge_width)))
                        break
                ch = Char(ch.code, ch.glyph_index, {**ch.attrs, "ADCO": bytes(a)}, ch.attr_order, ch.pre_raw, ch.uscu)
            mchr += ch.encode()
        eset = b"ESET" + b"\x00" * 4 + struct.pack("<H", len(chars))
        csml_body = bytes(csml_head[8:]) + eset + bytes(mchr)
        csml = self.kind.encode() + struct.pack("<I", 8 + len(csml_body)) + csml_body
        out = self._assemble(omr0, ormp_chunk, csml)
        return self._finish_rebuild(out, changed, args, pos_in, edge_width, _line_w)

    def ink_bbox(self, edge=7):
        xs0, ys0, xs1, ys1 = [], [], [], []
        for ch, g, x, y, sc in self.layout():
            if not g.contours:
                continue
            l, t, r, btm = g.bbox()
            sx = getattr(sc, "sx", sc)
            xs0.append(x + l * sx); xs1.append(x + r * sx)
            ys0.append(y - t * sc); ys1.append(y - btm * sc)
        if not xs0:
            return None
        return (min(xs0) - edge, min(ys0) - edge, max(xs1) + edge, max(ys1) + edge)


# ----------------------------------------------------------------------------
# フォントファイルからグリフ生成（fontTools）
# ----------------------------------------------------------------------------
def _split_qspline(ip):
    """制御点が続く 2 次スプライン（最後だけ線上の点）→「制御点 1 + 終点 1」のレコードの列。
    VWS は 1 レコード 2 点の曲線しか正しく読まない（3 点以上を入れると字が崩れる。VWS 実機で確認）。
    間の線上の点は、隣り合う制御点の中点を整数で作る（VWS と同じく、足してから 0 方向へ切り捨て）"""
    out = []
    for i in range(len(ip) - 1):
        c = ip[i]
        if i == len(ip) - 2:
            e = ip[i + 1]
        else:
            e = (int((ip[i][0] + ip[i + 1][0]) / 2), int((ip[i][1] + ip[i + 1][1]) / 2))
        out.append((PRIM_QSPLINE, [c, e]))
    return out


class FontGlyphSource:
    """TTF/OTF からアウトラインを取り出し、VWS 形式（2048 セル・y 下向き・2次スプライン）へ変換。
    VWS の字形と点の位置までは一致しない（近似）。Windows にフォントが入っていれば gdiglyph.GdiGlyphSource を使うこと"""

    def __init__(self, path, font_number=0, fit=None):
        """fit = {"scale", "ox", "oy"}: fontid で実測した「フォント単位 → VWS の 2048 セル」の変換。
        指定があれば VWS が作る字形データと同じ座標になる"""
        from fontTools.ttLib import TTFont
        self.fit = fit
        self.font = TTFont(path, fontNumber=font_number)
        os2 = self.font["OS/2"]
        self.win_asc = os2.usWinAscent
        self.win_desc = os2.usWinDescent
        self.upm = self.font["head"].unitsPerEm
        # VWS の「文字サイズ」= セル高 (winAscent + winDescent)。Photoshop のサイズ = em
        # → Photoshop サイズ = VWS サイズ / cell_ratio
        self.cell_ratio = (self.win_asc + self.win_desc) / self.upm
        self.scale = CELL / (self.win_asc + self.win_desc)
        self.ascent = round(self.win_asc * self.scale)
        self.descent = CELL - self.ascent
        if fit:
            self.scale = fit["scale"]
            self.cell_ratio = CELL / (self.scale * self.upm)
        self.cmap = self.font.getBestCmap()
        self.gs = self.font.getGlyphSet()

    def has(self, code):
        return code in self.cmap

    def glyph(self, code, font_id, usage=1) -> Glyph:
        from fontTools.pens.recordingPen import DecomposingRecordingPen
        from fontTools.pens.basePen import decomposeQuadraticSegment
        if code == 0:
            return Glyph(0, font_id, usage, [], self.ascent, self.descent)
        name = self.cmap.get(code)
        if name is None:
            raise KeyError(f"フォントに文字がありません: {chr(code)!r} U+{code:04X}")
        pen = DecomposingRecordingPen(self.gs)
        self.gs[name].draw(pen)
        sc = self.scale
        adv = self.gs[name].width * sc
        # 全角セル(2048)に中央寄せ（プロポーショナル字形も 2048 送りで扱う）
        ox = (CELL - adv) / 2 if adv < CELL else 0
        oy = 0.0
        if self.fit:
            ox, oy = self.fit.get("ox", 0.0), self.fit.get("oy", 0.0)

        def P(pt):
            return (int(round(pt[0] * sc + ox)), int(round(-pt[1] * sc + oy)))

        contours, cur, segs, last = [], None, [], None
        for op, args in pen.value:
            if op == "moveTo":
                cur, segs, last = P(args[0]), [], args[0]
            elif op == "lineTo":
                if segs and segs[-1][0] == PRIM_LINE:
                    segs[-1][1].append(P(args[0]))
                else:
                    segs.append((PRIM_LINE, [P(args[0])]))
                last = args[0]
            elif op == "qCurveTo":
                pts = list(args)
                if pts[-1] is None:  # 全点オフカーブの閉曲線
                    pts = pts[:-1]
                    mid = ((pts[-1][0] + pts[0][0]) / 2, (pts[-1][1] + pts[0][1]) / 2)
                    if cur is None:
                        cur = P(mid)
                    pts = pts + [mid]
                segs += _split_qspline([P(p) for p in pts])
                last = pts[-1]
            elif op == "curveTo":
                from fontTools.cu2qu import curve_to_quadratic
                q = curve_to_quadratic([last] + list(args), 1.0 / sc)
                segs += _split_qspline([P(p) for p in q[1:]])
                last = args[-1]
            elif op in ("closePath", "endPath"):
                if cur is not None and segs:
                    contours.append(Contour(cur, segs))
                cur, segs = None, []
        return Glyph(code, font_id, usage, contours, self.ascent, self.descent)


def build_text(template: TS1, text: str, font: FontGlyphSource | None = None,
               spacing=None, size=None, font_id=None, newline_code=0x0D, prefer_template=False, mags=None,
               keep_style=None):
    """テンプレートの書式で text を組み直し、(chars, glyphs) を返す。

    - keep_style: True なら、字ごとの書式（装飾の切り替え・倍率・字間・縦ずらし）を「何字目か」でそのまま引き継ぐ
      （i 字目は雛形の i 字目の属性を写す。雛形より長いぶんは直前の字の書式が続く）。同じ文言なら元とバイト単位で同じになる。
      None（既定）は、装飾つきの雛形（光彩・立体・グラデーション・模様・字ごとの装飾）のときだけ True

    - mags: 文字ごとの倍率 [(横 %, 縦 %), ...]（None なら全部テンプレート先頭文字の値）。変わる所にだけ ACMG を書く

    - グリフ: font 指定時はフォントから生成、未指定ならテンプレートのキャッシュから流用
      prefer_template=True なら、テンプレートにある文字は VWS が作った字形をそのまま使い、無い文字だけフォントから作る
      （フォント番号が同じときだけ。VWS の字形と完全に同じになる）
    - spacing: 文字ごとの字間 px のリスト（None ならテンプレート先頭文字の値）
    """
    fid = font_id if font_id is not None else (template.font_id or 0x45F)
    tmpl_glyphs = {g.code: g for g in template.glyphs if g.font_id == fid}
    if font is not None and not prefer_template:
        tmpl_glyphs = {}
    codes = [newline_code if c == "\n" else ord(c) for c in text] + [0]

    # グリフ表: テンプレート同様、末尾の文字から順に登録（先頭は空グリフ）
    gtag = template.glyphs[0].tag if template.glyphs else b"RCHR"
    order = [0]
    for c in reversed(codes):
        if c not in order:
            order.append(c)      # 改行（U+000D）も字と同じ順で入る（VWS のファイルでは空の字形を持っている）
    usage = {c: codes.count(c) for c in order}
    term = next((g for g in template.glyphs if g.code == 0), None)

    def empty(code, advance=CELL):
        """輪郭の無い字形（改行・空白）。VWS の空の字形と同じ形のヘッダ: 範囲 0、A・C = 0、B = 送り"""
        g = Glyph(code, fid, 1, [], term.ascent if term else 1802, term.descent if term else 246)
        hdr = bytearray(term.header_raw if (term and term.header_raw) else g.build_header())
        struct.pack_into("<I", hdr, 32, code)
        struct.pack_into("<iii", hdr, 44, 0, int(advance), 0)
        g.header_raw = bytes(hdr)
        return g

    glyphs = []
    for c in order:
        if c == newline_code and c not in tmpl_glyphs:
            g = empty(c)
        elif font is not None and c != 0 and c not in tmpl_glyphs:
            g = font.glyph(c, fid, usage[c])
            if not g.contours:       # 空白: 送りだけを持つ空の字形にする（専用空白 U+E73E と同じ形）
                g = empty(c, g.advance_units())
        elif c in tmpl_glyphs:
            src = tmpl_glyphs[c]
            g = Glyph(c, src.font_id, usage[c], src.contours, src.ascent, src.descent, src.header_raw, None, src.tag)
        elif c == 0:
            g = Glyph(0, fid, 1, [], 1802, 246)
        else:
            raise KeyError(f"テンプレートに無い文字です（--font でフォントを指定してください）: {chr(c)}")
        g.usage = usage[c]
        g.tag = gtag
        if g.rchr_raw is None and g.header_raw is not None:
            # 使用回数だけ変わる場合もヘッダはそのまま
            pass
        glyphs.append(g)
    index = {c: i for i, c in enumerate(order)}

    first = template.chars[0]
    last = template.chars[-1] if template.chars and template.chars[-1].code == 0 else None
    # 行頭の字間（ACSP）は並べ方に効かないので、雛形の値をそのまま残す（VWS が書いた値を変えない）。
    # ただし書かれていない字は直前の値を引き継ぐので、2 字目は「雛形の行頭の値」と違えば必ず書く
    prev = first.spacing() if spacing is not None else None

    def mag_of(ch):
        p = ch.attrs.get("ACMG")
        return (i32(p, 2) / FIX, i32(p, 6) / FIX) if p is not None else (100.0, 100.0)

    prev_mag = mag_of(first)
    chars = []
    if keep_style is None:
        keep_style = not template.is_plain()
    if keep_style:
        tmpl_chars = [ch for ch in template.chars if ch.code != 0]
        cur_sp, cur_mg = 0.0, (100.0, 100.0)          # いま作っている並びで、その字に効く字間・倍率
        t_sp, t_mg = template.spacings(), template.magnifications()
        for i, c in enumerate(codes):
            if c == 0:
                if last is not None:
                    chars.append(Char(0, 0, dict(last.attrs), list(last.attr_order), last.pre_raw, first.uscu))
                else:
                    chars.append(Char(0, 0, uscu=first.uscu))
                continue
            if i < len(tmpl_chars):
                src = tmpl_chars[i]
                attrs, order_t, pre = dict(src.attrs), list(src.attr_order), src.pre_raw
                want_sp, want_mg = t_sp[i], t_mg[i]
            else:
                attrs, order_t, pre = {}, [], (tmpl_chars[-1].pre_raw if tmpl_chars else b"\x00" * 22)
                want_sp, want_mg = cur_sp, cur_mg
            if spacing is not None and i < len(spacing):
                want_sp = float(spacing[i])
            if mags is not None and i < len(mags):
                want_mg = (float(mags[i][0]), float(mags[i][1]))
            if "ACSP" in attrs:
                cur_sp = i32(attrs["ACSP"], 2) / FIX
            if "ACMG" in attrs:
                cur_mg = (i32(attrs["ACMG"], 2) / FIX, i32(attrs["ACMG"], 6) / FIX)
            if want_sp != cur_sp:
                if "ACSP" not in attrs:
                    order_t.append("ACSP")
                attrs["ACSP"] = struct.pack("<Hi", 1, fx(want_sp))
                cur_sp = want_sp
            if want_mg != cur_mg:
                if "ACMG" not in attrs:
                    order_t.append("ACMG")
                attrs["ACMG"] = struct.pack("<Hii", 1, fx(want_mg[0]), fx(want_mg[1]))
                cur_mg = want_mg
            if i == 0:
                if size is not None and "ACSZ" in attrs:
                    attrs["ACSZ"] = struct.pack("<Hii", 1, fx(size[0]), fx(size[1]))
                if font_id is not None and "ACTP" in attrs:
                    attrs["ACTP"] = struct.pack("<HI", 1, font_id)
            chars.append(Char(c, index.get(c, 0), attrs, order_t, pre, first.uscu))
        return chars, glyphs
    for i, c in enumerate(codes):
        extra, extra_order = {}, []
        if mags is not None and i < len(mags) and c != 0:
            m = (float(mags[i][0]), float(mags[i][1]))
            if i == 0 or m != prev_mag:
                extra["ACMG"] = struct.pack("<Hii", 1, fx(m[0]), fx(m[1]))
                extra_order.append("ACMG")
            prev_mag = m
        if i == 0:
            attrs, order_t = dict(first.attrs), list(first.attr_order)
            if "ACMG" in extra:
                if "ACMG" not in attrs:
                    order_t.append("ACMG")
                attrs["ACMG"] = extra["ACMG"]
            if size is not None and "ACSZ" in attrs:
                attrs["ACSZ"] = struct.pack("<Hii", 1, fx(size[0]), fx(size[1]))
            if font_id is not None and "ACTP" in attrs:
                attrs["ACTP"] = struct.pack("<HI", 1, font_id)
            if spacing is not None and "ACSP" not in attrs:
                prev = 0.0
            chars.append(Char(c, index.get(c, 0), attrs, order_t, uscu=first.uscu))
        elif c == 0:
            # 終端は雛形の終端をそのまま使う（字間などが付いていることがある）
            if last is not None:
                chars.append(Char(0, 0, dict(last.attrs), list(last.attr_order), last.pre_raw, first.uscu))
            else:
                chars.append(Char(0, 0, uscu=first.uscu))
        elif spacing is not None and i < len(spacing) and (spacing[i] != prev or spacing[i] != 0):
            # 字間が変わる文字・0 以外の文字には ACSP を明示（「その文字だけ」「以降継続」どちらの解釈でも同じ結果になる）
            chars.append(Char(c, index.get(c, 0), {**extra, "ACSP": struct.pack("<Hi", 1, fx(spacing[i]))},
                              extra_order + ["ACSP"], uscu=first.uscu))
            prev = spacing[i]
        else:
            chars.append(Char(c, index.get(c, 0), dict(extra), list(extra_order), uscu=first.uscu))
    return chars, glyphs
