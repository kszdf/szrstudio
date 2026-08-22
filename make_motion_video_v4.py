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
TRANS_TYPES = ["wipe_lr", "wipe_tb", "zoom", "fade", "slide_lr", "iris"]
# 插画风格轮替(每幕换一种观感, 但保持财税专业家族感, 不撞款)
IMG_STYLES = [
    "扁平矢量商务插画，简洁几何化人物与场景",
    "等距 isometric 立体商务插画，干净留白",
    "半色调双色 duotone 财经插画，现代克制",
    "写实电影感插画，景深与明暗光影",
    "手绘线条信息图插画，清爽专业",
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
def get_palette(tone):
    if tone == "risk":
        return dict(bg_top=(30, 14, 16), bg_bot=(52, 24, 28), accent=(244, 63, 63),
                    accent2=(251, 191, 191), glow=(190, 40, 40))
    if tone == "safe":
        return dict(bg_top=(10, 26, 30), bg_bot=(16, 46, 50), accent=(16, 185, 129),
                    accent2=(153, 246, 206), glow=(16, 150, 100))
    return dict(bg_top=(14, 22, 42), bg_bot=(30, 40, 58), accent=(245, 158, 11),
                accent2=(254, 215, 110), glow=(200, 140, 30))

WHITE = (250, 251, 253)
MUTED = (200, 210, 222)

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
    d = ImageDraw.Draw(img)
    f = font(size, "serif")
    d.text((cx + 4, cy + 4), text, font=f, fill=(0, 0, 0), anchor=anchor)
    d.text((cx, cy), text, font=f, fill=fill, anchor=anchor)

def draw_number(img, num, sub, cx, cy, accent):
    d = ImageDraw.Draw(img)
    fn = font(190, "sans")
    d.text((cx + 5, cy + 5), num, font=fn, fill=(0, 0, 0), anchor="mm")
    d.text((cx, cy), num, font=fn, fill=accent, anchor="mm")
    if sub:
        fs = font(46, "hei")
        d.text((cx, cy + 150), sub, font=fs, fill=WHITE, anchor="mm")

def draw_subtitle(img, text):
    d = ImageDraw.Draw(img)
    lines = wrap(text, 15)
    size = 52
    lh = 74
    total_h = len(lines) * lh
    y0 = H - 150 - total_h + lh
    cx = W // 2
    f = font(size, "hei")
    for i, ln in enumerate(lines):
        y = y0 + i * lh
        d.text((cx, y), ln, font=f, fill=WHITE, anchor="mm",
               stroke_width=7, stroke_fill=(0, 0, 0))

# ============================== 分镜(LLM + 规则) ==============================
TEMPLATES = ["bigtext", "number", "compare", "checklist",
             "statement", "steps", "stat2"]

SB_PROMPT = """你是财税口播短视频的分镜导演。下面是一段口播稿按序号切好的句子。
为每个句子判断最适合的「视觉呈现类型(visual_type)」, 并给出对应内容。
核心原则: 该是场景是场景, 该是表格是表格, 该是清单是清单, 不要一律生图。

visual_type 取值与含义:
- "scene": 内容讲「场景/情境/故事/人物」, 用一张扁平矢量商务插画表现。给 image_prompt(扁平矢量商务插画描述, 干净克制专业, 无文字无数字无字母)。
- "table": 内容含「数据对比/税率/金额对照/比率」, 必须用表格才清晰。给 table{"head":[列1,列2],"rows":[[值,值],...]}。
- "list": 内容讲「步骤/要点/清单/注意事项」, 适合打勾清单。给 items:[项1,项2,...](≤5项, 每项≤10字)。
- "number": 内容聚焦「一个关键数字/日期/比率」, 适合数字大卡。给 highlight_num(原样如"500万"/"25%"/"5月31日") + num_sub(数字含义≤8字)。
- "quote": 内容是「强警示语/金句/收口」, 适合大字卡。给 quote_text(≤14字)。

每句还要给: title(顶部精炼主标题≤12字, 口语化抓人)、tone("risk"风险/"safe"合规/"neutral"中性)。

判断优先级: 有两组以上数字对照 → table; 单句含关键数字/日期 → number; 列要点步骤 → list; 短促警示/金句(且非以上) → quote; 其余讲人/事/情境 → scene。注意 quote 仅用于真正短促有力的金句, 同一视频连续 quote 不超过2张, 避免整片都是文字卡。

输出: 严格 JSON 数组, 每句一个元素, 不要解释或代码块:
[{"idx":0,"visual_type":"scene","image_prompt":"...","title":"...","tone":"risk"},
 {"idx":1,"visual_type":"table","table":{"head":["项目","税率"],"rows":[["一般纳税人","13%"],["小规模","3%"]]},"title":"...","tone":"neutral"},
 {"idx":2,"visual_type":"list","items":["合同","入库单","发票"],"title":"...","tone":"safe"},
 {"idx":3,"visual_type":"number","highlight_num":"25%","num_sub":"综合税负","title":"...","tone":"risk"},
 {"idx":4,"visual_type":"quote","quote_text":"这样列支才稳","title":"...","tone":"safe"}]

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
        if 0 <= idx < len(sentences):
            vtype = str(item.get("visual_type", "scene"))
            if vtype not in ("scene", "table", "list", "number", "quote"):
                vtype = "scene"
            sc = {
                "visual_type": vtype,
                "title": str(item.get("title", ""))[:14] or sentences[idx][:12],
                "tone": item.get("tone", "neutral"),
                "image_prompt": str(item.get("image_prompt", ""))[:200] or "专业财经扁平插画, 简洁商务",
                "highlight_num": str(item.get("highlight_num", ""))[:20],
                "num_sub": str(item.get("num_sub", ""))[:12],
                "quote_text": str(item.get("quote_text", ""))[:16],
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
    # 清单: 列要点/步骤/顿号列举/长句
    if re.search(r"清单|要点|步骤|注意|一是|二是|三是|首先|其次|最后|、", sent) or len(sent) > 40:
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

def _render_number(sc, pal, p, a):
    img = gradient_bg(pal)
    draw_number(img, sc.get("highlight_num", ""), sc.get("num_sub", ""), W // 2, 780, pal["accent"])
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

def render_scene_frame(idx, local, sentences, tl, sb, imgs):
    """按 visual_type 分支渲染某场景帧, 再做统一入场 + 底部字幕。"""
    sc = sb[idx]
    vtype = sc.get("visual_type", "scene")
    pal = get_palette(sc.get("tone", "neutral"))
    p = min(1.0, local / ENTR)
    a = ease(p)
    scdur = (tl[idx][1] - tl[idx][0]) if idx < len(tl) else 3.0
    if vtype == "scene":
        img = _render_scene(sc, pal, idx, local, scdur, imgs[idx])
    elif vtype == "table":
        img = _render_table(sc, pal, p, a)
    elif vtype == "list":
        img = _render_list(sc, pal, p, a)
    elif vtype == "number":
        img = _render_number(sc, pal, p, a)
    elif vtype == "quote":
        img = _render_quote(sc, pal, p, a)
    else:
        img = _render_scene(sc, pal, idx, local, scdur, imgs[idx])
    # 统一入场: 整帧缩放淡入
    sca = 1.0 + (1 - a) * 0.05
    if sca != 1.0:
        nw, nh = int(W * sca), int(H * sca)
        resized = img.resize((nw, nh), Image.LANCZOS)
        framed = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        framed.alpha_composite(resized, ((W - nw) // 2, (H - nh) // 2))
        img = framed
    if a < 1:
        fade = Image.new("RGBA", (W, H), (0, 0, 0, int(120 * (1 - a))))
        img = Image.alpha_composite(img, fade)
    draw_subtitle(img, sentences[idx])
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
        zd = cur.resize((nw, nh), Image.LANCZOS)
        cm = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        cm.alpha_composite(zd, ((W - nw) // 2, (H - nh) // 2))
        return _composite_masked(prev, cm, None, int(255 * p))
    if ttype == "slide_lr":
        off = int(W * (1 - p))
        cm = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        cm.alpha_composite(cur.convert("RGBA"), (off, 0))
        return Image.alpha_composite(prev.convert("RGBA"), cm).convert("RGB")
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
                                  sentences, tl, sb, imgs)
        curf = render_scene_frame(cur, local, sentences, tl, sb, imgs)
        return apply_transition(prev, curf, local / TRANS, ttype)
    return render_scene_frame(cur, local, sentences, tl, sb, imgs)

# ============================== 主流程 ==============================
def main():
    ap = argparse.ArgumentParser(description="幕后音图解视频生成器 v4 (智能生图版)")
    ap.add_argument("--script", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="图解视频")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--no-gen", action="store_true", help="跳过生图, 渐变占位(调试)")
    ap.add_argument("--regen", action="store_true", help="强制重生生图")
    ap.add_argument("--preview", type=int, default=0, help="只渲染前 N 帧")
    args = ap.parse_args()

    script_path, audio, out = Path(args.script), Path(args.audio), Path(args.out)
    # 单实例互斥锁: 防后台重复启动造成写同一文件冲突
    import atexit
    _lock = out.with_suffix(".lock")
    try:
        _fd = os.open(str(_lock), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        os.close(_fd)
    except FileExistsError:
        sys.exit(f"[互斥] 检测到锁文件 {_lock}, 疑似已有实例运行或上次异常残留。确认无重复进程后删除该文件再运行。")
    atexit.register(lambda: os.path.exists(str(_lock)) and os.remove(str(_lock)))
    text = clean_script(script_path.read_text(encoding="utf-8"))
    sentences = split_sentences(text)
    if not sentences:
        sys.exit("稿子解析后为空")

    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", str(audio)],
                       capture_output=True, text=True)
    dur = float(r.stdout.strip())
    tl = timeline(sentences, dur)
    print(f"[1/6] 稿件 {len(sentences)} 句, 音频 {dur:.1f}s")

    if args.no_llm:
        sb = [rule_one(i, s, len(sentences)) for i, s in enumerate(sentences)]
        print("[2/6] 规则分镜")
    else:
        try:
            sb = llm_storyboard(sentences)
            print("[2/6] DeepSeek 分镜完成")
        except Exception as e:
            print(f"[2/6] LLM 分镜失败({e}), 回退规则")
            sb = [rule_one(i, s, len(sentences)) for i, s in enumerate(sentences)]

    sb_path = out.with_suffix(".v4storyboard.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    sb_path.write_text(json.dumps(
        [{"idx": i, "start": tl[i][0], "end": tl[i][1], "sentence": sentences[i], **sb[i]}
         for i in range(len(sentences))], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"      分镜已存: {sb_path}")

    # 生图(仅 scene 类型调万相)
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
        print("[6/6] 已拼品牌片头")
    else:
        mid.replace(out)
        print("[6/6] 无片头, 直接输出")

    shutil.rmtree(frames_dir, ignore_errors=True)
    print(f"\n成品: {out}  ({out.stat().st_size // 1024} KB)")
    print(f"分镜审查: {sb_path}")

if __name__ == "__main__":
    main()
