#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_motion_video_v3.py — 幕后音模式图解视频生成器（v3 · 图文结合 + 专业质感）

相对 v2 的核心升级:
  · 语义自动配图: LLM 分镜为每页选 visual 图标(发票/账本/税局/天平/盾牌/警示章/时钟/钱袋…),
    代码现绘一套统一风格的财税具象图标, 作为画面视觉主角(大尺寸水印 + 前景实色), 真正图文结合。
  · 专业设计系统: 宋体标题(权威) + 雅黑数字(醒目) + 黑体正文;
    三层画面结构: 动态背景层 / 中景图标层 / 前景卡片层(带投影); 统一间距留白。
  · 高级转场与节奏: 场景间从左到右擦除(wipe)转场 + 图标旋入 + 卡片弹出;
    分句只在句末切(逗号并入), 杜绝碎卡。
  · 摆脱基础排版: 各模板改成"大图 + 精炼文字 + 卡片"图文版式, 不再纯文字占位。

接口:
  D:/heygem/py310/Scripts/python.exe make_motion_video_v3.py \
      --script 稿.md --audio 音频.wav --out 成品.mp4 --title 暂估成本
  --no-llm   规则分镜
  --preview N 只渲染前 N 帧(快速看风格)
"""
import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
FFMPEG = r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin/ffmpeg.exe"
FFPROBE = FFMPEG.replace("ffmpeg.exe", "ffprobe.exe")
TMP = BASE / "_tmp_motion_v3"

# 字体
HEI = str(BASE / "fonts/simhei.ttf")
SANS_B = r"C:/Windows/Fonts/msyhbd.ttc"
SERIF = r"C:/Windows/Fonts/NotoSerifSC-VF.ttf"

W, H = 1080, 1920
FPS = 30
TRANS = 0.55        # 入场/转场时长(秒)
ENTR = TRANS         # 入场动画时长(秒)
WORK_TOP, WORK_BOT = 360, 1320     # 卡片工作区(更靠中, 留白更足)
SUB_Y = H - 460                     # 字幕安全区顶端

TEMPLATES = ["bigtext", "number", "compare", "checklist",
             "statement", "steps", "stat2"]


# ============================== 字体 ==============================
_F = {}
def font(size, kind="hei"):
    key = (size, kind)
    if key not in _F:
        path = {"hei": HEI, "sans": SANS_B, "serif": SERIF}[kind]
        _F[key] = ImageFont.truetype(path, size, index=0)
    return _F[key]


# ============================== 配色(情绪) ==============================
def get_palette(tone):
    if tone == "risk":
        return dict(bg_top=(28, 14, 16), bg_bot=(48, 22, 26), accent=(244, 63, 63),
                    accent2=(251, 191, 191), glow=(190, 40, 40),
                    panel=(24, 12, 14), panel_line=(120, 42, 46),
                    shadow=(0, 0, 0))
    if tone == "safe":
        return dict(bg_top=(10, 24, 28), bg_bot=(15, 42, 46), accent=(16, 185, 129),
                    accent2=(153, 246, 206), glow=(16, 150, 100),
                    panel=(11, 28, 32), panel_line=(28, 110, 78),
                    shadow=(0, 0, 0))
    return dict(bg_top=(14, 21, 40), bg_bot=(28, 38, 56), accent=(245, 158, 11),
                accent2=(254, 215, 110), glow=(200, 140, 30),
                panel=(13, 20, 38), panel_line=(72, 92, 132),
                shadow=(0, 0, 0))
PAL_DEFAULT = get_palette("neutral")

WHITE = (248, 250, 252)
MUTED = (148, 163, 184)


# ============================== 缓动 ==============================
def ease(p):
    p = max(0.0, min(1.0, p))
    return 1 - (1 - p) ** 3

def ease_inout(p):
    p = max(0.0, min(1.0, p))
    return p * p * (3 - 2 * p)

def ease_back(p):
    p = max(0.0, min(1.0, p))
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (p - 1) ** 3 + c1 * (p - 1) ** 2


# ============================== 文本处理 ==============================
def clean_script(text):
    lines = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("==="):
            continue
        lines.append(s)
    return "".join(lines)

def split_sentences(text):
    """只在句末(。！？)切, 逗号并入; 超长句才在逗号处切, 杜绝碎卡。"""
    raw = re.split(r"(?<=[。！？])", text)
    out = []
    for b in raw:
        b = b.strip()
        if not b:
            continue
        if len(b) > 44:
            for s in re.split(r"(?<=，)", b):
                s = s.strip()
                if s:
                    out.append(s)
        else:
            out.append(b)
    return out

def wrap(seg, n=12):
    if len(seg) <= n:
        return [seg]
    return [seg[i:i + n] for i in range(0, len(seg), n)]


# ============================== 背景(动态, 缓存静态底) ==============================
_BG_CACHE = {}
_HALO = {}
_DEC = {}

def _bg_static(pal):
    """渐变底 + 精细网格 + 品牌区 + 底部线(静态, 缓存)。"""
    bg = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(bg)
    for y in range(0, H, 2):
        t = y / H
        c = tuple(int(a + (b - a) * t) for a, b in zip(pal["bg_top"], pal["bg_bot"]))
        d.line([(0, y), (W, y)], fill=c)
        d.line([(0, y + 1), (W, y + 1)], fill=c)
    # 精细网格(低对比)
    g = tuple(min(255, c + 10) for c in pal["bg_bot"])
    for x in range(0, W, 120):
        d.line([(x, 0), (x, H)], fill=g, width=1)
    for y in range(0, H, 120):
        d.line([(0, y), (W, y)], fill=g, width=1)
    # 品牌区
    d.text((W // 2, 84), "老张讲财税", font=font(46, "serif"), fill=MUTED, anchor="mm")
    d.line([(W // 2 - 170, 144), (W // 2 + 170, 144)], fill=pal["accent"], width=4)
    d.line([(90, H - 250), (W - 90, H - 250)], fill=pal["panel_line"], width=2)
    return bg

def _make_halo(pal):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([W * 0.05, H * 0.0, W * 0.95, H * 0.60], fill=(*pal["glow"], 60))
    d.ellipse([W * 0.35, H * 0.45, W * 1.1, H * 1.15], fill=(*pal["glow"], 36))
    return img.filter(ImageFilter.GaussianBlur(140))

def _make_dec(pal):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.arc([60, 60, 360, 360], 200, 320, fill=(*pal["accent"], 50), width=10)
    d.arc([80, 80, 330, 330], 20, 160, fill=(*pal["accent"], 30), width=6)
    d.line([(W - 40, H - 520), (W - 520, H - 40)], fill=(*pal["accent"], 24), width=8)
    d.arc([W - 380, H - 380, W - 80, H - 80], 30, 200, fill=(*pal["accent"], 40), width=8)
    return img

def dyn_bg(t, tone):
    pal = get_palette(tone)
    if tone not in _BG_CACHE:
        _BG_CACHE[tone] = _bg_static(pal)
        _HALO[tone] = _make_halo(pal)
        _DEC[tone] = _make_dec(pal)
    img = _BG_CACHE[tone].copy().convert("RGBA")
    breath = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(t * 0.7))
    halo = _HALO[tone]
    sc = 1.0 + 0.06 * math.sin(t * 0.5)
    nw, nh = int(W * sc), int(H * sc)
    hl = halo.resize((nw, nh))
    off = ((W - nw) // 2 + int(40 * math.sin(t * 0.4)),
           (H - nh) // 2 + int(30 * math.cos(t * 0.4)))
    hb = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    hb.paste(hl, off)
    r, g, b, a = hb.split()
    a = a.point(lambda v: int(v * breath))
    img = Image.alpha_composite(img, Image.merge("RGBA", (r, g, b, a)))
    dec = _DEC[tone].rotate(t * 6, resample=Image.BICUBIC, center=(210, 210))
    dec2 = _DEC[tone].rotate(-t * 4, resample=Image.BICUBIC, center=(W - 230, H - 230))
    img = Image.alpha_composite(img, dec)
    img = Image.alpha_composite(img, dec2)
    return img.convert("RGB")


# ============================== 图标库(具象化, 统一风格) ==============================
_ICON_CACHE = {}

def _lw(size):
    return max(3, size // 22)

# 每个函数在 0..size 坐标内绘制, 主色 col, 辅色 acc
def _ic_invoice(d, s, col, acc):
    lw = _lw(s)
    x0, y0, x1, y1 = s*0.18, s*0.16, s*0.82, s*0.86
    d.rounded_rectangle([x0, y0, x1, y1], radius=s*0.04, outline=col, width=lw, fill=(*col, 30))
    # 表格横线
    for i in range(1, 5):
        yy = y0 + (y1 - y0) * i / 5
        d.line([(x0 + s*0.06, yy), (x1 - s*0.06, yy)], fill=col, width=max(2, lw//2))
    # 印章
    d.ellipse([x1 - s*0.26, y1 - s*0.26, x1 - s*0.04, y1 - s*0.04], outline=acc, width=lw)

def _ic_contract(d, s, col, acc):
    lw = _lw(s)
    x0, y0, x1, y1 = s*0.20, s*0.14, s*0.80, s*0.88
    d.rounded_rectangle([x0, y0, x1, y1], radius=s*0.03, outline=col, width=lw, fill=(*col, 28))
    # 折角
    d.polygon([(x1 - s*0.16, y0), (x1, y0), (x1, y0 + s*0.16)], fill=acc)
    for i in range(1, 5):
        yy = y0 + (y1 - y0) * i / 5
        d.line([(x0 + s*0.07, yy), (x1 - s*0.07, yy)], fill=col, width=max(2, lw//2))

def _ic_ledger(d, s, col, acc):
    lw = _lw(s)
    x0, y0, x1, y1 = s*0.16, s*0.14, s*0.84, s*0.88
    d.rounded_rectangle([x0, y0, x1, y1], radius=s*0.03, outline=col, width=lw, fill=(*col, 26))
    d.line([(x0 + (x1-x0)*0.42, y0), (x0 + (x1-x0)*0.42, y1)], fill=acc, width=lw)  # 书脊
    for i in range(1, 6):
        yy = y0 + (y1 - y0) * i / 6
        d.line([(x0 + s*0.05, yy), (x0 + (x1-x0)*0.40, yy)], fill=col, width=max(2, lw//2))
        d.line([(x0 + (x1-x0)*0.44, yy), (x1 - s*0.05, yy)], fill=col, width=max(2, lw//2))

def _ic_building(d, s, col, acc):
    lw = _lw(s)
    x0, y0, x1, y1 = s*0.26, s*0.18, s*0.74, s*0.88
    d.rectangle([x0, y0, x1, y1], outline=col, width=lw, fill=(*col, 24))
    # 柱子
    n = 4
    for i in range(1, n):
        xx = x0 + (x1 - x0) * i / n
        d.line([(xx, y0 + s*0.05), (xx, y1 - s*0.10)], fill=acc, width=max(2, lw//2))
    # 顶
    d.polygon([(x0 - s*0.04, y0), (x1 + s*0.04, y0), (s*0.5, y0 - s*0.12)], outline=col, width=lw)
    # 门
    d.rectangle([s*0.42, y1 - s*0.16, s*0.58, y1], outline=acc, width=lw)

def _ic_scale(d, s, col, acc):
    lw = _lw(s)
    cy = s*0.42
    d.line([(s*0.18, cy), (s*0.82, cy)], fill=col, width=lw)          # 横梁
    d.line([(s*0.5, cy), (s*0.5, s*0.82)], fill=col, width=lw)        # 立柱
    d.line([(s*0.32, s*0.86), (s*0.68, s*0.86)], fill=col, width=lw)  # 底座
    for cx in (s*0.22, s*0.78):
        d.line([(cx, cy), (cx, cy + s*0.14)], fill=acc, width=lw)
        d.line([(cx - s*0.12, cy + s*0.14), (cx + s*0.12, cy + s*0.14)], fill=acc, width=lw)
        d.ellipse([cx - s*0.12, cy + s*0.14, cx + s*0.12, cy + s*0.26], outline=acc, width=lw)

def _ic_shield(d, s, col, acc):
    lw = _lw(s)
    pts = [(s*0.5, s*0.08), (s*0.88, s*0.22), (s*0.84, s*0.58),
           (s*0.5, s*0.92), (s*0.16, s*0.58), (s*0.12, s*0.22)]
    d.polygon(pts, outline=col, width=lw, fill=(*col, 26))
    # 对勾
    d.line([(s*0.34, s*0.50), (s*0.46, s*0.64)], fill=acc, width=lw)
    d.line([(s*0.46, s*0.64), (s*0.70, s*0.34)], fill=acc, width=lw)

def _ic_warning(d, s, col, acc):
    lw = _lw(s)
    pts = [(s*0.5, s*0.10), (s*0.90, s*0.86), (s*0.10, s*0.86)]
    d.polygon(pts, outline=col, width=lw, fill=(*col, 26))
    d.line([(s*0.5, s*0.36), (s*0.5, s*0.62)], fill=acc, width=lw)
    d.ellipse([s*0.5 - s*0.03, s*0.68, s*0.5 + s*0.03, s*0.74], fill=acc)

def _ic_clock(d, s, col, acc):
    lw = _lw(s)
    cx, cy, r = s*0.5, s*0.5, s*0.38
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=lw, fill=(*col, 22))
    for i in range(12):
        a = i * math.pi / 6
        d.line([(cx + (r-6)*math.sin(a), cy - (r-6)*math.cos(a)),
                (cx + (r-16)*math.sin(a), cy - (r-16)*math.cos(a))], fill=col, width=max(2, lw//2))
    d.line([(cx, cy), (cx, cy - r*0.55)], fill=acc, width=lw)       # 分针
    d.line([(cx, cy), (cx + r*0.42, cy)], fill=acc, width=lw)       # 时针
    d.ellipse([cx - s*0.03, cy - s*0.03, cx + s*0.03, cy + s*0.03], fill=acc)

def _ic_calculator(d, s, col, acc):
    lw = _lw(s)
    x0, y0, x1, y1 = s*0.22, s*0.14, s*0.78, s*0.88
    d.rounded_rectangle([x0, y0, x1, y1], radius=s*0.06, outline=col, width=lw, fill=(*col, 24))
    d.rounded_rectangle([x0 + s*0.06, y0 + s*0.06, x1 - s*0.06, y0 + s*0.24],
                        radius=s*0.03, outline=acc, width=lw)        # 屏幕
    # 按钮 3x3
    bx, by = x0 + s*0.10, y0 + s*0.32
    bw, bh = (x1 - x0) * 0.26, (y1 - y0) * 0.18
    for r in range(3):
        for c in range(3):
            px, py = bx + c * (x1 - x0) * 0.30, by + r * (y1 - y0) * 0.21
            d.rounded_rectangle([px, py, px + bw, py + bh], radius=s*0.02, outline=acc, width=max(2, lw//2))

def _ic_magnifier(d, s, col, acc):
    lw = _lw(s)
    cx, cy, r = s*0.42, s*0.42, s*0.26
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=lw, fill=(*col, 22))
    d.line([(cx + r*0.7, cy + r*0.7), (s*0.86, s*0.86)], fill=acc, width=int(lw*1.4))

def _ic_moneybag(d, s, col, acc):
    lw = _lw(s)
    cx = s*0.5
    d.pieslice([cx - s*0.34, s*0.34, cx + s*0.34, s*0.94], 0, 180, outline=col, width=lw, fill=(*col, 24))
    d.line([(cx - s*0.18, s*0.30), (cx + s*0.18, s*0.30)], fill=col, width=lw)  # 束口
    d.line([(cx - s*0.10, s*0.20), (cx + s*0.10, s*0.20)], fill=acc, width=lw)
    # ¥
    d.line([(cx - s*0.12, s*0.52), (cx + s*0.12, s*0.52)], fill=acc, width=lw)
    d.line([(cx, s*0.42), (cx, s*0.70)], fill=acc, width=lw)
    d.line([(cx - s*0.12, s*0.62), (cx + s*0.12, s*0.62)], fill=acc, width=lw)

def _ic_checklist(d, s, col, acc):
    lw = _lw(s)
    x0, y0, x1, y1 = s*0.16, s*0.12, s*0.84, s*0.90
    d.rounded_rectangle([x0, y0, x1, y1], radius=s*0.04, outline=col, width=lw, fill=(*col, 22))
    for i in range(4):
        yy = y0 + s*0.14 + i * (y1 - y0) * 0.20
        d.rounded_rectangle([x0 + s*0.08, yy, x0 + s*0.20, yy + s*0.10],
                            radius=s*0.02, outline=acc, width=lw)
        d.line([(x0 + s*0.10, yy + s*0.05), (x0 + s*0.15, yy + s*0.10)], fill=acc, width=lw)
        d.line([(x0 + s*0.15, yy + s*0.10), (x0 + s*0.20, yy + s*0.0)], fill=acc, width=lw)
        d.line([(x0 + s*0.28, yy + s*0.05), (x1 - s*0.08, yy + s*0.05)], fill=col, width=max(2, lw//2))

def _ic_ban(d, s, col, acc):
    lw = _lw(s)
    cx, cy, r = s*0.5, s*0.5, s*0.40
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=lw, fill=(*col, 22))
    d.line([(cx - r*0.7, cy - r*0.7), (cx + r*0.7, cy + r*0.7)], fill=acc, width=int(lw*1.2))

def _ic_person(d, s, col, acc):
    lw = _lw(s)
    cx, cy = s*0.5, s*0.36
    r = s*0.18
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=lw, fill=(*col, 24))
    d.arc([s*0.18, s*0.58, s*0.82, s*0.96], 180, 360, fill=acc, width=lw)

def _ic_chart(d, s, col, acc):
    lw = _lw(s)
    x0, y0, x1, y1 = s*0.20, s*0.20, s*0.84, s*0.84
    d.line([(x0, y0), (x0, y1)], fill=col, width=lw)
    d.line([(x0, y1), (x1, y1)], fill=col, width=lw)
    hs = [0.35, 0.55, 0.42, 0.72]
    n = len(hs)
    bw = (x1 - x0) * 0.18
    for i, hh in enumerate(hs):
        bx = x0 + s*0.06 + i * (x1 - x0) * 0.22
        bh = (y1 - y0) * hh
        d.rectangle([bx, y1 - bh, bx + bw, y1], outline=acc, width=lw, fill=(*acc, 40))

def _ic_seal(d, s, col, acc):
    lw = _lw(s)
    cx, cy, r = s*0.5, s*0.5, s*0.40
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=lw, fill=(*col, 18))
    d.ellipse([cx - r*0.78, cy - r*0.78, cx + r*0.78, cy + r*0.78], outline=col, width=max(2, lw//2))
    # 五角星
    R, r2 = r*0.5, r*0.22
    pts = []
    for i in range(10):
        ang = -math.pi/2 + i * math.pi/5
        rad = R if i % 2 == 0 else r2
        pts.append((cx + rad*math.cos(ang), cy + rad*math.sin(ang)))
    d.polygon(pts, outline=acc, width=lw)

def _ic_arrow(d, s, col, acc):
    lw = _lw(s)
    d.line([(s*0.18, s*0.78), (s*0.78, s*0.22)], fill=col, width=lw)
    d.polygon([(s*0.78, s*0.22), (s*0.62, s*0.24), (s*0.76, s*0.40)], fill=acc)

_ICON_DRAW = {
    "invoice": _ic_invoice, "contract": _ic_contract, "ledger": _ic_ledger,
    "building": _ic_building, "scale": _ic_scale, "shield": _ic_shield,
    "warning": _ic_warning, "clock": _ic_clock, "calculator": _ic_calculator,
    "magnifier": _ic_magnifier, "moneybag": _ic_moneybag, "checklist": _ic_checklist,
    "ban": _ic_ban, "person": _ic_person, "chart": _ic_chart,
    "seal": _ic_seal, "arrow": _ic_arrow,
}

VISUAL_ALIAS = {
    "发票": "invoice", "单据": "invoice", "invoice": "invoice",
    "合同": "contract", "协议": "contract", "文档": "contract", "contract": "contract",
    "账": "ledger", "账本": "ledger", "账簿": "ledger", "ledger": "ledger",
    "税局": "building", "税务局": "building", "大楼": "building", "building": "building",
    "天平": "scale", "合规": "scale", "公平": "scale", "scale": "scale",
    "盾": "shield", "安全": "shield", "shield": "shield",
    "警告": "warning", "风险": "warning", "warning": "warning",
    "时间": "clock", "期限": "clock", "日期": "clock", "clock": "clock",
    "税负": "calculator", "计算": "calculator", "calculator": "calculator",
    "稽查": "magnifier", "放大镜": "magnifier", "查": "magnifier", "magnifier": "magnifier",
    "罚款": "moneybag", "钱": "moneybag", "金额": "moneybag", "moneybag": "moneybag",
    "清单": "checklist", "要点": "checklist", "checklist": "checklist",
    "禁止": "ban", "虚开": "ban", "ban": "ban",
    "老板": "person", "人": "person", "企业": "person", "person": "person",
    "对比": "chart", "图表": "chart", "chart": "chart",
    "印章": "seal", "税章": "seal", "seal": "seal",
    "趋势": "arrow", "增长": "arrow", "arrow": "arrow",
}

def resolve_visual(v):
    if not v:
        return None
    v = str(v).strip().lower()
    for k, val in VISUAL_ALIAS.items():
        if k in v or v in k:
            return val
    return None

def draw_icon(name, size, color, accent, spin=0.0):
    """返回 RGBA 图标(尺寸 size×size, 居中, 透明背景); spin 弧度可旋转。"""
    key = (name, size, color, accent, round(spin, 2))
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    base = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(base)
    fn = _ICON_DRAW.get(name)
    if fn:
        fn(d, size, color, accent)
    img = base.rotate(math.degrees(spin), resample=Image.BICUBIC, center=(size/2, size/2)) if spin else base
    _ICON_CACHE[key] = img
    return img

def overlay_icon(rgb_img, name, color, accent, cx, cy, size, alpha=1.0, spin=0.0):
    """把图标画到 rgb_img 上(cx,cy 为中心)。返回 RGB。"""
    ic = draw_icon(name, size, color, accent, spin)
    if alpha < 1.0:
        r, g, b, a = ic.split()
        a = a.point(lambda v: int(v * alpha))
        ic = Image.merge("RGBA", (r, g, b, a))
    base = rgb_img.convert("RGBA")
    base.alpha_composite(ic, (int(cx - size/2), int(cy - size/2)))
    return base


# ============================== 卡片容器 + 投影 + 文字基元 ==============================
def drop_shadow(card_rgba, dx=16, dy=24, blur=30, alpha=110):
    a = card_rgba.split()[3]
    sh = a.filter(ImageFilter.GaussianBlur(blur)).point(lambda v: int(v * alpha / 255))
    sized = Image.new("RGBA", (card_rgba.width + dx*2, card_rgba.height + dy*2), (0, 0, 0, 0))
    sized.alpha_composite(Image.merge("RGBA", (sh, sh, sh, sh)), (dx, dy))
    return sized

def compose_card(bg, card, alpha, scale, ox, oy):
    if scale != 1.0 or ox or oy:
        nw, nh = max(1, int(W * scale)), max(1, int(H * scale))
        card = card.resize((nw, nh))
    # 投影层
    sh = drop_shadow(card, dx=18, dy=26, blur=34, alpha=120)
    base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    base.alpha_composite(sh, ((W - sh.width)//2 + ox, (H - sh.height)//2 + oy))
    base.alpha_composite(card, ((W - card.width)//2 + ox, (H - card.height)//2 + oy))
    if alpha < 1.0:
        r, g, b, a = base.split()
        a = a.point(lambda v: int(v * alpha))
        base = Image.merge("RGBA", (r, g, b, a))
    return Image.alpha_composite(bg.convert("RGBA"), base).convert("RGB")

def draw_highlight(d, text, x, y, size, normal, hi, highlight, pulse=0.0, kind="hei"):
    f = font(size, kind)
    cur = x
    for ch in text:
        if highlight and ch in highlight:
            br = int(255 * pulse * 0.35)
            col = tuple(min(255, c + br) for c in hi)
            d.text((cur, y), ch, font=f, fill=col, anchor="lm")
        else:
            d.text((cur, y), ch, font=f, fill=normal, anchor="lm")
        cur += d.textlength(ch, font=f)
    return cur

def draw_subtitle(img, text):
    d = ImageDraw.Draw(img)
    lines = wrap(text, 13)
    size = 46
    line_h = size + 12
    y0 = SUB_Y + 30
    for i, ln in enumerate(lines):
        w = d.textlength(ln, font=font(size))
        x = (W - w) // 2
        y = y0 + i * line_h
        for dx in (-3, 0, 3):
            for dy in (-3, 0, 3):
                if dx or dy:
                    d.text((x + dx, y + dy), ln, font=font(size), fill=(0, 0, 0))
        d.text((x, y), ln, font=font(size), fill=WHITE)


# ============================== 7 种模板(图文版) ==============================
def tpl_bigtext(img, f, p, local, pal, visual, layout):
    d = ImageDraw.Draw(img)
    text = str(f.get("text", ""))[:16]
    hl = f.get("highlight", "")
    pulse = 0.5 + 0.5 * math.sin(local * 3)
    # 中景: 大图标水印
    if visual:
        img = overlay_icon(img, visual, pal["accent"], pal["accent2"], W//2, 760, 720, 0.14)
        d = ImageDraw.Draw(img)
    size = 132 if len(text) <= 5 else (104 if len(text) <= 9 else 80)
    lines = wrap(text, 7)
    y0 = 1180 - (len(lines) - 1) * (size + 26) // 2
    for i, ln in enumerate(lines):
        y = y0 + i * (size + 26)
        x = W // 2
        draw_highlight(d, ln, x, y, size, WHITE, pal["accent2"], hl, pulse, "serif")
    # 下划装饰
    w = d.textlength(text[:7], font=font(size))
    lw = int(ease(p) * (w + 60))
    if lw > 4:
        d.line([(W//2 - lw//2, y0 + len(lines)*(size+26) - size//2 + 20),
                (W//2 + lw//2, y0 + len(lines)*(size+26) - size//2 + 20)],
               fill=pal["accent"], width=10)

def tpl_number(img, f, p, local, pal, visual, layout):
    d = ImageDraw.Draw(img)
    num = str(f.get("number", ""))
    label = str(f.get("label", ""))
    sub = str(f.get("sub", ""))
    hl = f.get("highlight", "")
    # 前景大图标(左)
    if visual:
        img = overlay_icon(img, visual, pal["accent"], pal["accent2"], 300, 800, 360, 0.9, spin=local*0.6)
        d = ImageDraw.Draw(img)
    if label:
        draw_highlight(d, label, 620, 600, 54, MUTED, pal["accent2"], hl, kind="sans")
    m = re.match(r"^(\d+(?:\.\d+)?)(.*)$", num)
    shown = num
    if m:
        target = float(m.group(1))
        suffix = m.group(2)
        isint = "." not in m.group(1)
        cur = target * ease(p)
        shown = (f"{int(cur)}" if isint else f"{cur:.1f}") + suffix
    size = 170 if len(shown) <= 5 else (130 if len(shown) <= 8 else 100)
    pop = 1.0 + 0.05 * max(0.0, math.sin(local * 5)) * (1 - p)
    d.text((640, 820), shown, font=font(int(size * pop), "sans"), fill=pal["accent"], anchor="mm")
    d.line([(470, 940), (820, 940)], fill=pal["panel_line"], width=3)
    if sub:
        a = max(0.0, (p - 0.45) / 0.55)
        if a > 0:
            draw_highlight(d, sub[:18], 640, 1030, 44, MUTED, pal["accent2"], hl, a, "hei")

def tpl_compare(img, f, p, local, pal, visual, layout):
    d = ImageDraw.Draw(img)
    p1 = ease(min(1.0, p / 0.55))
    p2 = ease(max(0.0, (p - 0.35) / 0.65))
    dx1 = int((1 - p1) * 220)
    dx2 = int((1 - p2) * 220)
    box1 = (130 - dx1, 560, W - 130 - dx1, 880)
    box2 = (130 + dx2, 960, W - 130 + dx2, 1280)
    d.rounded_rectangle(box1, radius=30, fill=(58, 24, 24), outline=(244, 63, 63), width=5)
    d.rounded_rectangle(box2, radius=30, fill=(14, 50, 34), outline=(16, 185, 129), width=5)
    img = overlay_icon(img, "warning", (244, 63, 63), (251, 191, 191), box1[0] + 80, box1[1] + 70, 120, 0.9)
    img = overlay_icon(img, "shield", (16, 185, 129), (153, 246, 206), box2[0] + 80, box2[1] + 70, 120, 0.9)
    d = ImageDraw.Draw(img)
    d.text((box1[0] + 150, box1[1] + 60), "✗ " + str(f.get("left_title", "错误"))[:7],
           font=font(54, "serif"), fill=(244, 63, 63), anchor="lm")
    d.text((box1[0] + 36, box1[1] + 170), str(f.get("left_sub", ""))[:15],
           font=font(42, "hei"), fill=(251, 191, 191), anchor="lm")
    d.text((box2[0] + 150, box2[1] + 60), "✓ " + str(f.get("right_title", "正确"))[:7],
           font=font(54, "serif"), fill=(16, 185, 129), anchor="lm")
    d.text((box2[0] + 36, box2[1] + 170), str(f.get("right_sub", ""))[:15],
           font=font(42, "hei"), fill=(153, 246, 206), anchor="lm")

def tpl_checklist(img, f, p, local, pal, visual, layout):
    d = ImageDraw.Draw(img)
    # 顶部大图标
    if visual:
        img = overlay_icon(img, visual, pal["accent"], pal["accent2"], W//2, 470, 200, 0.95)
        d = ImageDraw.Draw(img)
    title = str(f.get("title", "关键要点"))
    hl = f.get("highlight", "")
    d.text((W//2, 640), title, font=font(66, "serif"), fill=WHITE, anchor="mm")
    d.line([(W//2 - 150, 700), (W//2 + 150, 700)], fill=pal["accent"], width=6)
    items = [str(x)[:12] for x in (f.get("items") or [])][:4]
    if not items:
        items = ["要点一", "要点二", "要点三"]
    step = 1.0 / max(len(items), 1)
    for i, it in enumerate(items):
        pi = ease((p - i * step * 0.7) / (step * 0.7 + 0.0001))
        if pi <= 0:
            continue
        dx = int((1 - pi) * 120)
        y = 850 + i * 150
        d.ellipse((180 + dx, y - 30, 246 + dx, y + 36), outline=pal["accent"], width=6)
        if pi > 0.55:
            d.line([(196 + dx, y + 2), (216 + dx, y + 22)], fill=pal["accent"], width=7)
            d.line([(216 + dx, y + 22), (240 + dx, y - 8)], fill=pal["accent"], width=7)
        draw_highlight(d, it, 290 + dx, y + 4, 52, WHITE, pal["accent2"], hl, pi, "hei")

def tpl_statement(img, f, p, local, pal, visual, layout):
    d = ImageDraw.Draw(img)
    if visual:
        img = overlay_icon(img, visual, pal["accent"], pal["accent2"], 200, 760, 300, 0.85, spin=local*0.3)
        d = ImageDraw.Draw(img)
    hl = f.get("highlight", "")
    pulse = 0.5 + 0.5 * math.sin(local * 3)
    d.text((W - 180, 520), "”", font=font(200, "serif"), fill=pal["accent"], anchor="mm")
    text = str(f.get("text", ""))[:30]
    lines = wrap(text, 11)
    y = 820
    for i, ln in enumerate(lines):
        draw_highlight(d, ln, W//2 - d.textlength(ln, font=font(80, "serif"))/2 + 90,
                       y + i * 104, 80, WHITE, pal["accent2"], hl, pulse, "serif")
    d.text((W//2 + 90, y + len(lines) * 104 + 40), "—— 口播原文",
           font=font(36, "hei"), fill=MUTED, anchor="mm")

def tpl_steps(img, f, p, local, pal, visual, layout):
    d = ImageDraw.Draw(img)
    if visual:
        img = overlay_icon(img, visual, pal["accent"], pal["accent2"], W//2, 470, 180, 0.9)
        d = ImageDraw.Draw(img)
    items = [str(x)[:14] for x in (f.get("items") or [])][:4]
    if not items:
        items = ["第一步", "第二步", "第三步"]
    hl = f.get("highlight", "")
    top = 600
    step_h = 155
    n = len(items)
    step = 1.0 / max(n, 1)
    for i, it in enumerate(items):
        pi = ease((p - i * step * 0.7) / (step * 0.7 + 0.0001))
        if pi <= 0:
            continue
        cy = top + i * step_h + 40
        if i < n - 1:
            d.line([(210, cy + 40), (210, cy + step_h)], fill=pal["panel_line"], width=5)
        d.ellipse((160, cy, 260, cy + 100), fill=pal["accent"])
        d.text((210, cy + 50), str(i + 1), font=font(58, "sans"), fill=(8, 8, 8), anchor="mm")
        dx = int((1 - pi) * 140)
        draw_highlight(d, it, 310 + dx, cy + 50, 56, WHITE, pal["accent2"], hl, pi, "hei")

def tpl_stat2(img, f, p, local, pal, visual, layout):
    d = ImageDraw.Draw(img)
    if visual:
        img = overlay_icon(img, visual, pal["accent"], pal["accent2"], W//2, 520, 220, 0.85)
        d = ImageDraw.Draw(img)
    lnum = str(f.get("left_num", ""))
    lsub = str(f.get("left_sub", ""))
    rnum = str(f.get("right_num", ""))
    rsub = str(f.get("right_sub", ""))
    title = str(f.get("title", ""))
    C = 0.5 + 0.5 * math.sin(local * 3)
    if title:
        d.text((W//2, 700), title, font=font(58, "serif"), fill=WHITE, anchor="mm")
    d.text((W//2 - 260, 920), lnum, font=font(150, "sans"), fill=(244, 63, 63), anchor="mm")
    if lsub:
        d.text((W//2 - 260, 1080), lsub[:10], font=font(42, "hei"), fill=(251, 191, 191), anchor="mm")
    d.text((W//2 + 260, 920), rnum, font=font(150, "sans"), fill=(16, 185, 129), anchor="mm")
    if rsub:
        d.text((W//2 + 260, 1080), rsub[:10], font=font(42, "hei"), fill=(153, 246, 206), anchor="mm")
    aw = 70 + int(24 * C)
    d.line([(W//2 - 90, 920), (W//2 + 90 - aw, 920)], fill=pal["accent2"], width=14)
    d.polygon([(W//2 + 90 - aw, 920 - 34), (W//2 + 90, 920), (W//2 + 90 - aw, 920 + 34)], fill=pal["accent2"])

TPLS = {"bigtext": tpl_bigtext, "number": tpl_number, "compare": tpl_compare,
        "checklist": tpl_checklist, "statement": tpl_statement,
        "steps": tpl_steps, "stat2": tpl_stat2}

ANIM_DEFAULT = {"bigtext": "pop", "number": "pop", "compare": "slide_up",
                "checklist": "slide_up", "statement": "fade", "steps": "slide_left",
                "stat2": "pop"}

def anim_params(anim, p):
    a = ease(p)
    if anim == "slide_up":
        return a, 1.0, 0, int((1 - a) * 90)
    if anim == "slide_left":
        return a, 1.0, -int((1 - a) * 160), 0
    if anim == "pop":
        sc = 0.84 + 0.16 * ease_back(p)
        return a, sc, 0, 0
    return a, 1.0, 0, 0


# ============================== 分镜(LLM + 规则) ==============================
SB_PROMPT = """你是财税口播短视频的分镜导演。下面是一段口播稿按序号切好的句子。
为每个句子选图卡模板, 提取关键信息(数字/条款/对比项必须原样取), 并标出本句要强调的关键词、判断情绪基调, 以及匹配一张贴切的配图图标。

