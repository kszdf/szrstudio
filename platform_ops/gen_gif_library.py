# -*- coding: utf-8 -*-
"""
生成「幕后音·动态画面」动态GIF底图库 (D:\\heygem_data\\gpt_sovits\\gif_library)

风格基准: 视频号「建筑财税张老师」幕后音 —— 深色专业底 + 持续运动(像动态GIF一样动)。
文件命名含 tone 标签(neutral/risk/safe)与语义段(仓库/账本/图表…),
make_motion_video_v4.py 的 _pick_gif 按 文案语义段 > tone标签 > 情绪词 > 兜底 命中。

规格: 540x960, 12fps, 3s 循环(帧0==帧N, 无缝循环)。
用法: D:/heygem/py310/Scripts/python.exe gen_gif_library.py
"""
import math
import os

from PIL import Image, ImageDraw

OUT = r"D:\heygem_data\gpt_sovits\gif_library"
W, H, FPS, DUR = 540, 960, 12, 3.0
N = int(FPS * DUR)


def vgrad(pal_top, pal_bot):
    bg = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(bg)
    for y in range(H):
        t = y / H
        c = tuple(int(a + (b - a) * t) for a, b in zip(pal_top, pal_bot))
        d.line([(0, y), (W, y)], fill=c)
    return bg


def _float_particles(d, t, n, color, seed=0, speed=44):
    """上浮光点(周期性, 无缝循环)。"""
    for i in range(n):
        per = 5.0 + ((i + seed) % 4)
        yy = (H + 40) - ((t * speed + (i + seed) * per * speed) % (H + 80))
        xx = 40 + (((i + seed) * 157) % (W - 80))
        r = 2 + ((i + seed) % 2)
        a = 60 + 26 * math.sin(2 * math.pi * t / per + i)
        d.ellipse([xx - r, yy - r, xx + r, yy + r], fill=color + (max(0, int(a)),))


def make_neutral():
    """深海军蓝 + 金色折线图逐步绘制 + 上浮金粒子。"""
    top, bot = (8, 14, 30), (24, 36, 60)
    accent, accent2 = (245, 158, 11), (254, 215, 110)
    pts = [(0.10, 0.62), (0.30, 0.48), (0.50, 0.56), (0.70, 0.38), (0.90, 0.44)]
    xs = [int(p[0] * W) for p in pts]
    y0 = H * 0.62
    ys = [int(y0 - p[1] * H * 0.34) for p in pts]
    frames = []
    for f in range(N):
        t = f / FPS
        img = vgrad(top, bot)
        d = ImageDraw.Draw(img)
        for gx in range(0, W + 1, 60):
            a = 10 + 6 * math.sin(2 * math.pi * t / 3.0 + gx / 97.0)
            d.line([(gx, 0), (gx, H)], fill=(255, 255, 255, max(0, int(a))))
        prog = (t % 3.0) / 3.0
        shown = int(prog * (len(pts) - 1)) + 1
        for i in range(1, shown):
            d.line([(xs[i - 1], ys[i - 1]), (xs[i], ys[i])], fill=accent + (255,), width=6)
        cx, cy = xs[shown - 1], ys[shown - 1]
        r = 9 + 4 * math.sin(2 * math.pi * t * 2)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=accent2 + (255,))
        _float_particles(d, t, 8, accent2, seed=2)
        frames.append(img)
    return frames


def make_risk():
    """深酒红 + 警示环脉冲(雷达式) + 红色感叹号 + 上升红粒子。"""
    top, bot = (34, 12, 18), (62, 22, 30)
    accent, accent2 = (244, 63, 63), (251, 191, 191)
    cx, cy = W // 2, H * 0.30
    frames = []
    for f in range(N):
        t = f / FPS
        img = vgrad(top, bot)
        d = ImageDraw.Draw(img)
        for k in range(3):
            p = ((t % 3.0) / 3.0 + k / 3.0) % 1.0
            rr = int(40 + p * 240)
            a = int(90 * (1 - p))
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=accent + (a,), width=5)
        d.rounded_rectangle([cx - 26, cy - 130, cx + 26, cy + 60], radius=14, fill=accent + (255,))
        d.polygon([(cx, cy + 95), (cx - 30, cy + 170), (cx + 30, cy + 170)], fill=accent + (255,))
        _float_particles(d, t, 10, accent2, seed=1, speed=46)
        frames.append(img)
    return frames


def make_safe():
    """深青绿 + 盾牌呼吸光晕 + 对勾 + 上浮绿粒子。"""
    top, bot = (10, 30, 26), (20, 54, 44)
    accent, accent2 = (16, 185, 129), (153, 246, 206)
    cx, cy = W // 2, H * 0.32
    frames = []
    for f in range(N):
        t = f / FPS
        img = vgrad(top, bot)
        d = ImageDraw.Draw(img)
        glow_r = int(150 + 26 * math.sin(2 * math.pi * t / 2.2))
        for rr in range(glow_r, glow_r - 60, -6):
            a = int(24 * (1 - (glow_r - rr) / 60.0))
            if a > 0:
                d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=accent + (a,))
        d.polygon([(cx - 70, cy - 90), (cx + 70, cy - 90), (cx + 70, cy + 20),
                   (cx, cy + 120), (cx - 70, cy + 20)], outline=accent + (255,), width=8)
        d.line([(cx - 34, cy - 8), (cx - 10, cy + 18), (cx + 38, cy - 42)],
               fill=accent2 + (255,), width=12)
        _float_particles(d, t, 9, accent2, seed=3)
        frames.append(img)
    return frames


def make_warehouse():
    """空仓库货架(语义: 仓库/账本/库存) + 飘尘, 命中"库存虚高"类文案。"""
    top, bot = (16, 20, 28), (34, 40, 50)
    shelf_col = (120, 132, 148)
    frames = []
    for f in range(N):
        t = f / FPS
        img = vgrad(top, bot)
        d = ImageDraw.Draw(img)
        # 货架(透视: 左墙+右墙+横板)
        d.polygon([(0, 0), (W, 0), (W, H * 0.55), (0, H * 0.62)], fill=(26, 32, 42, 255))
        d.polygon([(0, 0), (W // 2, H * 0.16), (W // 2, H * 0.5), (0, H * 0.62)],
                  fill=(44, 52, 66, 255))
        for k in range(5):
            yy = H * (0.24 + k * 0.07)
            d.line([(0, yy), (W, yy - H * 0.02)], fill=shelf_col + (255,), width=5)
        # 稀疏空箱(库存空了的暗示)
        for i, (bx, by) in enumerate([(60, 700), (150, 640), (400, 680), (320, 750), (470, 720)]):
            r = 30 + (i % 3) * 12
            d.rectangle([bx - r, by - r, bx + r, by + r], outline=(90, 100, 114, 255), width=4)
        # 飘尘
        _float_particles(d, t, 12, (200, 210, 224), seed=5, speed=36)
        frames.append(img)
    return frames


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, maker in (
        ("neutral_蓝_图表.gif", make_neutral),
        ("risk_红_警示.gif", make_risk),
        ("safe_绿_合规.gif", make_safe),
        ("仓库_账本_空货架.gif", make_warehouse),
    ):
        frames = maker()
        p = os.path.join(OUT, name)
        frames[0].save(p, save_all=True, append_images=frames[1:],
                       duration=int(1000 / FPS), loop=0)
        print("OK", p, len(frames), "帧")


if __name__ == "__main__":
    main()
