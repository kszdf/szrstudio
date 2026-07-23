#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
封面文字叠加器 v5 (PIL) - 视频号财税大号版式
参考：视频号"张老师老板财税"封面风格
- 浅蓝渐变底（非照片底）
- 左侧浅灰半透明大字水印（背景字）
- 右侧人物照片 + 黄色描边（贴图感）
- 底部深色横条 + 主标题(白粗) + 副标题(浅灰小)
- 只留「老张讲财税」品牌名（去掉慧根堂）
用法:
  python overlay_cover_pil.py
"""
import re
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

BASE = Path("D:/heygem_data/gpt_sovits")
BG = BASE / "covers/portrait_src.jpg"
PKG = BASE / "qwen_out/batch1_pkg"
OUT = BASE / "covers/final"
FONT = str(BASE / "fonts/simhei.ttf")
BRAND = "老张讲财税"
W, H = 1080, 1920

# 浅蓝渐变色（参考视频号财税封面）
BG_TOP = (200, 220, 239)      # 顶部浅蓝
BG_BOT = (91, 146, 197)       # 底部中蓝
WATERMARK_COLOR = (255, 255, 255)  # 大字水印色（白）
WATERMARK_ALPHA = 90          # 水印透明度 0-255
YELLOW = (255, 200, 50)       # 人物描边黄
BLACK_EDGE = (20, 20, 20)     # 外圈黑边
WHITE = (255, 255, 255)
LIGHT_GRAY = (220, 220, 225)  # 副标题浅灰
DARK_BAR = (10, 20, 38, 200)  # 底部条 (R,G,B,A)

# 字号
WM_SIZE = 230            # 大字水印字号
TITLE_SIZE = 76          # 主标题字号
SUB_SIZE = 38            # 副标题字号
BRAND_SIZE = 30          # 品牌名字号

# 区域
PHOTO_LEFT = 380         # 人物区起始 x
PHOTO_W_RATIO = 0.62     # 人物区宽占 W 比例


def make_bg_gradient():
    """浅蓝渐变背景（顶浅底深）"""
    bg = Image.new("RGB", (W, H), BG_TOP)
    px = bg.load()
    for y in range(H):
        t = y / (H - 1)
        r = int(BG_TOP[0] * (1 - t) + BG_BOT[0] * t)
        g = int(BG_TOP[1] * (1 - t) + BG_BOT[1] * t)
        b = int(BG_TOP[2] * (1 - t) + BG_BOT[2] * t)
        for x in range(W):
            px[x, y] = (r, g, b)
    return bg


def parse_title(publish_md: Path) -> str:
    txt = publish_md.read_text(encoding="utf-8")
    m = re.search(r"\*\*标题\*\*[：:]\s*(.+)", txt)
    return m.group(1).strip() if m else ""


def short_title(title: str, limit: int = 9) -> str:
    """短标题：取第一个逗号前，否则截前 limit 字。"""
    for sep in ("，", ",", "、", " "):
        if sep in title:
            return title.split(sep)[0].strip()
    return title[:limit].strip()


def wrap_cn(text: str, max_chars: int = 8):
    """短标题换行：超过 max_chars 强制拆两行（在 4-5 字处），最多2行。"""
    if len(text) <= max_chars:
        return [text]
    # 找一个标点切；没有就 4+剩余
    for sep_pos in range(2, len(text) - 1):
        ch = text[sep_pos]
        if ch in "，。！？、； ":
            return [text[:sep_pos], text[sep_pos + 1:]]
    # 无标点 → 在 4 拆
    mid = 4 if len(text) <= 8 else 5
    return [text[:mid], text[mid:]]


def make_watermark(draw, text, size):
    """左侧浅灰半透明大字水印"""
    font = ImageFont.truetype(FONT, size)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    # 多次叠加"水印"字样
    lines = [text, text[::-1] if len(text) > 4 else text]
    y = 180
    for ln in lines:
        ld.text((40, y), ln, font=font, fill=(255, 255, 255, WATERMARK_ALPHA))
        y += int(size * 1.05)
    # 右侧也透一点（避免画面太左）
    ld.text((W - size * 3, int(H * 0.6)), text, font=font, fill=(255, 255, 255, WATERMARK_ALPHA - 20))
    return layer


def paste_photo_with_border(canvas, photo):
    """人物照贴右侧，黄色描边"""
    pw, ph = photo.size
    target_h = int(H * 0.78)  # 人物区高约占整张 78%
    # 人物宽度按比例
    target_w = int(pw * target_h / ph)
    if target_w > W - PHOTO_LEFT - 40:
        target_w = W - PHOTO_LEFT - 40
        target_h = int(ph * target_w / pw)
    photo2 = photo.resize((target_w, target_h), Image.LANCZOS)

    # 贴图位置（右侧居中略上）
    x = PHOTO_LEFT + (W - PHOTO_LEFT - target_w) // 2
    y = int(H * 0.05)

    # 黄色描边（外描黑边 6px + 黄边 4px）
    edge = 6
    yellow = Image.new("RGBA", (target_w + edge * 2, target_h + edge * 2), (0, 0, 0, 0))
    yd = ImageDraw.Draw(yellow)
    yd.rectangle([0, 0, target_w + edge * 2 - 1, target_h + edge * 2 - 1], fill=YELLOW + (255,))
    yellow.paste(photo2, (edge, edge))
    # 描边微调：把人物边缘往黄内偏移 1-2px 模拟"抠图感"
    canvas.paste(yellow, (x - edge, y - edge), yellow)


def draw_text_stroke(draw, xy, text, font, fill, stroke_color, stroke_width=4, anchor="mm"):
    """带描边的文字（先画 stroke 偏移多次，再画主字）"""
    cx, cy = xy
    for dx in range(-stroke_width, stroke_width + 1):
        for dy in range(-stroke_width, stroke_width + 1):
            if dx * dx + dy * dy <= stroke_width * stroke_width:
                draw.text((cx + dx, cy + dy), text, font=font, fill=stroke_color, anchor=anchor)
    draw.text((cx, cy), text, font=font, fill=fill, anchor=anchor)


def draw_bottom_bar(draw, lines, sub=None):
    """底部深色横条 + 主标题(白粗) + 副标题(浅灰) + 品牌名"""
    bar_top = H - 460
    draw.rectangle([(0, bar_top), (W, H)], fill=DARK_BAR)

    title_font = ImageFont.truetype(FONT, TITLE_SIZE)
    sub_font = ImageFont.truetype(FONT, SUB_SIZE)
    brand_font = ImageFont.truetype(FONT, BRAND_SIZE)

    # 主标题（带黑色描边，多行）
    line_h = TITLE_SIZE + 18
    total_h = line_h * len(lines)
    start_y = bar_top + (380 - total_h) // 2 + 20
    for i, ln in enumerate(lines):
        cy = start_y + i * line_h + TITLE_SIZE // 2
        draw_text_stroke(draw, (W // 2, cy), ln, title_font, WHITE, BLACK_EDGE, stroke_width=5, anchor="mm")

    # 副标题
    if sub:
        sub_y = start_y + total_h + 24
        draw.text((W // 2, sub_y), sub, font=sub_font, fill=LIGHT_GRAY, anchor="mm")

    # 品牌名（右下小）
    draw.text((W - 30, H - 30), BRAND, font=brand_font, fill=(255, 220, 130), anchor="rb")


def main():
    if not BG.exists():
        sys.exit(f"没找到底图 {BG}")
    OUT.mkdir(parents=True, exist_ok=True)
    dirs = sorted([d for d in PKG.iterdir() if d.is_dir() and re.match(r"\d+", d.name)])
    if not dirs:
        sys.exit(f"没找到素材包 {PKG}")

    photo = Image.open(BG).convert("RGB")

    for d in dirs:
        title_full = parse_title(d / "publish.md")
        if not title_full:
            print(f"  [skip] {d.name} 无标题")
            continue
        short = short_title(title_full, 8)
        lines = wrap_cn(short, max_chars=8)
        sub = title_full  # 副标题用完整文案

        # 画布
        canvas = make_bg_gradient().convert("RGBA")

        # 左侧大字水印
        wm = make_watermark(canvas, "老张讲财税", WM_SIZE)
        canvas = Image.alpha_composite(canvas, wm)

        # 右侧人物 + 黄描边
        paste_photo_with_border(canvas, photo)

        draw = ImageDraw.Draw(canvas, "RGBA")
        # 底部条 + 主副标题 + 品牌
        draw_bottom_bar(draw, lines, sub)

        out = OUT / f"{d.name}_cover.png"
        canvas.convert("RGB").save(out, "PNG", quality=95)
        shutil.copy(out, d / "cover.png")
        print(f"  -> {out.name}  短标题: {short}  完整: {title_full}")


if __name__ == "__main__":
    main()
