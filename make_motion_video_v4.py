#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_motion_video_v4.py — 幕后音模式图解视频生成器（v4 · 智能生图版）

核心升级(相对 v3):
  · 真·智能生图: 每幕由 DeepSeek 生成「画面意象描述(image_prompt)」,
    调通义万相(wanx)生成语义匹配的写实插画, 作为视频主视觉底图(不再是渐变+小图标)。
  · 文字退居浮层: 插画主导画面, 文字只做 顶部精炼标题 + 关键数字(代码叠加保证准确) + 底部字幕。
  · 暗化层保证可读: 顶部/底部渐变黑带, 让浮层文字在任何插画上都清晰。
  · 准确性: image_prompt 强制无文字; 数字/金额仍代码绘制, 不靠生图。
  · 生图缓存: 按 prompt 哈希缓存, 同稿重跑不重复烧钱/耗时; 失败自动降级为渐变占位。

接口:
  D:/heygem/py310/Scripts/python.exe make_motion_video_v4.py \
      --script 稿.md --audio 音频.wav --out 成品.mp4 --title 暂估成本
  --no-llm   规则分镜(不调 DeepSeek)
  --no-gen   跳过万相生图, 用渐变占位(调试渲染/转场用, 不联网)
  --regen    强制重新生图(忽略缓存)
