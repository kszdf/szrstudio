#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_motion_video_v2.py — 幕后音模式图解视频生成器（v2 增强版）

相对 v1 的增强:
  · 分层动态背景: 渐变底 + 呼吸光晕 + 缓慢旋转角落光环(每帧都"活")
  · 情绪配色: 分镜打 tone(risk/safe/neutral), 风险=红 安全=绿 中性=金, 全片主色随内容切换
  · 卡片容器: 圆角半透明面板 + 顶部强调条, 不再是裸文字, 有层次
  · 关键词高亮: 分镜标 highlight, 句中重点词金色脉冲强调
  · 7 种模板: 原 5 + steps(步骤时间轴) + stat2(双数字对比大卡)
  · 多样化入场: fade / 上滑 / 左滑 / 弹入(back-ease), 不再全是淡入
  · Ken Burns 转场: 场景切换整体轻微缩放平移, 不是简单 blend
  · 持续微动效: 光晕呼吸 + 关键词脉动 + 数字到位后弹一下
  · 严格分区: 卡片工作区 y∈[300,1300], 字幕安全区 y∈[1450,1850], 永不重叠

接口与 v1 完全一致:
  D:/heygem/py310/Scripts/python.exe make_motion_video_v2.py \
      --script 稿.md --audio 音频.wav --out 成品.mp4 --title 暂估成本
  --no-llm   规则分镜
  --preview N 只渲染前 N 帧(快速看风格)
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
FFMPEG = r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin/ffmpeg.exe"
FFPROBE = FFMPEG.replace("ffmpeg.exe", "ffprobe.exe")
TMP = BASE / "_tmp_motion_v2"
FONT = str(BASE / "fonts/simhei.ttf")

W, H = 1080, 1920
FPS = 30
ENTR = 0.55          # 入场动画时长(秒)
WORK_TOP, WORK_BOT = 300, 1300          # 卡片工作区
SUB_Y = H - 470                        # 字幕安全区顶端

TEMPLATES = ["bigtext", "number", "compare", "checklist",
             "statement", "steps", "stat2"]


# ============================== 配色(情绪) ==============================
def get_palette(tone):
    if tone == "risk":
        return dict(bg_top=(30, 16, 18), bg_bot=(52, 26, 30), accent=(239, 68, 68),
                    accent2=(252, 165, 165), glow=(239, 68, 68),
                    panel=(26, 14, 16), panel_line=(120, 40, 44))
    if tone == "safe":
        return dict(bg_top=(11, 26, 30), bg_bot=(16, 44, 50), accent=(34, 197, 94),
                    accent2=(134, 239, 172), glow=(34, 197, 94),
                    panel=(12, 30, 34), panel_line=(30, 100, 70))
    # neutral
    return dict(bg_top=(15, 23, 42), bg_bot=(30, 41, 59), accent=(245, 158, 11),
                accent2=(252, 211, 77), glow=(245, 158, 11),
                panel=(14, 22, 40), panel_line=(70, 90, 130))
PAL_DEFAULT = get_palette("neutral")

WHITE = (248, 250, 252)
MUTED = (148, 163, 184)
LINE = (51, 65, 85)


# ============================== 字体 ==============================
_F = {}
def font(size):
    if size not in _F:
        _F[size] = ImageFont.truetype(FONT, size)
    return _F[size]


# ============================== 缓动 ==============================
def ease(p):
    p = max(0.0, min(1.0, p))
    return 1 - (1 - p) ** 3

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
    big = re.split(r"(?<=[。！？])", text)
    result = []
    for b in big:
        b = b.strip()
        if not b:
            continue
        subs = re.split(r"(?<=，)", b)
        buf = ""
        for s in subs:
            s = s.strip()
            if not s:
                continue
            if len(s) < 5 and buf:
                buf += s
            else:
                if buf:
                    result.append(buf)
                buf = s
        if buf:
            result.append(buf)
    return result

def wrap(seg, n=12):
    if len(seg) <= n:
        return [seg]
    return [seg[i:i + n] for i in range(0, len(seg), n)]


# ============================== 背景(动态) ==============================
_HALO = {}
_DEC = {}
def _make_halo(pal):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([W * 0.05, H * 0.02, W * 0.95, H * 0.62], fill=(*pal["glow"], 70))
    d.ellipse([W * 0.35, H * 0.45, W * 1.1, H * 1.15], fill=(*pal["glow"], 42))
    return img.filter(ImageFilter.GaussianBlur(130))

