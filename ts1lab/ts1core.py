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
        return b"RCHR" + struct.pack("<I", 8 + len(body)) + body


def parse_rchr(b, o) -> Glyph:
    _, size = chunk_at(b, o, b"RCHR")
    usage = u16(b, o + 10)
    font_id = u32(b, o + 14)
    code = u32(b, o + 22)
    w = o + 26
    chunk_at(b, w, b"WVCT")
    dlen = u32(b, w + 8)
    hdr_start = w + 12
    data_start = o + size - dlen
    hdr = bytes(b[hdr_start:data_start])
    asc = i32(hdr, 36)
    desc = -i32(hdr, 40)
    contours = []
    p, end = data_start, o + size
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
    return Glyph(code, font_id, usage, contours, asc, desc, hdr, bytes(b[o:o + size]))


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
    assert p + 20 == o + size, f"MCHR 0x{o:x} の長さ不一致"
    return Char(code, gi, attrs, order, pre), size


# ----------------------------------------------------------------------------
# ファイル全体
# ----------------------------------------------------------------------------
@dataclass
class HistoryEntry:
    id: int
    time: int
    user: str
    action: str


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
        self._parse_history()
        self._parse_ormp()
        self._parse_layer()

    # -- OFRI: 描画範囲 -----------------------------------------------------
    def _parse_ofri(self):
        o = self.off["OFRI"]
        self.ofri_bbox_off = o + 0x24
        self.ofri_bbox = tuple(i32(self.data, self.ofri_bbox_off + 4 * k) / FIX for k in range(4))

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
            if t == b"RCHR":
                self.resources.append(("RCHR", parse_rchr(b, p)))
            else:
                self.resources.append((t.decode(), bytes(b[p:p + cs])))
            p += cs
        assert p == o + s, "ORMP の長さ不一致"
        self.ormp_head = bytes(b[o + 8:o + 10])

    @property
    def glyphs(self):
        return [r for t, r in self.resources if t == "RCHR"]

    def colors(self, order="RGBA"):
        out = []
        for t, raw in self.resources:
            if t == "CRGB":
                c = raw[14:18]
                if order == "RGBA":
                    out.append((c[0], c[1], c[2], c[3]))
                else:  # BGRA
                    out.append((c[2], c[1], c[0], c[3]))
        return out

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
        c = p
        chunk_at(b, c, b"CSML")
        self.off["CSML"] = c
        self.pos = (i32(b, c + 10) / FIX, i32(b, c + 14) / FIX)  # (x, ベースライン y)
        # MCHR 列の直前の ESET を探す
        q = b.find(b"CSTR", c)
        self.off["CSTR"] = q
        self.box = (i32(b, q + 30) / FIX, i32(b, q + 34) / FIX)  # 文字枠 (幅, 高さ)
        e = b.find(b"ESET", q)
        self.off["ESET_chars"] = e
        nchars = u16(b, e + 8)
        p = e + 10
        self.chars = []
        for _ in range(nchars):
            ch, sz = parse_mchr(b, p)
            self.chars.append(ch)
            p += sz
        assert p == len(b), "MCHR 列のあとに未知のデータ"
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
        """ADCO 内の縁幅（推定）。サンプルでは 6"""
        p = self.base_attr("ADCO")
        return p[88] if p and len(p) > 92 else None

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
    def layout(self, newline_code=0x0D):
        """各文字の配置を返す: [(Char, Glyph, x原点, ベースラインy, scale)]

        見本画像（VWS 出力）との照合で確認したルール
          - 文字送り = 文字サイズ（全角セル）
          - ACSP（字間）は「その文字の直前」の間隔に効く（1 文字目の ACSP は無視）
          - 行は CSML の x から幅 CSTR.width の枠内で中央揃え
          - CSML の y がベースライン（複数行の扱いは未確認）
        """
        size_w, size_h = self.char_size
        sp = self.spacings()
        glyphs = self.glyphs
        lines, cur = [], []
        for ch, s in zip(self.chars, sp):
            if ch.code == 0:
                continue
            if ch.code in (0x0A, newline_code):
                lines.append(cur); cur = []
                continue
            cur.append((ch, s))
        lines.append(cur)
        x0, y0 = self.pos
        bw, _ = self.box
        pitch = size_h + self.line_spacing
        out = []
        for li, line in enumerate(lines):
            if not line:
                continue
            width = len(line) * size_w + sum(s for _, s in line[1:])
            x = x0 + (bw - width) / 2
            y = y0 - (len(lines) - 1 - li) * pitch
            for k, (ch, s) in enumerate(line):
                if k:
                    x += size_w + s
                g = glyphs[ch.glyph_index]
                out.append((ch, g, x, y, size_w / CELL))
        return out

    # -- 情報 ---------------------------------------------------------------
    def info(self, color_order="BGRA"):
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
            "edge_width_guess": self.edge_width(),
            "colors": ["#%02X%02X%02X%02X" % c for c in self.colors(color_order)],
            "spacings": self.spacings(),
            "ofri_bbox": self.ofri_bbox,
            "glyph_count": len(self.glyphs),
            "history_count": len(self.history),
        }

    # -- 再構築 -------------------------------------------------------------
    def rebuild(self, chars=None, glyphs=None, pos=None, colors=None, clear_history=False,
                keep_glyph_header=True, edge_width=None):
        """テンプレート（self）の構造を保ったまま、文字列・グリフ・位置・色を差し替えて書き出す"""
        b = self.data
        changed = chars is not None or glyphs is not None or pos is not None
        chars = self.chars if chars is None else chars
        glyphs = self.glyphs if glyphs is None else glyphs
        old_ng = len(self.glyphs)
        others = [(t, r) for t, r in self.resources if t != "RCHR"]
        shift = len(glyphs) - old_ng  # 色リソース番号のずれ

        # ---- ORMP
        ormp = bytearray()
        for g in glyphs:
            ormp += g.encode(keep_header=keep_glyph_header)
        for t, raw in others:
            if t == "CRGB" and colors:
                k = sum(1 for tt, _ in others[:others.index((t, raw))] if tt == "CRGB")
                if k < len(colors) and colors[k] is not None:
                    raw = raw[:14] + bytes(colors[k])
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

        mchr = bytearray()
        for ch in chars:
            if shift and "ADCO" in ch.attrs:
                a = bytearray(ch.attrs["ADCO"])
                for k in (8, 58):  # 色リソース参照（推定）
                    struct.pack_into("<H", a, k, u16(a, k) + shift)
                if edge_width is not None:
                    a[88] = a[92] = int(edge_width)
                ch = Char(ch.code, ch.glyph_index, {**ch.attrs, "ADCO": bytes(a)}, ch.attr_order, ch.pre_raw)
            elif edge_width is not None and "ADCO" in ch.attrs:
                a = bytearray(ch.attrs["ADCO"])
                a[88] = a[92] = int(edge_width)
                ch = Char(ch.code, ch.glyph_index, {**ch.attrs, "ADCO": bytes(a)}, ch.attr_order, ch.pre_raw)
            mchr += ch.encode()
        eset = b"ESET" + b"\x00" * 4 + struct.pack("<H", len(chars))
        csml_body = bytes(csml_head[8:]) + eset + bytes(mchr)
        csml = b"CSML" + struct.pack("<I", 8 + len(csml_body)) + csml_body

        s = self.off["SLYR"]
        slyr_body = b[s + 8:c] + csml
        slyr = b"SLYR" + struct.pack("<I", 8 + len(slyr_body)) + slyr_body

        # ---- SWRK
        sw = 0x10
        mid = b[sw + 8:om] + omr0 + ormp_chunk + b[o + u32(b, o + 4):s]
        swrk_body = mid + slyr
        swrk = b"SWRK" + struct.pack("<I", 8 + len(swrk_body)) + swrk_body
        tsrc_body = b[8:sw] + swrk
        out = bytearray(b"TSRC" + struct.pack("<I", 8 + len(tsrc_body)) + tsrc_body)

        # ---- OFRI 描画範囲の更新
        if changed:
            tmp = TS1(bytes(out))
            bb = tmp.ink_bbox(edge=(edge_width or self.edge_width() or 6) + 1)
            if bb:
                struct.pack_into("<iiii", out, self.ofri_bbox_off, *(fx(round(v)) for v in bb))
        return bytes(out)

    def ink_bbox(self, edge=7):
        xs0, ys0, xs1, ys1 = [], [], [], []
        for ch, g, x, y, sc in self.layout():
            if not g.contours:
                continue
            l, t, r, btm = g.bbox()
            xs0.append(x + l * sc); xs1.append(x + r * sc)
            ys0.append(y - t * sc); ys1.append(y - btm * sc)
        if not xs0:
            return None
        return (min(xs0) - edge, min(ys0) - edge, max(xs1) + edge, max(ys1) + edge)