"""
import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
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
TMP = BASE / "_tmp_motion_v4"
WANX_CACHE = BASE / "_wanx_cache"
WANX_MODEL = "wanx2.1-t2i-turbo"

HEI = str(BASE / "fonts/simhei.ttf")
SANS_B = r"C:/Windows/Fonts/msyhbd.ttc"
SERIF = r"C:/Windows/Fonts/NotoSerifSC-VF.ttf"

W, H = 1080, 1920
FPS = 30
TRANS = 0.7          # 转场时长(秒), 加长到能看清
ENTR = 0.6           # 入场动画时长(秒)
SUB_Y = H - 200      # 字幕基准 y

# 转场类型(按场景序号循环, 制造节奏变化, 破解"每幕都一样"的单调)
TRANS_TYPES = ["wipe_lr", "wipe_tb", "zoom", "fade", "slide_lr", "iris",
               "blur_fade", "flash", "push", "soft_rotate", "glitch", "luma"]
# 插画风格轮替(每幕换一种观感, 但保持财税专业家族感, 不撞款)
# 统一约束：深色专业底 + 金色点缀 + 写实/数据化，杜绝卡通化、低幼感
IMG_STYLES = [
    "写实金融办公场景插画，深色专业背景，暖色台灯光影，景深虚实，商务权威",
    "财经数据可视化信息图，深蓝底色配金色数据图表与折线，克制专业",
    "半色调双色 duotone 财经插画，深蓝金双色，现代克制",
    "写实电影感插画，深色背景景深与明暗光影，金融氛围",
    "深色商务剪影插画，金色描边，沉稳大气，低饱和",
    "商务极简扁平插画，深灰蓝底大量留白，低饱和高级灰，克制专业",
    "3D 等距财经数据插画，深蓝金配色，专业立体，简洁",
]

# ============================== 字体 ==============================
_F = {}
def font(size, kind="hei"):
    key = (size, kind)
    if key not in _F:
        path = {"hei": HEI, "sans": SANS_B, "serif": SERIF}[kind]
        _F[key] = ImageFont.truetype(path, size, index=0)
    return _F[key]

# ============================== 配色(情绪, 用于降级占位/数字强调) ==============================
# ============================== 包装主题预设 (借鉴开拍"网感模板") ==============================
# 每套含 risk/safe/neutral 三档配色(保留风险红/合规绿语义) + 动效组合
STYLE_PRESETS = {
    "财经严谨": {
        "risk":    dict(bg_top=(30, 14, 16), bg_bot=(52, 24, 28), accent=(244, 63, 63), accent2=(251, 191, 191), glow=(190, 40, 40)),
        "safe":    dict(bg_top=(10, 26, 30), bg_bot=(16, 46, 50), accent=(16, 185, 129), accent2=(153, 246, 206), glow=(16, 150, 100)),
        "neutral": dict(bg_top=(14, 22, 42), bg_bot=(30, 40, 58), accent=(245, 158, 11), accent2=(254, 215, 110), glow=(200, 140, 30)),
        "entrance": "fade_scale", "dual_line": True, "kw_pop": True, "cover": "dark",
    },
    "带货活力": {
        "risk":    dict(bg_top=(34, 12, 22), bg_bot=(60, 20, 46), accent=(244, 63, 94), accent2=(252, 165, 195), glow=(200, 40, 90)),
        "safe":    dict(bg_top=(12, 30, 24), bg_bot=(20, 54, 42), accent=(16, 200, 140), accent2=(165, 250, 215), glow=(16, 160, 110)),
        "neutral": dict(bg_top=(26, 12, 44), bg_bot=(52, 24, 78), accent=(236, 72, 153), accent2=(249, 168, 212), glow=(200, 50, 130)),
        "entrance": "slide_in", "dual_line": True, "kw_pop": True, "cover": "vivid",
    },
    "简约高级": {
        "risk":    dict(bg_top=(34, 22, 24), bg_bot=(52, 34, 36), accent=(220, 90, 90), accent2=(240, 200, 200), glow=(180, 70, 70)),
        "safe":    dict(bg_top=(22, 34, 30), bg_bot=(36, 52, 46), accent=(90, 190, 160), accent2=(200, 235, 220), glow=(70, 160, 130)),
        "neutral": dict(bg_top=(24, 24, 28), bg_bot=(40, 40, 46), accent=(220, 220, 225), accent2=(170, 170, 180), glow=(130, 130, 140)),
        "entrance": "fade_scale", "dual_line": False, "kw_pop": True, "cover": "minimal",
    },
}
STYLE_NAME = "财经严谨"   # 全局, main 按 --style 覆盖

def get_palette(tone):
    pres = STYLE_PRESETS.get(STYLE_NAME, STYLE_PRESETS["财经严谨"])
    return pres.get(tone, pres["neutral"])

def _pop(local, appear, dur=0.20):
    """关键词弹入缩放因子: 1 -> 1.22 -> 1 (三角 overshoot), 借鉴开拍 pop 强调。"""
    x = (local - appear) / dur
    if x <= 0 or x >= 1:
        return 1.0
    return 1.0 + 0.22 * (1 - abs(2 * x - 1))

WHITE = (250, 251, 253)
MUTED = (200, 210, 222)

# 双声对话音色: 男=张老师克隆音(zhangc2), 女=江老师克隆音(jiangnv3), 均 cosyvoice-v3-plus
MALE_VOICE = "cosyvoice-v3-plus-zhangc2-28a7c3541e1c45518a03046c11baeb1d"
FEMALE_VOICE = "cosyvoice-v3-plus-jiangnv3-991b204c1d564ac7a60f0cb9a8fd78bd"

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

def wrap(seg, n=15):
    if len(seg) <= n:
        return [seg]
    return [seg[i:i + n] for i in range(0, len(seg), n)]


def _wrap_px(text, d, f, max_w):
    """按像素宽度换行（中文按字），防标题溢出屏幕。"""
    lines, cur = [], ""
    for ch in text or "":
        if d.textlength(cur + ch, font=f) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines

# ============================== 万相生图 ==============================
_WANX_LOCK = threading.Lock()
def wanx_image(prompt, api_key, size="720*1280", regen=False):
    """调通义万相生成插画, 返回本地 jpg 路径。命中缓存则跳过网络。"""
    WANX_CACHE.mkdir(parents=True, exist_ok=True)
    h = hashlib.md5(prompt.encode("utf-8")).hexdigest()
    cache = WANX_CACHE / f"{h}.jpg"
    if cache.exists() and not regen:
        return cache
    from dashscope import ImageSynthesis
    rsp = ImageSynthesis.call(model=WANX_MODEL, prompt=prompt, size=size, n=1,
                              api_key=api_key)
    if rsp.status_code == 200:
        url = rsp.output.results[0].url
        for _ in range(3):
            try:
                urllib.request.urlretrieve(url, cache)
                if cache.stat().st_size > 1000:
                    return cache
            except Exception:
                pass
        raise RuntimeError("下载生图失败")
    raise RuntimeError(f"wanx {rsp.status_code}: {rsp.message}")

def cover_resize(img, tw, th):
    iw, ih = img.size
    scale = max(tw / iw, th / ih)
    nw, nh = int(iw * scale), int(ih * scale)
    img = img.resize((nw, nh), Image.LANCZOS)
    return img.crop(((nw - tw) // 2, (nh - th) // 2,
                     (nw - tw) // 2 + tw, (nh - th) // 2 + th))

def zoom(img, scale):
    w, h = img.size
    nw, nh = int(w * scale), int(h * scale)
    img = img.resize((nw, nh), Image.LANCZOS)
    return img.crop(((nw - w) // 2, (nh - h) // 2,
                     (nw - w) // 2 + w, (nh - h) // 2 + h))

# 降级占位图(生图失败时用渐变, 不阻塞)
def fallback_img(tone):
    pal = get_palette(tone)
    bg = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(bg)
    for y in range(0, H, 2):
        t = y / H
        c = tuple(int(a + (b - a) * t) for a, b in zip(pal["bg_top"], pal["bg_bot"]))
        d.line([(0, y), (W, y)], fill=c)
    return bg.convert("RGBA")

# ============================== 暗化浮层(缓存) ==============================
_DARK_TOP = None
_DARK_BOT = None
def dark_overlay():
    global _DARK_TOP, _DARK_BOT
    if _DARK_TOP is None:
        top = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ga = Image.new("L", (1, 680))
        for y in range(680):
            ga.putpixel((0, y), int(210 * (1 - y / 680) ** 1.3))
        band = Image.new("RGBA", (W, 680), (0, 0, 0, 255))
        band.putalpha(ga.resize((W, 680)))
        top.paste(band, (0, 0))
        _DARK_TOP = top
        bot = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        gb = Image.new("L", (1, 680))
        for y in range(680):
            gb.putpixel((0, y), int(220 * (y / 680) ** 1.4))
        bandb = Image.new("RGBA", (W, 680), (0, 0, 0, 255))
        bandb.putalpha(gb.resize((W, 680)))
        bot.paste(bandb, (0, H - 680))
        _DARK_BOT = bot
    return _DARK_TOP, _DARK_BOT

# ============================== 文字浮层 ==============================
def draw_title(img, text, cx, cy, size, fill, anchor="mm"):
    """顶部标题渲染（带防溢出：按像素换行 + 超长自动缩字号，绝不溢出屏幕）。"""
    d = ImageDraw.Draw(img)
    max_w = W - 120          # 左右各留 60px 安全边距
    # 自适应字号：从 size 往下试，直到标题 ≤2 行且不超宽
    f = font(size, "serif")
    lines = _wrap_px(text, d, f, max_w)
    while (len(lines) > 2 or any(d.textlength(ln, font=f) > max_w for ln in lines)) and size > 40:
        size -= 6
        f = font(size, "serif")
        lines = _wrap_px(text, d, f, max_w)
    lines = lines[:2]        # 最多 2 行，超出截断
    line_h = int(size * 1.15)
    total_h = line_h * len(lines)
    start_y = cy - total_h // 2
    for i, ln in enumerate(lines):
        y = start_y + i * line_h
        d.text((cx + 4, y + 4), ln, font=f, fill=(0, 0, 0), anchor=anchor)
        d.text((cx, y), ln, font=f, fill=fill, anchor=anchor)

def draw_number(img, num, sub, cx, cy, accent, scale=1.0):
    d = ImageDraw.Draw(img)
    fn = font(190, "sans")
    if scale != 1.0:
        pad = 280
        layer = Image.new("RGBA", (2 * pad, 2 * pad), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        ld.text((pad, pad), num, font=fn, fill=accent, anchor="mm")
        nw, nh = int(2 * pad * scale), int(2 * pad * scale)
        layer = layer.resize((nw, nh), Image.LANCZOS)
        img.alpha_composite(layer, (cx - nw // 2, cy - nh // 2))
    else:
        d.text((cx + 5, cy + 5), num, font=fn, fill=(0, 0, 0), anchor="mm")
        d.text((cx, cy), num, font=fn, fill=accent, anchor="mm")
    if sub:
        fs = font(46, "hei")
        d.text((cx, cy + 150), sub, font=fs, fill=WHITE, anchor="mm")

def _split_fragments(line, keywords):
    """把一行按关键词切成 [(文本, 是否关键词)] 片段, 关键词作为一个整体不拆开。"""
    if not keywords:
        return [(line, False)]
    import re as _re
    pat = "(" + "|".join(_re.escape(k) for k in keywords if k) + ")"
    if len(pat) <= 2:
        return [(line, False)]
    parts = _re.split(pat, line)
    frags = []
    for p in parts:
        if not p:
            continue
        if any(p == k for k in keywords if k):
            frags.append((p, True))
        else:
            frags.append((p, False))
    return frags


def draw_text_fragments(d, text, cx, y, sz, pal, reveal=None, keywords=None,
                        local=0.0, scdur=3.0, total_chars=1, char_start=0, align="center"):
    """核心逐片段绘制: 非关键词白字黑描边; 关键词独立圆角胶囊(accent 底 + 白字), 二者不重叠。
    align='center' 整行居中; 'left' 从 cx 起向右排。返回本行占用宽度。
    关键词支持 pop 弹入(_pop), 弹入时胶囊与文字同步缩放。"""
    f = font(sz, "hei")
    line_w = d.textlength(text, font=f)
    x = (cx - line_w / 2) if align == "center" else cx
    kwpop = STYLE_PRESETS.get(STYLE_NAME, STYLE_PRESETS["财经严谨"]).get("kw_pop", True)
    frags = _split_fragments(text, keywords)
    pos = char_start
    for seg, is_kw in frags:
        seg_w = d.textlength(seg, font=f)
        if reveal is not None:
            vis = max(0, min(len(seg), int(reveal * total_chars) - pos))
            if vis <= 0:
                pos += len(seg); x += seg_w; continue
            if vis < len(seg):
                seg = seg[:vis]; seg_w = d.textlength(seg, font=f)
        if is_kw:
            appear = (pos / total_chars) * min(scdur, 0.5) if total_chars else 0
            scale = _pop(local, appear) if kwpop else 1.0
            kf = font(int(sz * scale), "hei")
            kw_w = d.textlength(seg, font=kf)
            pad_x, pad_y = sz * 0.16, sz * 0.14
            box = [x - pad_x, y - sz * 0.60 - pad_y, x + kw_w + pad_x, y + sz * 0.60 + pad_y]
            d.rounded_rectangle(box, radius=sz * 0.42, fill=pal["accent"])
            d.text((x, y), seg, font=kf, fill=WHITE, anchor="ls",
                   stroke_width=2, stroke_fill=(0, 0, 0))
            x += kw_w
        else:
            d.text((x, y), seg, font=f, fill=WHITE, anchor="ls",
                   stroke_width=7, stroke_fill=(0, 0, 0))
            x += seg_w
        pos += len(seg)
    return line_w


def draw_subtitle(img, text, pal, reveal=None, keywords=None, local=0.0, scdur=3.0):
    """底部字幕: 白字黑描边; 关键词独立胶囊高亮(不覆盖白字); 双行网感排版; 打字机逐字。"""
    pres = STYLE_PRESETS.get(STYLE_NAME, STYLE_PRESETS["财经严谨"])
    dual = pres.get("dual_line", True)
    d = ImageDraw.Draw(img)
    lines = wrap(text, 15)
    cx = W // 2
    total_chars = sum(len(l) for l in lines) or 1
    shown = (max(0, min(total_chars, int(reveal * total_chars))) if reveal is not None
             else total_chars)
    n = len(lines)
    lh = 74
    base_size = 52
    total_h = n * lh
    y0 = H - 150 - total_h + lh
    drawn = 0
    for i, ln in enumerate(lines):
        sz = base_size
        xoff = 0
        if dual and n == 2 and i == 1:      # 双行网感: 副行小一号且右错
            sz = 44
            xoff = 70
        y = y0 + i * lh
        cx_line = cx + xoff
        visible = ln[: max(0, shown - drawn)] if reveal is not None else ln
        if visible:
            draw_text_fragments(d, visible, cx_line, y, sz, pal,
                                reveal=None, keywords=keywords, local=local,
                                scdur=scdur, total_chars=total_chars,
                                char_start=drawn, align="center")
        drawn += len(ln)

# ============================== 分镜(LLM + 规则) ==============================
TEMPLATES = ["bigtext", "number", "compare", "checklist",
             "statement", "steps", "stat2"]

SB_PROMPT = """你是财税口播短视频的分镜导演。下面是一段口播稿按序号切好的句子。
为每个句子判断最适合的「视觉呈现类型(visual_type)」, 并给出对应内容。

