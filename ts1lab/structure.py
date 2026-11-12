"""TS1 の全バイトを項目に割り当てる構造ダンプと、2 ファイルの項目単位の差分

  dump(data)        → [Field(path, offset, raw, name, value, note)]  （全バイトを漏れなくカバー）
  diff(data_a, data_b) → 変わった項目だけを列挙

差分解析の使い方: VWS で設定を 1 つだけ変えて保存した .ts1 を 2 つ用意して比べると、
その設定がどの項目に入っているかが分かる。
"""
from __future__ import annotations

import datetime
import struct
from dataclasses import dataclass

from ts1core import read_str


@dataclass
class Field:
    path: str
    offset: int
    raw: bytes
    name: str
    value: object = None
    note: str = ""

    def text(self):
        v = self.value
        if v is None:
            v = self.raw.hex(" ") if len(self.raw) <= 48 else f"{self.raw[:24].hex(' ')} … ({len(self.raw)}B)"
        return v


# 項目の型:  u8 u16 u32 i32 / fx = 24.8 固定小数 / time = UNIX 時刻 / bN = N バイト / rest = 残り全部
def _read(b, o, fmt):
    if fmt == "u8":
        return 1, b[o]
    if fmt == "u16":
        return 2, struct.unpack_from("<H", b, o)[0]
    if fmt == "u32":
        return 4, struct.unpack_from("<I", b, o)[0]
    if fmt == "i32":
        return 4, struct.unpack_from("<i", b, o)[0]
    if fmt == "fx":
        return 4, struct.unpack_from("<i", b, o)[0] / 256.0
    if fmt == "time":
        t = struct.unpack_from("<I", b, o)[0]
        return 4, datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if fmt == "rgba":
        return 4, "#%02X%02X%02X α%d" % (b[o], b[o + 1], b[o + 2], b[o + 3])
    if fmt.startswith("b"):
        return int(fmt[1:]), None
    raise ValueError(fmt)


# 単純チャンクの項目定義（ペイロード先頭から）。(名前, 型, 説明)
SCHEMA = {
    "OFRI": [("ver", "u16", ""), ("a", "u16", "不明 (40)"), ("b", "u16", "不明 (3)"), ("c", "fx", "不明 (113.0)"),
             ("zero", "b18", ""), ("left", "fx", "描画範囲 左"), ("top", "fx", "描画範囲 上"),
             ("right", "fx", "描画範囲 右"), ("bottom", "fx", "描画範囲 下")],
    "OOCI": [("ver", "u16", ""), ("zero", "b10", ""), ("frames", "u32", "イン側の長さ? (60)")],
    "OOCO": [("ver", "u16", ""), ("zero", "b10", ""), ("frames", "u32", "アウト側の長さ? (60)")],
    "CRGB": [("ver", "u16", ""), ("type", "u32", "使用回数（この色を指している装飾の数。以前「色種別」と読んでいた）"), ("color", "rgba", "R G B A")],
    "CGRD": [("ver", "u16", ""), ("usage", "u16", "使用回数"), ("zero", "b6", ""), ("count", "u8", "色の数 n。続けて 色 4 バイト × n、位置 u8 × n、[WPF1 模様の画像]、末尾")],
    "CPTN": [("ver", "u16", ""), ("usage", "u16", "使用回数"), ("zero", "b6", "続けて WPF4 模様の画像、末尾")],
    "ORSC": [("ver", "u16", ""), ("value", "u32", "不明 (0)")],
    "VIDT": [],
    "MSCR": [("ver", "u16", ""), ("value", "b8", "不明 (0)。1 行もの（CSSL）のファイルにだけある")],
    "RBID": [("ver", "u16", ""), ("a", "u32", "不明 (4)"), ("rest", "b16", "")],
    "PROP": [("ver", "u16", ""), ("a", "u32", "不明 (4)"), ("rest", "b14", "")],
    "EASG": [("ver", "u16", ""), ("rest", "b16", "")],
    "USCU": [("ver", "u16", ""), ("a", "u32", "不明 (1)"), ("count", "u32", "コード数 (1)"), ("code", "u32", "文字コード（EMTR と同じ）")],
    "MVP_": [("ver", "u16", ""), ("type", "u16", "移動種別 (2)"), ("v0", "fx", ""), ("v1", "fx", ""),
             ("v2", "fx", ""), ("move", "fx", "移動量 px（イン -50 / アウト +50。XTRS の 位置B = 位置A + 移動量）")],
    "3FLG": [("ver", "u16", ""), ("flag", "u16", "不明")],
    "GROP": [("ver", "u16", ""), ("value", "u32", "不明")],
    "GRAP": [("ver", "u16", ""), ("a", "u32", "不明"), ("b", "u32", "不明")],
    "LGOP": [("ver", "u16", ""), ("a", "u32", "不明"), ("b", "u32", "不明"), ("c", "u16", "不明 (1)"), ("d", "u16", "不明 (1)")],
    "FRTV": [("ver", "u16", ""), ("flag", "u16", "不明 (1)")],
    "EVEB": [("ver", "u16", ""), ("flag", "u16", "不明 (1)")],
    "SRSC": [("ver", "u16", ""), ("a", "u32", "不明 (1)"), ("b", "i32", "不明 (-1)")],
    "VSBL": [("ver", "u16", ""), ("visible", "u16", "表示 (1)")],
    "MASK": [("ver", "u16", ""), ("mask", "u16", "マスク (0)")],
    "AFIN": [("ver", "u16", ""), ("value", "u32", "不明 (0)")],
    "ACTP": [("ver", "u16", ""), ("font_id", "u32", "フォント番号 (1119 = ウィン L7)")],
    "ACSZ": [("ver", "u16", ""), ("width", "fx", "文字幅 px"), ("height", "fx", "文字高 px")],
    "AFLF": [("ver", "u16", ""), ("value", "u32", "不明 (4)")],
    "ASLP": [("ver", "u16", ""), ("a", "fx", "斜体?"), ("b", "fx", "")],
    "AROT": [("ver", "u16", ""), ("angle", "fx", "回転?")],
    "ALSP": [("ver", "u16", ""), ("line_spacing", "fx", "行間 px")],
    "ACMG": [("ver", "u16", ""), ("x", "fx", "倍率 X %?"), ("y", "fx", "倍率 Y %?")],
    "ACSP": [("ver", "u16", ""), ("spacing", "fx", "字間 px（この文字の直前）")],
}


