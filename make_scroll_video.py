#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
滚动字幕卡短视频生成器（不出镜 · 双声 · 逐字卡拉OK高亮 · 5行窗口滚动）

功能:
  - 逐句 TTS（男=张老师克隆音 zhangc2 / 女=江老师克隆音 jiangnv3，均 cosyvoice-v3-plus）
  - 真实音频时长驱动时间轴，画面当前句逐字与声音同步渐亮（灰→金黄）
  - 屏幕固定 5 行文字窗口：当前句在底部，其余 4 句（已读）向上滚动，读毕滚出顶部消失
  - 不显示"张老师/女声主播"任何角色标签，仅以音色区分男女声
  - 底部品牌条「追梦 · 老张讲财税」
  - 默认背景：浅色海景沙滩拍滚动画（numpy 程序化生成，海水轻轻在沙滩拍滚）
    --bg-style blackgold 切黑金流动；--bg <图片> 用任意静态图作底

用法:
  python make_scroll_video.py --dialogue demo_dialogue_v2.txt --out output/video/scroll.mp4
  python make_scroll_video.py --dialogue x.txt --out x.mp4 --bg-style blackgold
  python make_scroll_video.py --dialogue x.txt --out x.mp4 --bg covers/bg_seaside.png
  python make_scroll_video.py --dialogue x.txt --out x.mp4 --dry-tts   # 跳过真实TTS（用静音占位）快速验证画面