核心原则（务必遵守，避免把视频做成枯燥的清单堆砌）:
1. 该是场景是场景, 该是表格是表格, 该是清单是清单, 不要一律生图。
2. 跨多句的「有序流程/步骤」(如"第一步盘点→接着函证→然后补凭证")——每一步单独成一张 step 卡并标 step_no, 严禁把整段流程压进一张 list 卡。
3. 对比/反比句(如"账上有的、实物没有, 那就是漏洞")用 scene, 不要拆成 list。
4. list 只用于「同一句内」的并列要点/注意事项; 凡需跨句顺承或对比的内容, 都用 step 或 scene, 绝不 list。

visual_type 取值:
- "scene": 讲「场景/情境/故事/人物/对比」, 用一张扁平矢量商务插画表现。给 image_prompt(扁平矢量商务插画描述, 干净克制专业, 无文字无数字无字母)。
- "table": 含「两组以上数字对照/税率/金额对照」, 必须用表格才清晰。给 table{"head":[列1,列2],"rows":[[值,值],...]}。
- "list": 仅限「单句内的并列要点/注意事项」, 给 items:[项1,项2,...](≤5项, 每项≤10字)。
- "step": 是「有序流程中的某一环」, 与上下句构成先后顺序。给 step_no(第几步, 从1起, 整数) + image_prompt(同 scene 要求)。此类型不写 items。
- "number": 聚焦「一个关键数字/日期/比率」, 给 highlight_num(原样如"500万"/"25%"/"5月31日") + num_sub(数字含义≤8字)。
- "quote": 真正短促有力的「警示语/金句/收口」(≤14字), 给 quote_text。同一视频连续 quote 不超过2张。