class Dumper:
    def __init__(self, data: bytes):
        self.b = bytes(data)
        self.out: list[Field] = []

    def add(self, path, o, n, name, value=None, note=""):
        if n > 0:
            self.out.append(Field(path, o, self.b[o:o + n], name, value, note))
        return o + n

    def fields(self, path, o, end, schema):
        for name, fmt, note in schema:
            if o >= end:
                break
            n, v = _read(self.b, o, fmt)
            o = self.add(path, o, n, name, v, note)
        if o < end:
            o = self.add(path, o, end - o, "rest", None, "未解明")
        return o

    def hdr(self, path, o, tag=None):
        t = self.b[o:o + 4].decode("latin1")
        if tag and t != tag:
            raise ValueError(f"0x{o:x}: {tag} を期待したが {t!r}")
        size = struct.unpack_from("<I", self.b, o + 4)[0]
        self.add(path, o, 8, "(tag+size)", f"{t} {size}")
        return t, size

    def eset(self, path, o):
        n = struct.unpack_from("<H", self.b, o + 8)[0]
        self.add(path, o, 10, "ESET", n, "要素数")
        return o + 10, n

    # ---- 各チャンク -------------------------------------------------------
    def simple(self, path, o):
        t, size = self.hdr(path, o)
        self.fields(path, o + 8, o + size, SCHEMA.get(t, []))
        return o + size

    def run(self):
        b = self.b
        _, size = self.hdr("TSRC", 0, "TSRC")
        self.add("TSRC", 8, 8, "header", None, "00 01 00 01 … (版?)")
        o = 16
        _, ssize = self.hdr("SWRK", o, "SWRK")
        self.fields("SWRK", o + 8, o + 18, [("ver", "u16", ""), ("saved", "time", "書き出し時刻"),
                                              ("id", "u32", "不明（ID?）")])
        o += 18
        for tag in ("OFRI", "OOCI", "OOCO"):
            o = self.simple(f"SWRK/{tag}", o)
        while b[o:o + 4] not in (b"OMR0", b""):      # 1 行もの（CSSL）のファイルには MSCR がある
            o = self.simple(f"SWRK/{b[o:o + 4].decode('latin1')}", o)
        o = self.omr0("SWRK/OMR0", o)
        if b[o:o + 4] == b"ODTT":
            o = self.odtt("SWRK/ODTT", o)
        o = self.ormp("SWRK/ORMP", o)
        o, n = self.eset("SWRK", o)
        for i in range(n):
            o = self.slyr(f"SWRK/SLYR[{i}]", o)
        if o < len(b):
            self.add("(tail)", o, len(b) - o, "rest", None, "未解明")
        return self.out

    def omr0(self, path, o):
        b = self.b
        _, size = self.hdr(path, o, "OMR0")
        n = struct.unpack_from("<H", b, o + 12)[0]
        self.add(path, o + 8, 6, "count", n, "編集履歴の件数")
        p = o + 14
        for i in range(n):
            s = p
            eid, ts = struct.unpack_from("<II", b, p)
            p += 8
            user, p = read_str(b, p)
            act, p = read_str(b, p)
            t = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            self.add(f"{path}/[{i}]", s, p - s, "entry", f"{eid} {t} {user} | {act[:60]}")
        return o + size

    def odtt(self, path, o):
        """フォント表（内蔵番号でないフォントを使うときだけある）"""
        b = self.b
        _, size = self.hdr(path, o, "ODTT")
        n = struct.unpack_from("<H", b, o + 12)[0]
        self.fields(path, o + 8, o + 14, [("ver", "u16", ""), ("zero", "u16", ""), ("count", "u16", "フォント数")])
        p = o + 14
        for i in range(n):
            fp = f"{path}/[{i}]"
            p = self.fields(fp, p, p + 16, [("font_id", "u32", "フォント番号（ACTP / RCHR と同じ値）"), ("weight", "u32", "太さ (700 = Bold)"),
                                             ("f0", "u8", ""), ("pitch_family", "u8", "0x12 ピッチと系統?"), ("f2", "u8", ""),
                                             ("charset", "u8", "文字セット (0x80 = 日本語)"), ("f4", "b4", "")])
            for nm, note in (("name", "書体名"), ("postscript", "PostScript 名")):
                st, q = read_str(b, p)
                p = self.add(fp, p, q - p, nm, st, note)
        return o + size

    def ormp(self, path, o):
        b = self.b
        _, size = self.hdr(path, o, "ORMP")
        n = struct.unpack_from("<H", b, o + 10)[0]
        self.fields(path, o + 8, o + 12, [("ver", "u16", ""), ("count", "u16", "リソース数")])
        p = o + 12
        counts = {}
        for i in range(n):
            t = b[p:p + 4].decode("latin1")
            k = counts.get(t, 0)
            counts[t] = k + 1
            cp = f"{path}/{t}[{k}]"   # 種類ごとの通し番号（グリフ数が変わってもずれない）
            if t in ("RCHR", "RUCH"):
                code = struct.unpack_from("<I", b, p + 22)[0]
                cp = f"{path}/RCHR'{chr(code) if code else '終端'}'"   # RUCH も同じ名前で並べる（比較しやすいように）
                _, rs = self.hdr(cp, p)
                self.fields(cp, p + 8, p + 26, [("ver", "u16", ""), ("usage", "u16", "使用回数"), ("zero", "u16", ""),
                                                 ("font_id", "u32", "フォント番号"), ("zero2", "u32", ""),
                                                 ("code", "u32", "文字コード " + (chr(code) if code else "(終端)"))])
                w = p + 26
                dl = struct.unpack_from("<I", b, w + 8)[0]
                self.add(cp + "/WVCT", w, 12, "(tag+size+len)")
                h = w + 12
                self.fields(cp + "/WVCT", h, h + 60, [
                    ("contours", "u16", "輪郭数"), ("fmt", "u16", "(1)"),
                    ("left", "fx", ""), ("top", "fx", ""), ("right", "fx", ""), ("bottom", "fx", ""),
                    ("cell_w", "u16", "2048"), ("cell_h", "u16", "2048"), ("black_w", "u16", ""), ("black_h", "u16", ""),
                    ("font_id", "u32", ""), ("code", "u32", ""), ("ascent", "i32", ""), ("descent", "i32", ""),
                    ("abcA", "i32", ""), ("abcB", "i32", ""), ("abcC", "i32", ""), ("zero", "u32", "")])
                self.add(cp + "/WVCT", h + 60, dl, "outline", f"{dl} バイト", "輪郭データ")
                if h + 60 + dl < p + rs:      # RUCH: WVCT のあとに コード数 + 文字コード
                    self.fields(cp, h + 60 + dl, p + rs, [("ucount", "u32", "コード数 (1)"), ("ucode", "u32", "文字コード")])
                p += rs
            else:
                p = self.simple(cp, p)
        return o + size

    def slyr(self, path, o):
        b = self.b
        _, size = self.hdr(path, o, "SLYR")
        name, p = read_str(b, o + 8)
        self.add(path, o + 8, p - (o + 8), "name", name, "レイヤ名")
        p = self.add(path, p, 2, "u16", struct.unpack_from("<H", b, p)[0], "不明")
        p, n = self.eset(path, p)
        for i in range(n):
            t = b[p:p + 4]
            if t in (b"CSML", b"CSSL"):
                p = self.csml(f"{path}/CSML[{i}]", p)     # CSSL（1 行もの）も同じ名前で並べる
            else:                                         # 図形のまとまり（CGRP）など。中身は未解明
                _, es = self.hdr(f"{path}/{t.decode('latin1')}[{i}]", p)
                self.add(f"{path}/{t.decode('latin1')}[{i}]", p + 8, es - 8, "rest", None, "未解明（文字以外の要素）")
                p += es
        return o + size

    def csml(self, path, o):
        b = self.b
        _, size = self.hdr(path, o)
        end = o + size
        p = self.fields(path, o + 8, o + 26, [("ver", "u16", ""), ("x", "fx", "文字枠の左 px"), ("y", "fx", "ベースライン px"),
                                               ("a", "i32", "不明 (-1)"), ("b", "u16", "不明 (0)"), ("c", "u16", "不明 (1)")])
        counts = {}
        while p < end:
            t = b[p:p + 4].decode("latin1")
            k = counts.get(t, 0)
            counts[t] = k + 1
            cp = f"{path}/{t}[{k}]"
            if t == "XTRS":
                p = self.xtrs(cp, p)
            elif t == "SCLS":
                self.add(cp, p, 4, "(tag)", "SCLS")
                self.fields(cp, p + 4, p + 20, [("zero", "u32", ""), ("a", "u8", "不明 (2) 揃え?"),
                                                  ("b", "u32", "不明 (145)"), ("c", "u32", "不明 (50)"), ("d", "b3", "")])
                p += 20
            elif t == "CSTR":
                n = struct.unpack_from("<H", b, p + 4)[0]
                self.add(cp, p, 6, "(tag+count)", f"CSTR {n}")
                if n >= 8:
                    names = [("scale_x", "倍率 X %?"), ("v1", ""), ("scale_y", "倍率 Y %?"), ("v3", ""), ("v4", ""), ("v5", ""),
                             ("box_w", "文字枠の幅 px"), ("box_h", "文字枠の高さ px")]
                    cnt = n
                else:   # CSSL: 件数 2 と書かれているが値は 4 つ（0, 0, 行の幅, 0）
                    names = [("v0", ""), ("v1", ""), ("line_w", "行の幅 px（字送りと字間の合計）"), ("v3", "")]
                    cnt = 4
                q = p + 6
                for i in range(cnt):
                    nm, note = names[i] if i < len(names) else (f"v{i}", "")
                    q = self.add(cp, q, 4, nm, struct.unpack_from("<i", b, q)[0] / 256.0, note)
                p = q
            elif t == "ESET":
                p, n = self.eset(path + "/chars", p)
                for i in range(n):
                    p = self.mchr(f"{path}/MCHR[{i}]", p)
            else:
                p = self.simple(cp, p)
        return end

    def xtrs(self, path, o):
        b = self.b
        _, size = self.hdr(path, o, "XTRS")
        end = o + size
        vid = b.find(b"VIDT", o, end)
        p = self.fields(path, o + 8, o + 34, [
            ("ver", "u16", ""), ("kind", "u16", "対象 1〜4"), ("dir", "u16", "0 = イン / 1 = アウト"), ("zero", "u16", ""),
            ("frames", "u32", "長さ（フレーム）"), ("a", "u32", "不明 (1)"), ("zero2", "b8", ""), ("move", "u16", "2 = 移動あり")])
        if struct.unpack_from("<H", b, o + 32)[0] == 2:
            p = self.fields(path, p, p + 24, [("x0", "fx", "位置 A x"), ("y0", "fx", "位置 A y"), ("z0", "fx", ""),
                                               ("x1", "fx", "位置 B x"), ("y1", "fx", "位置 B y"), ("z1", "fx", "")])
        p = self.fields(path, p, p + 25, [("u8", "u8", "")] + [(f"scale{i}", "u32", "倍率 % (100)") for i in range(6)])
        q = self.fields(path, p, min(p + 27, vid if vid > 0 else end),
                        [("zero3", "b23", ""), ("opacity0", "u16", "不透明度 %? (100)"), ("opacity1", "u16", "不透明度 %? (100)")])
        if q < (vid if vid > 0 else end):
            self.add(path, q, (vid if vid > 0 else end) - q, "rest", None, "未解明")
        if vid > 0:
            q = self.simple(path + "/VIDT", vid)
            if q < end:
                self.add(path, q, end - q, "tail", None, "未解明")
        return end

    def mchr(self, path, o):
        b = self.b
        _, size = self.hdr(path, o, "MCHR")
        end = o + size
        uscu = b[end - 22:end - 18] == b"USCU"
        emtr_end = end - 22 if uscu else end
        code = struct.unpack_from("<I", b, emtr_end - 16)[0]
        p = self.fields(path, o + 8, o + 34, [("ver", "u16", ""), ("glyph", "u16", "グリフ番号（ORMP の添字）"), ("zero", "b18", ""),
                                                 ("shift", "fx", "不明（大きさを変えた字に 4〜14、終端に 18 が入ることがある。縦のずらし?）")])
        if b[p:p + 4] == b"DAAR":
            _, ds = self.hdr(path + "/DAAR", p)
            n = struct.unpack_from("<H", b, p + 10)[0]
            self.fields(path + "/DAAR", p + 8, p + 12, [("ver", "u16", ""), ("count", "u16", "属性数")])
            q = p + 12
            for _ in range(n):
                t = b[q:q + 4].decode("latin1")
                if t == "ADCO":
                    q = self.adco(f"{path}/DAAR/ADCO", q)
                else:
                    q = self.simple(f"{path}/DAAR/{t}", q)
            p += ds
        self.add(path, p, 4, "(tag)", "EMTR")
        self.fields(path, p + 4, emtr_end, [("code", "u32", "文字 " + (chr(code) if code else "(終端)")), ("zero", "b12", "")])
        if uscu:
            self.simple(path + "/USCU", emtr_end)
        return end

    def adco(self, path, o):
        b = self.b
        _, size = self.hdr(path, o, "ADCO")
        n = b[o + 10]
        self.fields(path, o + 8, o + 12, [("ver", "u16", ""), ("count", "u8", "装飾数"), ("head2", "u8", "1 のとき追加見出し 16 バイトが続く")])
        p = o + 12
        if b[o + 11]:
            self.add(path, p, 16, "head_ext", None, "追加見出し（未解明）")
            p += 16
        for i in range(n):
            ext = struct.unpack_from("<H", b, p + 2)[0]
            ln = 50 + 32 * ext
            self.fields(f"{path}/[{i}]", p, p + ln, [
                ("kind", "u16", "0 = 文字面 / 9 = 縁 / 1 = 影 / 3・4・16・2048・2049 は FORMAT.md「装飾の種別」"),
                ("ext", "u16", "1 のとき後ろに 32 バイト付く（1 行ものの文字面で 1）"),
                ("color_res", "u32", "色リソース番号（CRGB / CGRD / CPTN）"), ("flags", "u32", "不明（模様つきで 0x30、光沢で 0x100）"),
                ("zero", "b16", ""), ("order", "u16", "重ね方? (4 が普通 / 5・6)"), ("extra", "u16", "太さへの追加分 px（外へ広がる量 = 太さ + これ）"),
                ("opacity", "u16", "不透明度（256 = 100%）"), ("width", "i32", "太さ px（光彩は負）"), ("width2", "i32", "太さ 2 px"), ("zero2", "b8", "")])
            p += ln
        if p < o + size:
            self.add(path, p, o + size - p, "rest", None, "未解明")
        return o + size