可用模板与字段:
- bigtext: 大字冲击卡(开头钩子/警告/结论)。fields: {"text":"≤16字", "highlight":"句中重点词"}
- number: 数字冲击卡(具体数字/期限/金额)。fields: {"number":"原样如500万/5月31日", "label":"≤8字", "sub":"≤18字", "highlight":"重点"}
- compare: 对比卡(两种做法/后果)。fields: {"left_title":"≤6字(危险)", "left_sub":"≤15字", "right_title":"≤6字(正确)", "right_sub":"≤15字", "highlight":"重点"}
- checklist: 清单卡(多个条件/材料/步骤)。fields: {"title":"≤8字", "items":["≤12字",...](3-4项), "highlight":"重点"}
- statement: 引用/结论卡(事实/期限/法条)。fields: {"text":"≤30字原文", "highlight":"重点"}
- steps: 步骤时间轴卡(流程/先后顺序/条件链)。fields: {"items":["≤14字",...](3-4步), "highlight":"重点"}
- stat2: 双数字对比大卡(两个数字放一起对比)。fields: {"title":"≤10字", "left_num":"原样数字", "left_sub":"≤10字", "right_num":"原样数字", "right_sub":"≤10字"}

tone(情绪): "risk"(风险/后果/处罚/警告) / "safe"(合规/正确做法/建议) / "neutral"(中性)。
visual(配图图标, 从下列选最贴切的英文键, 不要自造):
  invoice(发票) contract(合同文档) ledger(账本) building(税务局大楼) scale(天平/合规)
  shield(盾牌/安全) warning(警告/风险) clock(期限/日期) calculator(税负/计算)
  magnifier(稽查/放大镜) moneybag(罚款/金额) checklist(清单/要点) ban(禁止/虚开)
  person(老板/企业) chart(对比/图表) seal(印章/税章) arrow(趋势/增长)