每句还要给: title(顶部精炼主标题≤12字, 口语化抓人, 不要千篇一律写"财税干货")、tone("risk"风险/"safe"合规/"neutral"中性")、keywords(本句最该强调的 1-3 个词, 每词≤4字, 用于视频里高亮强调, 如["暂估","成本"]; 没有给 [])。

判断优先级: 两组以上数字对照→table; 单句关键数字/日期→number; 单句内并列要点→list; 有序流程中的一环→step; 短促金句(且非以上)→quote; 讲人/事/情境/对比→scene。

输出: 严格 JSON 数组, 每句一个元素, 不要解释或代码块:
[{"idx":0,"visual_type":"scene","image_prompt":"...","title":"...","tone":"risk","keywords":["虚开","红线"]},
 {"idx":1,"visual_type":"step","step_no":1,"image_prompt":"...","title":"...","tone":"neutral"},
 {"idx":2,"visual_type":"table","table":{"head":["项目","税率"],"rows":[["一般纳税人","13%"],["小规模","3%"]]},"title":"...","tone":"neutral"},
 {"idx":3,"visual_type":"list","items":["合同","入库单","发票"],"title":"...","tone":"safe"},
 {"idx":4,"visual_type":"number","highlight_num":"25%","num_sub":"综合税负","title":"...","tone":"risk"},
 {"idx":5,"visual_type":"quote","quote_text":"这样列支才稳","title":"...","tone":"safe"}]

句子列表:
"""

def _llm_call(fn, prompt, cfg, timeout=90, retries=2):
    """带重试的 LLM 调用; 全部失败抛最后一个异常。"""
    last = None
    for _ in range(retries + 1):
        try:
            return fn(prompt, model=cfg["model"], key=cfg["key"],
                      base_url=cfg["base_url"], timeout=timeout)
        except Exception as e:  # 超时/网络/HTTP 错误均重试
            last = e
            time.sleep(2)
    raise last


SB_DIALOG_PROMPT = SB_PROMPT.replace(
    "你是财税口播短视频的分镜导演。下面是一段口播稿按序号切好的句子。",
    "你是财税口播短视频的分镜导演。下面是一段**男女对话稿**（女声提问/抛出场景，男声解答）按序号切好的句子。\n"
    "对话稿的视觉原则：普通一问一答保持 \"dialog\" 上下分屏气泡；只有**讲案例/故事/情境/对比**的句子才用 \"scene\" 配插画生图；"
    "数据对照仍用 table、关键数字用 number、短金句用 quote。\n"
    "新增 visual_type: \"dialog\" = 男女对话问答句（上下分屏+气泡台词），不给 image_prompt。"
).replace(
    "visual_type 取值:\n- \"scene\"",
    "visual_type 取值:\n- \"dialog\": 男女对话问答句(上下分屏+气泡), 不给 image_prompt\n- \"scene\"",
)


def llm_storyboard(sentences, dialogue=False):
    """用 LLM 生成「叙事感知」分镜: 区分 有序流程(step) / 并列清单(list) / 对比(scene),
    根治把内容拍平成一个清单卡的堆砌问题。带双模型降级 + 重试; 失败抛异常交由 main 回退规则。
    dialogue=True 时用对话感知提示词（scene 句接万相生图，问答句保持 dialog 气泡）。"""
    sys.path.insert(0, str(BASE))
    from model_providers import ensure_env, get_text_config, deepseek_chat
    ensure_env()
    listing = "\n".join(f"{i}. {s}" for i, s in enumerate(sentences))
    prompt_tpl = SB_DIALOG_PROMPT if dialogue else SB_PROMPT

    # 模型降级链: 优先配置默认(deepseek) → 失败试 qwen → 再失败抛异常
    used_provider = None
    try:
        cfg = get_text_config()            # 默认优先 deepseek
        used_provider = cfg["provider"]
        raw = _llm_call(deepseek_chat, prompt_tpl + listing, cfg, timeout=90)
    except Exception as e1:
        try:
            cfg = get_text_config(force_provider="qwen")
            used_provider = "qwen"
            raw = _llm_call(deepseek_chat, prompt_tpl + listing, cfg, timeout=90)
        except Exception as e2:
            raise RuntimeError(f"LLM 分镜双模型均失败: {e1} | {e2}")
    print(f"  [分镜模型] 使用 {used_provider} 成功")

    raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.M).strip()
    data = json.loads(raw)
    out = {}
    for item in data:
        idx = int(item.get("idx", -1))
        if 0 <= idx < len(sentences):
            vtype = str(item.get("visual_type", "scene"))
            if vtype not in ("scene", "table", "list", "step", "number", "quote", "dialog"):
                vtype = "scene"
            if vtype == "compare":          # 对比句渲染层走 scene
                vtype = "scene"
            sc = {
                "visual_type": vtype,
                "title": str(item.get("title", ""))[:14] or sentences[idx][:12],
                "tone": item.get("tone", "neutral"),
                "image_prompt": str(item.get("image_prompt", ""))[:200] or "专业财经扁平插画, 简洁商务",
                "highlight_num": str(item.get("highlight_num", ""))[:20],
                "num_sub": str(item.get("num_sub", ""))[:12],
                "quote_text": str(item.get("quote_text", ""))[:16],
                "step_no": int(item.get("step_no", 0) or 0),
                "keywords": [str(x)[:4] for x in (item.get("keywords") or [])][:3],
            }
            tb = item.get("table")
            if isinstance(tb, dict) and tb.get("head") and tb.get("rows"):
                sc["table"] = {"head": [str(x)[:8] for x in tb["head"][:3]],
                               "rows": [[str(c)[:12] for c in r][:3] for r in tb["rows"][:5]]}
            its = item.get("items")
            if isinstance(its, list) and its:
                sc["items"] = [str(x)[:12] for x in its[:5]]
            out[idx] = sc
    for i in range(len(sentences)):
        if i not in out:
            out[i] = rule_one(i, sentences[i], len(sentences))
    return [out[i] for i in range(len(sentences))]

# 规则兜底: 关键词 → 画面意象 + 标题
VISUAL_IMG = {
    "虚开": ("一个醒目的红色禁止符号压在虚假的账目纸张上, 拒绝与警示氛围", "虚开发票=红线"),
    "暂估": ("企业会计在电脑前纠结暂估成本的入账, 略带焦虑的沉思", "暂估成本有讲究"),
    "稽查": ("税务稽查员在明亮办公室仔细翻阅企业账本, 聚光灯打在账册上", "稽查一查一个准"),
    "查": ("放大镜检查财务报表细节, 严谨稽查氛围", "稽查盯着呢"),
    "补税": ("税单与滞纳金通知单铺在桌面, 红色印章醒目, 压力感", "补税+滞纳金"),
    "滞纳": ("日历翻到截止日, 时钟滴答, 时间紧迫感", "别过期限"),
    "期限": ("一本日历翻到关键日期页, 旁边放着发票, 时间节点感", "取票有期限"),
    "合规": ("一面坚固的盾牌散发绿色安全光晕, 守护企业财务, 安稳感", "这样列才稳"),
    "安全": ("盾牌与对勾, 绿色安全光晕, 安心合规", "合规就安心"),
    "老板": ("一位中小企业老板在办公桌前审视财务报表, 若有所思", "老板必看"),
    "发票": ("一叠整齐的增值税发票与凭证, 真实可信", "发票要齐"),
    "合同": ("正式的商业合同文档, 庄重专业", "合同是根"),
    "税局": ("税务局办公大楼, 庄重权威建筑", "税局在看着"),
    "风险": ("暗色背景中红色警示光束, 风险预警感", "这有风险"),
}
DEFAULT_IMG = ("专业财经内容写实插画, 沉稳商务氛围, 电影质感", "财税干货")

def rule_one(idx, sent, total):
    nums = re.findall(r"\d+(?:\.\d+)?万?%?", sent)
    tone = "risk" if re.search(r"怕|风险|罚|亏|坑|错|虚开|补税|滞纳|？|禁止|红线", sent) else "neutral"
    if re.search(r"合规|安全|正确|建议|应该|可以|稳", sent):
        tone = "safe"
    # 表格: 含对比词且有≥2个数字
    if re.search(r"率|对比|相比|相差|对照|比.*高|比.*低", sent) and len(nums) >= 2:
        return {"visual_type": "table", "title": "数据对比", "tone": tone,
                "table": {"head": ["项目", "数值"],
                           "rows": [["数值A", nums[0]], ["数值B", nums[1]]]}}
    # 数字大卡: 单一关键数字且句短
    if nums and len(sent) <= 22:
        return {"visual_type": "number", "title": sent[:12], "tone": tone,
                "highlight_num": nums[0], "num_sub": ""}
    # 五步法序列词 → step 流程卡(强调顺序推进, 不作并列平铺)
    if re.search(r"头一步|第一步|第二步|第三步|第四步|第五步|接着|然后|再补|收尾|最后一步|第[①②③④⑤]步", sent):
        title = re.sub(r"^(头一步|第一步|第二步|第三步|第四步|第五步|接着|然后|再补|收尾|最后一步)[，,，]?\s*", "", sent)
        return {"visual_type": "step", "title": (title or sent)[:20], "tone": tone, "step_no": 0}
    # 真并列列举 → list(仅明确列举引导词或收口总结词, 去掉"有顿号即list"的误判)
    ENUM_LEAD = ["清单", "要点", "步骤", "一是", "二是", "三是", "首先", "其次", "比如以下", "包括"]
    ENUM_TAIL = ["这都算", "这三处", "这类", "主要有", "分别是", "以下几类", "以下几处"]
    if any(k in sent for k in ENUM_LEAD) or any(t in sent for t in ENUM_TAIL):
        parts = re.split(r"[、，,]", sent)
        items = [p.strip()[:10] for p in parts if p.strip()][:5]
        if len(items) >= 2:
            return {"visual_type": "list", "title": sent[:12], "tone": tone, "items": items}
    # 默认场景
    img_p, title = DEFAULT_IMG
    for kw, (p, t) in VISUAL_IMG.items():
        if kw in sent:
            img_p, title = p, t
            break
    # 默认标题用句子实义前 14 字, 避免反复出现"财税干货"显单调
    if title == "财税干货":
        title = sent[:14]
    return {"visual_type": "scene", "image_prompt": img_p, "title": title, "tone": tone}

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
def gradient_bg(pal):
    """按情绪配色的渐变底图, 用于非 scene 类型(表格/清单/数字/金句)。"""
    bg = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(bg)
    for y in range(0, H, 2):
        t = y / H
        c = tuple(int(a + (b - a) * t) for a, b in zip(pal["bg_top"], pal["bg_bot"]))
        d.line([(0, y), (W, y)], fill=c)
    return bg.convert("RGBA")

def _render_scene(sc, pal, idx, local, scdur, base_img):
    """场景主视觉: 肯·伯恩斯运镜 + 4 种标题版式轮替, 破解单调。"""
    np_ = min(1.0, local / scdur) if scdur and scdur > 0 else 1.0
    kb = idx % 5
    if kb == 0:
        sca, dx, dy = 1.0 + 0.07 * np_, 0, 0
    elif kb == 1:
        sca, dx, dy = 1.07 - 0.07 * np_, 0, 0
    elif kb == 2:
        sca, dx, dy = 1.14, int(70 * (0.5 - np_)), 0
    elif kb == 3:
        sca, dx, dy = 1.14, int(-70 * (0.5 - np_)), 0
    else:
        sca, dx, dy = 1.0, 0, 0
    if base_img:
        img = kb_zoom(base_img, sca, dx, dy).convert("RGBA")
    else:
        img = fallback_img(sc.get("tone", "neutral"))
    dt, db = dark_overlay()
    img = Image.alpha_composite(img, dt)
    img = Image.alpha_composite(img, db)
    # 4 种标题版式轮替
    layout = idx % 4
    title = sc.get("title", "")
    d = ImageDraw.Draw(img)
    if layout == 0:
        ty = 360
        draw_title(img, title, W // 2, ty, 88, WHITE)
        d.line([(W // 2 - 150, ty + 80), (W // 2 + 150, ty + 80)], fill=pal["accent"], width=6)
    elif layout == 1:
        tx, ty = 120, 330
        draw_title(img, title, tx, ty, 82, WHITE, anchor="la")
        d.rectangle([tx - 28, ty - 70, tx - 14, ty + 70], fill=pal["accent"])
    elif layout == 2:
        ty = H - 430
        panel = Image.new("RGBA", (W - 160, 200), (8, 12, 22, 175))
        img.alpha_composite(panel, (80, ty - 90))
        draw_title(img, title, W // 2, ty, 76, WHITE)
    else:
        d.text((W // 2, 250), "财税图解", font=font(40, "hei"), fill=pal["accent2"], anchor="mm")
        card = Image.new("RGBA", (W - 200, 260), (8, 12, 22, 150))
        img.alpha_composite(card, (100, 320))
        draw_title(img, title, W // 2, 450, 84, WHITE)
    # 角落装饰几何(accent 低透明), 交替方位增加节奏
    if idx % 2 == 0:
        d.ellipse([W - 230, 120, W - 90, 260], outline=pal["accent"], width=8)
    else:
        for k in range(3):
            d.line([(90 + k * 26, H - 360), (90 + k * 26, H - 250)], fill=pal["accent"], width=6)
    return img

def _render_table(sc, pal, p, a):
    img = gradient_bg(pal)
    tb = sc.get("table", {"head": ["项目", "数值"], "rows": [["—", "—"]]})
    head = tb["head"][:3]
    rows = tb["rows"][:5]
    ncol, nrow = len(head), len(rows)
    px, py = 110, 540
    pw = W - 2 * px
    col_w = pw // ncol
    row_h = 132
    head_h = 116
    th = head_h + nrow * row_h + 80
    d = ImageDraw.Draw(img)
    img.alpha_composite(Image.new("RGBA", (pw, th), (12, 16, 26, 200)), (px, py))
    d.rounded_rectangle([px, py, W - px, py + th], radius=28, outline=pal["accent"], width=5)
    d.rounded_rectangle([px, py, W - px, py + 16], radius=6, fill=pal["accent"])
    hy = py + 58
    for c, htext in enumerate(head):
        cx = px + col_w * c + col_w // 2
        d.text((cx, hy), htext, font=font(46, "sans"), fill=WHITE, anchor="mm")
    d.line([(px + 20, py + head_h - 8), (W - px - 20, py + head_h - 8)], fill=pal["accent2"], width=3)
    ry = py + head_h + row_h // 2
    for r, row in enumerate(rows):
        rowcol = (255, 255, 255, 22) if r % 2 == 0 else (0, 0, 0, 45)
        d.rectangle([px + 20, ry - row_h // 2 + 14, W - px - 20, ry + row_h // 2 - 14], fill=rowcol)
        for c, cell in enumerate(row[:ncol]):
            cx = px + col_w * c + col_w // 2
            is_num = (c == ncol - 1) and bool(re.match(r"^[\d.万%]+", cell))
            col = pal["accent"] if is_num else WHITE
            sz = 56 if is_num else 42
            d.text((cx, ry), cell, font=font(sz, "sans" if is_num else "hei"), fill=col, anchor="mm")
        ry += row_h
    draw_title(img, sc.get("title", ""), W // 2, 360, 80, WHITE)
    return img

def _render_list(sc, pal, p, a):
    img = gradient_bg(pal)
    items = sc.get("items", ["—"])[:5]
    n = len(items)
    px, py = 120, 470
    item_h, gap = 122, 26
    th = n * item_h + (n - 1) * gap + 150
    d = ImageDraw.Draw(img)
    img.alpha_composite(Image.new("RGBA", (W - 2 * px, th), (12, 16, 26, 190)), (px, py))
    d.rounded_rectangle([px, py, W - px, py + th], radius=26, outline=pal["accent"], width=4)
    draw_title(img, sc.get("title", ""), W // 2, 330, 80, WHITE)
    top = py + 90
    for i, it in enumerate(items):
        y = top + i * (item_h + gap) + item_h // 2
        cx = px + 95
        d.ellipse([cx - 40, y - 40, cx + 40, y + 40], fill=pal["accent"], outline=pal["accent2"], width=5)
        d.line([(cx - 18, y), (cx - 3, y + 15), (cx + 22, y - 18)], fill=(255, 255, 255), width=10)
        d.text((cx + 88, y), it, font=font(50, "hei"), fill=WHITE, anchor="lm")
    return img

def _render_step(sc, pal, idx, local, scdur, base_img):
    """五步法流程卡: 左侧大序号 + 右侧步骤名, 强调顺序推进(不作并列平铺)。"""
    np_ = min(1.0, local / scdur) if scdur and scdur > 0 else 1.0
    kb = idx % 5
    if kb == 0:
        sca, dx, dy = 1.0 + 0.07 * np_, 0, 0
    elif kb == 1:
        sca, dx, dy = 1.07 - 0.07 * np_, 0, 0
    elif kb == 2:
        sca, dx, dy = 1.14, int(70 * (0.5 - np_)), 0
    elif kb == 3:
        sca, dx, dy = 1.14, int(-70 * (0.5 - np_)), 0
    else:
        sca, dx, dy = 1.0, 0, 0
    if base_img:
        img = kb_zoom(base_img, sca, dx, dy).convert("RGBA")
    else:
        img = fallback_img(sc.get("tone", "neutral"))
    dt, db = dark_overlay()
    img = Image.alpha_composite(img, dt)
    img = Image.alpha_composite(img, db)
    d = ImageDraw.Draw(img)
    sn = sc.get("step_no", 1)
    st = sc.get("step_total", sn)
    # 顶部步骤进度条: 强调"有序推进"而非并列平铺
    bx, by, bw = 120, 130, W - 240
    d.text((bx, by - 64), f"第 {sn} / {st} 步", font=font(44, "hei"), fill=WHITE, anchor="lm")
    d.rounded_rectangle([bx, by, bx + bw, by + 22], radius=11, fill=(255, 255, 255, 45))
    fillw = int(bw * (sn / st)) if st else bw
    d.rounded_rectangle([bx, by, bx + fillw, by + 22], radius=11, fill=pal["accent"])
    for k in range(1, st + 1):
        px = bx + bw * (k - 1) // max(1, st - 1) if st > 1 else bx + bw // 2
        col = pal["accent"] if k <= sn else (255, 255, 255, 80)
        d.ellipse([px - 13, by - 13, px + 13, by + 35], fill=col)
    d.text((235, 470), f"{sn:02d}", font=font(300, "hei"), fill=pal["accent"], anchor="mm")
    d.text((235, 848), "STEP", font=font(70, "hei"), fill=(255, 255, 255), anchor="mm")
    d.line([(520, 430), (520, 910)], fill=pal["accent"], width=6)
    draw_title(img, sc.get("title", ""), 770, 570, 74, WHITE)
    return img

def _render_number(sc, pal, p, a):
    img = gradient_bg(pal)
    draw_number(img, sc.get("highlight_num", ""), sc.get("num_sub", ""), W // 2, 780,
                pal["accent"], scale=0.9 + 0.1 * a)
    draw_title(img, sc.get("title", ""), W // 2, 360, 80, WHITE)
    return img

def _render_quote(sc, pal, p, a):
    img = gradient_bg(pal)
    d = ImageDraw.Draw(img)
    d.rectangle([W // 2 - 370, 600, W // 2 - 330, 960], fill=pal["accent"])
    qt = sc.get("quote_text", "")
    f = font(96, "serif")
    lines = wrap(qt, 8)
    yy = 750 - (len(lines) - 1) * 60
    for ln in lines:
        d.text((W // 2 + 3, yy + 3), ln, font=f, fill=(0, 0, 0), anchor="mm")
        d.text((W // 2, yy), ln, font=f, fill=WHITE, anchor="mm")
        yy += 120
    draw_title(img, sc.get("title", ""), W // 2, 360, 80, WHITE)
    return img

def _avatar(d, cx, cy, r, label, accent, active):
    """简洁圆形头像占位: 圆底 + 角色色环 + 首字; active 时加粗环表示正在说话。"""
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(18, 24, 38),
              outline=accent, width=10 if active else 4)
    if active:                                  # 正在说话: 外圈柔光
        d.ellipse([cx - r - 12, cy - r - 12, cx + r + 12, cy + r + 12],
                  outline=accent, width=4)
    f = font(int(r * 0.95), "hei")
    d.text((cx, cy), label, font=f, fill=WHITE, anchor="mm",
           stroke_width=3, stroke_fill=(0, 0, 0))


def _render_dialog(sc, pal, idx, local, scdur, role, sentence):
    """男女对话: 上下分屏, 当前说话人侧亮 + 名牌 + 头像, 另一侧压暗;
    底部气泡台词(角色名标签 + 关键词胶囊)。对话模式不调万相生图, 纯代码绘制。"""
    img = gradient_bg(pal)
    d = ImageDraw.Draw(img)
    speaker = role                       # "F" / "M"
    female_active = (speaker == "F")
    male_active = (speaker == "M")

    # 上方: 江老师(女)
    _avatar(d, W // 2, 320, 120, "江", (255, 120, 170), female_active)
    draw_title(img, "江老师 · 财税顾问", W // 2, 500, 50,
               (255, 150, 190) if female_active else (150, 152, 162), anchor="mm")
    # 下方: 张老师(男)
    _avatar(d, W // 2, H // 2 + 320, 120, "张", (245, 158, 11), male_active)
    draw_title(img, "张老师 · 财税专家", W // 2, H // 2 + 500, 50,
               (245, 190, 80) if male_active else (150, 152, 162), anchor="mm")

    # 压暗非说话侧
    if not female_active:
        d.rectangle([0, 0, W, H // 2], fill=(0, 0, 0, 120))
    if not male_active:
        d.rectangle([0, H // 2, W, H], fill=(0, 0, 0, 120))
    # 中间分隔条
    d.rectangle([0, H // 2 - 4, W, H // 2 + 4], fill=pal["accent"])

    # 底部气泡
    bw, bh = W - 140, 380
    bx, by = 70, H - 440
    d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=30,
                        fill=(245, 247, 252, 238), outline=pal["accent"], width=5)
    tag = "江老师" if speaker == "F" else "张老师"
    tag_col = (214, 56, 116) if speaker == "F" else (198, 134, 16)
    d.rounded_rectangle([bx + 28, by + 26, bx + 28 + 150, by + 26 + 56],
                        radius=18, fill=tag_col)
    d.text((bx + 103, by + 54), tag, font=font(38, "hei"), fill=WHITE, anchor="mm")
    # 台词(气泡内, 左对齐, 关键词胶囊, 角色色)
    pal2 = dict(pal); pal2["accent"] = tag_col
    lines = wrap(sentence, 15)
    ly = by + 130
    for ln in lines[:3]:
        if ln.strip():
            draw_text_fragments(d, ln, bx + 36, ly, 46, pal2,
                                reveal=None, keywords=sc.get("keywords"),
                                local=local, scdur=scdur,
                                total_chars=max(1, len(sentence)),
                                char_start=0, align="left")
        ly += 70
    return img


def render_scene_frame(idx, local, sentences, tl, sb, imgs, trans=False):
    """按 visual_type 分支渲染某场景帧, 再做统一入场 + 底部字幕。
    trans=True 表示正处转场中, 入场交给转场处理, 避免重复动效(克制原则)。"""
    sc = sb[idx]
    vtype = sc.get("visual_type", "scene")
    pal = get_palette(sc.get("tone", "neutral"))
    p = min(1.0, local / ENTR)
    a = ease(p)
    scdur = (tl[idx][1] - tl[idx][0]) if idx < len(tl) else 3.0
    if vtype == "dialog":
        # 对话模式: 上下分屏 + 气泡台词, 字幕已在 _render_dialog 内绘制
        role = sc.get("role", "M")
        return _render_dialog(sc, pal, idx, local, scdur, role, sentences[idx]).convert("RGB")
    if vtype == "scene":
        img = _render_scene(sc, pal, idx, local, scdur, imgs[idx])
    elif vtype == "table":
        img = _render_table(sc, pal, p, a)
    elif vtype == "list":
        img = _render_list(sc, pal, p, a)
    elif vtype == "step":
        img = _render_step(sc, pal, idx, local, scdur, imgs[idx])
    elif vtype == "number":
        img = _render_number(sc, pal, p, a)
    elif vtype == "quote":
        img = _render_quote(sc, pal, p, a)
    else:
        img = _render_scene(sc, pal, idx, local, scdur, imgs[idx])
    # 统一入场(转场进行中时跳过, 交给转场)
    if not trans:
        estyle = STYLE_PRESETS.get(STYLE_NAME, STYLE_PRESETS["财经严谨"]).get("entrance", "fade_scale")
        if estyle == "slide_in":
            off = int((1 - a) * 60)
            moved = Image.new("RGBA", (W, H))
            moved.alpha_composite(img.convert("RGBA"), (off, 0))
            img = moved
        else:  # fade_scale
            sca = 1.0 + (1 - a) * 0.04
            if sca != 1.0:
                nw, nh = int(W * sca), int(H * sca)
                resized = img.resize((nw, nh), Image.LANCZOS)
                framed = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                framed.alpha_composite(resized, ((W - nw) // 2, (H - nh) // 2))
                img = framed
        if a < 1:
            fade = Image.new("RGBA", (W, H), (0, 0, 0, int(120 * (1 - a))))
            img = Image.alpha_composite(img, fade)
    # 字幕: scene/step 用打字机逐字(旁白同步感), 其余淡入; 关键词高亮
    kw = sc.get("keywords")
    if vtype in ("scene", "step"):
        draw_subtitle(img, sentences[idx], pal, reveal=p, keywords=kw, local=local, scdur=scdur)
    else:
        draw_subtitle(img, sentences[idx], pal, reveal=None, keywords=kw, local=local, scdur=scdur)
    return img.convert("RGB")

def wipe_mask(prog):
    m = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(m)
    edge = int(W * ease_inout(prog))
    soft = 120
    for x in range(W):
        v = 0
        if x < edge - soft:
            v = 255
        elif x < edge:
            v = int(255 * (x - (edge - soft)) / soft)
        d.line([(x, 0), (x, H)], fill=v)
    return m

def wipe_mask_tb(prog):
    m = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(m)
    edge = int(H * ease_inout(prog))
    soft = 140
    for y in range(H):
        v = 0
        if y < edge - soft:
            v = 255
        elif y < edge:
            v = int(255 * (y - (edge - soft)) / soft)
        d.line([(0, y), (W, y)], fill=v)
    return m

def iris_mask(prog):
    m = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(m)
    r = int(max(W, H) * 0.72 * ease_inout(prog))
    d.ellipse([W // 2 - r, H // 2 - r, W // 2 + r, H // 2 + r], fill=255)
    return m.filter(ImageFilter.GaussianBlur(60))

def kb_zoom(img, scale, dx=0, dy=0):
    """等比缩放并平移取景, 用于肯·伯恩斯运镜。"""
    w, h = img.size
    nw, nh = int(w * scale), int(h * scale)
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - W) // 2 - dx
    top = (nh - H) // 2 - dy
    return img.crop((left, top, left + W, top + H))

def _composite_masked(prev, cur, mask_or_none, alpha=255):
    """把 cur 按 mask(或整体 alpha) 盖到 prev 上。"""
    cur_a = cur.convert("RGBA")
    r, g, b, c = cur_a.split()
    if mask_or_none is not None:
        newc = ImageChops.multiply(c, mask_or_none)
    else:
        newc = ImageChops.multiply(c, Image.new("L", (W, H), alpha))
    masked = Image.merge("RGBA", (r, g, b, newc))
    return Image.alpha_composite(prev.convert("RGBA"), masked).convert("RGB")

def apply_transition(prev, cur, prog, ttype):
    """把 cur 按转场类型盖到 prev 上, prog∈[0,1]。"""
    p = ease_inout(prog)
    if ttype == "wipe_tb":
        return _composite_masked(prev, cur, wipe_mask_tb(prog))
    if ttype == "iris":
        return _composite_masked(prev, cur, iris_mask(prog))
    if ttype == "fade":
        return _composite_masked(prev, cur, None, int(255 * p))
    if ttype == "zoom":
        sca = 1.14 - 0.14 * p
        nw, nh = int(W * sca), int(H * sca)
        zd = cur.resize((nw, nh), Image.LANCZOS).convert("RGBA")
        # 居中裁剪回 W×H, 避免 alpha_composite 的偏移/尺寸不匹配
        left, top = (nw - W) // 2, (nh - H) // 2
        zd = zd.crop((left, top, left + W, top + H))
        return _composite_masked(prev, zd, None, int(255 * p))
    if ttype == "slide_lr":
        off = int(W * (1 - p))
        cm = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        cm.alpha_composite(cur.convert("RGBA"), (off, 0))
        return Image.alpha_composite(prev.convert("RGBA"), cm).convert("RGB")
    if ttype == "blur_fade":
        prev_b = prev.convert("RGBA").filter(ImageFilter.GaussianBlur(10 * (1 - p)))
        cur_b = cur.convert("RGBA").filter(ImageFilter.GaussianBlur(10 * p))
        return _composite_masked(prev_b, cur_b, None, int(255 * p))
    if ttype == "flash":
        base = _composite_masked(prev, cur, None, int(255 * p))
        intensity = int(200 * (1 - abs(2 * p - 1)))
        if intensity > 0:
            base = Image.alpha_composite(
                base.convert("RGBA"),
                Image.new("RGBA", (W, H), (255, 255, 255, intensity))).convert("RGB")
        return base
    if ttype == "push":
        off = int(W * (1 - p))
        pm = Image.new("RGBA", (W, H))
        pm.alpha_composite(prev.convert("RGBA"), (-off, 0))
        cm = Image.new("RGBA", (W, H))
        cm.alpha_composite(cur.convert("RGBA"), (off, 0))
        return Image.alpha_composite(pm, cm).convert("RGB")
    if ttype == "soft_rotate":
        ang = (1 - p) * 9
        rc = cur.convert("RGBA").rotate(ang, resample=Image.BICUBIC, center=(W // 2, H // 2))
        return _composite_masked(prev, rc, None, int(255 * p))
    if ttype == "glitch":
        base = _composite_masked(prev, cur, None, int(255 * p))
        if 0.15 < p < 0.85:
            curc = cur.convert("RGBA")
            r, g, b, al = curc.split()
            sh = int(26 * (1 - abs(2 * p - 1)))
            r2 = ImageChops.offset(r, sh, 0)
            g2 = ImageChops.offset(g, -sh, 0)
            gl = Image.merge("RGBA", (r2, g2, b, al))
            base = Image.alpha_composite(base.convert("RGBA"), gl).convert("RGB")
        return base
    if ttype == "luma":
        base = _composite_masked(prev, cur, None, int(255 * p))
        dip = int(70 * (1 - abs(2 * p - 1)))
        if dip > 0:
            base = Image.alpha_composite(
                base.convert("RGBA"),
                Image.new("RGBA", (W, H), (0, 0, 0, dip))).convert("RGB")
        return base
    # 默认 wipe_lr
    return _composite_masked(prev, cur, wipe_mask(prog))

def render_frame(t, sentences, tl, sb, imgs):
    cur = len(tl) - 1
    for k, (s0, s1) in enumerate(tl):
        if t < s1:
            cur = k
            break
    s0, s1 = tl[cur]
    local = max(0.0, t - s0)
    if cur > 0 and local < TRANS:
        ttype = TRANS_TYPES[(cur - 1) % len(TRANS_TYPES)]
        prev = render_scene_frame(cur - 1, (tl[cur - 1][1] - tl[cur - 1][0]) - 0.001,
                                  sentences, tl, sb, imgs, trans=True)
        curf = render_scene_frame(cur, local, sentences, tl, sb, imgs, trans=True)
        return apply_transition(prev, curf, local / TRANS, ttype)
    return render_scene_frame(cur, local, sentences, tl, sb, imgs)


# ============================== 对话模式(男女双声) ==============================
def parse_dialogue(text):
    """解析剧本: 以 女：/男：(或 江：/张：) 开头的行识别角色, 其余行续接上一句。
    返回 [{'role':'F'/'M','text':...}, ...]"""
    import re
    segs, cur = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^(女|男|江|张)\s*[：:]\s*(.*)$", line)
        if m:
            who, content = m.group(1), m.group(2).strip()
            role = "F" if who in ("女", "江") else "M"
            if cur and cur["role"] == role:
                cur["text"] += content
            else:
                cur = {"role": role, "text": content}
                segs.append(cur)
        else:
            if cur:
                cur["text"] += line
            else:
                cur = {"role": "M", "text": line}
                segs.append(cur)
    return [s for s in segs if s["text"].strip()]


def _synth_segment_subprocess(text, voice, out_path, timeout=90, retries=3):
    """子进程跑 synth_natural, 带超时(防 dashscope 网络卡死无限挂起) + 重试 + 断点续传。
    已知坑: jiangnv3 女声对 '那[，]?我' 邻接会返回异常小文件/None, 自动改写('那个，我')救活。
    返回 True=已得有效音频(含续传命中), False=彻底失败。"""
    import re as _re
    MIN_VALID = 6000          # 真实语音(即便只有'嗯')>6KB; 崩坏输出仅2-4KB
    op = Path(out_path)
    if op.exists() and op.stat().st_size > MIN_VALID:
        return True           # 续传命中, 跳过
    gpt_dir = str(BASE).replace("\\", "\\\\")
    candidates = [text]
    # 破 jiangnv3 女声坑：句首"那/那么"会返回异常小音频(0.05s)，去掉"那"；
    # "那...我"邻接 → "那个，我"
    fixed1 = _re.sub(r"^那么?[，。…、：；\s]*", "", text)
    if fixed1 != text and fixed1:
        candidates.append(fixed1)
    fixed2 = _re.sub(r"那[，。…、：；\s]*我", "那个，我", text)
    if fixed2 != text and fixed2 not in candidates:
        candidates.append(fixed2)
    last = ""
    for cand in candidates:
        code = (
            "import sys; sys.path.insert(0, r'%s');"
            "from qwen_tts import synth_natural;"
            "synth_natural(%r, %r, %r)"
        ) % (gpt_dir, cand, voice, str(out_path))
        for attempt in range(retries):
            try:
                r = subprocess.run([sys.executable, "-c", code], timeout=timeout,
                                   capture_output=True, text=True, cwd=str(BASE), check=False)
                if op.exists() and op.stat().st_size > MIN_VALID:
                    return True
                last = (r.stderr or r.stdout or "")[-300:]
            except subprocess.TimeoutExpired:
                last = f"超时({timeout}s)"
            try:                                    # 清半截/崩坏文件, 下次重来
                if op.exists() and op.stat().st_size <= MIN_VALID:
                    op.unlink()
            except OSError:
                pass
            time.sleep(1)
    print(f"  [!] 段落合成失败(已重试并改写): {text[:22]} | {last}")
    return False


def build_dialogue_audio(segments, out_wav, gap_ms=350):
    """逐轮用对应音色合成(子进程带超时) + 轮间静音, ffmpeg 拼接为单轨。
    断点续传: 已合成的 tN.wav 跳过(目录按 out 名确定, 重跑不重复烧额度)。
    返回 (总时长秒, 每轮时间轴 [(start,end),...])。"""
    key = hashlib.md5(str(out_wav).encode()).hexdigest()[:8]
    tmp_dir = TMP / f"dlg_{key}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    parts = []
    for i, seg in enumerate(segments):
        voice = FEMALE_VOICE if seg["role"] == "F" else MALE_VOICE
        p = str(tmp_dir / f"t{i}.wav")
        print(f"  [{i+1}/{len(segments)}] {seg['role']} 合成: {seg['text'][:20]}")
        ok = _synth_segment_subprocess(seg["text"], voice, p, timeout=90, retries=3)
        if not ok:
            raise RuntimeError(
                f"段落合成失败(已重试且尝试改写): {seg['text'][:30]}。"
                f"可重跑本命令自动续传已成功段, 仅重试此段。")
        r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1", p],
                           capture_output=True, text=True)
        parts.append((p, float(r.stdout.strip() or 1.0)))
    # 拼接(轮间插入静音)
    lines, cumul, tl = [], 0.0, []
    for i, (p, dur) in enumerate(parts):
        tl.append((cumul, cumul + dur))
        lines.append(f"file '{p.replace(chr(92), '/')}'")
        cumul += dur
        if i < len(parts) - 1:
            gp = str(tmp_dir / f"g{i}.wav")
            subprocess.run([FFMPEG, "-y", "-f", "lavfi",
                            "-i", f"anullsrc=r=22050:cl=mono:d={gap_ms/1000:.3f}",
                            "-c:a", "pcm_s16le", gp], capture_output=True, text=True)
            lines.append(f"file '{gp.replace(chr(92), '/')}'")
            cumul += gap_ms / 1000.0
    listf = tmp_dir / "list.txt"
    listf.write_text("\n".join(lines), encoding="utf-8")
    subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(listf),
                    "-c", "copy", str(out_wav)], capture_output=True, text=True)
    return cumul, tl


# ============================== 主流程 ==============================
def main():
    ap = argparse.ArgumentParser(description="幕后音图解视频生成器 v4 (智能生图版)")
    ap.add_argument("--script", default="", help="口播稿 md(与 --storyboard 二选一)")
    ap.add_argument("--audio", default="", help="音频文件(对话模式可省略, 自动双声生成)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="图解视频")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--no-gen", action="store_true", help="跳过生图, 渐变占位(调试)")
    ap.add_argument("--regen", action="store_true", help="强制重生生图")
    ap.add_argument("--preview", type=int, default=0, help="只渲染前 N 帧")
    ap.add_argument("--storyboard", default="", help="载入外部分镜JSON(含sentence/visual_type等), 跳过LLM与稿件解析")
    ap.add_argument("--style", default="财经严谨", help="包装主题预设: 财经严谨/带货活力/简约高级")
    ap.add_argument("--dialogue", action="store_true", help="对话模式: 脚本含 女：/男： 前缀, 自动双声拼接+上下分屏")
    args = ap.parse_args()
    global STYLE_NAME
    if args.style in STYLE_PRESETS:
        STYLE_NAME = args.style
    else:
        print(f"[警告] 未知 --style '{args.style}', 用默认 '财经严谨'")

    if not args.storyboard and not args.script:
        sys.exit("需提供 --script 稿件 或 --storyboard 分镜JSON 之一")

    out = Path(args.out)
    # 单实例互斥锁: 防后台重复启动造成写同一文件冲突
    import atexit
    _lock = out.with_suffix(".lock")
    try:
        _fd = os.open(str(_lock), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        os.close(_fd)
    except FileExistsError:
        sys.exit(f"[互斥] 检测到锁文件 {_lock}, 疑似已有实例运行或上次异常残留。确认无重复进程后删除该文件再运行。")
    atexit.register(lambda: os.path.exists(str(_lock)) and os.remove(str(_lock)))

    if args.dialogue:
        # ---- 对话模式: 解析 女:/男: 前缀 → 双声拼接 → 分镜 ----
        from model_providers import ensure_env
        ensure_env()
        raw = Path(args.script).read_text(encoding="utf-8")
        segs = parse_dialogue(raw)
        if not segs:
            sys.exit("对话模式未解析到对话(需以 女：/男： 开头)")
        dlg_audio = out.with_suffix(".dialogue.wav")
        dur, tl = build_dialogue_audio(segs, str(dlg_audio))
        args.audio = str(dlg_audio)
        sentences = [s["text"] for s in segs]
        # 分镜：先走 LLM 叙事分镜（让"场景/情境"句产出 scene+image_prompt，接万相生图），
        # 再给每一句补齐对话所需字段（role），渲染时 dialog 类型仍走上下分屏气泡台词。
        # LLM 失败时回退纯 dialog 分镜（不阻塞出片）。
        try:
            sb = llm_storyboard(sentences, dialogue=True)
            print("[2/6] 对话分镜(LLM 叙事 + 上下分屏 · 气泡台词)")
        except Exception as e:
            print(f"[2/6] LLM 分镜失败({e}), 回退纯对话分镜")
            sb = [{"visual_type": "dialog", "role": s["role"],
                   "tone": "neutral", "keywords": None} for s in segs]
        # 补齐对话角色字段（渲染 dialog 类型需要 role）
        for i, sc in enumerate(sb):
            sc["role"] = segs[i]["role"]
            # 对话句（无明确 scene 意象的）保持上下分屏气泡，避免 LLM 误判成 scene 后丢失台词感
            if sc.get("visual_type") in ("scene", "step") and not sc.get("image_prompt"):
                sc["visual_type"] = "dialog"
        print(f"[1/6] 对话 {len(segs)} 轮, 双声音频 {dur:.1f}s")
    elif args.storyboard:
        data = json.loads(Path(args.storyboard).read_text(encoding="utf-8"))
        sentences = [it.get("sentence", "") for it in data]
        sb = []
        for it in data:
            sc = {k: v for k, v in it.items()
                  if k not in ("sentence", "idx", "start", "end")}
            sb.append(sc)
        print(f"[2/6] 载入外部分镜 {len(sb)} 句")
    else:
        text = clean_script(Path(args.script).read_text(encoding="utf-8"))
        sentences = split_sentences(text)
        if not sentences:
            sys.exit("稿子解析后为空")

    if not args.dialogue:
        if not args.audio:
            sys.exit("非对话模式需提供 --audio 音频文件")
        r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1", str(args.audio)],
                           capture_output=True, text=True)
        dur = float(r.stdout.strip())
        tl = timeline(sentences, dur)
        print(f"[1/6] 稿件 {len(sentences)} 句, 音频 {dur:.1f}s")

        if args.storyboard:
            pass
        elif args.no_llm:
            sb = [rule_one(i, s, len(sentences)) for i, s in enumerate(sentences)]
            print("[2/6] 规则分镜")
        else:
            try:
                sb = llm_storyboard(sentences)
                print("[2/6] LLM 智能分镜完成")
            except Exception as e:
                print(f"[2/6] LLM 分镜失败({e}), 回退规则")
                sb = [rule_one(i, s, len(sentences)) for i, s in enumerate(sentences)]

    # 给 step 类型按出现顺序编号, 并记总数(顶部进度条用)
    step_total = sum(1 for _sc in sb if _sc.get("visual_type") == "step")
    _step = 0
    for _sc in sb:
        if _sc.get("visual_type") == "step":
            _step += 1
            _sc["step_no"] = _step
            _sc["step_total"] = step_total

    sb_path = out.with_suffix(".v4storyboard.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    sb_path.write_text(json.dumps(
        [{"idx": i, "start": tl[i][0], "end": tl[i][1], "sentence": sentences[i], **sb[i]}
         for i in range(len(sentences))], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"      分镜已存: {sb_path}")

    # 生图(仅 scene 类型调万相; dialogue 模式经 LLM 分镜后 scene 句同样生图)
    imgs = []
    scene_n = sum(1 for sc in sb if sc.get("visual_type") == "scene")
    if args.no_gen:
        print("[3/6] 跳过生图(渐变占位)")
        for sc in sb:
            imgs.append(None if sc.get("visual_type") != "scene" else fallback_img(sc.get("tone", "neutral")))
    else:
        from model_providers import ensure_env
        ensure_env()
        api_key = os.getenv("DASHSCOPE_API_KEY")
        done = 0
        print(f"[3/6] 通义万相生图 {scene_n} 张(仅 scene 类型) ...")
        for i, sc in enumerate(sb):
            if sc.get("visual_type") != "scene":
                imgs.append(None)   # 非 scene 用代码绘制, 不联网生图
                continue
            style = IMG_STYLES[i % len(IMG_STYLES)]
            prompt = sc["image_prompt"] + ("，" + style + "，低饱和商务配色，画面纯净无文字无字母无数字，竖版9:16构图")
            try:
                jpg = wanx_image(prompt, api_key, regen=args.regen)
                imgs.append(cover_resize(Image.open(jpg).convert("RGB"), W, H))
                done += 1
                print(f"      [{done}/{scene_n}] 生图OK: {sc['title']}")
            except Exception as e:
                print(f"      [{i+1}] 生图失败({e}), 降级占位")
                imgs.append(fallback_img(sc.get("tone", "neutral")))

    frames_dir = TMP / f"frames_{uuid.uuid4().hex[:8]}"
    frames_dir.mkdir(parents=True, exist_ok=True)
    n = int((args.preview if args.preview else dur) * FPS)
    print(f"[4/6] 渲染 {n} 帧 @ {FPS}fps ...")
    for i in range(n):
        t = i / FPS
        img = render_frame(t, sentences, tl, sb, imgs)
        img.save(frames_dir / f"f_{i:05d}.png", "PNG")
        if i % 60 == 0 or i == n - 1:
            print(f"      渲染 {int(100 * (i + 1) / n)}%")
    print("[4/6] 渲染完成")

    print("[5/6] ffmpeg 合成 ...")
    mid = frames_dir / "mid.mp4"
    cmd = [FFMPEG, "-y", "-r", str(FPS), "-i", str(frames_dir / "f_%05d.png"),
           "-i", str(args.audio), "-c:v", "libx264", "-pix_fmt", "yuv420p",
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
        print("[6/6] 已拼品牌片头")
    else:
        mid.replace(out)
        print("[6/6] 无片头, 直接输出")

    shutil.rmtree(frames_dir, ignore_errors=True)
    print(f"\n成品: {out}  ({out.stat().st_size // 1024} KB)")
    print(f"分镜审查: {sb_path}")
    # 一键 AI 封面帧(借鉴开拍: 按场景模板自动填标题)
    try:
        cov = make_cover(sb, sentences, args.title)
        cov_path = out.with_suffix(".cover.png")
        cov.save(str(cov_path))
        print(f"封面帧: {cov_path}")
    except Exception as e:
        print(f"[封面] 生成失败(不影响正片): {e}")

def make_cover(sb, sentences, title):
    """一键 AI 封面帧(借鉴开拍): 复用分镜 title 大字 + 背景渐变 + 风格装饰。"""
    pres = STYLE_PRESETS.get(STYLE_NAME, STYLE_PRESETS["财经严谨"])
    pal = pres["neutral"]
    img = gradient_bg(pal)
    d = ImageDraw.Draw(img)
    d.text((W // 2, 300), "财税图解", font=font(46, "hei"), fill=pal["accent2"], anchor="mm")
    cover_title = title
    for sc in sb:
        if sc.get("title"):
            cover_title = sc["title"]
            break
    lines = wrap(cover_title, 9)
    f = font(100, "hei")
    y = 720
    for ln in lines:
        d.text((W // 2, y), ln, font=f, fill=WHITE, anchor="mm",
               stroke_width=10, stroke_fill=(0, 0, 0))
        y += 124
    sub = (sentences[0][:16] if sentences else "")
    if sub:
        d.text((W // 2, y + 30), sub, font=font(40, "hei"), fill=pal["accent2"], anchor="mm")
    d.line([(W // 2 - 220, y + 90), (W // 2 + 220, y + 90)], fill=pal["accent"], width=6)
    return img

if __name__ == "__main__":
    main()
