#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_motion_video_v4.py — 幕后音·动态画面视频生成器（v4 · 智能生图 + 动态GIF 版）

风格基准: 视频号「建筑财税张老师」幕后音风格 —— 男声 / 女声 / 男女对话 配音 +
底部大字字幕(白字黑描边 + 关键词高亮) + 动态画面(像动态GIF一样持续运动)。

v4 核心特性(相对 v3):
  · 真·智能生图: 每幕由 DeepSeek 生成「画面意象描述(image_prompt)」,
    调通义万相(wanx)生成语义匹配的写实插画, 作为视频主视觉底图(不再是渐变+小图标)。
  · 动态画面(仿动态GIF): 底图持续运动 —— 呼吸运镜 + 斜向扫光 + 上浮粒子;
    若 gif_library/ 内放了动态GIF(文件名含 risk/safe/neutral 或场景关键词), 直接循环播放该GIF作底图。
  · 已取消"智能图解"卡片: table/list/step/number/quote 信息卡一律不用(极不成熟),
    画面只分 scene(动态画面) / dialog(男女对话上下分屏气泡) 两种。
  · 文字退居浮层: 插画/动图主导画面, 文字只做 顶部精炼标题 + 关键数字(代码叠加保证准确) + 底部字幕。
  · 暗化层保证可读: 顶部/底部渐变黑带, 让浮层文字在任何插画上都清晰。
  · 准确性: image_prompt 强制无文字; 数字/金额仍代码绘制, 不靠生图。
  · 生图缓存: 按 prompt 哈希缓存, 同稿重跑不重复烧钱/耗时; 失败自动降级为动画渐变占位。
  · 真实素材库(v5.1): model_keys.env 填 PEXELS_API_KEY/PIXABAY_API_KEY 后,
    底图优先取 Pexels/Pixabay 真实动态视频(风景/城市/内容相关, 无人物), 万相生图/照片库降为兜底;
    --no-stock 可强制关闭, 无 key/断网自动回退, 绝不阻塞出片。

接口:
  D:/heygem/py310/Scripts/python.exe make_motion_video_v4.py \
      --script 稿.md --audio 音频.wav --out 成品.mp4 --title 暂估成本
  --no-llm   规则分镜(不调 DeepSeek)
  --no-gen   跳过万相生图, 用动画渐变占位(调试渲染/转场用, 不联网)
  --regen    强制重新生图(忽略缓存)
  --gif-dir  动态GIF底图库目录(默认 gif_library/), 传 none 关闭GIF
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

# 转场类型(按场景序号循环, 制造节奏变化, 破解"每幕都一样"的单调)
TRANS_TYPES = ["wipe_lr", "wipe_tb", "zoom", "fade", "slide_lr", "iris",
               "blur_fade", "flash", "push", "soft_rotate", "glitch", "luma"]