# ----------------------------------------------------------------------------
# フォントファイルからグリフ生成（fontTools）
# ----------------------------------------------------------------------------
class FontGlyphSource:
    """TTF/OTF からアウトラインを取り出し、VWS 形式（2048 セル・y 下向き・2次スプライン）へ変換"""

    def __init__(self, path, font_number=0):
        from fontTools.ttLib import TTFont
        self.font = TTFont(path, fontNumber=font_number)
        os2 = self.font["OS/2"]
        self.win_asc = os2.usWinAscent
        self.win_desc = os2.usWinDescent
        self.scale = CELL / (self.win_asc + self.win_desc)
        self.ascent = round(self.win_asc * self.scale)
        self.descent = CELL - self.ascent
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

        def P(pt):
            return (int(round(pt[0] * sc + ox)), int(round(-pt[1] * sc)))

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
                segs.append((PRIM_QSPLINE, [P(p) for p in pts]))
                last = pts[-1]
            elif op == "curveTo":
                from fontTools.cu2qu import curve_to_quadratic
                q = curve_to_quadratic([last] + list(args), 1.0 / sc)
                segs.append((PRIM_QSPLINE, [P(p) for p in q[1:]]))
                last = args[-1]
            elif op in ("closePath", "endPath"):
                if cur is not None and segs:
                    contours.append(Contour(cur, segs))
                cur, segs = None, []
        return Glyph(code, font_id, usage, contours, self.ascent, self.descent)