依赖: Pillow, numpy, ffmpeg(全量), dashscope(真实TTS)
"""
import os
import sys
import argparse
import subprocess
import wave
import shutil
import tempfile
import hashlib
import json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
FFMPEG = r"D:\ffmpeg\ffmpeg-8.1.2-full_build\bin\ffmpeg.exe"

# 让 qwen_tts 在导入时就把 DASHSCOPE_API_KEY 灌进环境变量
try:
    from qwen_tts import synth as _qwen_synth, DEFAULT_VOICE_ID as _DEFAULT_MALE
except Exception as e:  # pragma: no cover
    print(f"[WARN] 无法导入 qwen_tts: {e}")
    _qwen_synth = None
    _DEFAULT_MALE = ""

# 角色音色（定稿）
MALE_VOICE = ""   # 新租户初始无自带声音；须由租户克隆/选择后显式传入
MALE_MODEL = "cosyvoice-v3-plus"
FEMALE_VOICE = ""   # 新租户初始无自带声音；须由租户克隆/选择后显式传入
FEMALE_MODEL = "cosyvoice-v3-plus"

# 情绪→韵律映射表（D 基调：专家味 + 自然起伏，男女声分设相对倍率 + 句后停顿）
# rel = (语速倍率, 音高倍率, 音量倍率)，作用在 CLI 传入的男女声基准之上；pause = 句后静音秒数
EMOTION_PROSODY = {
    "narrate":  {"rel": (1.00, 1.00, 1.00), "pause": 0.18},  # 平铺叙述、交代背景
    "emphasis": {"rel": (1.14, 1.04, 1.10), "pause": 0.42},  # 重点强调、核心结论（提速加大音量）
    "warn":     {"rel": (1.06, 0.97, 1.04), "pause": 0.40},  # 风险警示、提醒（略快、压低音高）
    "query":    {"rel": (1.09, 1.03, 1.02), "pause": 0.38},  # 反问、抛问、悬念（略快、略升）
    "light":    {"rel": (1.10, 1.05, 0.94), "pause": 0.20},  # 轻松调侃、缓和（略快、略升、略轻）
    "ending":   {"rel": (1.02, 1.00, 0.98), "pause": 0.15},  # 收尾落点、总结（回归平稳）
}
_EMOTION_CACHE = {}


def _parse_emotion_list(text, n):
    """容错解析 DeepSeek 返回的情绪 key 数组，补齐/截断到 n 个。"""
    try:
        s = text.strip()
        if s.startswith("```"):
            s = s.split("```")[1]
        arr = json.loads(s)
        if isinstance(arr, list) and all(isinstance(x, str) for x in arr):
            arr = [a for a in arr if a in EMOTION_PROSODY] or ["narrate"]
            if len(arr) < n:
                arr += ["narrate"] * (n - len(arr))
            return arr[:n]
    except Exception:
        pass
    # 退化：从文本里挑枚举词
    found = [w for w in EMOTION_PROSODY if w in text]
    if found:
        return (found + ["narrate"] * n)[:n]
    return ["narrate"] * n


def annotate_emotions(segs):
    """一次性让 DeepSeek 标注整段对话每句的语义情绪，返回与 segs 等长的情绪 key 列表。
    情绪枚举见 EMOTION_PROSODY。按对话内容哈希缓存，避免重合成重复调用。任何失败回退 narrate。"""
    if not segs:
        return []
    raw = "|".join(f"{r}:{t}" for r, t in segs)
    key = hashlib.md5(raw.encode("utf-8")).hexdigest()
    if key in _EMOTION_CACHE:
        return _EMOTION_CACHE[key]
    try:
        from model_providers import get_text_config, deepseek_chat
        cfg = get_text_config()
        lines = "\n".join(f"{i+1}. [{'女' if r == 'F' else '男'}] {t}" for i, (r, t) in enumerate(segs))
        prompt = (
            "你是一名短视频配音导演。下面是一段男女对白（或独白），请为每一句话标注最适合的"
            "情绪基调，用于驱动 TTS 的语速/音高/音量/停顿，让配音有自然的快慢轻重起伏、像专家在讲解而不是念稿。\n"
            "情绪只能从以下 6 类选一：\n"
            "narrate = 平铺叙述、交代背景\n"
            "emphasis = 重点强调、核心结论（应提速、加大音量）\n"
            "warn = 风险警示、提醒注意（语速略快、音高压低）\n"
            "query = 反问、抛问、制造悬念（语速略快、音调略升）\n"
            "light = 轻松调侃、缓和气氛（语速略快、音调略升）\n"
            "ending = 收尾落点、总结（回归平稳）\n"
            "只输出一个 JSON 数组，元素为对应句子的情绪 key 字符串，不要解释、不要 markdown、不要代码块。\n"
            "示例输出: [\"narrate\",\"emphasis\",\"warn\",\"query\",\"ending\"]\n\n"
            f"对白：\n{lines}"
        )
        resp = deepseek_chat(prompt, cfg["model"], cfg["key"], cfg.get("base_url"), timeout=60)
        arr = _parse_emotion_list(resp, len(segs))
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 情绪标注失败，回退 narrate: {e}")
        arr = ["narrate"] * len(segs)
    _EMOTION_CACHE[key] = arr
    return arr

# 画布
W, H = 1080, 1920
FPS = 24
FONT_PATH = os.path.join(HERE, "fonts", "simhei.ttf")

# 字幕窗口布局（竖屏中央偏上区域，5 行槽位）
PANEL_TOP = 560
PANEL_BOTTOM = 1560
N_ROWS = 5
ROW_H = (PANEL_BOTTOM - PANEL_TOP) / N_ROWS  # 200
MAX_W = W - 120  # 文字最大宽度（留出描边余量，避免长句贴边/溢出）
LEFT_X = 80      # 字幕统一左对齐起始 x（右侧留 40px 余量，安全不溢出）

# 字号（竖屏1080宽下，当前句约11-12字/行，符合视频号每行12-15字规范；醒目不溢出）
HL_SIZE = 92       # 当前句（卡拉OK）：白字黑边，最醒目
HIST_SIZE = 70     # 历史句
STROKE_W = 5       # 白字黑描边，保证浅/深背景都清晰可读（全网规范首推白字黑边）
STROKE_FILL = (0, 0, 0)   # 黑边

# 颜色：还原 8385 经典「白字黑边」清晰风格（全网规范首推：任何背景都清晰），关键词红色高亮
SUB_DONE = (255, 255, 255)   # 当前句已读：纯白（卡拉OK 高亮扫过，最清晰）
SUB_TODO = (205, 212, 226)   # 当前句未读：浅蓝灰，提示"待读"，与纯白形成逐字高亮对比
HIST_RGB = (190, 198, 214)   # 历史句基础：淡蓝灰（配合 alpha 逐渐淡出）
BRAND_RGB = (255, 201, 64)   # 品牌条（金色，与标题金呼应，深色背景上更高级）

# 顶部固定标题（≤10字，自动生成，不随滚动；参照视频号常规大小，不喧宾夺主）
TITLE_MAX_CHARS = 10
TITLE_SIZE = 84               # 视频号常规标题字号（之前 200 过大，缩小到舒服比例）
TITLE_FILL = (255, 201, 64)   # 醒目金
TITLE_STROKE = (16, 30, 60)   # 深海军蓝描边
TITLE_STROKE_W = 4
TITLE_TOP = 84                   # 标题区顶部 y（缩小与正文之间的留白）
TITLE_RULE = (255, 188, 48)      # 标题下装饰金线
TITLE_RULE_W = 4
TITLE_BAND = (16, 30, 60)        # 标题底纹条（深蓝，增强视觉冲击力）
TITLE_BAND_A = 205               # 底纹条不透明度
TITLE_PAD_Y = 12                 # 底纹条上下内边距（缩小，避免"点的位置空间"过大）

# 副标题（出片界面填写，标题下方一行小字说明，如"建筑财税·避坑指南"）
SUBTITLE_SIZE = 38
SUBTITLE_FILL = (255, 201, 64)     # 与标题同金，保持统一视觉
SUBTITLE_STROKE = (16, 30, 60)     # 深海军蓝描边，浅背景可读
SUBTITLE_STROKE_W = 3
SUBTITLE_GAP = 16                 # 副标题与标题金线间距

# 强调高亮（关键词 / **...** 手动标记）：统一红色，覆盖黄色
EMPH_DONE = (255, 40, 40)     # 当前句已读强调：红
EMPH_TODO = (255, 120, 120)   # 当前句未读强调：浅红
EMPH_HIST = (255, 55, 55)     # 历史句强调：红
KEYWORDS = ["虚开发票", "税务稽查", "金税四期", "公转私", "暂估成本", "个人卡流水",
            "个人卡", "偷税", "逃税", "留抵退税", "进项发票", "销项发票",
            "滞纳金", "税务风险", "隐匿收入"]

# 正文滚动字幕（提词器式：逐物理行，从上往下阅读）
# 当前行固定在中上部高亮；已读行在其上方逐行上移并淡出；未读行在其下方暗显待进入。
CURRENT_Y = 880                  # 当前（正在朗读）行的垂直中心，中上部（不靠下）
ROW_GAP = 172                    # 相邻物理行固定垂直间距（统一节奏，行与行分开）
MAX_HIST = 2                     # 当前行上方最多显示的历史行数（已读、向上淡出）
MAX_NEXT = 2                     # 当前行下方最多显示的未读行数（暗黄、待讲）
NEXT_SIZE = 62                   # 未读行字号（比当前行小，明显"待讲"态）

# 字幕可调参数（CLI --subtitle-* 覆盖；main() 中按字号比例派生 hist/next/行距/锚点）。
# 默认与上方恒定值一致，未传参时渲染结果完全不变。
SUB_SIZE = HL_SIZE               # 当前行字号（默认 92）
SUB_MAX_LINES = 3                # 单条字幕最大折行数（默认 3）
SUB_STROKE = STROKE_W            # 当前行描边宽度（默认 5）
SUB_POSITION = "bottom"          # 字幕块垂直位置：bottom / center
SUB_HIST = HIST_SIZE             # 历史行字号（按比例派生）
SUB_NEXT = NEXT_SIZE             # 未读行字号（按比例派生）
SUB_GAP = ROW_GAP                # 行间距（按比例派生）
SUB_CURRENT_Y = CURRENT_Y       # 当前行 Y 锚点（按位置派生）
HIST_ALPHA_BASE = 230            # 最新历史行不透明度
HIST_ALPHA_STEP = 70             # 每往上一行透明度下降，最老行逐渐隐去
NEXT_ALPHA = 200                 # 未读行不透明度（暗黄、弱存在感）
NEXT_RGB = (135, 142, 158)        # 未读行暗黄，明显比当前行暗，提示"还没讲到"

# 字幕整体风格（CLI --subtitle-style 覆盖）：dynamic=卡拉OK高亮(默认) / minimal=纯净白字 / bubble=气泡底衬
SUBTITLE_STYLE = "dynamic"

# ---------------------------------------------------------------- 工具
def _cjk_wrap(draw, text, font, max_w):
    """纯中文/中英混排按字符贪婪换行（无空格分词）。
    额外修正：
      1. 遇到标点导致溢出时，优先把标点留在上一行末尾（允许 6% 轻微溢出），避免新行以标点开头。
      2. 禁止出现仅含标点的孤行；若有，合并回上一行（允许最多 12% 轻微溢出），保证视觉完整。"""
    lines, cur = [], ""
    PUNCT = "。，、；：！？）」』》.,;:!?)]}>"
    for ch in text:
        would_fit = draw.textlength(cur + ch, font=font) <= max_w
        if would_fit:
            cur += ch
        else:
            # 若溢出字符是标点，尝试把它“吸”到当前行末尾（允许 6% 轻微溢出），
            # 让下一行从非标点字符开始。
            if ch in PUNCT and cur and draw.textlength(cur + ch, font=font) <= max_w * 1.06:
                cur += ch
            else:
                if cur:
                    lines.append(cur)
                cur = ch
    if cur:
        lines.append(cur)
    # 后处理：禁止仅含标点的孤行，强制合并回上一行（单个标点不会导致明显溢出）
    cleaned = []
    for ln in lines:
        if cleaned and ln and all(c in PUNCT for c in ln):
            cleaned[-1] = cleaned[-1] + ln
            continue
        cleaned.append(ln)
    return cleaned


def _wrap_to_lines(draw, text, base_size, max_w, max_lines, min_size):
    """优先保证 行数<=max_lines 且 单行宽<=max_w；放不下则缩字号到 min_size，仍超则按 min_size 分行。"""
    from PIL import ImageFont
    size = base_size
    while size >= min_size:
        font = ImageFont.truetype(FONT_PATH, size)
        lines = _cjk_wrap(draw, text, font, max_w)
        if len(lines) <= max_lines:
            return lines, size
        size -= 2
    # 退到最小字号仍超：就用最小字号分行（可能超过 max_lines，但保证不溢出宽度）
    font = ImageFont.truetype(FONT_PATH, min_size)
    return _cjk_wrap(draw, text, font, max_w), min_size


def _draw_text_with_stroke(draw, xy, text, font, fill, stroke_w=STROKE_W, stroke_fill=(0, 0, 0)):
    draw.text(xy, text, font=font, fill=fill, stroke_width=stroke_w, stroke_fill=stroke_fill)


# ---------------------------------------------------------------- 强调 / 关键词高亮
def _clean_markers(text):
    """去掉 ** 标记，得到纯朗读文本（供 TTS 使用）。"""
    return text.replace("**", "")


def _split_by_keywords(chunk):
    """把 chunk 中命中的关键词切成强调段，其余为普通段。"""
    out, i, buf = [], 0, ""
    while i < len(chunk):
        hit = None
        for kw in KEYWORDS:
            if chunk.startswith(kw, i):
                hit = kw
                break
        if hit:
            if buf:
                out.append((False, buf)); buf = ""
            out.append((True, hit))
            i += len(hit)
        else:
            buf += chunk[i]; i += 1
    if buf:
        out.append((False, buf))
    return out


def split_emphasis(raw):
    """将文本拆成 (是否强调, 文本段) 列表。
    - 手动标记 **...** 视为强调
    - 其余文本自动高亮命中 KEYWORDS 的关键词
    """
    parts, i, buf = [], 0, ""
    while i < len(raw):
        if raw.startswith("**", i):
            if buf:
                parts.append((False, buf)); buf = ""
            j = raw.find("**", i + 2)
            if j == -1:
                buf = raw[i + 2:]; i = len(raw); break
            parts.append((True, raw[i + 2:j])); i = j + 2
        else:
            buf += raw[i]; i += 1
    if buf:
        parts.append((False, buf))
    out = []
    for emph, chunk in parts:
        if emph:
            out.append((True, chunk))
        else:
            out.extend(_split_by_keywords(chunk))
    return out


def _wrap_chars(draw, chars, font, max_w):
    """chars: list[(ch, emph)]，按宽度贪婪换行，保留每字强调标记。"""
    lines, cur, cw = [], [], 0.0
    for ch, emph in chars:
        w = draw.textlength(ch, font=font)
        if cw + w > max_w and cur:
            lines.append(cur); cur, cw = [], 0.0
        cur.append((ch, emph)); cw += w
    if cur:
        lines.append(cur)
    return lines


def _layout_chars(draw, raw, base_size, max_w, max_lines, min_size):
    """返回 (lines, fs)；lines: list[list[(ch, emph)]]，优先保证 行数<=max_lines。"""
    size = base_size
    while size >= min_size:
        font = ImageFont.truetype(FONT_PATH, size)
        segs = split_emphasis(raw)
        chars = [(c, e) for e, chunk in segs for c in chunk]
        lines = _wrap_chars(draw, chars, font, max_w)
        if len(lines) <= max_lines:
            return lines, size
        size -= 2
    font = ImageFont.truetype(FONT_PATH, min_size)
    segs = split_emphasis(raw)
    chars = [(c, e) for e, chunk in segs for c in chunk]
    return _wrap_chars(draw, chars, font, max_w), min_size


# ---------------------------------------------------------------- 背景动画（numpy）
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def gen_seaside(t, w=W, h=H):
    """浅色海景 + 沙滩潮汐拍滚（numpy 程序化，海水在沙滩轻轻拍滚）。"""
    yy = np.arange(h)[:, None].astype(np.float32)
    xx = np.arange(w)[None, :].astype(np.float32)
    img = np.empty((h, w, 3), dtype=np.float32)

    horizon = h * 0.55
    beach = h * 0.80

    # 天空：顶部浅蓝 -> 地平线近白
    sky_top = np.array([205, 232, 252], dtype=np.float32)
    sky_bot = np.array([232, 245, 255], dtype=np.float32)
    f = np.clip(yy / horizon, 0, 1)  # (h,1)
    sky = (sky_top[None, None, :] * (1 - f)[..., None]
           + sky_bot[None, None, :] * f[..., None])  # (h,1,3)

    # 海面：青蓝 + 流动波纹高光
    sea_base = np.array([70, 158, 205], dtype=np.float32)
    ripple = 26.0 * np.sin(xx * 0.012 + t * 0.9 + yy * 0.010)  # (h,w)
    sea = sea_base[None, None, :] + ripple[..., None]  # (h,w,3)
    # 近岸海面略浅
    shallow = np.clip((yy - horizon) / (beach - horizon), 0, 1) * 30  # (h,1)
    sea = sea + shallow[..., None]  # (h,1,1) broadcasts over (h,w,3)

    # 沙滩：米黄
    sand = np.array([238, 223, 184], dtype=np.float32)[None, None, :]  # (1,1,3)

    # 行选择掩码 (h,1,1)，可正确广播到 (h,w,3)
    m_sky = (yy < horizon)[..., None]
    m_sea = ((yy >= horizon) & (yy < beach))[..., None]
    bg = np.where(m_sky, sky, np.where(m_sea, sea, sand))

    # 潮汐泡沫带：在 beach 线附近随相位上下拍滚
    foam_y = beach + 22 * np.sin(t * 1.15 + xx * 0.006)  # (1,w)
    dist = np.abs(yy - foam_y)  # (h,w)
    foam = np.clip(1.0 - dist / 14.0, 0, 1)  # (h,w) 泡沫强度
    foam_rgb = np.array([252, 252, 250], dtype=np.float32)[None, None, :]
    bg = bg * (1 - foam[..., None]) + foam_rgb * foam[..., None]

    return np.clip(bg, 0, 255).astype(np.uint8)


def gen_black_gold(t, w=W, h=H):
    """黑金流动背景。"""
    yy = np.arange(h)[:, None].astype(np.float32)
    xx = np.arange(w)[None, :].astype(np.float32)
    val = 38 + 34 * np.sin((xx + yy) * 0.0045 + t * 0.8)
    val = np.clip(val, 0, 255)
    g = val[..., None]
    rgb = np.concatenate([g, g * 0.80, g * 0.22], axis=2)
    # 金色对角光晕（缓慢流动，克制）
    cx = w * 0.75 + 40.0 * np.sin(t * 0.32)
    cy = h * 0.25 + 20.0 * np.cos(t * 0.26)
    d2 = (xx - cx) ** 2 + (yy - cy) ** 2
    glow = np.exp(-d2 / (2 * 170.0 ** 2))
    gold = np.array([201, 162, 39], dtype=np.float32)[None, None, :]
    rgb = rgb + glow[..., None] * gold * 0.22
    return np.clip(rgb, 0, 255).astype(np.uint8)


def gen_deepnavy(t, w=W, h=H):
    """深海军蓝渐变 + 金色光晕 + 光点上浮（财税高级感，对标财经大号）。
    降采样(1/4)计算后上采样，性能优化：全图 exp 从 6 次降为低分辨率 6 次，速度提升 ~16 倍。"""
    S = 4
    sw, sh = w // S, h // S
    yy = np.arange(sh)[:, None].astype(np.float32) * S
    xx = np.arange(sw)[None, :].astype(np.float32) * S

    # 1) 深蓝渐变（提亮：可辨识的深蓝，非近黑）
    top = np.array([30, 52, 100], dtype=np.float32)
    bot = np.array([14, 26, 56], dtype=np.float32)
    f = np.clip(yy / h, 0, 1)
    base = top[None, None, :] * (1 - f)[..., None] + bot[None, None, :] * f[..., None]

    gold = np.array([214, 176, 54], dtype=np.float32)[None, None, :]

    # 2) 金色对角光晕（明显，缓慢流动）
    cx = w * 0.78 + 40.0 * np.sin(t * 0.35)
    cy = h * 0.22 + 20.0 * np.cos(t * 0.28)
    d2 = (xx - cx) ** 2 + (yy - cy) ** 2
    glow = np.exp(-d2 / (2 * 210.0 ** 2))
    bg = base + glow[..., None] * gold * 0.55

    # 3) 顶部金色装饰带
    band = np.exp(-((yy - 110.0) ** 2) / (2 * 28.0 ** 2))
    bg = bg + band[..., None] * gold * 0.28

    # 4) 金色光点上浮
    dots = np.zeros((sh, sw), dtype=np.float32)
    for (px, py, amp, sp) in [(0.18, 0.62, 0.14, 0.20), (0.62, 0.80, 0.11, 0.16),
                               (0.38, 0.45, 0.09, 0.24), (0.85, 0.55, 0.12, 0.18),
                               (0.50, 0.30, 0.10, 0.22)]:
        dx = px * w + 30.0 * np.sin(t * sp)
        dy = (py * h - 20.0 * t * sp) % h
        d2 = (xx - dx) ** 2 + (yy - dy) ** 2
        dots = dots + np.exp(-d2 / (2 * 95.0 ** 2)) * amp
    bg = bg + dots[..., None] * gold * 0.7

    # 上采样回全分辨率
    bg = np.repeat(np.repeat(bg, S, axis=0), S, axis=1)[:h, :w]
    return np.clip(bg, 0, 255).astype(np.uint8)


def gen_inkblue(t, w=W, h=H):
    """墨蓝 + 网格线 + 顶部光线扫过（数据/专业感，适合政策解读与干货清单）。
    降采样计算 + 网格用取模判断（去掉全图 exp 循环），性能优化。"""
    S = 4
    sw, sh = w // S, h // S
    yy = np.arange(sh)[:, None].astype(np.float32) * S
    xx = np.arange(sw)[None, :].astype(np.float32) * S

    # 墨蓝渐变（提亮）
    top = np.array([26, 44, 82], dtype=np.float32)
    bot = np.array([14, 26, 54], dtype=np.float32)
    f = np.clip(yy / h, 0, 1)
    bg = top[None, None, :] * (1 - f)[..., None] + bot[None, None, :] * f[..., None]

    # 网格线（取模判断，快；硬边细线 + 低强度）
    step = 108
    grid = (((xx % step) < 2).astype(np.float32) + ((yy % step) < 2).astype(np.float32))
    cyan = np.array([110, 175, 220], dtype=np.float32)[None, None, :]
    bg = bg + grid[..., None] * cyan * 0.22

    # 顶部光线斜扫
    sweep = np.exp(-((xx - (w * 0.5 + 160.0 * np.sin(t * 0.3))) ** 2) / (2 * 70.0 ** 2))
    sweep = sweep * np.clip(1.0 - yy / h, 0, 1)
    bg = bg + sweep[..., None] * cyan * 0.16

    # 金色点缀光点
    gold = np.array([214, 176, 54], dtype=np.float32)[None, None, :]
    dots = np.zeros((sh, sw), dtype=np.float32)
    for (px, py, amp, sp) in [(0.30, 0.70, 0.10, 0.18), (0.70, 0.45, 0.08, 0.22),
                               (0.50, 0.85, 0.09, 0.15)]:
        dx = px * w + 25.0 * np.sin(t * sp)
        dy = (py * h - 15.0 * t * sp) % h
        d2 = (xx - dx) ** 2 + (yy - dy) ** 2
        dots = dots + np.exp(-d2 / (2 * 90.0 ** 2)) * amp
    bg = bg + dots[..., None] * gold * 0.5

    # 上采样回全分辨率
    bg = np.repeat(np.repeat(bg, S, axis=0), S, axis=1)[:h, :w]
    return np.clip(bg, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- 对话解析 + TTS
def parse_dialogue(path, default_role="M"):
    segs = []
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    return parse_dialogue_text(text, default_role)


def parse_dialogue_text(text, default_role="M"):
    """解析 女：/男：/旁白： 对话体。
    - 若整段无任何角色前缀（纯独白），则全部段落统一使用 default_role（"M"=男声 / "F"=女声）。
      用于单人独白场景：男声独白传 "M"、女声独白传 "F"。
    - 若含角色前缀，则按行分配：女：/女: → F，男：/男: → M，旁白：→ M，无前缀行 → M。"""
    RAW_PREFIXES = ("女：", "女:", "男：", "男:", "旁白：", "旁白:")
    def _has_prefix(l):
        s = l.strip()
        return any(s.startswith(p) for p in RAW_PREFIXES)
    lines = [l for l in text.splitlines() if l.strip()]
    # 纯独白（无任一行带角色前缀）：整段统一用 default_role，实现男/女单人独白
    if lines and not any(_has_prefix(l) for l in lines):
        segs = [(default_role, l.strip()) for l in lines if l.strip()]
        return segs
    # 含角色前缀：逐行解析
    segs = []
    for line in lines:
        line = line.strip()
        if line.startswith("女") or line.startswith("女：") or line.startswith("女:"):
            role = "F"
            text = line[line.find("：") + 1:] if "：" in line else line[line.find(":") + 1:]
        elif line.startswith("男") or line.startswith("男：") or line.startswith("男:"):
            role = "M"
            text = line[line.find("：") + 1:] if "：" in line else line[line.find(":") + 1:]
        elif line.startswith("旁白") or line.startswith("旁白：") or line.startswith("旁白:"):
            # 旁白默认使用男声；后续如需独立旁白声线可扩展为 "N"
            role = "M"
            text = line[line.find("：") + 1:] if "：" in line else line[line.find(":") + 1:]
        else:
            # 无角色前缀：默认男声
            role = "M"
            text = line
        text = text.strip()
        if text:
            segs.append((role, text))
    return segs


def synth_dialogue_audio(dialogue_text, out_wav, dry=False, gap=0.28,
                         female_voice=FEMALE_VOICE, female_model=FEMALE_MODEL,
                         male_voice=MALE_VOICE, male_model=MALE_MODEL,
                         male_rate=0.98, female_rate=0.98, male_pitch=0.95,
                         female_pitch=1.02, male_vol=53, female_vol=49,
                         default_role="M"):
    """独立合成男女双声对话音频（平台「出音频」对话试听用）。"""
    segs = parse_dialogue_text(dialogue_text, default_role)
    if not segs:
        raise SystemExit("对话稿为空或解析失败")
    tmpdir = tempfile.mkdtemp(prefix="scroll_audio_")
    try:
        seg_wavs, t = [], 0.0
        for i, (role, text) in enumerate(segs):
            wav = os.path.join(tmpdir, f"a_{i:03d}.wav")
            d = tts_one(text, role, wav, dry, female_voice, female_model, male_voice, male_model,
                         male_rate=male_rate, female_rate=female_rate, male_pitch=male_pitch,
                         female_pitch=female_pitch, male_vol=male_vol, female_vol=female_vol)
            seg_wavs.append(wav)
            t += d + (0 if i == len(segs) - 1 else gap)
        if len(seg_wavs) == 1:
            shutil.copy(seg_wavs[0], out_wav)
        else:
            build_audio(seg_wavs, gap, out_wav, tmpdir)
        return out_wav, segs
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _wav_duration(path):
    """用 ffprobe 取真实音频时长；某些 TTS 返回的 wav 头 nframes 异常，ffprobe 按实际数据解码更准确。"""
    ffprobe = r"D:\ffmpeg\ffmpeg-8.1.2-full_build\bin\ffprobe.exe"
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, check=True)
        return float(r.stdout.strip())
    except Exception:
        with wave.open(path, "rb") as wf:
            return wf.getnframes() / wf.getframerate()


def tts_one(text, role, out_wav, dry, female_voice, female_model, male_voice, male_model,
            male_rate=0.98, female_rate=0.98, male_pitch=0.95, female_pitch=1.02,
            male_vol=53, female_vol=49, emotion="narrate"):
    """逐句合成。支持分声线独立调感情/快慢，并按情绪映射表施加韵律起伏。
    speech_rate 越低越慢；pitch_rate 越高越亮/尖；volume 为音量(0-100)。
    emotion 取值见 EMOTION_PROSODY：narrate/emphasis/warn/query/light/ending。"""
    voice = female_voice if role == "F" else male_voice
    model = female_model if role == "F" else male_model
    prof = EMOTION_PROSODY.get(emotion, EMOTION_PROSODY["narrate"])
    role_key = "F" if role == "F" else "M"
    rel_sr, rel_pr, rel_vol = prof["rel"]
    # 情绪相对倍率叠加在 CLI 基准之上（基准=男女声默认音色，情绪=起伏曲线）
    speech_rate = round((female_rate if role == "F" else male_rate) * rel_sr, 3)
    pitch_rate = round((female_pitch if role == "F" else male_pitch) * rel_pr, 3)
    volume = int(round((female_vol if role == "F" else male_vol) * rel_vol))
    # 轻微长短句微调（在情绪基准上再叠加，不喧宾夺主）
    _n = len(text.strip())
    if _n <= 10:
        speech_rate = round(speech_rate * 1.03, 3)
    elif _n >= 36:
        speech_rate = round(speech_rate * 0.95, 3)
    if dry or _qwen_synth is None:
        # 静音占位（2.4s），仅验证渲染/编码链路
        subprocess.run(
            [FFMPEG, "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
             "-t", "2.4", "-c:a", "pcm_s16le", out_wav],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return 2.4
    try:
        # 分声线参数：男声基准 speech_rate 稍快、叠加长短句快慢节奏；女声略快亲和；pitch 微调冷暖
        _qwen_synth(_clean_markers(text), voice, out_wav, model=model,
                    speech_rate=speech_rate, pitch_rate=pitch_rate, volume=volume)
    except SystemExit:
        raise
    return _wav_duration(out_wav)


def build_audio(seg_wavs, gap, out_audio, tmpdir):
    """句间插入 gap 秒静音，用 concat demuxer 拼成总音频。"""
    gap_wav = os.path.join(tmpdir, "gap.wav")
    subprocess.run(
        [FFMPEG, "-y", "-f", "lavfi", "-i", f"anullsrc=r=22050:cl=mono",
         "-t", f"{gap:.3f}", "-c:a", "pcm_s16le", gap_wav],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    files = []
    for i, sw in enumerate(seg_wavs):
        files.append(sw)
        if i < len(seg_wavs) - 1:
            files.append(gap_wav)
    listfile = os.path.join(tmpdir, "audio_list.txt")
    with open(listfile, "w", encoding="utf-8") as f:
        for p in files:
            f.write(f"file '{p.replace(chr(92), '/')}'\n")
    subprocess.run(
        [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", listfile, "-c", "copy", out_audio],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out_audio


# ---------------------------------------------------------------- 渲染
def _build_flat_lines(segs, starts, durs, draw):
    """把每段对话拆成物理行，并给每行按字数比例切分该段时长，分配时间区间。
    返回 list[dict]: seg, role, chars(list[(ch,emph)]), start, end,
                     char_off(段内字符偏移), seg_start, seg_dur, seg_total。
    这样渲染时可「逐物理行」像提词器一样滚动，而不是整段折多行一次性出现。
    """
    flat = []
    for i, (role, text) in enumerate(segs):
        raw = _clean_markers(text)
        lines, fs = _layout_chars(draw, raw, SUB_SIZE, MAX_W, max_lines=SUB_MAX_LINES, min_size=58)
        # 兜底：长句在最小字号仍折行时，若最后一行只剩 ≤3 个字符，把它合并回上一行，
        # 避免画面出现孤零零的小尾巴（如"了。"）。轻微溢出由 MAX_W 的屏幕边距消化。
        if len(lines) >= 2 and len(lines[-1]) <= 3:
            lines[-2].extend(lines[-1])
            lines = lines[:-1]
        seg_dur = durs[i]
        seg_total = max(1, sum(len(ln) for ln in lines))
        acc = 0
        for ln in lines:
            n = len(ln)
            ls = starts[i] + (acc / seg_total) * seg_dur
            le = starts[i] + ((acc + n) / seg_total) * seg_dur
            flat.append({
                "seg": i, "role": role, "chars": ln,
                "start": ls, "end": le, "char_off": acc,
                "seg_start": starts[i], "seg_dur": seg_dur, "seg_total": seg_total,
                "size": fs,                       # 该段实际换行字号，渲染必须一致，否则溢出
            })
            acc += n
    return flat


def _find_current_line(flat, tc):
    """按内容时间 tc 定位当前物理行：落在 [start,end) 内即命中；否则取最后一个 start<=tc 的。"""
    for i, fl in enumerate(flat):
        if fl["start"] <= tc < fl["end"]:
            return i
    idx = 0
    for i, fl in enumerate(flat):
        if fl["start"] <= tc:
            idx = i
    return idx


def _fit_bg(im, mode="fill"):
    """把背景图按缩放模式适配到画布 (W,H)。
    fill(填充/cover)   : 等比放大到覆盖全屏，居中裁切（全幅铺满，无黑边，最常用）
    contain(适应)      : 等比缩放到完整可见，居中放置，多余处填黑（留黑边）
    stretch(拉伸)      : 直接拉满画布（可能变形，原默认行为）
    """
    im = im.convert("RGB")
    iw, ih = im.size
    if mode == "stretch":
        return im.resize((W, H))
    if mode == "fill":  # cover
        scale = max(W / iw, H / ih)
        nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
        scaled = im.resize((nw, nh))
        left = (nw - W) // 2
        top = (nh - H) // 2
        return scaled.crop((left, top, left + W, top + H))
    # contain
    scale = min(W / iw, H / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    scaled = im.resize((nw, nh))
    base = Image.new("RGB", (W, H), (0, 0, 0))
    base.paste(scaled, ((W - nw) // 2, (H - nh) // 2))
    return base


def render_frame(bg_rgb, flat, tc, t,
                 bg_static=None, bg_frames=None, bg_fps=1.0, intro=False,
                 title="", subtitle=""):
    """返回 PIL.Image（1080x1920），已叠加字幕窗口与品牌条。
    flat: _build_flat_lines 输出；tc: 内容时间轴（已扣除 intro 静音前缀）。
    渲染逻辑（提词器式，从上往下阅读）：
      当前行固定在 CURRENT_Y 高亮（卡拉OK 逐字）；
      已读行在其上方逐行上移并淡出；
      未读行在其下方暗显、待进入。
    """
    if bg_frames is not None:
        idx = int(t * bg_fps) % len(bg_frames)
        img = bg_frames[idx].copy().convert("RGBA")
    elif bg_static is not None:
        img = bg_static.copy().resize((W, H)).convert("RGBA")
    else:
        img = Image.fromarray(bg_rgb).convert("RGBA")
    draw = ImageDraw.Draw(img, "RGBA")

    from PIL import ImageFont

    if intro:
        f = ImageFont.truetype(FONT_PATH, 72)
        _draw_text_with_stroke(draw, (W // 2 - draw.textlength("老张讲财税", font=f) / 2, H // 2 - 60),
                               "老张讲财税", f, (255, 255, 255), stroke_w=3)
        f2 = ImageFont.truetype(FONT_PATH, 40)
        _draw_text_with_stroke(draw, (W // 2 - draw.textlength("建筑财税·避坑指南", font=f2) / 2, H // 2 + 40),
                               "建筑财税·避坑指南", f2, (220, 230, 245), stroke_w=2)
        _draw_brand(draw)
        return img.convert("RGB")

    if not flat:
        _draw_brand(draw)
        if title:
            _draw_title(draw, title, subtitle)
        return img.convert("RGB")

    cur_idx = _find_current_line(flat, tc)
    fl = flat[cur_idx]

    # 当前行卡拉OK已读字符数：段内全局 done → 当前行局部 done
    seg_prog = (tc - fl["seg_start"]) / fl["seg_dur"] if fl["seg_dur"] > 0 else 0.0
    seg_prog = min(1.0, max(0.0, seg_prog))
    done_global = seg_prog * fl["seg_total"]
    local_done = int(min(len(fl["chars"]), max(0, done_global - fl["char_off"])))

    # 收集可见行：上方历史（已读、淡出） / 当前（高亮） / 下方未读（暗显）
    items = []  # (y, kind, flat_line, depth)
    hi = cur_idx - 1
    depth = 1
    while hi >= 0 and depth <= MAX_HIST:
        items.append((SUB_CURRENT_Y - depth * SUB_GAP, "hist", flat[hi], depth))
        depth += 1
        hi -= 1
    items.append((SUB_CURRENT_Y, "cur", fl, 0))
    ui = cur_idx + 1
    depth = 1
    while ui < len(flat) and depth <= MAX_NEXT:
        items.append((SUB_CURRENT_Y + depth * SUB_GAP, "next", flat[ui], depth))
        depth += 1
        ui += 1

    # 自上而下绘制（上方先画，下方后画；无重叠，纯为层次稳定）
    style = SUBTITLE_STYLE
    for y, kind, fll, depth in sorted(items, key=lambda it: it[0]):
        # 必须使用该行实际换行字号；长句被缩字号后若硬用大号会溢出屏幕
        base_size = fll.get("size", SUB_SIZE)
        if kind == "cur":
            font = ImageFont.truetype(FONT_PATH, base_size)
            if style == "bubble":
                _draw_bubble(draw, fll["chars"], y, font, 210)
            if style == "minimal":
                # 纯净白字：当前行全部按"已读"着色，去掉卡拉OK逐字渐变
                _draw_karaoke_line(draw, fll["chars"], y, font, 0, len(fll["chars"]))
            else:
                _draw_karaoke_line(draw, fll["chars"], y, font,
                                   fll["char_off"], fll["char_off"] + local_done)
        elif kind == "hist":
            font = ImageFont.truetype(FONT_PATH, min(SUB_HIST, base_size))
            alpha = max(40, HIST_ALPHA_BASE - (depth - 1) * HIST_ALPHA_STEP)
            if style == "bubble":
                _draw_bubble(draw, fll["chars"], y, font, max(70, alpha))
            _draw_history_line(draw, fll["chars"], y, font, alpha)
        else:  # next（未读）
            font = ImageFont.truetype(FONT_PATH, min(SUB_NEXT, base_size))
            if style == "bubble":
                _draw_bubble(draw, fll["chars"], y, font, NEXT_ALPHA)
            _draw_next_line(draw, fll["chars"], y, font, NEXT_ALPHA)

    _draw_brand(draw)
    if title:
        _draw_title(draw, title, subtitle)
    return img.convert("RGB")


def _draw_karaoke_line(draw, line, y, font, done_start, done_end):
    """绘制当前段落的单个物理行；done_start/end 是相对当前段落首字符的已读范围。"""
    lw = sum(draw.textlength(ch, font=font) for ch, _ in line)
    x = LEFT_X
    for idx, (ch, emph) in enumerate(line):
        gi = done_start + idx
        if done_start <= gi < done_end:
            col = EMPH_DONE if emph else SUB_DONE
        else:
            col = EMPH_TODO if emph else SUB_TODO
        _draw_text_with_stroke(draw, (x, y), ch, font, col, stroke_w=SUB_STROKE)
        x += draw.textlength(ch, font=font)


def _draw_history_line(draw, line, y, font, alpha=255):
    """绘制历史段落的单个物理行；alpha 越低越淡（已读越久越隐去）。"""
    lw = sum(draw.textlength(ch, font=font) for ch, _ in line)
    x = LEFT_X
    fill_base = HIST_RGB + (alpha,)
    emph_fill = EMPH_HIST + (alpha,)
    stroke_fill = STROKE_FILL + (alpha,)
    for ch, emph in line:
        _draw_text_with_stroke(draw, (x, y), ch, font,
                               emph_fill if emph else fill_base,
                               stroke_w=2, stroke_fill=stroke_fill)
        x += draw.textlength(ch, font=font)


def _draw_next_line(draw, line, y, font, alpha=255):
    """绘制未读行：暗黄、低存在感，提示"还没讲到"。"""
    lw = sum(draw.textlength(ch, font=font) for ch, _ in line)
    x = LEFT_X
    fill_base = NEXT_RGB + (alpha,)
    emph_fill = EMPH_HIST + (alpha,)
    stroke_fill = STROKE_FILL + (alpha,)
    for ch, emph in line:
        _draw_text_with_stroke(draw, (x, y), ch, font,
                               emph_fill if emph else fill_base,
                               stroke_w=2, stroke_fill=stroke_fill)
        x += draw.textlength(ch, font=font)


def _line_width(draw, chars, font):
    """计算一行字符（list[(ch,emph)]）的总像素宽度。"""
    return sum(draw.textlength(ch, font=font) for ch, _ in chars)


def _draw_bubble(draw, chars, y, font, alpha=200):
    """在当前行文字下方绘制半透明圆角气泡底衬（bubble 字幕风格用）。"""
    w = _line_width(draw, chars, font)
    pad_x, pad_y = 20, 12
    x0 = LEFT_X - pad_x
    x1 = LEFT_X + w + pad_x
    y0 = y - pad_y
    y1 = y + font.size + pad_y
    draw.rounded_rectangle([x0, y0, x1, y1], radius=20,
                           fill=(12, 18, 34, alpha),
                           outline=(255, 255, 255, int(alpha * 0.55)))


def _draw_brand(draw):
    from PIL import ImageFont
    f = ImageFont.truetype(FONT_PATH, 38)
    txt = "追梦 · 老张讲财税"
    x = (W - draw.textlength(txt, font=f)) / 2
    _draw_text_with_stroke(draw, (x, H - 96), txt, f, BRAND_RGB, stroke_w=2)


def _auto_title(segs):
    """自动生成 ≤10 字标题：取首句核心短语（去开场客套、截到首个断句标点前）。"""
    leadins = ["张老师，", "张老师:", "老师，", "老师:", "我想请教一下", "我想问一下",
               "我想咨询", "啊，", "哎，", "那个", "请问", "您说", "是这样的"]
    for role, text in segs:
        t = _clean_markers(text).strip()
        for p in leadins:
            if t.startswith(p):
                t = t[len(p):].strip()
        # 截到首个断句标点前，得到核心短语
        for sep in ["，", "？", "?", "。", "！"]:
            if sep in t:
                t = t.split(sep)[0]
                break
        t = t.strip().rstrip("呢吗吧啊呀哦呃")
        if t:
            return t[:TITLE_MAX_CHARS]
    return "追梦短视频"


def _draw_title(draw, title, subtitle=""):
    """顶部固定标题：深蓝底纹条 + 大字(金+深蓝描边) + 金色装饰线，不随字幕滚动。
    可选副标题：标题金线下方一行小字（金+深蓝描边）。"""
    from PIL import ImageFont
    font = ImageFont.truetype(FONT_PATH, TITLE_SIZE)
    # 自动换行（按字符），最多 2 行，确保不超宽
    lines = _cjk_wrap(draw, title, font, MAX_W)[:2]
    line_h = int(TITLE_SIZE * 1.12)
    pad_y = TITLE_PAD_Y
    band_top = TITLE_TOP - pad_y
    band_bottom = TITLE_TOP + len(lines) * line_h + pad_y
    # 标题底纹条（深蓝半透明，整行宽，增强视觉冲击力）
    draw.rectangle([0, band_top, W, band_bottom],
                   fill=(TITLE_BAND[0], TITLE_BAND[1], TITLE_BAND[2], TITLE_BAND_A))
    # 标题文字（金 + 深蓝描边）
    y = TITLE_TOP
    widths = []
    for line in lines:
        lw = draw.textlength(line, font=font)
        widths.append(lw)
        x = (W - lw) / 2
        _draw_text_with_stroke(draw, (x, y), line, font, TITLE_FILL,
                               stroke_w=TITLE_STROKE_W, stroke_fill=TITLE_STROKE)
        y += line_h
    # 标题下装饰金线（宽度取最宽行，居中）
    rule_w = min(MAX_W, max(widths) + 36)
    ry = band_bottom + 6
    draw.line([(W / 2 - rule_w / 2, ry), (W / 2 + rule_w / 2, ry)],
              fill=TITLE_RULE, width=TITLE_RULE_W)
    # 副标题（金线下方一行，金+深蓝描边；自动换行最多 2 行）
    if subtitle and subtitle.strip():
        sfont = ImageFont.truetype(FONT_PATH, SUBTITLE_SIZE)
        slines = _cjk_wrap(draw, subtitle.strip(), sfont, MAX_W)[:2]
        sline_h = int(SUBTITLE_SIZE * 1.25)
        sy = ry + SUBTITLE_GAP
        for sl in slines:
            slw = draw.textlength(sl, font=sfont)
            _draw_text_with_stroke(draw, ((W - slw) / 2, sy), sl, sfont,
                                   SUBTITLE_FILL, stroke_w=SUBTITLE_STROKE_W,
                                   stroke_fill=SUBTITLE_STROKE)
            sy += sline_h


# ---------------------------------------------------------------- 主流程
def make_video(dialogue, out_path, bg_style="deepnavy", bg_image=None, dry=False,
               gap=0.28, no_intro=False, bgm=None, title=None, subtitle=None,
               bg_fit="fill",
               female_voice=FEMALE_VOICE, female_model=FEMALE_MODEL,
               male_voice=MALE_VOICE, male_model=MALE_MODEL,
               male_rate=0.98, female_rate=0.98, male_pitch=0.95,
               female_pitch=1.02, male_vol=53, female_vol=49,
               subtitle_size=HL_SIZE, subtitle_lines=3, subtitle_outline=STROKE_W,
               subtitle_position="bottom",
               subtitle_style="dynamic", export_ass=False, no_burn_sub=False,
               default_role="M"):
    # 字幕可调参数 → 派生模块全局（历史/未读/行距/锚点随当前行字号同比例，避免重叠或溢出）
    ratio = max(0.4, min(1.6, subtitle_size / HL_SIZE))
    globals().update(dict(
        SUB_SIZE=subtitle_size,
        SUB_MAX_LINES=subtitle_lines,
        SUB_STROKE=subtitle_outline,
        SUB_POSITION=subtitle_position,
        SUB_HIST=max(20, int(round(HIST_SIZE * ratio))),
        SUB_NEXT=max(20, int(round(NEXT_SIZE * ratio))),
        SUB_GAP=max(60, int(round(ROW_GAP * ratio))),
        SUB_CURRENT_Y=(H // 2) if subtitle_position == "center" else CURRENT_Y,
        SUBTITLE_STYLE=subtitle_style,
    ))
    segs = parse_dialogue(dialogue, default_role)
    if not segs:
        raise SystemExit("对话文件为空或解析失败")
    title = title or _auto_title(segs)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tmpdir = tempfile.mkdtemp(prefix="scroll_")

    # 0) 情绪标注（一次 DeepSeek 调用标注整段对话，带内容哈希缓存；失败回退 narrate）
    emotions = annotate_emotions(segs)
    print(f"[INFO] 情绪标注: {emotions}")
    # 1) TTS 逐句（按情绪映射表施加语速/音高/音量/停顿，自然起伏去机械感）
    seg_wavs, starts, durs = [], [], []
    t = 0.0
    for i, (role, text) in enumerate(segs):
        emo = emotions[i] if i < len(emotions) else "narrate"
        wav = os.path.join(tmpdir, f"a_{i:03d}.wav")
        d = tts_one(text, role, wav, dry, female_voice, female_model, male_voice, male_model,
                    male_rate=male_rate, female_rate=female_rate, male_pitch=male_pitch,
                    female_pitch=female_pitch, male_vol=male_vol, female_vol=female_vol,
                    emotion=emo)
        seg_wavs.append(wav)
        starts.append(t)
        durs.append(d)
        pause_i = EMOTION_PROSODY.get(emo, EMOTION_PROSODY["narrate"])["pause"]
        t += d + (0 if i == len(segs) - 1 else max(gap, pause_i))
    total = t if seg_wavs else 0
    # 预计算物理行时间轴（提词器式逐行滚动用）
    _tmp_draw = ImageDraw.Draw(Image.new("RGBA", (W, H)))
    flat = _build_flat_lines(segs, starts, durs, _tmp_draw)
    # 不烧字幕模式：仅背景+品牌+标题，字幕交由后续 auto_edit 重烧
    render_flat = [] if no_burn_sub else flat
    if export_ass:
        _export_ass(flat, out_path)
    intro_dur = 0.0 if no_intro else 1.4

    audio_wav = os.path.join(tmpdir, "audio_total.wav")
    build_audio(seg_wavs, gap, audio_wav, tmpdir)
    # 修复开场不同步：intro 标题页期间音频应为静音，使音画时间轴对齐
    if intro_dur > 0:
        intro_sil = os.path.join(tmpdir, "intro_silence.wav")
        subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
                        "-t", f"{intro_dur:.3f}", "-c:a", "pcm_s16le", intro_sil],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        final_audio = os.path.join(tmpdir, "audio_final.wav")
        listfile = os.path.join(tmpdir, "intro_list.txt")
        with open(listfile, "w", encoding="utf-8") as f:
            f.write(f"file '{intro_sil.replace(chr(92), '/')}'\n")
            f.write(f"file '{audio_wav.replace(chr(92), '/')}'\n")
        subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", listfile,
                        "-c", "copy", final_audio], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        audio_wav = final_audio

    # 2) 编码（rawvideo 管道）
    out_duration = intro_dur + total
    bg_static = None
    bg_frames = None
    bg_fps = 1.0
    if bg_image:
        if bg_image.lower().endswith(".gif"):
            im = Image.open(bg_image)
            bg_frames = []
            durations = []
            for i in range(im.n_frames):
                im.seek(i)
                durations.append(im.info.get("duration", 100))
                bg_frames.append(_fit_bg(im, bg_fit))
            avg_dur = sum(durations) / max(1, len(durations))
            bg_fps = 1000.0 / avg_dur if avg_dur > 0 else 10.0
        else:
            bg_static = _fit_bg(Image.open(bg_image).convert("RGB"), bg_fit)

    cmd = [FFMPEG, "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-i", audio_wav]
    if bgm:
        cmd += ["-i", bgm, "-filter_complex", "[1:a][2:a]amix=inputs=2[a]", "-map", "0:v", "-map", "[a]"]
    else:
        cmd += ["-map", "0:v", "-map", "1:a"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-crf", "23", "-c:a", "aac", "-b:a", "128k", "-shortest",
            "-movflags", "+faststart", out_path]

    ffmpeg_log = os.path.join(tmpdir, "ffmpeg.log")
    with open(ffmpeg_log, "w", encoding="utf-8") as flog:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=flog, stderr=subprocess.STDOUT)
        n_frames = int(out_duration * FPS) + 2
        frame_bytes = W * H * 3
        _BG_FUNCS = {"deepnavy": gen_deepnavy, "blackgold": gen_black_gold,
                     "inkblue": gen_inkblue, "seaside": gen_seaside}
        bg_func = _BG_FUNCS.get(bg_style, gen_deepnavy)
        try:
            for fi in range(n_frames):
                tt = fi / FPS
                if tt < intro_dur:
                    bg = (bg_func(tt) if (bg_static is None and bg_frames is None) else None)
                    frame = render_frame(bg, render_flat, 0.0, tt,
                                         bg_static=bg_static, bg_frames=bg_frames, bg_fps=bg_fps, intro=True, title=title, subtitle=subtitle or "")
                else:
                    tc = tt - intro_dur
                    if bg_static is None and bg_frames is None:
                        bg = bg_func(tc)
                    else:
                        bg = None
                    frame = render_frame(bg, render_flat, tc, tc,
                                         bg_static=bg_static, bg_frames=bg_frames, bg_fps=bg_fps, title=title, subtitle=subtitle or "")
                # 分块写入管道，避免单帧 6.2MB 直写触发 Windows 管道 EINVAL
                data = np.asarray(frame, dtype=np.uint8).tobytes()
                for off in range(0, frame_bytes, 1 << 20):
                    chunk = data[off:off + (1 << 20)]
                    if not chunk:
                        break
                    try:
                        proc.stdin.write(chunk)
                    except (BrokenPipeError, OSError):
                        # ffmpeg 已因 -shortest 结束，停止写帧
                        break
                if proc.poll() is not None:
                    break
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass
        rc = proc.wait()
    if rc != 0:
        try:
            log_tail = open(ffmpeg_log, encoding="utf-8", errors="ignore").read()[-1500:]
        except Exception:
            log_tail = "(无法读取日志)"
        raise RuntimeError(f"ffmpeg 退出码 {rc}，部分日志：\n{log_tail}")
    shutil.rmtree(tmpdir, ignore_errors=True)
    size_kb = os.path.getsize(out_path) // 1024
    if bg_frames is not None:
        bg_label = "GIF动态背景"
    elif bg_static is not None:
        bg_label = "自定义背景"
    else:
        bg_label = bg_style
    print(f"成品: {out_path}  ({size_kb} KB)\n"
          f"   {W}x{H} 竖屏 | {bg_label} | "
          f"大字逐字高亮 | 段落分明 | 声画同步 | 不出镜")
    return out_path


def _sec_to_ass(ts):
    """秒 → ASS 时间格式 h:mm:ss.cc。"""
    h = int(ts // 3600)
    m = int((ts % 3600) // 60)
    s = ts % 60
    return f"{h:01d}:{m:02d}:{s:05.2f}"


def _export_ass(flat, out_path):
    """导出 ASS 字幕文件（与成品 mp4 同名 .ass），含逐物理行时间与纯文本，便于二次剪辑/换风格重烧。"""
    ass_path = os.path.splitext(out_path)[0] + ".ass"
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, Outline, Shadow, Alignment, MarginL, MarginR, MarginV",
        "Style: Default,SimHei,92,&H00FFFFFF,&H00000000,0,2,80,40,880",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for fl in flat:
        text = "".join(ch for ch, _ in fl["chars"])
        text = text.replace(",", "，")  # ASS 字段逗号需转义，简易替换为全角
        lines.append(
            f"Dialogue: 0,{_sec_to_ass(fl['start'])},{_sec_to_ass(fl['end'])},"
            f"Default,,0,0,0,,{text}"
        )
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[INFO] 已导出 ASS 字幕: {ass_path}")
    return ass_path


def naturalize_dialogue(text):
    """用 DeepSeek 把书面双声稿改写为自然口语（加语气词、去 AI 播音腔）。
    严格保留 女：/男： 角色标记与行结构；任何失败都回退原稿，绝不阻塞出片。"""
    try:
        from model_providers import get_text_config, deepseek_chat
    except Exception as e:
        print(f"[WARN] 无法导入 model_providers，跳过自然化: {e}")
        return text
    try:
        cfg = get_text_config()
    except Exception as e:
        print(f"[WARN] 获取文本模型配置失败，跳过自然化: {e}")
        return text
    prompt = (
        "你是一位资深财税短视频脚本编辑。下面的双声对话稿由两位财税专家出镜讲解：\n"
        "女声=江老师（专业财税顾问，负责替企业主抛出常见的疑问与真实场景）；\n"
        "男声=张老师（实战派税务顾问，负责耐心、通俗地逐一解答）。\n"
        "任务：把生硬的书面稿，改写成「真实财税咨询室里，江老师替客户发问、张老师娓娓道来解答」的自然对话——专业可信、有来有回、像真人在交谈，彻底去除 AI 播音腔与机械朗读感。\n"
        "严格要求：\n"
        "1. 角色分工固定：女声台词以「提问 / 抛出场景 / 表疑问」为主（如「那这种情况税务局会怎么认定？」「老板用个人卡收货款，真的有风险吗？」）；男声台词以「耐心解答 / 通俗讲解」为主，二者你来我往、自然互动；\n"
        "2. 称呼规范：女声称呼男声专家时用「张老师」（如「张老师，那这种情况…？」），严禁使用「张哥」之类过于随意的叫法；但也不要强行在每句、每个视频都加称呼——视对话内容是否需要点名而定，符合日常交谈习惯即可；\n"
        "3. 语气词要极克制：除非上下文自然需要，否则不加任何填充式语气词；严禁使用「啊、嘛、呢、哎哟、好家伙、对喽、嗯、哦、对的、是的呢、说白了、对吧」等口头禅或承接词；对话感来自'女问男答、你来我往'的内容互动，不是靠语气词堆砌；老张是实战派，说话干脆、直给、不拖长音、不哼嗯接话；"
        "4. 节奏与韵律：用逗号、句号制造自然停顿，像真人慢慢讲；长短句结合；男声讲解时该慢的地方慢（重点、结论）、该快的地方快（承接、过渡），避免一字一顿的匀速机械感；\n"
        "5. 适度软化书面腔（如「应当」改「一般得」、「然而」改「不过」、「例如」改「比方说」、「进行核查」改「核对一下」），但必须保持财税专业准确性与术语规范，不编造数据、不改动原意、不丢专业权威感；\n"
        "6. 必须严格保留每一行开头的角色标记「女：」或「男：」，不得增删角色、不得合并行、不得改变标记写法；不得删除或省略原稿任何一句，必须逐句对应改写，保持原句数量与顺序；仅做语气软化、加少量自然承接；\n"
        "7. 在结尾处，用女声追加一句自然的咨询引导钩子（仅此一次，不超过 2 句），自然引导观众「留言或私信咨询相关问题」，例如：「要是您也碰到了上面说的这些问题，欢迎在评论区留言，或者私信我们详细聊聊～」；\n"
        "8. 不要输出任何解释、不要加标题，只输出改写后的对话稿本身。\n\n"
        "原稿：\n" + text
    )
    try:
        out = deepseek_chat(prompt, cfg["model"], cfg["key"],
                            cfg.get("base_url", "https://api.deepseek.com"), timeout=90)
        out = out.strip()
        # 去掉可能的 ``` 代码块包裹
        if out.startswith("```"):
            parts = out.split("```")
            out = parts[1] if len(parts) > 1 else out
            if out[:4] in ("text", "txt", "对话", "原稿"):
                out = out.split("\n", 1)[1] if "\n" in out else out
        out = out.strip()
        # 校验：改写后若完全丢失角色标记，判定失败回退原稿
        if not any(m in out for m in ("女：", "男：", "女:", "男:")):
            print("[WARN] 自然化输出丢失角色标记，回退原稿")
            return text
        return out
    except Exception as e:
        print(f"[WARN] 自然化调用失败，使用原稿: {e}")
        return text


def main():
    ap = argparse.ArgumentParser(description="滚动字幕卡短视频生成（不出镜·双声·卡拉OK）")
    ap.add_argument("--dialogue", required=True, help="文稿 txt：男女对话每行 女：/男： 开头；单人独白直接写文案（--default-role 指定 M/F）")
    ap.add_argument("--out", required=True, help="输出 mp4 路径")
    ap.add_argument("--bg-style", default="deepnavy",
                    choices=["deepnavy", "blackgold", "inkblue", "seaside"],
                    help="背景风格：deepnavy 深海军蓝(默认高级感) / blackgold 黑金 / inkblue 墨蓝网格 / seaside 浅海景(休闲)")
    ap.add_argument("--bg", default=None, help="自定义背景图片路径（覆盖 --bg-style）")
    ap.add_argument("--bg-fit", default="fill", choices=["fill", "contain", "stretch"],
                    help="背景缩放模式：fill=填充/覆盖(默认) contain=适应/留边 stretch=拉伸/变形")
    ap.add_argument("--dry-tts", action="store_true", help="跳过真实TTS，用静音占位快速验画面")
    ap.add_argument("--gap", type=float, default=0.28, help="句间静音秒数（0.28 给配音呼吸/停顿感，更像专家讲解）")
    ap.add_argument("--no-intro", action="store_true", help="不生成开头标题页")
    ap.add_argument("--bgm", default=None, help="背景音乐 mp3（可选，与配音混音）")
    ap.add_argument("--title", default=None, help="顶部固定标题（覆盖自动生成，≤10字最佳）")
    ap.add_argument("--subtitle", default=None, help="副标题（标题下方一行小字，如：建筑财税·避坑指南）")
    ap.add_argument("--female-voice", default=FEMALE_VOICE)
    ap.add_argument("--female-model", default=FEMALE_MODEL)
    ap.add_argument("--male-voice", default=MALE_VOICE)
    ap.add_argument("--male-model", default=MALE_MODEL)
    # 分声线感情/快慢（speech_rate 越低越慢；pitch_rate 越高越亮；volume 0-100）
    ap.add_argument("--male-rate", type=float, default=0.98, help="男声语速（默认0.98稍快、叠加长短句快慢节奏、去机械感）")
    ap.add_argument("--female-rate", type=float, default=0.98, help="女声语速（默认0.98自然略快、亲和）")
    ap.add_argument("--male-pitch", type=float, default=0.95, help="男声音调（默认0.95更低沉老练）")
    ap.add_argument("--female-pitch", type=float, default=1.02, help="女声音调（默认1.02略亮、清晰）")
    ap.add_argument("--male-vol", type=int, default=53, help="男声音量(0-100，略高显权威)")
    ap.add_argument("--female-vol", type=int, default=49, help="女声音量(0-100)")
    ap.add_argument("--subtitle-size", type=int, default=HL_SIZE, help="字幕当前行字号（默认92，范围48-140）")
    ap.add_argument("--subtitle-lines", type=int, default=3, choices=[1, 2, 3], help="单条字幕最大折行数（默认3）")
    ap.add_argument("--subtitle-outline", type=int, default=STROKE_W, help="字幕描边宽度（默认5，0=无描边）")
    ap.add_argument("--subtitle-position", default="bottom", choices=["bottom", "center"],
                    help="字幕块垂直位置：bottom=底部(默认) / center=居中")
    ap.add_argument("--subtitle-style", default="dynamic",
                    choices=["dynamic", "minimal", "bubble"],
                    help="字幕整体风格：dynamic=卡拉OK高亮(默认) / minimal=纯净白字 / bubble=气泡底衬")
    ap.add_argument("--font", default=None, help="字幕字体路径（默认黑体 fonts/simhei.ttf）")
    ap.add_argument("--default-role", default="M", choices=["M", "F"],
                    help="单人独白声线：纯独白稿（无 女：/男： 前缀）统一使用的声线，M=男声(默认) / F=女声")
    ap.add_argument("--export-ass", action="store_true",
                    help="同时导出 ASS 字幕文件（与成品同名 .ass），便于二次剪辑/换风格重烧")
    ap.add_argument("--no-burn-sub", action="store_true",
                    help="成品视频不烧录字幕（仅背景+品牌+标题），字幕交由后续 auto_edit 重烧")
    ap.add_argument("--natural", action="store_true",
                    help="调用 DeepSeek 把书面稿自动改写为自然口语（加语气词、去AI感、像真人在叙事）")
    args = ap.parse_args()

    # 字幕字体：--font 指定则覆盖默认黑体（路径不存在回退默认）
    if args.font:
        if os.path.exists(args.font):
            global FONT_PATH
            FONT_PATH = args.font
        else:
            print(f"[WARN] 字体路径不存在，回退默认黑体: {args.font}")

    dialogue_arg = args.dialogue
    if args.natural:
        try:
            with open(args.dialogue, encoding="utf-8-sig") as _f:
                _raw = _f.read()
            _natural = naturalize_dialogue(_raw)
            _tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                               delete=False, encoding="utf-8")
            _tmp.write(_natural)
            _tmp.close()
            dialogue_arg = _tmp.name
            print(f"[INFO] 已自然化改写对话稿（{len(_raw)}→{len(_natural)}字），临时文件: {dialogue_arg}")
        except Exception as e:
            print(f"[WARN] 自然化预处理失败，使用原稿: {e}")

    make_video(dialogue_arg, args.out, bg_style=args.bg_style, bg_image=args.bg,
               dry=args.dry_tts, gap=args.gap, no_intro=args.no_intro, bgm=args.bgm,
               bg_fit=args.bg_fit, subtitle_size=args.subtitle_size,
               subtitle_lines=args.subtitle_lines, subtitle_outline=args.subtitle_outline,
               subtitle_position=args.subtitle_position,
               subtitle_style=args.subtitle_style,
               export_ass=args.export_ass, no_burn_sub=args.no_burn_sub,
               title=args.title, subtitle=args.subtitle,
               female_voice=args.female_voice, female_model=args.female_model,
               male_voice=args.male_voice, male_model=args.male_model,
               male_rate=args.male_rate, female_rate=args.female_rate,
               male_pitch=args.male_pitch, female_pitch=args.female_pitch,
               male_vol=args.male_vol, female_vol=args.female_vol,
               default_role=args.default_role)


if __name__ == "__main__":
    main()