# 插画风格轮替(每幕换一种观感, 但保持财税专业家族感, 不撞款)
# 统一约束：真实摄影写实风(杜绝插画/卡通/扁平矢量), 深色专业底 + 自然光影
# v5 定稿：背景一律「无人物风景/城市景观」(用户要求不要人物出镜背景)
IMG_STYLES = [
    "真实风景摄影，海边沙滩与蓝天白云，自然光线，电影级画质，无人物",
    "真实风景摄影，城市天际线，蓝天白云，自然光，高清质感，无人物",
    "真实风景摄影，森林湖泊，晨光薄雾，自然光影，纪实摄影，无人物",
    "真实风景摄影，绿色草原远山，明亮自然光，清晰细节，无人物",
    "真实风景摄影，湖面倒影，自然色调，专业摄影质感，无人物",
    "真实风景摄影，青山云海，自然光，纪实感，高端质感，无人物",
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


def _wrap_px_semantic(text, d, f, max_w):
    """按像素宽度 + 语义标点换行：超宽时回退最近标点后断开，
    不把句子腰斩成「上半句，/下半句」。无标点才按宽度硬断。"""
    BREAK_AFTER = "，。；！？、：)）】》\"”"
    lines, cur = [], ""
    last_break = -1
    for ch in text or "":
        if d.textlength(cur + ch, font=f) > max_w and cur:
            if last_break >= 0:
                lines.append(cur[:last_break + 1])
                cur = cur[last_break + 1:]
                last_break = -1
            else:
                lines.append(cur)
                cur = ""
        cur += ch
        if ch in BREAK_AFTER:
            last_break = len(cur) - 1
    if cur:
        lines.append(cur)
    return lines

# ============================== 万相生图 ==============================
def wanx_image(prompt, api_key, size="720*1280", regen=False):
    """调通义万相生成插画, 返回本地 jpg 路径。命中缓存则跳过网络。
    并发安全: 不同 prompt 哈希到不同缓存文件, 可并行调用(已去掉全局锁串行)。"""
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
        # v5 定稿：暗化强度下调(210→130 / 220→140)，保证风景底图清晰可见，
        # 仅在中部字幕上下留适度压暗提升可读性，不再让顶部/底部整片发黑。
        top = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ga = Image.new("L", (1, 680))
        for y in range(680):
            ga.putpixel((0, y), int(130 * (1 - y / 680) ** 1.3))
        band = Image.new("RGBA", (W, 680), (0, 0, 0, 255))
        band.putalpha(ga.resize((W, 680)))
        top.paste(band, (0, 0))
        _DARK_TOP = top
        bot = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        gb = Image.new("L", (1, 680))
        for y in range(680):
            gb.putpixel((0, y), int(140 * (y / 680) ** 1.4))
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
    """中部逐行滚动字幕(对标视频号「建筑财税张老师」):
    字幕块置于屏幕**中部区域**; 最多 5 行; **自下而上逐行**——当前行在块底部逐字打出,
    念完的行上移并渐隐(隐去); 每行带半透明深色圆角底条, 任意背景上均可读。"""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    pres = STYLE_PRESETS.get(STYLE_NAME, STYLE_PRESETS["财经严谨"])
    d = ImageDraw.Draw(img)
    _SUB_MAX_W = int(W * 0.80)   # 中部滚动字幕留更足边距, 防贴边
    _f_sub = ImageFont.truetype(str(BASE / "fonts/simhei.ttf"), 50)
    lines = _wrap_px_semantic(text, d, _f_sub, _SUB_MAX_W) if text else []
    if not lines:
        return img
    MAX_LINES = 5
    cx = W // 2
    total_chars = sum(len(l) for l in lines) or 1
    shown = (max(0, min(total_chars, int(reveal * total_chars))) if reveal is not None
             else total_chars)
    n = len(lines)
    lh = 70
    base_size = 50
    # 每行字符区间
    spans = []
    cum = 0
    for ln in lines:
        spans.append((cum, cum + len(ln)))
        cum += len(ln)
    # 当前行(正在念): shown 落在哪一行(第一个 shown<end 的行); 念完则最后一行
    cur_i = n - 1
    for i, (st, en) in enumerate(spans):
        if shown < en:
            cur_i = i
            break
    # 滚动窗口: 最多5行 = [cur_i-4, cur_i]; 自下而上——当前行在块底,
    # 上面排已念完的行; 最旧一行随当前行打字进度渐隐(念完隐去)
    win = list(range(max(0, cur_i - (MAX_LINES - 1)), cur_i + 1))
    alphas = {}
    for i in win:
        if i == cur_i:
            alphas[i] = 1.0
        elif i == win[0]:
            st, en = spans[cur_i]
            prog = (shown - st) / max(1, (en - st)) if shown < en else 1.0
            alphas[i] = max(0.0, 1.0 - prog)
        else:
            alphas[i] = 1.0
    visible = [i for i in win if alphas.get(i, 0.0) > 0.01]
    if not visible:
        return img
    last_vis = visible[-1]
    y_bot = int(H * 0.60)          # 块底部 = 当前行基线, 位于屏幕中部区域
    for i in visible:
        alpha = alphas[i]
        if alpha <= 0.01:
            continue
        st, en = spans[i]
        line = lines[i]
        if reveal is not None and shown < en:          # 当前行: 只打已念部分
            line = line[: max(0, shown - st)]
        if not line:
            continue
        y = y_bot - (last_vis - i) * lh                # 已念完的行向上排
        # 画到透明层(底条+文字), 再按 alpha 整体合成(淡入/渐隐)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        f = font(base_size, "hei")
        line_w = ld.textlength(line, font=f)
        pad_x, pad_y = 36, 16
        bx0 = cx - line_w / 2 - pad_x
        bx1 = cx + line_w / 2 + pad_x
        by0 = y - base_size * 0.62 - pad_y
        by1 = y + base_size * 0.62 + pad_y
        ld.rounded_rectangle([bx0, by0, bx1, by1], radius=22,
                             fill=(8, 12, 22, 150), outline=pal["accent"] + (70,), width=2)
        draw_text_fragments(ld, line, cx, y, base_size, pal,
                            reveal=None, keywords=keywords, local=local,
                            scdur=scdur, total_chars=total_chars,
                            char_start=st, align="center")
        if alpha < 0.999:
            r, g, b, a = layer.split()
            a = a.point(lambda v: int(v * alpha))
            layer = Image.merge("RGBA", (r, g, b, a))
        # 用 paste 按 alpha 蒙版合成: alpha_composite 在底图完全不透明时会丢弃图层
        img.paste(layer, (0, 0), layer)
    return img

# ============================== 分镜(LLM + 规则) ==============================
SB_PROMPT = """你是财税口播短视频的分镜导演。下面是一段口播稿按序号切好的句子。
为每个句子判断最适合的「视觉呈现类型(visual_type)」, 并给出对应内容。

核心原则（已取消"智能图解"卡片：表格/清单/步骤/数字/金句信息卡一律不用）:
1. 一律用 "scene"（场景画面），画面 = 动态底图 + 字幕，绝不出现代码绘制的生硬信息卡。
2. 每句给 image_prompt：与句子内容强相关的**无人物风景/城市景观真实摄影描述**（海滩、湖泊、森林、草原、城市天际线等自然或城市场景；写实照片、自然光、无文字无数字无字母，禁止出现人物，禁止插画、禁止卡通、禁止扁平矢量）。

visual_type 取值:
- "scene": 讲「场景/情境/故事/人物/对比/流程/数据」, 一律 scene, 给 image_prompt(真实摄影写实照片描述)。

每句还要给: title(顶部精炼主标题≤12字, 口语化抓人, 不要千篇一律写"财税干货")、tone("risk"风险/"safe"合规/"neutral"中性")、keywords(本句最该强调的 1-3 个词, 每词≤4字, 用于字幕高亮, 如["暂估","成本"]; 没有给 [])。

输出: 严格 JSON 数组, 每句一个元素, 不要解释或代码块:
[{"idx":0,"visual_type":"scene","image_prompt":"企业仓库空空如也的俯拍真实照片, 货架稀疏, 账本堆满数字","title":"账实不符","tone":"risk","keywords":["库存虚高","账实不符"]},
 {"idx":1,"visual_type":"scene","image_prompt":"会计在电脑前整理进销存台账, 数据图表环绕","title":"三步处理","tone":"neutral","keywords":["盘点","台账"]}]

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
    "对话稿的视觉原则：普通一问一答保持 \"dialog\" 上下分屏气泡；讲案例/故事/情境/对比/流程/数据的句子用 \"scene\" 配插画生图（数据/流程/清单句同样 scene，不用任何信息卡）。\n"
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
            # 取消"智能图解"卡片(table/list/step/number/quote)：极不成熟，
            # 统一归并为 scene(动态画面+生图) / dialog(男女对话上下分屏) 两种。
            vtype = str(item.get("visual_type", "scene"))
            if vtype not in ("scene", "dialog"):
                vtype = "scene"
            sc = {
                "visual_type": vtype,
                "title": str(item.get("title", ""))[:14] or sentences[idx][:12],
                "tone": item.get("tone", "neutral"),
                "image_prompt": str(item.get("image_prompt", ""))[:200] or "专业财经真实照片, 商务场景, 写实摄影",
                "keywords": [str(x)[:4] for x in (item.get("keywords") or [])][:3],
            }
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
DEFAULT_IMG = ("专业财经内容真实照片, 商务场景实拍, 自然光, 写实摄影", "财税干货")

def rule_one(idx, sent, total):
    """规则兜底(取消智能图解卡片后): 一律 scene 场景画面, 按关键词选画面意象。"""
    tone = "risk" if re.search(r"怕|风险|罚|亏|坑|错|虚开|补税|滞纳|？|禁止|红线", sent) else "neutral"
    if re.search(r"合规|安全|正确|建议|应该|可以|稳", sent):
        tone = "safe"
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

def _render_scene(sc, pal, idx, local, scdur, base_img, t_global=0.0):
    """场景主视觉(动态画面版): 底图(内容GIF / 真实素材库视频 / AI生图 / 真实照片 / 渐变) + 呼吸运镜 + 扫光 + 粒子,
    每一幕都像动态 GIF 一样持续运动, 取代静态图解卡。"""
    np_ = min(1.0, local / scdur) if scdur and scdur > 0 else 1.0
    # 底图优先级: 内容匹配的动态GIF → AI生成动态视频 → 真实素材库视频 → AI生图 → 真实照片库 → 动画渐变
    gif_path = _pick_gif(sc, content_only=True)
    if gif_path is not None:
        base, base_kind = gif_frame_at(gif_path, t_global), "gif"
    elif isinstance(base_img, str):
        # AI 文生视频背景(imgs 里存的是 mp4 路径): 抽帧循环当动态底图
        frames = _frames_from_path(base_img)
        if frames:
            base, base_kind = frames[int(t_global * 10) % len(frames)], "video"
        else:
            base, base_kind = _real_bg_photo(sc, t_global), "still"
    else:
        stock_base = _stock_bg(sc, t_global)
        if stock_base is not None:
            base, base_kind = stock_base, "video"
        elif base_img is not None:
            base, base_kind = base_img, "still"
        else:
            base, base_kind = _real_bg_photo(sc, t_global), "still"
    # 呼吸运镜(慢速振荡, 破解静态感) + 肯·伯恩斯漂移轮替
    breathe = 1.0 + 0.045 * math.sin(2 * math.pi * t_global / 7.0)
    if base_kind == "video":
        # 真实视频本身在动: 只保留极轻微呼吸(防裁切字幕), 不做大范围漂移
        sca, dx, dy = 1.0 + 0.022 * math.sin(2 * math.pi * t_global / 9.0), 0, 0
    else:
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
    img = kb_zoom(base, sca * breathe, dx, dy).convert("RGBA")
    # 动态化: 扫光 + 上浮粒子(仿动态GIF的流动高光/光点)
    img = _light_sweep(img, t_global)
    img = _particles(img, t_global, pal)
    img = _edge_vignette(img)
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
        d.text((W // 2, 250), "财税干货", font=font(40, "hei"), fill=pal["accent2"], anchor="mm")
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

# ============================== 动态画面(仿动态GIF) ==============================
GIF_DIR = BASE / "gif_library"      # 动态GIF底图库: 文件名含 risk/safe/neutral 或关键词即按场景命中
GIF_ENABLED = True
_GIF_FRAMES = {}                    # path -> [RGBA帧...]
_GIF_CANDS = None                   # gif 候选列表(惰性缓存)

def load_gif_frames(path):
    """读入动态GIF全部帧(惰性缓存, 并预缩放到 1080x1920, 渲染期零重复缩放)。"""
    if path in _GIF_FRAMES:
        return _GIF_FRAMES[path]
    im = Image.open(path)
    frames = []
    try:
        while True:
            f = im.convert("RGBA")
            if f.size != (W, H):
                f = cover_resize(f, W, H)
            frames.append(f)
            im.seek(im.tell() + 1)
    except EOFError:
        pass
    if not frames:
        f = im.convert("RGBA")
        frames = [cover_resize(f, W, H) if f.size != (W, H) else f]
    _GIF_FRAMES[path] = frames
    return frames

def gif_frame_at(path, t, fps=12):
    """按时间取 GIF 循环帧(已预缩放)。"""
    frames = load_gif_frames(path)
    i = int(t * fps) % len(frames)
    return frames[i]

REAL_BG_DIR = BASE / "real_bg"      # 真实照片兜底库: 文件名含 tone 标签(safe/neutral/risk)与场景词

def _real_bg_photo(sc, t_global=0.0):
    """从 real_bg 照片库按 tone 挑一张真实照片作底(实景, 配呼吸运镜即成动态视频);
    无库/无图时回退动画渐变。杜绝卡通插画与空渐变。"""
    if REAL_BG_DIR.exists():
        cands = sorted(list(REAL_BG_DIR.glob("*.jpg")) + list(REAL_BG_DIR.glob("*.png")))
        if cands:
            tone = sc.get("tone", "neutral")
            # v5 定稿：real_bg 全部为明亮风景/城市景观照(无人物)，按 tone 命中对应文件；
            # 同 tone 多张时按标题哈希轮换，避免同一条视频内问答句背景重复单调。
            tags = {"risk": ("risk", "storm", "night", "dark"),
                    "safe": ("safe", "sea", "beach", "lake", "mountain", "bright"),
                    "neutral": ("neutral", "city", "forest", "grass", "lake")}.get(tone, ("neutral",))
            matched = [c for c in cands if any(t in c.stem for t in tags)]
            if matched:
                picked = matched[hash(sc.get("title", "") or "x") % len(matched)]
            else:
                picked = cands[hash(sc.get("title", "")) % len(cands)]
            img = Image.open(picked).convert("RGB")
            return cover_resize(img, W, H)
    return _animated_gradient(get_palette(sc.get("tone", "neutral")), t_global)


# ============================== 真实素材库(Pexels/Pixabay) ==============================
# 配置 model_keys.env 的 PEXELS_API_KEY / PIXABAY_API_KEY 后自动启用；
# 无 key / 断网 / 无命中时静默回退(万相生图 / real_bg 照片)，绝不阻塞出片。
STOCK_ENABLED = None        # None=未判定; False=显式关闭(--no-stock)或无key
AI_VIDEO_ENABLED = None     # None=未判定; False=显式关闭(--no-ai-video)或无阿里key
_STOCK_FRAMES = {}          # path -> [RGB帧...] (LRU多槽缓存)
_STOCK_ORDER = []           # 最近使用顺序(转场时 prev/cur 双幕同时渲染, 2槽刚好覆盖)
_STOCK_MAX = 2              # 最多缓存 2 段素材帧(转场只需 prev/cur; 控内存: 2×15帧×1.5MB≈45MB/worker)
_FRAME_EXT = ".jpg"         # 共享帧用 JPEG(q88), 内存/磁盘比 PNG 省约 4 倍

def _ai_video_enabled():
    """AI 动态视频背景(万相文生视频): 有阿里 DashScope key 即启用(复用配音/生图同一把key)。"""
    global AI_VIDEO_ENABLED
    if AI_VIDEO_ENABLED is None:
        try:
            import wanx_video
            AI_VIDEO_ENABLED = wanx_video.is_available()
        except Exception:
            AI_VIDEO_ENABLED = False
    return AI_VIDEO_ENABLED

def _stock_enabled():
    global STOCK_ENABLED
    if STOCK_ENABLED is None:
        try:
            import stock_footage
            # 在线 key 或 本地手动素材库(零注册) 任一可用即启用
            STOCK_ENABLED = stock_footage.is_enabled() or stock_footage.has_local()
        except Exception:
            STOCK_ENABLED = False
    return STOCK_ENABLED

# 共享帧缓存: 主进程渲染前预抽帧写盘, 12个渲染worker直接加载PNG(不再各自跑ffmpeg)
SHARED_FRAMES_DIR = BASE / "storage" / "wanx_videos" / "frames"

def _frames_key(path):
    return hashlib.md5(str(path).encode("utf-8")).hexdigest()

def _video_frames(path, fps=10, max_sec=1.5):
    """把素材视频抽成 1080x1920 等比裁切帧序列(10fps, 截前 max_sec 秒循环, JPEG q88 省内存)；
    persist=True 时同时写共享盘缓存(幂等, 已存在则跳过), 供多进程渲染worker直接加载。
    失败返回 None。"""
    d = TMP / ("stock_frames_" + uuid.uuid4().hex[:8])
    try:
        d.mkdir(parents=True, exist_ok=True)
        n = max(1, int(max_sec * fps))
        vf = (f"fps={fps},scale={W}:{H}:force_original_aspect_ratio=increase,"
              f"crop={W}:{H}")
        r = subprocess.run([FFMPEG, "-y", "-i", str(path), "-vf", vf,
                            "-frames:v", str(n), str(d / "f%03d.png")],
                           capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            print(f"      [stock] 抽帧失败: {str(r.stderr)[-160:]}")
            return None
        frames = [Image.open(p).convert("RGB") for p in sorted(d.glob("f*.png"))]
        if not frames:
            return None
        if persist:
            fdir = SHARED_FRAMES_DIR / _frames_key(path)
            if not (fdir.exists() and any(fdir.glob("f_*" + _FRAME_EXT))):
                try:
                    fdir.mkdir(parents=True, exist_ok=True)
                    for idx, f in enumerate(frames):
                        f.save(fdir / f"f_{idx:03d}{_FRAME_EXT}", "JPEG", quality=88)
                except Exception as e:
                    print(f"      [stock] 共享帧写盘失败: {str(e)[:80]}")
        return frames
    except Exception as e:
        print(f"      [stock] 抽帧异常: {str(e)[:120]}")
        return None
    finally:
        shutil.rmtree(d, ignore_errors=True)

def _warm_frames(path):
    """主进程预热: 预生成共享帧缓存(只写盘, 不入内存), 渲染worker直接加载不再各自ffmpeg抽帧。"""
    try:
        fdir = SHARED_FRAMES_DIR / _frames_key(path)
        if fdir.exists() and any(fdir.glob("f_*" + _FRAME_EXT)):
            return
        _video_frames(path, persist=True)
    except Exception:
        pass

def _frames_from_path(path):
    """按视频路径取帧序列: 共享盘缓存优先(worker直接加载JPEG), 否则抽帧; LRU多槽缓存。"""
    if path in _STOCK_FRAMES:
        _STOCK_ORDER.remove(path)
        _STOCK_ORDER.append(path)
        return _STOCK_FRAMES[path]
    try:
        frames = None
        fdir = SHARED_FRAMES_DIR / _frames_key(path)
        if fdir.exists():
            jpgs = sorted(fdir.glob("f_*" + _FRAME_EXT))
            if jpgs:
                frames = [Image.open(p).convert("RGB") for p in jpgs]
        if frames is None:
            frames = _video_frames(path, persist=True)
        if not frames:
            _STOCK_FRAMES[path] = None
            _STOCK_ORDER.append(path)
            return None
        while len(_STOCK_FRAMES) >= _STOCK_MAX:     # 满则淘汰最久未用
            evict = _STOCK_ORDER.pop(0)
            _STOCK_FRAMES.pop(evict, None)
        _STOCK_FRAMES[path] = frames
        _STOCK_ORDER.append(path)
        return frames
    except Exception as e:
        print(f"      [stock] 素材帧失败: {str(e)[:120]}")
        return None

def _stock_bg(sc, t_global=0.0):
    """按场景取真实素材视频当前帧(循环播放)；未启用/无素材返回 None。"""
    if not _stock_enabled():
        return None
    try:
        import stock_footage
        q = stock_footage.scene_query(sc)
        if not q:
            return None
        clip = stock_footage.fetch_clip(q)
        if not clip:
            return None
        frames = _frames_from_path(clip)
        if not frames:
            return None
        i = int(t_global * 10) % len(frames)
        return frames[i]
    except Exception:
        return None


def _pick_gif(sc, content_only=False):
    """按场景 tone/关键词/文案 从 gif_library 命中一张动态GIF(无库或未命中返回 None)。
    content_only=True 时只认「内容匹配」(文件名语义段, 如 仓库/账本)——
    通用色系GIF(risk/safe/neutral)不再替代AI生图, 保证每幕都有内容画面。"""
    if not GIF_ENABLED or not GIF_DIR.exists():
        return None
    cands = _GIF_CANDS if _GIF_CANDS is not None else sorted(GIF_DIR.glob("*.gif"))
    if not cands:
        return None
    tone = sc.get("tone", "neutral")
    kw_text = (str(sc.get("title", "")) + " " + " ".join(sc.get("keywords") or []) + " " +
               str(sc.get("sentence", "")))
    # 1) 文件名语义段(≥2字)命中文案 → 内容匹配, 唯一可替代生图的GIF;
    #    要求 ≥2 个语义段命中, 防止常见词(如"合规""安全")误判通用GIF为内容图
    for c in cands:
        segs = [s for s in c.stem.split("_") if len(s) >= 2]
        hits = [s for s in segs if s in kw_text]
        if len(hits) >= 2:
            return str(c)
    if content_only:
        return None
    # 2) tone 标签: risk→risk/红/警示, safe→safe/绿/合规, neutral→neutral/蓝/通用
    tag = {"risk": ("risk", "红", "警示"), "safe": ("safe", "绿", "合规"),
           "neutral": ("neutral", "蓝", "通用")}.get(tone, ("neutral", "蓝"))
    for c in cands:
        if any(t in c.stem for t in tag):
            return str(c)
    # 3) 文案情绪词粗匹配
    risk_words = ("风险", "稽查", "罚", "红线", "虚开", "补税", "亏损")
    safe_words = ("合规", "安全", "稳", "正确", "建议")
    for c in cands:
        if any(w in kw_text for w in risk_words) and "risk" in c.stem:
            return str(c)
        if any(w in kw_text for w in safe_words) and "safe" in c.stem:
            return str(c)
    # 4) 兜底: 任意一张(按标题哈希轮转)
    return str(cands[hash(sc.get("title", "")) % len(cands)])

def _light_sweep(img, t):
    """斜向柔和扫光, 每 4.5s 一轮, 仿动态GIF的高光流动。"""
    period = 4.5
    x = ((t % period) / period) * (W + 800) - 400
    band = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(band)
    d.polygon([(x, 0), (x + 360, 0), (x - 80, H), (x - 440, H)], fill=(255, 255, 255, 26))
    return Image.alpha_composite(img, band)

def _particles(img, t, pal):
    """上浮粒子: 金色/白色小光点上漂 + 轻微横摆, 增强动态感。"""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    n = 14
    for i in range(n):
        period = 9.0 + (i % 5)
        phase = i * 0.73
        yy = (H + 80) - ((t * 46 + phase * period * 46) % (H + 160))
        xx = 90 + ((i * 137) % (W - 180)) + 34 * math.sin(2 * math.pi * t / period + i)
        r = 2 + (i % 3)
        a = 34 + 18 * math.sin(2 * math.pi * t / period + phase)
        col = pal["accent2"] if i % 2 == 0 else (255, 255, 255)
        d.ellipse([xx - r, yy - r, xx + r, yy + r], fill=col + (max(0, int(a)),))
    return Image.alpha_composite(img, layer)

def _edge_vignette(img, edge=90, alpha=95):
    """左右边缘柔和暗角: 压低照片两侧高光(提升字幕可读性, 避免QC误判图内容为文字溢出)。"""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for x in range(min(edge, W // 2)):
        a = int(alpha * (1 - x / edge) ** 1.2)
        if a > 0:
            d.line([(x, 0), (x, H)], fill=(0, 0, 0, a))
            d.line([(W - 1 - x, 0), (W - 1 - x, H)], fill=(0, 0, 0, a))
    return Image.alpha_composite(img, layer)


def _animated_gradient(pal, t):
    """动画渐变底图: 慢速流动的径向光晕 + 呼吸明暗, 替代静态渐变(无生图时用)。"""
    img = gradient_bg(pal).convert("RGBA")
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx = W // 2 + int(150 * math.sin(2 * math.pi * t / 11.0))
    cy = 780 + int(100 * math.cos(2 * math.pi * t / 13.0))
    for i in range(50, 0, -1):
        rr = int(950 * i / 50)
        a = int(7 * (1 - i / 50) * (0.75 + 0.25 * math.sin(2 * math.pi * t / 9.0)))
        if a > 0:
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                      outline=pal["glow"] + (a,), width=4)
    return Image.alpha_composite(img, layer)

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
    t_global = (tl[idx][0] if idx < len(tl) else 0.0) + local
    # v5 定稿：dialog(问答句)与 scene 统一走「动态画面」——风景/城市底图 + 中部滚动字幕。
    # 取消原「上下分屏头像气泡」设计：用户要求背景不要人物(头像/人像)，问答句同样用风景底图。
    img = _render_scene(sc, pal, idx, local, scdur, imgs[idx], t_global)
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
    # 字幕: 逐行滚动, reveal 跟随旁白进度(local/scdur), 念完的行渐隐
    kw = sc.get("keywords")
    narr = min(1.0, local / scdur) if scdur > 0 else 1.0
    draw_subtitle(img, sentences[idx], pal, reveal=narr, keywords=kw, local=local, scdur=scdur)
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


# ============================== 并行帧渲染(Windows spawn 兼容) ==============================
_WK = {}   # 每 worker 进程的渲染上下文(经 initializer 注入)

def _worker_init(sentences, tl, sb, imgs, frames_dir, fps, style_name):
    global STYLE_NAME
    STYLE_NAME = style_name
    _WK["sentences"] = sentences
    _WK["tl"] = tl
    _WK["sb"] = sb
    _WK["imgs"] = imgs
    _WK["frames_dir"] = frames_dir
    _WK["fps"] = fps

def _render_one(i):
    w = _WK
    img = render_frame(i / w["fps"], w["sentences"], w["tl"], w["sb"], w["imgs"])
    img.save(Path(w["frames_dir"]) / f"f_{i:05d}.png", "PNG")
    return i


# ============================== 对话模式(男女双声) ==============================
def parse_dialogue(text, default_role="M"):
    """解析剧本: 以 女：/男：(或 江：/张：) 开头的行识别角色, 其余行续接上一句。
    支持行内角色标记(同一行出现 女：...男：... 也会切分); 无角色标记的文本归 default_role。
    BOM 免疫: 首行可能带 \ufeff(UTF-8 BOM), 先剥掉再匹配。
    返回 [{'role':'F'/'M','text':...}, ...]"""
    import re
    segs = []

    def add(role, txt):
        t = (txt or "").strip()
        if not t:
            return
        if segs and segs[-1]["role"] == role:
            segs[-1]["text"] += t
        else:
            segs.append({"role": role, "text": t})

    for raw in text.splitlines():
        line = raw.strip().lstrip("\ufeff")
        if not line:
            continue
        parts = re.split(r"(?=(?:女|男|江|张)\s*[：:])", line)
        for part in parts:
            p = part.strip()
            if not p:
                continue
            m = re.match(r"^(女|男|江|张)\s*[：:]\s*(.*)$", p)
            if m:
                add("F" if m.group(1) in ("女", "江") else "M", m.group(2))
            else:
                add(default_role, p)
    return segs


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


def build_dialogue_audio(segments, out_wav, gap_ms=350, tts_workers=4):
    """逐轮用对应音色合成(子进程带超时, 支持并行) + 轮间静音, ffmpeg 拼接为单轨。
    断点续传: 已合成的 tN.wav 跳过(目录按 out 名确定, 重跑不重复烧额度)。
    tts_workers>1 时并行合成多段(每段独立子进程, 互不阻塞; 留意 dashscope 限流)。
    返回 (总时长秒, 每轮时间轴 [(start,end),...])。"""
    key = hashlib.md5(str(out_wav).encode()).hexdigest()[:8]
    tmp_dir = TMP / f"dlg_{key}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    def _synth_one(i_seg):
        i, seg = i_seg
        voice = FEMALE_VOICE if seg["role"] == "F" else MALE_VOICE
        p = str(tmp_dir / f"t{i}.wav")
        ok = _synth_segment_subprocess(seg["text"], voice, p, timeout=120, retries=3)
        if not ok:
            raise RuntimeError(
                f"段落合成失败(已重试且尝试改写): {seg['text'][:30]}。"
                f"可重跑本命令自动续传已成功段, 仅重试此段。")
        r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1", p],
                           capture_output=True, text=True)
        return (p, float(r.stdout.strip() or 1.0))

    n = len(segments)
    parts = [None] * n
    workers = max(1, min(int(tts_workers), n))
    if workers > 1 and n > 1:
        import concurrent.futures
        print(f"  [并行TTS] {n} 段 x {workers} 并发 ...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            for (i, seg), res in zip(enumerate(segments),
                                     ex.map(_synth_one, list(enumerate(segments)))):
                print(f"  [{i+1}/{n}] {seg['role']} 合成OK: {seg['text'][:20]}")
                parts[i] = res
    else:
        for i, seg in enumerate(segments):
            print(f"  [{i+1}/{n}] {seg['role']} 合成: {seg['text'][:20]}")
            parts[i] = _synth_one((i, seg))
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
    ap = argparse.ArgumentParser(description="幕后音·动态画面视频生成器 v4 (智能生图+动态GIF版)")
    ap.add_argument("--script", default="", help="口播稿 md(与 --storyboard 二选一)")
    ap.add_argument("--audio", default="", help="音频文件(对话模式可省略, 自动双声生成)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="动态画面")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--no-gen", action="store_true", help="跳过生图, 动画渐变占位(调试)")
    ap.add_argument("--regen", action="store_true", help="强制重生生图")
    ap.add_argument("--preview", type=int, default=0, help="只渲染前 N 帧")
    ap.add_argument("--storyboard", default="", help="载入外部分镜JSON(含sentence/visual_type等), 跳过LLM与稿件解析")
    ap.add_argument("--style", default="财经严谨", help="包装主题预设: 财经严谨/带货活力/简约高级")
    ap.add_argument("--dialogue", action="store_true", help="对话模式: 脚本含 女：/男： 前缀, 自动双声拼接+上下分屏")
    ap.add_argument("--male-voice", default="", help="覆盖男声音色 voice_id(默认 zhangc2)")
    ap.add_argument("--female-voice", default="", help="覆盖女声音色 voice_id(默认 jiangnv3)")
    ap.add_argument("--default-role", default="M", help="无角色前缀文本的默认声线: M男 / F女")
    ap.add_argument("--gif-dir", default="", help="动态GIF底图库目录(默认 gif_library/; 传 none 关闭)")
    ap.add_argument("--workers", type=int, default=0, help="并行渲染进程数(0=自动: min(12, CPU核数-2); 1=串行)")
    ap.add_argument("--tts-workers", type=int, default=4, help="并行TTS段数(1=串行; 默认4, 留意API限流)")
    ap.add_argument("--no-stock", action="store_true", help="关闭真实素材库(Pexels/Pixabay/本地), 回到万相生图/照片库底图")
    ap.add_argument("--no-ai-video", action="store_true", help="关闭AI动态视频背景(万相文生视频), 回到生图/素材库底图")
    args = ap.parse_args()
    global STYLE_NAME, GIF_DIR, GIF_ENABLED, MALE_VOICE, FEMALE_VOICE, STOCK_ENABLED, AI_VIDEO_ENABLED
    if args.no_stock:
        STOCK_ENABLED = False
    if args.no_ai_video:
        AI_VIDEO_ENABLED = False
    if args.style in STYLE_PRESETS:
        STYLE_NAME = args.style
    else:
        print(f"[警告] 未知 --style '{args.style}', 用默认 '财经严谨'")
    if args.gif_dir:
        if args.gif_dir.lower() == "none":
            GIF_ENABLED = False
        else:
            GIF_DIR = Path(args.gif_dir)
            GIF_ENABLED = True
    if args.male_voice:
        MALE_VOICE = args.male_voice
    if args.female_voice:
        FEMALE_VOICE = args.female_voice

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
        segs = parse_dialogue(raw, default_role=args.default_role)
        if not segs:
            sys.exit("对话模式未解析到对话(需以 女：/男： 开头)")
        # 独白稿修复: 无角色前缀的整段文本会被 parse_dialogue 合并成 1 条(同角色相邻合并),
        # 导致全片只有 1 幕 1 个背景。按句切分恢复多幕(保持同一角色, 多幕=多背景多画面)。
        if len(segs) == 1 and segs[0]["role"] in ("M", "F"):
            parts = split_sentences(segs[0]["text"])
            segs = [{"role": segs[0]["role"], "text": p.strip()}
                    for p in parts if p and p.strip()]
        if not segs:
            sys.exit("对话模式未解析到对话(需以 女：/男： 开头)")
        dlg_audio = out.with_suffix(".dialogue.wav")
        dur, tl = build_dialogue_audio(segs, str(dlg_audio), tts_workers=args.tts_workers)
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
            if sc.get("visual_type") == "scene" and not sc.get("image_prompt"):
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

    sb_path = out.with_suffix(".v4storyboard.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    sb_path.write_text(json.dumps(
        [{"idx": i, "start": tl[i][0], "end": tl[i][1], "sentence": sentences[i], **sb[i]}
         for i in range(len(sentences))], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"      分镜已存: {sb_path}")

    # 生图(仅 scene 类型调万相; 命中动态GIF的场景跳过; 并行调用加速)
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
        ai_video_on = _ai_video_enabled()
        stock_on = _stock_enabled()
        if ai_video_on:
            print("[3/6] AI 动态视频背景已启用(万相文生视频, 复用阿里key, 零注册)")
        elif stock_on:
            print("[3/6] 真实素材库已启用(在线/本地), 优先真实视频背景, 生图仅兜底")
        need = []        # 生图任务 (i, prompt)
        vneed = []       # AI视频任务 (i, image_prompt, title)
        for i, sc in enumerate(sb):
            if _pick_gif({**sc, "sentence": sentences[i]}, content_only=True) is not None:
                imgs.append(None)   # 内容匹配动态GIF(如 仓库/账本)作底, 不烧钱生图
            elif ai_video_on:
                # AI 动态视频背景覆盖**每一幕**（scene 用内容描述; dialog/无prompt 句用风景描述,
                # 保证"讲发票就有发票画面、讲道理也有真实动态风景"，不再有幕落到静态照片兜底）
                if sc.get("image_prompt"):
                    vneed.append((i, sc["image_prompt"], sc.get("title", "")))
                else:
                    vneed.append((i, IMG_STYLES[i % len(IMG_STYLES)], sc.get("title", "")))
                imgs.append(None)   # AI视频占位, 成功后 imgs[i] 存 mp4 路径
            elif stock_on:
                imgs.append(None)   # 真实素材库优先: 渲染期取视频帧; 失败自动降级 real_bg
            elif sc.get("visual_type") != "scene":
                imgs.append(None)   # 非 scene 用代码绘制, 不联网生图
            else:
                style = IMG_STYLES[i % len(IMG_STYLES)]
                prompt = sc["image_prompt"] + ("，" + style + "，画面纯净无人物无文字无字母无数字，竖版9:16构图，真实摄影写实风格，禁止插画、禁止卡通、禁止扁平矢量")
                need.append((i, prompt))
                imgs.append(None)   # 占位, 保持 imgs 与 sb 等长(并行结果按索引回填)
        # 1) AI 动态视频背景(并行2路, 单段约30-90s; 失败幕自动补生图兜底, 不阻塞出片)
        if vneed:
            import concurrent.futures
            import wanx_video
            print(f"[3/6] AI 动态视频背景 {len(vneed)} 段(万相文生视频, 2路并行, 约30-90s/段) ...")
            done = 0
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
                for i, path in ex.map(lambda it: (it[0], wanx_video.gen_video(it[1], it[2])), vneed):
                    imgs[i] = path
                    done += 1
                    print(f"      [{done}/{len(vneed)}] AI视频{'OK' if path else '失败, 补生图'}: {sb[i]['title']}")
            for i, _, _ in vneed:
                sc = sb[i]
                if imgs[i] is not None:
                    continue
                style = IMG_STYLES[i % len(IMG_STYLES)]
                base_p = sc.get("image_prompt") or style
                prompt = base_p + ("，" + style + "，画面纯净无人物无文字无字母无数字，竖版9:16构图，真实摄影写实风格，禁止插画、禁止卡通、禁止扁平矢量")
                need.append((i, prompt))
        # 2) 静态生图(兜底: AI视频失败幕 / 未启用AI视频的scene幕)
        print(f"[3/6] 通义万相生图 {len(need)} 张(并行) ...")

        def _gen(ip):
            i, prompt = ip
            # 429 限流: 退避重试(最多3次), 避免 RateQuota 直接降级
            for attempt in range(3):
                try:
                    jpg = wanx_image(prompt, api_key, regen=args.regen)
                    return i, cover_resize(Image.open(jpg).convert("RGB"), W, H)
                except Exception as e:
                    if "429" in str(e) or "RateQuota" in str(e) or "rate limit" in str(e).lower():
                        import time as _t
                        _t.sleep(8 + attempt * 8)
                        continue
                    print(f"      [{i+1}] 生图失败({str(e)[:60]}), 降级真实照片")
                    return i, _real_bg_photo(sb[i])
            print(f"      [{i+1}] 生图失败(限流重试3次后仍失败), 降级真实照片")
            return i, _real_bg_photo(sb[i])

        if need:
            import concurrent.futures
            nw = min(2, len(need))   # 并发生图(2路, 429 有退避重试, 防限流)
            done = 0
            with concurrent.futures.ThreadPoolExecutor(max_workers=nw) as ex:
                for i, img in ex.map(_gen, need):
                    imgs[i] = img
                    done += 1
                    print(f"      [{done}/{len(need)}] 生图OK: {sb[i]['title']}")

    # AI 视频背景预热: 主进程预抽帧写共享盘缓存, 12个渲染worker直接加载PNG(不再各自跑ffmpeg)
    video_paths = [p for p in imgs if isinstance(p, str)]
    if video_paths:
        print(f"[3.5/6] 预抽 AI 视频背景帧 {len(video_paths)} 段(共享缓存) ...")
        for p in video_paths:
            _warm_frames(p)

    frames_dir = TMP / f"frames_{uuid.uuid4().hex[:8]}"
    frames_dir.mkdir(parents=True, exist_ok=True)
    n = int((args.preview if args.preview else dur) * FPS)
    workers = args.workers
    if workers <= 0:
        workers = max(1, min(12, (os.cpu_count() or 4) - 2))
    print(f"[4/6] 渲染 {n} 帧 @ {FPS}fps (并行 workers={workers}) ...")
    if workers <= 1 or n < workers * 6:
        # 串行渲染(预览小帧数/显式 --workers 1)
        for i in range(n):
            t = i / FPS
            img = render_frame(t, sentences, tl, sb, imgs)
            img.save(frames_dir / f"f_{i:05d}.png", "PNG")
            if i % 60 == 0 or i == n - 1:
                print(f"      渲染 {int(100 * (i + 1) / n)}%")
    else:
        # 多进程并行渲染: 16 核机器串行 ~3.4fps, 8 进程可到 ~25fps+
        try:
            import multiprocessing as _mp
            _mp.set_start_method("spawn", force=True)
        except Exception:
            pass
        import concurrent.futures
        init = (sentences, tl, sb, imgs, str(frames_dir), FPS, STYLE_NAME)
        done = 0
        with concurrent.futures.ProcessPoolExecutor(
                max_workers=workers, initializer=_worker_init, initargs=init) as ex:
            for _ in ex.map(_render_one, range(n)):
                done += 1
                if done % 60 == 0 or done == n:
                    print(f"      渲染 {int(100 * done / n)}%")
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
    d.text((W // 2, 300), "财税干货", font=font(46, "hei"), fill=pal["accent2"], anchor="mm")
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
