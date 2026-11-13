# -*- coding: utf-8 -*-
"""模様の画像（.ts1 の中の WPF0）の展開と圧縮。

.ts1 のグラデーション（CGRD）と模様（CPTN）は、文字面に貼る 1 枚絵を持てる。入れ物は
    WPF1 / WPF4, 長さ, [WPF0, u32 種類, u16 幅, u16 高さ, u32 1, u32 24, u32 データ長, データ]
で、データは下の独自の可逆圧縮（解析して分かったもの。資料は無い）。

面（プレーン）
    データは「面」を順に並べたもの。1 面 = 幅 × 高さ の 0〜255 の値。面の数は種類で決まる:
        種類 17 (0x11): 1 面（濃淡だけ）
        種類 3        : 4 面（かすれ模様のサンプル。4 枚とも別々の絵。4 コマぶんなのか縦に 4 画面ぶんなのかは未確認）
    最後の面のあとに ff ff ff ff が来る。2 面目以降の 1 行目は、前の面の最終行を「真上の行」として予測する。

行
    面は上の行から順に、行ごとに独立したバイト列で入っている（行の先頭は必ずバイト境界）。
    行の 1 バイト目が種類:
        0 : 生の行。続く「幅」バイトが、各画素の（画素 − 真上の画素）mod 256
        1 : 続く 1 バイト d。この行は全部の画素が「真上の画素 + d」（d = 0 なら上の行と同じ）
        2 : 続く 1 バイトが先頭画素の（画素 − 真上の画素）。そのあとにビット列（各バイトの上位ビットから）で
            2 画素目以降の「差分」が並ぶ。行が終わったら次のバイト境界まで 0 で埋める

差分
    予測 = 左 + 上 − 左上（mod 256。画像の外は 0）、差分 = 画素 − 予測。
    言い換えると D[x] = 画素 − 真上の画素 としたとき、差分[x] = D[x] − D[x−1]。

字句（種類 2 の行のビット列）
    000            差分 0（1 画素）
    001            +1
    010            −1
    011 nnnn       差分 0 が n + 3 個続く（3〜17 個）。nnnn = 1111 のときは続く 8 ビット e で 18 + e 個（18〜273 個）
    10 s m         ±(2 + m)   m は 1 ビット
    110 s mm       ±(4 + m)   m は 2 ビット
    1110 s mmm     ±(8 + m)   m は 3 ビット
    1111 bbbbbbbb  差分そのもの（mod 256 の 1 バイト）。ただし 11111111 は「行の残りの差分は全部 0」
    s = 1 が負。負のとき m の各ビットは反転して入る

確かめ方: 装飾サンプル 3 本（網点・縞・かすれ）の全データが最後の ff ff ff ff まで矛盾なく読め、
encode(decode(データ)) が元のバイト列と完全に一致する（python wpf.py selftest 素材.ts1）。
"""
import struct
import sys

import numpy as np

PLANES_BY_KIND = {17: 1, 3: 4}


