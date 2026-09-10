# -*- coding: utf-8 -*-
"""LINE公式アカウント用 背景画像 (1080 x 878)"""
import os
from PIL import Image, ImageDraw
from gfx import JPFont, linear_gradient, radial_glow
import icon as ic

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

W, H, S = 1080, 878, 2
CW, CH = W * S, H * S

NAVY_0, NAVY_1 = (28, 56, 148), (7, 14, 44)
CYAN = (72, 216, 240)
CORAL = (255, 84, 104)
SUB_D = (178, 197, 236)

TITLE_A, TITLE_B = "話した言葉が、", "そのまま原稿に。"
LEAD = "AIが音声をテキスト化し、そのまま校閲・整文までワンストップ。"
NAME = "尾藤AI 校閲＆文字起こしサービス"
NAME_EN = "AI TRANSCRIPTION & PROOFREADING"
PILLS = ["文字起こし", "校閲・校正", "整文・リライト"]


def px(v):
    return round(v * S)


def circle_mark(size):
    """アイコン(variant A)を円形に切り抜いたロゴマーク"""
    src = ic.finish(ic.variant_a(), "_mark", save=False).convert("RGBA").resize((px(size), px(size)), Image.LANCZOS)
    m = Image.new("L", (px(size) * 4, px(size) * 4), 0)
    ImageDraw.Draw(m).ellipse([0, 0, px(size) * 4 - 1, px(size) * 4 - 1], fill=255)
    src.putalpha(m.resize((px(size), px(size)), Image.LANCZOS))
    return src


def pill(d, dl, cx, cy, text, size, dark):
    f = JPFont(700, px(size), tracking=px(1))
    tw = f.width(text) / S
    h = size + 30
    box = [px(cx - tw / 2 - 30), px(cy - h / 2), px(cx + tw / 2 + 30), px(cy + h / 2)]
    if dark:
        dl.rounded_rectangle(box, radius=px(h / 2), fill=(255, 255, 255, 36),
                             outline=(255, 255, 255, 120), width=px(1.8))
        f.draw(d, (px(cx), px(cy + 1)), text, (255, 255, 255), anchor="mm")
    else:
        dl.rounded_rectangle(box, radius=px(h / 2), fill=(255, 255, 255, 240),
                             outline=(150, 176, 226, 190), width=px(1.8))
        f.draw(d, (px(cx), px(cy + 1)), text, (24, 50, 122), anchor="mm")


def build(dark=True):
    if dark:
        img = linear_gradient((CW, CH), NAVY_0, NAVY_1, angle=48).convert("RGBA")
        img.alpha_composite(radial_glow((CW, CH), (px(200), px(90)), px(780), CYAN, 62, 2.0))
        img.alpha_composite(radial_glow((CW, CH), (px(960), px(700)), px(760), (92, 126, 255), 85, 1.8))
        img.alpha_composite(radial_glow((CW, CH), (px(540), px(1050)), px(780), (4, 8, 30), 150, 1.2))
        title_c, sub_c, wave_c, wave_a = (255, 255, 255), SUB_D, (255, 255, 255), 26
    else:
        img = linear_gradient((CW, CH), (247, 250, 255), (214, 226, 250), angle=60).convert("RGBA")
        img.alpha_composite(radial_glow((CW, CH), (px(900), px(90)), px(700), (110, 180, 255), 95, 1.8))
        img.alpha_composite(radial_glow((CW, CH), (px(120), px(820)), px(620), (150, 200, 255), 90, 1.8))
        title_c, sub_c, wave_c, wave_a = (14, 28, 70), (78, 102, 156), (46, 96, 208), 34
    layer = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))   # 半透明要素はレイヤー経由で合成
    dl = ImageDraw.Draw(layer)

    # 下部の装飾波形（プロフィール画像・アカウント名が重なる領域なので控えめに）
    hs = [22, 46, 78, 34, 96, 56, 26, 70, 42, 86, 32, 60, 22, 48, 78, 36, 94, 52,
          24, 68, 40, 84, 30, 58, 20, 46, 74, 34, 90, 50, 24, 64]
    x = 34
    for h in hs:
        dl.rounded_rectangle([px(x), px(796 - h / 2), px(x + 12), px(796 + h / 2)],
                             radius=px(6), fill=wave_c + (wave_a,))
        x += 33

    widths = [JPFont(700, px(28), tracking=px(1)).width(t) / S + 60 for t in PILLS]
    span = sum(widths) + 26 * (len(PILLS) - 1)
    cx0 = 540 - span / 2

    img.alpha_composite(layer)
    layer2 = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    dl = ImageDraw.Draw(layer2)
    d = ImageDraw.Draw(img)

    # ロゴロックアップ
    img.alpha_composite(circle_mark(78), (px(78), px(70)))
    JPFont(900, px(31), tracking=px(1.5)).draw(d, (px(178), px(100)), NAME, title_c, anchor="lm")
    JPFont(500, px(20), tracking=px(3)).draw(d, (px(180), px(133)), NAME_EN, sub_c, anchor="lm")

    # ヒーローコピー（後半に赤の校正ライン）
    ft = JPFont(900, px(58), tracking=px(2))
    total = (ft.width(TITLE_A) + ft.width(TITLE_B)) / S
    x0 = 540 - total / 2
    ft.draw(d, (px(x0), px(312)), TITLE_A, title_c, anchor="lm")
    xb = x0 + ft.width(TITLE_A) / S
    ft.draw(d, (px(xb), px(312)), TITLE_B, title_c, anchor="lm")
    d.rounded_rectangle([px(xb - 2), px(352), px(xb + ft.width(TITLE_B) / S - 40), px(359)],
                        radius=px(3.5), fill=CORAL)

    JPFont(500, px(29), tracking=px(1.5)).draw(d, (px(540), px(424)), LEAD, sub_c, anchor="mm")

    cx = cx0
    for t, wd in zip(PILLS, widths):
        pill(d, dl, cx + wd / 2, 522, t, 28, dark)
        cx += wd + 26
    img.alpha_composite(layer2)
    d = ImageDraw.Draw(img)
    cx = cx0
    for t, wd in zip(PILLS, widths):   # ピルの文字はレイヤー合成後に描く
        f = JPFont(700, px(28), tracking=px(1))
        f.draw(d, (px(cx + wd / 2), px(523)), t,
               (255, 255, 255) if dark else (24, 50, 122), anchor="mm")
        cx += wd + 26

    return img.convert("RGB").resize((W, H), Image.LANCZOS)


if __name__ == "__main__":
    build(True).save(os.path.join(OUT, "bg-1080x878.png"))
    build(False).save(os.path.join(OUT, "bg-1080x878-light.png"))
    print("backgrounds written to", os.path.abspath(OUT))
