# -*- coding: utf-8 -*-
"""LINE プロフィール画像 (640 x 640) 生成"""
import os
from PIL import Image, ImageDraw, ImageFilter
from gfx import JPFont, linear_gradient, radial_glow

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

S = 4          # supersampling
N = 640        # 出力サイズ
C = N * S

INK    = (18, 32, 74)
FAINT  = (198, 211, 236)
CORAL  = (255, 84, 104)
CYAN   = (60, 214, 240)
BLUE   = (58, 106, 255)


def px(v):
    return v * S


def base_canvas(c0=(64, 104, 255), c1=(14, 28, 82)):
    img = linear_gradient((C, C), c0, c1, angle=55).convert("RGBA")
    img.alpha_composite(radial_glow((C, C), (px(150), px(120)), px(430), CYAN, 110, 1.8))
    img.alpha_composite(radial_glow((C, C), (px(560), px(600)), px(420), (20, 30, 110), 120, 1.6))
    return img


def wave_bars(d, cx0, cy, heights, w=14, gap=13, colors=(BLUE, CYAN)):
    x = cx0
    n = len(heights)
    for i, h in enumerate(heights):
        t = i / max(n - 1, 1)
        col = tuple(round(colors[0][k] + (colors[1][k] - colors[0][k]) * t) for k in range(3))
        d.rounded_rectangle([px(x), px(cy - h / 2), px(x + w), px(cy + h / 2)],
                            radius=px(w / 2), fill=col)
        x += w + gap
    return x - gap


def line_bar(d, x0, x1, cy, h, color):
    d.rounded_rectangle([px(x0), px(cy - h / 2), px(x1), px(cy + h / 2)],
                        radius=px(h / 2), fill=color)


def check(d, cx, cy, r, ring=True, fill=CORAL):
    if ring:
        d.ellipse([px(cx - r - 12), px(cy - r - 12), px(cx + r + 12), px(cy + r + 12)],
                  fill=(255, 255, 255))
    d.ellipse([px(cx - r), px(cy - r), px(cx + r), px(cy + r)], fill=fill)
    pts = [(cx - r * 0.44, cy + r * 0.02), (cx - r * 0.12, cy + r * 0.35), (cx + r * 0.46, cy - r * 0.34)]
    d.line([(px(x), px(y)) for x, y in pts], fill=(255, 255, 255), width=round(px(r * 0.22)), joint="curve")
    for x, y in pts:
        d.ellipse([px(x - r * 0.11), px(y - r * 0.11), px(x + r * 0.11), px(y + r * 0.11)],
                  fill=(255, 255, 255))


def bubble(d, box, r, tail=True, fill=(255, 255, 255)):
    x0, y0, x1, y1 = box
    d.rounded_rectangle([px(x0), px(y0), px(x1), px(y1)], radius=px(r), fill=fill)
    if tail:
        d.polygon([(px(x0 + 52), px(y1 - 26)), (px(x0 + 46), px(y1 + 74)), (px(x0 + 150), px(y1 - 4))],
                  fill=fill)


def variant_a():
    """吹き出し × 波形→テキスト × 校閲チェック"""
    img = base_canvas()
    # 吹き出しの落ち影
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ds = ImageDraw.Draw(shadow)
    ds.rounded_rectangle([px(104), px(160), px(548), px(414)], radius=px(64), fill=(4, 10, 34, 130))
    shadow = shadow.filter(ImageFilter.GaussianBlur(px(14)))
    img.alpha_composite(shadow)

    d = ImageDraw.Draw(img)
    bubble(d, (92, 132, 550, 398), 64)
    end = wave_bars(d, 142, 206, [32, 62, 98, 48, 80, 38], w=15, gap=13)
    line_bar(d, end + 24, 502, 206, 24, INK)
    line_bar(d, 142, 462, 270, 24, FAINT)
    line_bar(d, 142, 380, 330, 24, FAINT)
    check(d, 494, 406, 88)
    return img


def variant_b():
    """モノグラム「尾藤」+ 波形"""
    img = base_canvas((92, 126, 255), (12, 24, 74))
    d = ImageDraw.Draw(img)
    f = JPFont(900, px(196), tracking=px(2))
    f.draw(d, (px(320), px(258)), "尾藤", (255, 255, 255), anchor="mm")
    wave_bars(d, 196, 408, [24, 48, 72, 40, 62, 30], w=16, gap=16,
              colors=((255, 255, 255), CYAN))
    f2 = JPFont(700, px(52), tracking=px(6))
    f2.draw(d, (px(320), px(486)), "AI 校閲", (214, 228, 255), anchor="mm")
    return img


def variant_c():
    """丸バッジ: 音声波形 + 赤ペンチェック"""
    img = base_canvas((32, 60, 160), (8, 16, 52))
    d = ImageDraw.Draw(img)
    d.ellipse([px(72), px(72), px(568), px(568)], outline=(255, 255, 255, 60), width=px(6))
    wave_bars(d, 148, 300, [60, 120, 200, 96, 168, 72, 130], w=22, gap=20,
              colors=((255, 255, 255), CYAN))
    check(d, 470, 452, 96, ring=True)
    return img


def finish(img, name, save=True):
    out = img.convert("RGB").resize((N, N), Image.LANCZOS)
    if save:
        out.save(os.path.join(OUT, f"{name}.png"))
    return out


if __name__ == "__main__":
    finish(variant_a(), "icon-640")
    finish(variant_b(), "icon-640-monogram")
    finish(variant_c(), "icon-640-wave")
    print("icons written to", os.path.abspath(OUT))