# ---------------------------------------------------------------------------
# 展開
# ---------------------------------------------------------------------------
class _Bits:
    __slots__ = ("d", "p")

    def __init__(self, d, byte_pos):
        self.d = d
        self.p = byte_pos * 8

    def bit(self):
        b = (self.d[self.p >> 3] >> (7 - (self.p & 7))) & 1
        self.p += 1
        return b

    def bits(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | ((self.d[self.p >> 3] >> (7 - (self.p & 7))) & 1)
            self.p += 1
        return v


def _signed(br, nm, base):
    s = br.bit()
    m = br.bits(nm)
    if s:
        m = (~m) & ((1 << nm) - 1)
        return -(base + m)
    return base + m


def decode_plane(d, p, w, h, up=None):
    """d の位置 p から 1 面を読む → (uint8 の配列 (h, w), 次の位置)。up = 1 行目の「真上の行」（無ければ 0）"""
    out = np.zeros((h, w), np.uint8)
    up = np.zeros(w, np.int64) if up is None else np.asarray(up, np.int64)
    for r in range(h):
        t = d[p]
        if t == 1:
            row = (up + d[p + 1]) & 255
            p += 2
        elif t == 0:
            row = (up + np.frombuffer(d, np.uint8, w, p + 1).astype(np.int64)) & 255
            p += 1 + w
        elif t == 2:
            br = _Bits(d, p + 2)
            e = [d[p + 1]]
            while len(e) < w:
                if br.bit() == 0:
                    c = br.bits(2)
                    if c == 3:
                        k = br.bits(4)
                        if k == 15:
                            k = 15 + br.bits(8)
                        e.extend([0] * (k + 3))
                    else:
                        e.append((0, 1, -1)[c])
                elif br.bit() == 0:
                    e.append(_signed(br, 1, 2))
                elif br.bit() == 0:
                    e.append(_signed(br, 2, 4))
                elif br.bit() == 0:
                    e.append(_signed(br, 3, 8))
                else:
                    x = br.bits(8)
                    if x == 255:
                        e.extend([0] * (w - len(e)))
                        break
                    e.append(x)
            if len(e) != w:
                raise ValueError(f"行 {r}: 画素数が幅と合いません（{len(e)} / {w}）")
            row = (up + np.cumsum(np.asarray(e, np.int64))) & 255
            p = (br.p + 7) // 8
        else:
            raise ValueError(f"行 {r}: 知らない行の種類 {t}（位置 {p}）")
        out[r] = row
        up = row
    return out, p


def decode(data, w, h, n_planes=None):
    """圧縮データ → 面の一覧 [uint8 (h, w), ...] と、読み終えた位置（ff ff ff ff の手前）"""
    planes, p = [], 0
    while p < len(data) and data[p:p + 4] != b"\xff\xff\xff\xff":
        if n_planes is not None and len(planes) >= n_planes:
            break
        # 2 面目以降の 1 行目は、前の面の最終行を「真上の行」として予測する（面をまたいで続いている）
        pl, p = decode_plane(data, p, w, h, planes[-1][-1] if planes else None)
        planes.append(pl)
    return planes, p


# ---------------------------------------------------------------------------
# 圧縮
# ---------------------------------------------------------------------------
class _BitWriter:
    __slots__ = ("acc", "n", "out")

    def __init__(self):
        self.acc = 0
        self.n = 0
        self.out = bytearray()

    def put(self, v, nbits):
        self.acc = (self.acc << nbits) | v
        self.n += nbits
        while self.n >= 8:
            self.n -= 8
            self.out.append((self.acc >> self.n) & 255)
        self.acc &= (1 << self.n) - 1

    def finish(self):
        if self.n:
            self.out.append((self.acc << (8 - self.n)) & 255)
            self.acc = 0
            self.n = 0
        return bytes(self.out)


def _put_value(bw, e):
    """0 でない差分 e（−128〜127）を 1 つ書く"""
    a = -e if e < 0 else e
    s = 1 if e < 0 else 0
    if a == 1:
        bw.put(0b010 if s else 0b001, 3)
    elif a <= 3:
        m = a - 2
        bw.put((0b10 << 2) | (s << 1) | ((~m & 1) if s else m), 4)
    elif a <= 7:
        m = a - 4
        bw.put((0b110 << 3) | (s << 2) | ((~m & 3) if s else m), 6)
    elif a <= 15:
        m = a - 8
        bw.put((0b1110 << 4) | (s << 3) | ((~m & 7) if s else m), 8)
    else:
        bw.put((0b1111 << 8) | (e & 255), 12)


def _put_zeros(bw, n):
    """行の途中の 0 の連続（n 個）"""
    while n >= 18:
        k = min(n, 273)
        bw.put((0b0111111 << 8) | (k - 18), 15)
        n -= k
    if n >= 3:
        bw.put((0b011 << 4) | (n - 3), 7)
    else:
        for _ in range(n):
            bw.put(0, 3)


def encode_row(dv):
    """1 行ぶんの D[x] = 画素 − 真上の画素（0〜255 の配列）→ 行のバイト列"""
    w = len(dv)
    d = dv.astype(np.int64)
    e = np.empty(w, np.int64)
    e[0] = d[0]
    e[1:] = ((d[1:] - d[:-1] + 128) & 255) - 128          # −128〜127
    nz = np.nonzero(e[1:])[0] + 1
    if len(nz) == 0:
        return bytes((1, int(d[0])))
    bw = _BitWriter()
    pos = 1
    for i in nz:
        if i > pos:
            _put_zeros(bw, int(i - pos))
        _put_value(bw, int(e[i]))
        pos = int(i) + 1
    if pos < w:
        bw.put(0xFFF, 12)                                    # 残りは 0
    coded = bytes((2, int(d[0]))) + bw.finish()
    if (len(coded) - 2) * 10 > w * 9:                        # 圧縮しても 9 割までしか縮まない行は生のまま
        return bytes((0,)) + bytes(dv.astype(np.uint8))      # （網点のサンプル: 圧縮された行は最大 1720 バイト、生の行は圧縮すると 1730 バイト以上。幅 1920）
    return coded


def encode_plane(plane, up=None):
    h, w = plane.shape
    out = bytearray()
    up = np.zeros(w, np.int64) if up is None else np.asarray(up, np.int64)
    for r in range(h):
        row = plane[r].astype(np.int64)
        out += encode_row((row - up) & 255)
        up = row
    return bytes(out)


def encode(planes):
    """面の一覧 → 圧縮データ（最後の ff ff ff ff は付けない）"""
    planes = [np.asarray(p, np.uint8) for p in planes]
    return b"".join(encode_plane(p, planes[i - 1][-1] if i else None) for i, p in enumerate(planes))


# ---------------------------------------------------------------------------
# .ts1 の中の入れ物（CGRD / CPTN の中の WPF1 / WPF4 → WPF0）
# ---------------------------------------------------------------------------
class Texture:
    """リソース（CGRD か CPTN の生バイト列）の中の模様 1 枚。

    入れ物:  [WPF1 か WPF4][u32 長さ（見出し 8 バイトを除く）] の中に
             WPF0, u32 種類, u16 幅, u16 高さ, u32 1, u32 24, u32 L, 面のデータ, ff ff ff ff
             L = 「WPF0」のタグからデータの終わり（ff ff ff ff を含む）までの長さ = 24 + 面のデータ + 4
             CPTN（WPF4）はここで終わり。CGRD（WPF1）はこのあとに 82 バイト（未解明。最後の 4 バイトは L の控え）"""

    def __init__(self, raw):
        self.raw = bytes(raw)
        i = self.raw.find(b"WPF0")
        if i < 0:
            raise ValueError("模様の画像（WPF0）がありません")
        self.off = i
        self.kind, self.width, self.height, self.one, self.depth, self.length = struct.unpack_from("<IHHIII", self.raw, i + 4)
        self.data_off = i + 24
        self.data_end = i + self.length                       # ff ff ff ff の直後
        self.data = self.raw[self.data_off:self.data_end]
        self.used = None

    def planes(self):
        n = PLANES_BY_KIND.get(self.kind)
        pl, end = decode(self.data, self.width, self.height, n)
        self.used = end
        if self.data[end:] != b"\xff\xff\xff\xff":
            raise ValueError(f"面のデータのあとが ff ff ff ff ではありません（{self.data[end:end + 8].hex(' ')}）")
        return pl

    def images(self):
        """面ごとの濃淡画像（PIL の L）の一覧"""
        from PIL import Image
        return [Image.fromarray(p, "L") for p in self.planes()]

    def replaced(self, planes):
        """面を差し替えたリソースのバイト列を返す（長さの欄も書き直す）"""
        planes = [np.asarray(p, np.uint8) for p in planes]
        h, w = planes[0].shape
        new_data = encode(planes) + b"\xff\xff\xff\xff"
        new_len = 24 + len(new_data)
        delta = new_len - self.length
        raw = bytearray(self.raw[:self.data_off] + new_data + self.raw[self.data_end:])
        struct.pack_into("<IHHIII", raw, self.off + 4, self.kind, w, h, self.one, self.depth, new_len)
        outer = self.off - 8
        if raw[outer:outer + 3] == b"WPF":                    # 外側の入れ物（WPF1 / WPF4）の長さ
            struct.pack_into("<I", raw, outer + 4, struct.unpack_from("<I", raw, outer + 4)[0] + delta)
        struct.pack_into("<I", raw, 4, struct.unpack_from("<I", raw, 4)[0] + delta)   # リソース自身（CGRD / CPTN）の長さ
        if len(self.raw) - self.data_end >= 4 and struct.unpack_from("<I", self.raw, len(self.raw) - 4)[0] == self.length:
            struct.pack_into("<I", raw, len(raw) - 4, new_len)                           # 末尾にある長さの控え（CGRD）
        return bytes(raw)


def textures_of(ts1):
    """TS1 の中の模様つきリソース → [(ORMP の添字, タグ, Texture)]"""
    out = []
    for i, (t, r) in enumerate(ts1.resources):
        if t in ("CGRD", "CPTN") and isinstance(r, (bytes, bytearray)) and r.find(b"WPF0") >= 0:
            out.append((i, t, Texture(r)))
    return out


def selftest(path):
    from ts1core import TS1
    with open(path, "rb") as f:
        t = TS1(f.read())
    ok = True
    for i, tag, tex in textures_of(t):
        pl = tex.planes()
        again = encode(pl)
        same = again + b"\xff\xff\xff\xff" == tex.data
        ok &= same
        vals = [len(np.unique(p)) for p in pl]
        print(f"リソース {i}（{tag}）: 種類 {tex.kind}  {tex.width}×{tex.height}  面 {len(pl)}  値の種類 {vals}  "
              f"圧縮 {tex.used} バイト → 画像に戻して圧縮し直すと " + ("元と 1 バイトも違わない" if same else f"違う（{len(again)} バイト）"))
        if not same:
            n = next((k for k in range(min(len(again), tex.used)) if again[k] != tex.data[k]), None)
            print(f"    最初に違う位置 {n}: 元 {tex.data[n:n + 12].hex(' ')} / 作り直し {again[n:n + 12].hex(' ')}")
        rebuilt = tex.replaced(pl)
        print("    リソースの組み直し:", "元と同じ" if rebuilt == tex.raw else "違う")
        ok &= rebuilt == tex.raw
    return ok


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "selftest":
        sys.exit(0 if all([selftest(p) for p in sys.argv[2:]]) else 1)
    print(__doc__)