def build_text(template: TS1, text: str, font: FontGlyphSource | None = None,
               spacing=None, size=None, font_id=None, newline_code=0x0D):
    """テンプレートの書式で text を組み直し、(chars, glyphs) を返す。

    - グリフ: font 指定時はフォントから生成、未指定ならテンプレートのキャッシュから流用
    - spacing: 文字ごとの字間 px のリスト（None ならテンプレート先頭文字の値）
    """
    fid = font_id if font_id is not None else (template.font_id or 0x45F)
    tmpl_glyphs = {g.code: g for g in template.glyphs}
    codes = [newline_code if c == "\n" else ord(c) for c in text] + [0]

    # グリフ表: テンプレート同様、末尾の文字から順に登録（先頭は空グリフ）
    order = [0]
    for c in reversed(codes):
        if c not in order and c not in (0x0A, newline_code):
            order.append(c)
    usage = {c: codes.count(c) for c in order}
    glyphs = []
    for c in order:
        if font is not None and c != 0:
            g = font.glyph(c, fid, usage[c])
        elif c in tmpl_glyphs:
            src = tmpl_glyphs[c]
            g = Glyph(c, src.font_id, usage[c], src.contours, src.ascent, src.descent, src.header_raw, None)
        elif c == 0:
            g = Glyph(0, fid, 1, [], 1802, 246)
        else:
            raise KeyError(f"テンプレートに無い文字です（--font でフォントを指定してください）: {chr(c)}")
        g.usage = usage[c]
        if g.rchr_raw is None and g.header_raw is not None:
            # 使用回数だけ変わる場合もヘッダはそのまま
            pass
        glyphs.append(g)
    index = {c: i for i, c in enumerate(order)}

    first = template.chars[0]
    chars = []
    for i, c in enumerate(codes):
        if i == 0:
            attrs, order_t = dict(first.attrs), list(first.attr_order)
            if size is not None and "ACSZ" in attrs:
                attrs["ACSZ"] = struct.pack("<Hii", 1, fx(size[0]), fx(size[1]))
            if font_id is not None and "ACTP" in attrs:
                attrs["ACTP"] = struct.pack("<HI", 1, font_id)
            if spacing is not None and "ACSP" in attrs:
                attrs["ACSP"] = struct.pack("<Hi", 1, fx(spacing[0]))
            chars.append(Char(c, index.get(c, 0), attrs, order_t))
        elif c == 0:
            chars.append(Char(0, 0))
        elif spacing is not None and i < len(spacing) and (spacing[i] != spacing[i - 1] or spacing[i] != 0):
            # 字間が変わる文字・0 以外の文字には ACSP を明示（「その文字だけ」「以降継続」どちらの解釈でも同じ結果になる）
            chars.append(Char(c, index.get(c, 0), {"ACSP": struct.pack("<Hi", 1, fx(spacing[i]))}, ["ACSP"]))
        else:
            chars.append(Char(c, index.get(c, 0)))
    return chars, glyphs
