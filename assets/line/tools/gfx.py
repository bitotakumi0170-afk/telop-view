# -*- coding: utf-8 -*-
"""共通描画ヘルパー: Noto Sans JP のサブセットを束ねた多書体フォント + 図形ユーティリティ"""
import json, math, os, re, subprocess
from PIL import Image, ImageDraw, ImageFont
from fontTools.ttLib import TTFont

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "fontcache")
CSS_URL = ("https://fonts.googleapis.com/css2?"
           "family=Noto+Sans+JP:wght@400;500;700;900&display=swap")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
# 生成物で使用する可能性のある文字（サブセット選択に使用）
NEEDED = (
    "".join(chr(c) for c in range(0x20, 0x7f))            # ASCII
    + "".join(chr(c) for c in range(0x3040, 0x30ff))      # かな
    + "、。・ー〜（）％＆＋×！？「」：／"
    + "尾藤校閲文字起原稿話言葉声音自動化整理正誤脱字表記録議事用途動画編集"
    + "支援株式会社無料相談年月日時分秒品質高速安心対応可能内容確認共有納品"
    + "秘密保持守機能実績精度人手作業時短見積依頼問合先生成発注制作字幕"
)

def _build_font_index():
    os.makedirs(CACHE, exist_ok=True)
    idx_path = os.path.join(CACHE, "index.json")
    stamp = "".join(sorted(set(NEEDED)))
    if os.path.exists(idx_path):
        cached = json.load(open(idx_path))
        if cached.get("__chars__") == stamp:
            return cached
    css_path = os.path.join(CACHE, "noto.css")
    if not os.path.exists(css_path):
        subprocess.run(["curl", "-sSL", "-o", css_path, CSS_URL, "-H", "User-Agent: " + UA], check=True)
    css = open(css_path).read()
    faces = re.findall(r"font-weight:\s*(\d+);.*?src: url\((.*?)\) format\('woff2'\);\s*"
                       r"unicode-range:\s*([^;]+);", css, re.S)
    need = {ord(c) for c in NEEDED}
    index = {}
    for w, url, rng in faces:
        cps = set()
        for tok in rng.split(","):
            tok = tok.strip()[2:]
            if "-" in tok:
                a, b = tok.split("-")
                cps.update(range(int(a, 16), int(b, 16) + 1))
            else:
                cps.add(int(tok, 16))
        if not (cps & need):
            continue
        name = re.sub(r"[^0-9a-zA-Z]", "", url.split("/")[-1])[-16:]
        woff = os.path.join(CACHE, f"{w}_{name}.woff2")
        ttf = woff[:-6] + ".ttf"
        if not os.path.exists(ttf):
            subprocess.run(["curl", "-sSL", "-o", woff, url], check=True)
            f = TTFont(woff)
            f.flavor = None
            f.save(ttf)
        index.setdefault(w, []).append(ttf)
    index["__chars__"] = stamp
    json.dump(index, open(idx_path, "w"))
    return index


_INDEX = None


class JPFont:
    """必要な文字を含むサブセットを自動で選んで描画するフォールバック付きフォント"""

    _cache = {}

    def __init__(self, weight=700, size=48, tracking=0.0):
        global _INDEX
        if _INDEX is None:
            _INDEX = _build_font_index()
        self.weight, self.size, self.tracking = int(weight), size, tracking
        self.fonts = []
        paths = _INDEX.get(str(weight)) or _INDEX["400"]
        for path in paths:
            key = (path, size, self.weight)
            if key not in JPFont._cache:
                font = ImageFont.truetype(path, size)
                try:                      # Google Fonts が返すのは可変フォント
                    font.set_variation_by_axes([self.weight])
                except Exception:
                    pass
                JPFont._cache[key] = (font, set(TTFont(path).getBestCmap()))
            self.fonts.append(JPFont._cache[key])

    def _pick(self, ch):
        for font, cmap in self.fonts:
            if ord(ch) in cmap:
                return font
        print(f"[warn] グリフ未収録: {ch!r} — gfx.NEEDED に追加してください")
        return self.fonts[0][0]

    def width(self, text):
        w = 0.0
        for ch in text:
            w += self._pick(ch).getlength(ch) + self.tracking
        return w - (self.tracking if text else 0)

    def draw(self, d, xy, text, fill, anchor="lt"):
        x, y = xy
        if anchor[0] == "m":
            x -= self.width(text) / 2
        elif anchor[0] == "r":
            x -= self.width(text)
        for ch in text:
            font = self._pick(ch)
            d.text((x, y), ch, font=font, fill=fill, anchor="l" + anchor[1])
            x += font.getlength(ch) + self.tracking


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def linear_gradient(size, c0, c1, angle=45, steps=512):
    """angle: 0=左→右, 90=上→下"""
    import numpy as np
    w, h = size
    xs = np.linspace(0, 1, w)[None, :]
    ys = np.linspace(0, 1, h)[:, None]
    rad = math.radians(angle)
    t = xs * math.cos(rad) + ys * math.sin(rad)
    t = (t - t.min()) / (t.max() - t.min())
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    for i in range(3):
        arr[:, :, i] = (c0[i] + (c1[i] - c0[i]) * t).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


def radial_glow(size, center, radius, color, alpha=140, falloff=2.0):
    import numpy as np
    w, h = size
    xs = np.arange(w)[None, :] - center[0]
    ys = np.arange(h)[:, None] - center[1]
    dist = np.sqrt(xs ** 2 + ys ** 2) / radius
    a = np.clip(1 - dist, 0, 1) ** falloff * alpha
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[:, :, 0], arr[:, :, 1], arr[:, :, 2] = color
    arr[:, :, 3] = a.astype(np.uint8)
    return Image.fromarray(arr, "RGBA")


def rrect(d, box, r, fill=None, outline=None, width=1):
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