输出: 严格 JSON 数组, 每句一个元素, 不要解释或代码块:
[{"idx":0,"template":"bigtext","tone":"risk","visual":"warning","highlight":"虚开","fields":{...}}, ...]

句子列表:
"""

def llm_storyboard(sentences):
    sys.path.insert(0, str(BASE))
    from model_providers import ensure_env, get_text_config, deepseek_chat
    ensure_env()
    cfg = get_text_config()
    listing = "\n".join(f"{i}. {s}" for i, s in enumerate(sentences))
    raw = deepseek_chat(SB_PROMPT + listing, model=cfg["model"], key=cfg["key"],
                        base_url=cfg["base_url"], timeout=90)
    raw = raw.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.M).strip()
    data = json.loads(raw)
    out = {}
    for item in data:
        idx = int(item.get("idx", -1))
        tpl = item.get("template", "")
        if 0 <= idx < len(sentences) and tpl in TEMPLATES:
            out[idx] = {"template": tpl,
                        "tone": item.get("tone", "neutral"),
                        "visual": resolve_visual(item.get("visual", "")),
                        "highlight": item.get("highlight", ""),
                        "fields": item.get("fields") or {},
                        "anim": ANIM_DEFAULT.get(tpl, "fade")}
    for i in range(len(sentences)):
        if i not in out:
            out[i] = rule_one(i, sentences[i], len(sentences))
    return [out[i] for i in range(len(sentences))]

def rule_one(idx, sent, total):
    nums = re.findall(r"\d+(?:\.\d+)?万?", sent)
    visual = None
    for kw, v in VISUAL_ALIAS.items():
        if kw in sent:
            visual = v
            break
    if idx == 0 or idx == total - 1:
        tone = "risk" if re.search(r"怕|风险|罚|亏|坑|错|？", sent) else "neutral"
        return {"template": "bigtext", "tone": tone, "visual": visual or "warning",
                "highlight": "", "fields": {"text": sent[:14]}, "anim": "pop"}
    if len(re.split(r"[、，]", sent)) >= 4:
        return {"template": "checklist", "tone": "neutral", "visual": visual or "checklist",
                "highlight": "", "fields": {"title": "关键要点",
                "items": [x.strip()[:12] for x in re.split(r"[、，]", sent) if x.strip()][:4]},
                "anim": "slide_up"}
    if re.search(r"然后|接着|第一步|第二步|先|再|最后|流程|步骤", sent):
        return {"template": "steps", "tone": "safe", "visual": visual or "arrow",
                "highlight": "", "fields": {"items": [x.strip()[:14] for x in re.split(r"[、，]", sent) if x.strip()][:4] or ["按流程处理"]},
                "anim": "slide_left"}
    if nums:
        tone = "risk" if re.search(r"罚|倍|万|滞纳", sent) else "neutral"
        return {"template": "number", "tone": tone, "visual": visual or "calculator",
                "highlight": nums[0], "fields": {"number": nums[0], "label": "关键数字", "sub": sent[:16]},
                "anim": "pop"}
    if re.search(r"否则|不然|一旦|就是", sent):
        return {"template": "statement", "tone": "risk", "visual": visual or "warning",
                "highlight": "", "fields": {"text": sent[:26]}, "anim": "fade"}
    return {"template": "statement", "tone": "neutral", "visual": visual or "scale",
            "highlight": "", "fields": {"text": sent[:26]}, "anim": "fade"}


# ============================== 时间轴 ==============================
def timeline(sentences, dur):
    total = sum(len(s) for s in sentences) or 1
    usable = max(dur - 0.6, 0.5)
    t = 0.3
    out = []
    for s in sentences:
        sd = max(usable * len(s) / total, 1.0)
        out.append((t, t + sd))
        t += sd
    return out


# ============================== 渲染 ==============================
def render_scene_frame(idx, local, sentences, tl, sb):
    """渲染某场景在 local(场景内秒) 的完整帧。"""
    sc = sb[idx]
    tone = sc.get("tone", "neutral")
    pal = get_palette(tone)
    p = min(1.0, local / ENTR)
    img = dyn_bg(local, tone)
    # 前景卡片层
    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    TPLS[sc["template"]](card, sc["fields"], p, local, pal, sc.get("visual"), "auto")
    # 入场动画 + Ken Burns
    kb = 1.0 + (1 - p) * 0.03
    alpha, scale, ox, oy = anim_params(sc.get("anim", "fade"), p)
    scale *= kb
    img = compose_card(img, card, alpha, scale, ox, oy)
    if local >= 0:
        draw_subtitle(img, sentences[idx])
    return img

def wipe_mask(prog):
    """左→右展开蒙版(灰度), prog 0→1。"""
    m = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(m)
    edge = int(W * ease_inout(prog))
    soft = 80
    for x in range(W):
        v = 0
        if x < edge - soft:
            v = 255
        elif x < edge:
            v = int(255 * (x - (edge - soft)) / soft)
        d.line([(x, 0), (x, H)], fill=v)
    return m

def render_frame(t, sentences, tl, sb):
    cur = len(tl) - 1
    for k, (s0, s1) in enumerate(tl):
        if t < s1:
            cur = k
            break
    s0, s1 = tl[cur]
    local = max(0.0, t - s0)
    dur = s1 - s0
    # 转场: 场景开头 TRANS 内, 上一场景末态 + 当前场景擦除进入
    if cur > 0 and local < TRANS:
        prev = render_scene_frame(cur - 1, (tl[cur-1][1] - tl[cur-1][0]) - 0.001, sentences, tl, sb)
        curf = render_scene_frame(cur, local, sentences, tl, sb)
        m = wipe_mask(local / TRANS)
        cur_a = curf.convert("RGBA")
        r, g, b, ca = cur_a.split()
        # 用蒙版与现有 alpha 取交集(仅显示已擦除区域)
        new_a = ImageChops.multiply(ca, m)
        masked = Image.merge("RGBA", (r, g, b, new_a))
        out = Image.alpha_composite(prev.convert("RGBA"), masked)
        return out.convert("RGB")
    return render_scene_frame(cur, local, sentences, tl, sb)


# ============================== 主流程 ==============================
def main():
    ap = argparse.ArgumentParser(description="幕后音图解视频生成器 v3")
    ap.add_argument("--script", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="图解视频")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--preview", type=int, default=0, help="只渲染前 N 帧")
    args = ap.parse_args()

    script_path, audio, out = Path(args.script), Path(args.audio), Path(args.out)
    text = clean_script(script_path.read_text(encoding="utf-8"))
    sentences = split_sentences(text)
    if not sentences:
        sys.exit("稿子解析后为空")

    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", str(audio)],
                       capture_output=True, text=True)
    dur = float(r.stdout.strip())
    tl = timeline(sentences, dur)
    print(f"[1/5] 稿件 {len(sentences)} 句, 音频 {dur:.1f}s")

    if args.no_llm:
        sb = [rule_one(i, s, len(sentences)) for i, s in enumerate(sentences)]
        print("[2/5] 规则分镜")
    else:
        try:
            sb = llm_storyboard(sentences)
            print("[2/5] DeepSeek 分镜完成")
        except Exception as e:
            print(f"[2/5] LLM 分镜失败({e}), 回退规则")
            sb = [rule_one(i, s, len(sentences)) for i, s in enumerate(sentences)]

    sb_path = out.with_suffix(".v3storyboard.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    sb_path.write_text(json.dumps(
        [{"idx": i, "start": tl[i][0], "end": tl[i][1], "sentence": sentences[i], **sb[i]}
         for i in range(len(sentences))], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"      分镜已存: {sb_path}")

    frames_dir = TMP / f"frames_{uuid.uuid4().hex[:8]}"
    frames_dir.mkdir(parents=True, exist_ok=True)
    n = int((args.preview if args.preview else dur) * FPS)
    print(f"[3/5] 渲染 {n} 帧 @ {FPS}fps ...")
    for i in range(n):
        t = i / FPS
        img = render_frame(t, sentences, tl, sb)
        img.save(frames_dir / f"f_{i:05d}.png", "PNG")
        if i % 60 == 0 or i == n - 1:
            print(f"      渲染 {int(100 * (i + 1) / n)}%")
    print("[3/5] 渲染完成")

    print("[4/5] ffmpeg 合成 ...")
    mid = frames_dir / "mid.mp4"
    cmd = [FFMPEG, "-y", "-r", str(FPS), "-i", str(frames_dir / "f_%05d.png"),
           "-i", str(audio), "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-ar", "44100", "-shortest", str(mid)]
    rr = subprocess.run(cmd, capture_output=True, text=True)
    if rr.returncode != 0:
        sys.exit("合成失败:\n" + rr.stderr[-800:])

    intro = BASE / "covers/intro.mp4"
    if intro.exists():
        fc = ("[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
              "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1[v0];"
              "[1:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
              "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1[v1];"
              "[0:a]aresample=44100[a0];[1:a]aresample=44100[a1];"
              "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]")
        cmd2 = [FFMPEG, "-y", "-i", str(intro), "-i", str(mid), "-filter_complex", fc,
                "-map", "[v]", "-map", "[a]", "-pix_fmt", "yuv420p", str(out)]
        rr2 = subprocess.run(cmd2, capture_output=True, text=True)
        if rr2.returncode != 0:
            sys.exit("片头拼接失败:\n" + rr2.stderr[-800:])
        print("[5/5] 已拼品牌片头")
    else:
        mid.replace(out)
        print("[5/5] 无片头, 直接输出")

    shutil.rmtree(frames_dir, ignore_errors=True)
    print(f"\n成品: {out}  ({out.stat().st_size // 1024} KB)")
    print(f"分镜审查: {sb_path}")

if __name__ == "__main__":
    main()