def dump(data: bytes):
    out = Dumper(data).run()
    covered = sum(len(f.raw) for f in out)
    assert covered == len(data), f"カバー漏れ: {covered} / {len(data)}"
    return out


def format_dump(fields, show_zero=False, skip=("OMR0", "outline")):
    lines = []
    for f in fields:
        if not show_zero and f.name.startswith("zero"):
            if not any(f.raw):
                continue
        if any(s in f.path or s == f.name for s in skip) and f.name in ("entry",):
            continue
        note = f"  ; {f.note}" if f.note else ""
        lines.append(f"{f.offset:06X} {f.path}.{f.name} = {f.text()}{note}")
    return "\n".join(lines)


def diff(a: bytes, b: bytes, ignore=("OMR0", "/WVCT", "RCHR")):
    """項目単位の差分。[(path.name, 値A, 値B, 説明)]"""
    fa = {f"{f.path}.{f.name}": f for f in dump(a)}
    fb = {f"{f.path}.{f.name}": f for f in dump(b)}
    out = []
    for k in list(fa) + [k for k in fb if k not in fa]:
        if any(s in k for s in ignore):
            continue
        x, y = fa.get(k), fb.get(k)
        if x is None or y is None or x.raw != y.raw:
            note = (x or y).note
            out.append((k, x.text() if x else "(なし)", y.text() if y else "(なし)", note))
    return out