def _make_dec(pal):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 左上角大环
    d.arc([60, 60, 360, 360], 200, 320, fill=(*pal["accent"], 60), width=10)
    d.arc([80, 80, 330, 330], 20, 160, fill=(*pal["accent"], 35), width=6)
    # 右下角斜条
    d.line([(W - 40, H - 520), (W - 520, H - 40)], fill=(*pal["accent"], 28), width=8)
    d.arc([W - 380, H - 380, W - 80, H - 80], 30, 200, fill=(*pal["accent"], 45), width=8)
    return img

def _bg_base(pal):
    """静态渐变 + 品牌区 + 底部线(每帧 copy 底)。"""
    bg = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(bg)
    for y in range(H):
        t = y / H
        c = tuple(int(a + (b - a) * t) for a, b in zip(pal["bg_top"], pal["bg_bot"]))
        d.line([(0, y), (W, y)], fill=c)
    d.text((W // 2, 86), "老张讲财税", font=font(46), fill=MUTED, anchor="mm")
    d.line([(W // 2 - 180, 146), (W // 2 + 180, 146)], fill=pal["accent"], width=4)
    d.line([(80, H - 250), (W - 80, H - 250)], fill=pal["panel_line"], width=2)
    return bg

def dyn_bg(t, tone):
    """动态背景: 静态底 + 呼吸光晕 + 旋转角落光环。"""
    pal = get_palette(tone)
    if tone not in _HALO:
        _HALO[tone] = _make_halo(pal)
        _DEC[tone] = _make_dec(pal)
    img = _bg_base(pal).convert("RGBA")
    # 呼吸光晕
    breath = 0.55 + 0.45 * (0.5 + 0.5 * __import__("math").sin(t * 0.7))
    halo = _HALO[tone]
    sc = 1.0 + 0.06 * __import__("math").sin(t * 0.5)
    nw, nh = int(W * sc), int(H * sc)
    hl = halo.resize((nw, nh))
    off = ((W - nw) // 2 + int(40 * __import__("math").sin(t * 0.4)),
           (H - nh) // 2 + int(30 * __import__("math").cos(t * 0.4)))
    hb = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    hb.paste(hl, off)
    r, g, b, a = hb.split()
    a = a.point(lambda v: int(v * breath))
    hb = Image.merge("RGBA", (r, g, b, a))
    img = Image.alpha_composite(img, hb)
    # 旋转角落光环(每帧 6°/s)
    dec = _DEC[tone].rotate(t * 6, resample=Image.BICUBIC, center=(210, 210))
    dec2 = _DEC[tone].rotate(-t * 4, resample=Image.BICUBIC, center=(W - 230, H - 230))
    img = Image.alpha_composite(img, dec)
    img = Image.alpha_composite(img, dec2)
    return img.convert("RGB")


# ============================== 卡片容器/文字基元 ==============================
def draw_panel(d, pal, top=WORK_TOP, bot=WORK_BOT, title=None):
    d.rounded_rectangle([90, top, W - 90, bot], radius=36,
                        fill=pal["panel"], outline=pal["panel_line"], width=3)
    # 顶部强调条
    d.rounded_rectangle([90, top, W - 90, top + 14], radius=7, fill=pal["accent"])
    if title:
        d.text((W // 2, top + 70), title, font=font(60), fill=WHITE, anchor="mm")
        d.line([(W // 2 - 130, top + 120), (W // 2 + 130, top + 120)],
               fill=pal["accent"], width=6)

def draw_highlight(d, text, x, y, size, normal, hi, highlight, pulse=0.0):
    """逐字绘制, highlight 词用 hi(带脉冲提亮) 强调。anchor='lm'。"""
    f = font(size)
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


# ============================== 复合(入场转场) ==============================
def composite_card(bg, card, alpha, scale, ox, oy):
    if scale != 1.0 or ox or oy:
        nw, nh = max(1, int(W * scale)), max(1, int(H * scale))
        cl = card.resize((nw, nh))
        base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        base.paste(cl, ((W - nw) // 2 + ox, (H - nh) // 2 + oy))
        card = base
    if alpha < 1.0:
        r, g, b, a = card.split()
        a = a.point(lambda v: int(v * alpha))
        card = Image.merge("RGBA", (r, g, b, a))
    return Image.alpha_composite(bg.convert("RGBA"), card).convert("RGB")


# ============================== 7 种模板 ==============================
def tpl_bigtext(d, f, p, local, pal):
    text = str(f.get("text", ""))[:14]
    hl = f.get("highlight", "")
    size = 150 if len(text) <= 5 else (118 if len(text) <= 8 else 92)
    pulse = 0.5 + 0.5 * __import__("math").sin(local * 3)
    lines = wrap(text, 7)
    y0 = 760
    for i, ln in enumerate(lines):
        y = y0 + i * (size + 30)
        draw_highlight(d, ln, W // 2 - d.textlength(ln, font=font(size)) / 2,
                       y, size, WHITE, pal["accent2"], hl, pulse)
        lw = int(ease(p) * (d.textlength(ln, font=font(size)) + 40))
        if lw > 4:
            d.line([(W // 2 - lw // 2, y + size // 2 + 34),
                    (W // 2 + lw // 2, y + size // 2 + 34)],
                   fill=pal["accent"], width=10)

def tpl_number(d, f, p, local, pal):
    num = str(f.get("number", ""))
    label = str(f.get("label", ""))
    sub = str(f.get("sub", ""))
    hl = f.get("highlight", "")
    if label:
        draw_highlight(d, label, W // 2 - d.textlength(label, font=font(52)) / 2,
                       560, 52, MUTED, pal["accent2"], hl)
    m = re.match(r"^(\d+(?:\.\d+)?)(.*)$", num)
    shown = num
    if m:
        target = float(m.group(1))
        suffix = m.group(2)
        isint = "." not in m.group(1)
        cur = target * ease(p)
        shown = (f"{int(cur)}" if isint else f"{cur:.1f}") + suffix
    size = 168 if len(shown) <= 5 else (130 if len(shown) <= 8 else 100)
    # 到位弹一下
    pop = 1.0 + 0.05 * max(0.0, __import__("math").sin(local * 5)) * (1 - p)
    y = 900
    d.text((W // 2, y), shown, font=font(int(size * pop)),
           fill=pal["accent"], anchor="mm")
    d.line([(W // 2 - 170, y + 110), (W // 2 + 170, y + 110)],
           fill=pal["panel_line"], width=3)
    if sub:
        a = max(0.0, (p - 0.45) / 0.55)
        if a > 0:
            draw_highlight(d, sub[:18], W // 2 - d.textlength(sub[:18], font=font(44)) / 2,
                           1090, 44, MUTED, pal["accent2"], hl, a)

def tpl_compare(d, f, p, local, pal):
    p1 = ease(min(1.0, p / 0.55))
    p2 = ease(max(0.0, (p - 0.35) / 0.65))
    dx1 = int((1 - p1) * 200)
    dx2 = int((1 - p2) * 200)
    box1 = (130 - dx1, 560, W - 130 - dx1, 850)
    box2 = (130 + dx2, 920, W - 130 + dx2, 1210)
    d.rounded_rectangle(box1, radius=28, fill=(60, 26, 26), outline=(239, 68, 68), width=4)
    d.rounded_rectangle(box2, radius=28, fill=(16, 52, 36), outline=(34, 197, 94), width=4)
    d.text((box1[0] + 36, box1[1] + 50), "✗ " + str(f.get("left_title", "错误"))[:8],
           font=font(56), fill=(239, 68, 68), anchor="lm")
    d.text((box1[0] + 36, box1[1] + 140), str(f.get("left_sub", ""))[:15],
           font=font(42), fill=(252, 165, 165), anchor="lm")
    d.text((box2[0] + 36, box2[1] + 50), "✓ " + str(f.get("right_title", "正确"))[:8],
           font=font(56), fill=(34, 197, 94), anchor="lm")
    d.text((box2[0] + 36, box2[1] + 140), str(f.get("right_sub", ""))[:15],
           font=font(42), fill=(134, 239, 172), anchor="lm")

def tpl_checklist(d, f, p, local, pal):
    title = str(f.get("title", "关键要点"))
    hl = f.get("highlight", "")
    d.text((W // 2 - d.textlength(title, font=font(64)) / 2, 560),
           title, font=font(64), fill=WHITE, anchor="lm")
    d.line([(W // 2 - 150, 630), (W // 2 + 150, 630)], fill=pal["accent"], width=6)
    items = [str(x)[:12] for x in (f.get("items") or [])][:4]
    if not items:
        items = ["要点一", "要点二", "要点三"]
    step = 1.0 / max(len(items), 1)
    for i, it in enumerate(items):
        pi = ease((p - i * step * 0.7) / (step * 0.7 + 0.0001))
        if pi <= 0:
            continue
        dx = int((1 - pi) * 120)
        y = 780 + i * 150
        d.ellipse((150 + dx, y - 28, 206 + dx, y + 28), outline=pal["accent"], width=5)
        if pi > 0.55:
            d.line([(164 + dx, y), (181 + dx, y + 16)], fill=pal["accent"], width=6)
            d.line([(181 + dx, y + 16), (210 + dx, y - 15)], fill=pal["accent"], width=6)
        draw_highlight(d, it, 250 + dx, y, 50, WHITE, pal["accent2"], hl, pi)

def tpl_statement(d, f, p, local, pal):
    hl = f.get("highlight", "")
    pulse = 0.5 + 0.5 * __import__("math").sin(local * 3)
    d.text((120, 470), "“", font=font(170), fill=pal["accent"], anchor="lm")
    text = str(f.get("text", ""))[:26]
    lines = wrap(text, 11)
    y = 820
    for i, ln in enumerate(lines):
        draw_highlight(d, ln, W // 2 - d.textlength(ln, font=font(82)) / 2,
                       y + i * 108, 82, WHITE, pal["accent2"], hl, pulse)
    d.text((W // 2, y + len(lines) * 108 + 60), "—— 口播原文",
           font=font(36), fill=MUTED, anchor="mm")

def tpl_steps(d, f, p, local, pal):
    """步骤时间轴卡: 纵向步骤条, 逐条出现, 适合流程/条件。"""
    items = [str(x)[:14] for x in (f.get("items") or [])][:4]
    if not items:
        items = ["第一步", "第二步", "第三步"]
    hl = f.get("highlight", "")
    top = 600
    step_h = 150
    n = len(items)
    step = 1.0 / max(n, 1)
    for i, it in enumerate(items):
        pi = ease((p - i * step * 0.7) / (step * 0.7 + 0.0001))
        if pi <= 0:
            continue
        cy = top + i * step_h + 40
        # 连线
        if i < n - 1:
            d.line([(200, cy + 40), (200, cy + step_h)], fill=pal["panel_line"], width=4)
        # 节点圆
        d.ellipse((160, cy, 240, cy + 80), fill=pal["accent"])
        d.text((200, cy + 40), str(i + 1), font=font(52), fill=(10, 10, 10), anchor="mm")
        # 文案(滑入)
        dx = int((1 - pi) * 140)
        draw_highlight(d, it, 290 + dx, cy + 40, 56, WHITE, pal["accent2"], hl, pi)

def tpl_stat2(d, f, p, local, pal):
    """双数字对比大卡: 左(红,危险数字) 右(绿,安全数字) 中间箭头。"""
    lnum = str(f.get("left_num", ""))
    lsub = str(f.get("left_sub", ""))
    rnum = str(f.get("right_num", ""))
    rsub = str(f.get("right_sub", ""))
    title = str(f.get("title", ""))
    C = (0.5 + 0.5 * __import__("math").sin(local * 3))
    if title:
        d.text((W // 2 - d.textlength(title, font=font(56)) / 2, 540),
               title, font=font(56), fill=WHITE, anchor="lm")
    # 左
    d.text((W // 2 - 250, 800), lnum, font=font(150), fill=(239, 68, 68), anchor="mm")
    if lsub:
        d.text((W // 2 - 250, 950), lsub[:10], font=font(40), fill=(252, 165, 165), anchor="mm")
    # 右
    d.text((W // 2 + 250, 800), rnum, font=font(150), fill=(34, 197, 94), anchor="mm")
    if rsub:
        d.text((W // 2 + 250, 950), rsub[:10], font=font(40), fill=(134, 239, 172), anchor="mm")
    # 中间箭头(脉动)
    aw = 60 + int(20 * C)
    d.line([(W // 2 - 70, 800), (W // 2 + 70 - aw, 800)], fill=pal["accent2"], width=12)
    d.polygon([(W // 2 + 70 - aw, 800 - 30), (W // 2 + 70, 800),
               (W // 2 + 70 - aw, 800 + 30)], fill=pal["accent2"])

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
    return a, 1.0, 0, 0   # fade


# ============================== 分镜(LLM + 规则) ==============================
SB_PROMPT = """你是财税口播短视频的分镜导演。下面是一段口播稿按序号切好的句子。
为每个句子选图卡模板, 提取关键信息(数字/条款/对比项必须原样取), 并标出本句要强调的关键词、判断情绪基调。

可用模板与字段:
- bigtext: 大字冲击卡(开头钩子/警告/结论)。fields: {"text":"≤14字", "highlight":"句中重点词"}
- number: 数字冲击卡(具体数字/期限/金额)。fields: {"number":"原样如500万/5月31日", "label":"≤8字", "sub":"≤18字", "highlight":"重点"}
- compare: 对比卡(两种做法/后果)。fields: {"left_title":"≤6字(危险)", "left_sub":"≤15字", "right_title":"≤6字(正确)", "right_sub":"≤15字", "highlight":"重点"}
- checklist: 清单卡(多个条件/材料/步骤)。fields: {"title":"≤8字", "items":["≤12字",...](3-4项), "highlight":"重点"}
- statement: 引用/结论卡(事实/期限/法条)。fields: {"text":"≤26字原文", "highlight":"重点"}
- steps: 步骤时间轴卡(流程/先后顺序/条件链)。fields: {"items":["≤14字",...](3-4步), "highlight":"重点"}
- stat2: 双数字对比大卡(两个数字放一起对比, 如处罚vs合规)。fields: {"title":"≤10字", "left_num":"原样数字", "left_sub":"≤10字", "right_num":"原样数字", "right_sub":"≤10字"}

tone(情绪, 三选一): "risk"(讲风险/后果/处罚/警告) / "safe"(讲合规/正确做法/建议) / "neutral"(中性陈述)。

输出: 严格 JSON 数组, 每句一个元素, 不要解释或代码块:
[{"idx":0,"template":"bigtext","tone":"risk","highlight":"虚开","fields":{...}}, ...]

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
                        "highlight": item.get("highlight", ""),
                        "fields": item.get("fields") or {},
                        "anim": ANIM_DEFAULT.get(tpl, "fade")}
    for i in range(len(sentences)):
        if i not in out:
            out[i] = rule_one(i, sentences[i], len(sentences))
    return [out[i] for i in range(len(sentences))]

def rule_one(idx, sent, total):
    nums = re.findall(r"\d+(?:\.\d+)?万?", sent)
    if idx == 0 or idx == total - 1:
        tone = "risk" if re.search(r"怕|风险|罚|亏|坑|错", sent) else "neutral"
        return {"template": "bigtext", "tone": tone, "highlight": "",
                "fields": {"text": sent[:12]}, "anim": "pop"}
    if len(re.split(r"[、，]", sent)) >= 4:
        return {"template": "checklist", "tone": "neutral", "highlight": "",
                "fields": {"title": "关键要点",
                           "items": [x.strip()[:12] for x in re.split(r"[、，]", sent) if x.strip()][:4]},
                "anim": "slide_up"}
    if re.search(r"然后|接着|第一步|第二步|先|再|最后|流程|步骤", sent):
        return {"template": "steps", "tone": "safe", "highlight": "",
                "fields": {"items": [x.strip()[:14] for x in re.split(r"[、，]", sent) if x.strip()][:4] or ["按流程处理"]},
                "anim": "slide_left"}
    if nums:
        tone = "risk" if re.search(r"罚|倍|万|万以上|滞纳", sent) else "neutral"
        return {"template": "number", "tone": tone, "highlight": nums[0],
                "fields": {"number": nums[0], "label": "关键数字", "sub": sent[:16]},
                "anim": "pop"}
    if re.search(r"否则|不然|一旦|就是", sent):
        return {"template": "statement", "tone": "risk", "highlight": "",
                "fields": {"text": sent[:22]}, "anim": "fade"}
    return {"template": "statement", "tone": "neutral", "highlight": "",
            "fields": {"text": sent[:22]}, "anim": "fade"}


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


# ============================== 主流程 ==============================
def render_frame(t, sentences, tl, sb):
    cur = len(tl) - 1
    for k, (s0, s1) in enumerate(tl):
        if t < s1:
            cur = k
            break
    s0, s1 = tl[cur]
    local = max(0.0, t - s0)
    p = min(1.0, local / ENTR)
    tone = sb[cur].get("tone", "neutral")
    pal = get_palette(tone)
    img = dyn_bg(t, tone)
    # 卡片层
    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dc = ImageDraw.Draw(card)
    sc = sb[cur]
    TPLS[sc["template"]](dc, sc["fields"], p, local, pal)
    # Ken Burns: 场景内整体轻微缩放(入场阶段)
    kb = 1.0 + (1 - p) * 0.03
    alpha, scale, ox, oy = anim_params(sc.get("anim", "fade"), p)
    scale *= kb
    img = composite_card(img, card, alpha, scale, ox, oy)
    if t >= tl[0][0]:
        draw_subtitle(img, sentences[cur])
    return img

def main():
    ap = argparse.ArgumentParser(description="幕后音图解视频生成器 v2")
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

    sb_path = out.with_suffix(".v2storyboard.json")
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
