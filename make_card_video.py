# -*- coding: utf-8 -*-
"""信息卡片式解说(图解版) v1 — 幕后音 + 信息卡片逐条浮现 + 固定栏目头 + 底部字幕。

■ 为什么这么做(背景)
  平台 2026 早期做过"智能图解"卡片, 代码里留了原话「极不成熟」被砍,
  失败原因: 让 AI 同时管"讲什么"和"版面长啥样" → 版面千奇百怪、跟口播对不上。
  本版铁律:
      ① AI 只决定「哪几句话合成一屏 + 用哪种卡片类型」;
      ② 版面长什么样, 由本文件的模板**写死** —— 所以永远不会跑偏;
      ③ 幕内元素按 line 字数比例分配出现时刻 → 讲到哪条, 哪条弹出来(配合度的来源)。

■ 用法
  python make_card_video.py --plan plan.json --out out.mp4 [--voice VOICE_ID]
  python make_card_video.py --plan plan.json --out out.mp4 --no-audio   # 只验画面
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

BASE = Path(__file__).resolve().parent


def _move_file(src, dst):
    """跨盘安全移动文件。Windows 下 Path.replace / os.replace **不能跨盘**
    （临时目录在 C:、任务输出在 D: 时抛 WinError 17「系统无法将文件移到不同的磁盘驱动器」），
    故统一走 shutil.move（内部为复制后删除）。"""
    dst = Path(dst)
    if dst.exists():
        dst.unlink()
    shutil.move(str(src), str(dst))
    return dst
FFMPEG = r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin/ffmpeg.exe"
FFPROBE = FFMPEG.replace("ffmpeg.exe", "ffprobe.exe")

W, H, FPS = 1080, 1920, 30

# ---------------------------- 慧根堂配色(刻意避开参考号的配色, 只借结构) ----------------------------
BG       = (250, 247, 240)     # 米色底
CARD     = (255, 253, 248)     # 卡片白
CARD_BD  = (223, 215, 199)     # 卡片描边
PRIMARY  = (23, 51, 84)        # 藏青(主色)
PRIMARY2 = (38, 78, 124)       # 藏青亮
ACCENT   = (198, 143, 38)      # 琥珀金(栏目头/强调)
ACCENT_L = (243, 231, 202)     # 琥珀浅底
WARN     = (176, 42, 42)       # 警示红
WARN_BG  = (253, 242, 240)     # 警示浅底
DARK     = (30, 42, 60)        # 深色概念卡
GREEN    = (28, 116, 84)       # 合规绿
INK      = (40, 44, 52)        # 正文
SUB      = (126, 132, 142)     # 辅助灰
WHITE    = (255, 255, 255)

HEAD_TOP, HEAD_BOT = 58, 202           # 固定栏目头
CARD_TOP, CARD_BOT = 252, 1486         # 卡片区
SUB_BASELINE = 1690                    # 字幕首行基线
CX = W // 2

_F = {}


def font(size, style="hei"):
    key = (size, style)
    if key not in _F:
        name = {"hei": "simhei.ttf", "kai": "kai.ttf", "xing": "xingkai.ttf"}[style]
        alt = {"hei": r"C:/Windows/Fonts/simhei.ttf", "kai": r"C:/Windows/Fonts/simkai.ttf",
               "xing": r"C:/Windows/Fonts/STXINGKA.TTF"}[style]
        p = BASE / "fonts" / name
        _F[key] = ImageFont.truetype(str(p if p.exists() else alt), size)
    return _F[key]


def _cv(x):
    """坐标转 float 元组(避免 Pillow 对 tuple[int] 的边界抱怨)。"""
    return tuple(float(v) for v in x)


# ---------------------------- 基础图元 ----------------------------
def rr(d, box, r, **kw):
    d.rounded_rectangle(_cv(box), radius=r, **kw)


def shadow(img, box, r=28, blur=18, alpha=52, dy=10):
    """给卡片加柔和阴影: 偏移圆角矩形 → 高斯模糊 → 叠加。"""
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    dl = ImageDraw.Draw(lay)
    x0, y0, x1, y1 = box
    rr(dl, (x0, y0 + dy, x1, y1 + dy), r, fill=(40, 46, 60, alpha))
    lay = lay.filter(ImageFilter.GaussianBlur(blur))
    return Image.alpha_composite(img.convert("RGBA"), lay)


def text_c(d, xy, s, f, fill, anchor="mm", stroke=0, stroke_fill=None):
    d.text((float(xy[0]), float(xy[1])), s, font=f, fill=fill, anchor=anchor,
           stroke_width=stroke, stroke_fill=stroke_fill or (0, 0, 0))


# 行首禁则标点：不允许出现在行首（否则观感像"半句话"，或把「〔2012〕」这类括号组拆断）
# ★ 右括号类一律用 \u 转义书写 —— 全角符号形近，直接敲极易混进 U+FF09「）」而漏掉 U+3015「〕」
#   （2026-09-11 踩过：漏了 〕，屏上出现「苏地税规〔2012」/「〕1号文」断行）
_NO_LINE_START = (
    "，。、；：！？"                            # 中文点号  U+FF0C 3002 3001 FF1B FF1A FF01 FF1F
    "\u3015\u3011\u300d\u300f\u300b\u3009"     # 〕 】 」 』 》 〉
    "%,.;:!?)]}"                               # 半角
)


def wrap_px(d, s, f, max_w):
    """按像素宽度折行(中文友好) + 两项排版保护：

    ① 行首禁则标点：能塞回上一行就塞回去，避免新行以「，。」开头；
    ② 禁孤字行：末行只剩 1~2 字时，从上一行末尾挪字过来
       —— 这是「对比卡经常出现单字成行」的根因（n 个字刚好溢出 → n-1 字 + 1 字）。
    """
    lines, cur = [], ""
    for ch in s:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if d.textlength(cur + ch, font=f) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    lines = [ln for ln in lines if ln] or [""]

    # ① 行首禁则标点 → 并回上一行
    for i in range(1, len(lines)):
        while lines[i] and lines[i][0] in _NO_LINE_START:
            lines[i - 1] += lines[i][0]
            lines[i] = lines[i][1:]
    lines = [ln for ln in lines if ln] or [""]

    # ② 禁孤字行 → 末行不足 3 字时从上一行末尾挪字（保证上一行仍 ≥2 字）
    while len(lines) >= 2 and len(lines[-1]) < 3 and len(lines[-2]) > 2:
        lines[-1] = lines[-2][-1] + lines[-1]
        lines[-2] = lines[-2][:-1]
    return lines


# ---------------------------- 元素序列(逐条浮现) ----------------------------
class Seq:
    """把一张卡片的元素按顺序登记, 自动分配出现时刻。

    出现时刻 = 该元素对应解说句在整卡解说里的字数占比 → 讲到了才弹出来。
    """

    def __init__(self, lines):
        self.lines = [ln for ln in lines if ln] or [""]
        self.total = sum(len(ln) for ln in self.lines) or 1
        self.cum = []
        acc = 0
        for ln in self.lines:
            self.cum.append(acc)
            acc += len(ln)
        self.els = []
        self._i = 0

    def at(self, grp=None):
        """grp=对应第几句解说(缺省按登记顺序顺延)。"""
        if grp is None:
            grp = self._i
            self._i += 1
        grp = max(0, min(grp, len(self.cum) - 1))
        return self.cum[grp] / self.total

    def add(self, kind, at=None, dur=0.10, **kw):
        el = {"kind": kind, "at": self.at(at), "dur": dur}
        el.update(kw)
        self.els.append(el)
        return el

    def base(self, **kw):
        """常显底(卡片底板), at=0。"""
        return self.add("base", at=0, dur=0.001, **kw)


def _alpha(el, p):
    """元素当前透明度 0~1(带淡入)。"""
    if p < el["at"]:
        return 0.0
    return min(1.0, (p - el["at"]) / max(el["dur"], 1e-6))


# ---------------------------- 卡片底板(缓存) ----------------------------
_BASE_CACHE = {}


def card_plate(inner_top=CARD_TOP, inner_bot=CARD_BOT, fill=CARD, outline=CARD_BD, radius=34):
    key = ("plate", inner_top, inner_bot, fill, outline, radius)
    if key not in _BASE_CACHE:
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        img = shadow(img, (56, inner_top, W - 56, inner_bot), r=radius)
        d = ImageDraw.Draw(img)
        rr(d, (56, inner_top, W - 56, inner_bot), radius, fill=fill + (255,),
           outline=outline + (255,), width=3)
        _BASE_CACHE[key] = img
    return _BASE_CACHE[key].copy()


def header_layer(brand, sub=""):
    """固定栏目头: 全片不变, 观众随时划进来都知道在讲什么(品牌锚定)。"""
    key = ("head", brand, sub)
    if key not in _BASE_CACHE:
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        box = (56, HEAD_TOP, W - 56, HEAD_BOT)
        img = shadow(img, box, r=26, blur=14, alpha=44, dy=7)
        d = ImageDraw.Draw(img)
        rr(d, box, 26, fill=ACCENT + (255,), outline=(46, 40, 26, 255), width=4)
        rr(d, (56 + 18, HEAD_TOP + 20, 56 + 18 + 12, HEAD_BOT - 20), 6, fill=PRIMARY + (255,))
        text_c(d, (56 + 18 + 12 + 26, (HEAD_TOP + HEAD_BOT) // 2 - 6), brand,
               font(52, "hei"), PRIMARY, anchor="lm")
        if sub:
            text_c(d, (W - 90, (HEAD_TOP + HEAD_BOT) // 2 - 6), sub,
                   font(34, "hei"), (92, 74, 34), anchor="rm")
        _BASE_CACHE[key] = img
    return _BASE_CACHE[key].copy()


def card_title(d, s, y, color=PRIMARY, size=66, bar=True):
    """卡片内小标题: 左竖条 + 文字。"""
    if bar:
        rr(d, (110, y - size * 0.46, 122, y + size * 0.46), 6, fill=ACCENT + (255,))
    text_c(d, (152 if bar else 110, y), s, font(size, "hei"), color, anchor="lm")


# ---------------------------- 各类型卡片渲染 ----------------------------
def _col(x, default):
    """颜色字段容错: JSON 里是数组 → 元组。"""
    if not x:
        return default
    return tuple(x)


def draw_card(img, card, p):
    """把一张卡片画进 img(p=卡内进度 0~1)。返回 img。"""
    d = ImageDraw.Draw(img)
    t = card.get("type", "points")
    fn = {
        "title": _c_title, "points": _c_points, "nums": _c_nums,
        "compare": _c_compare, "warn": _c_warn, "law": _c_law, "cta": _c_cta,
    }.get(t, _c_points)
    return fn(img, card, p, d)


def _c_title(img, card, p, d):
    s = Seq(card.get("lines") or [card.get("title", "")])
    kicker = card.get("kicker")
    y = 680
    if kicker:
        a = _alpha(s.base(), p)
        if a > 0:
            tw = d.textlength(kicker, font=font(36, "hei"))
            box = (CX - tw / 2 - 34, y - 198, CX + tw / 2 + 34, y - 132)
            lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            dl = ImageDraw.Draw(lay)
            rr(dl, box, 18, fill=ACCENT_L + (int(255 * a),), outline=ACCENT + (int(255 * a),), width=3)
            text_c(dl, (CX, y - 165), kicker, font(36, "hei"), PRIMARY)
            img = Image.alpha_composite(img, lay)
            d = ImageDraw.Draw(img)
            s.add("text", at=0, dur=0.12, xy=(CX, y - 165), text=kicker, size=36, color=PRIMARY, skip=True)
    # 主标题(可能 2 行) —— 标题是"这一屏讲什么", 开场即出, 不跟着逐句走
    title = card.get("title", "")
    f = font(104, "hei")
    lines = wrap_px(d, title, f, W - 280)
    for i, ln in enumerate(lines):
        el = s.add("text", at=0, dur=0.14)
        a = _alpha(el, p)
        if a > 0:
            _t(img, (CX, y + i * 124), ln, f, PRIMARY, a)
    y2 = y + 124 * len(lines) + 20
    el = s.add("line", at=0, dur=0.14)
    a = _alpha(el, p)
    if a > 0:
        _ln(img, (CX - 150 * a, y2), (CX + 150 * a, y2), ACCENT, 8)
    if card.get("sub"):
        el = s.add("text", at=0, dur=0.16)
        a = _alpha(el, p)
        if a > 0:
            _t(img, (CX, y2 + 96), card["sub"], font(52, "hei"), SUB, a)
    return img


def _c_points(img, card, p, d):
    items = card.get("items", [])[:6]
    s = Seq(card.get("lines") or [it if isinstance(it, str) else it.get("text", "") for it in items])
    y0 = 470 if not card.get("title") else 520
    if card.get("title"):
        el = s.add("header", at=0, dur=0.10)
        if _alpha(el, p) > 0:
            card_title(d, card["title"], 380)
    n = max(1, len(items))
    gap = min(180, int((CARD_BOT - y0 - 60) / n))
    for i, it in enumerate(items):
        it = it if isinstance(it, dict) else {"text": str(it)}
        y = y0 + i * gap
        el = s.add("row", at=min(i, len(s.lines) - 1), dur=0.12)
        a = _alpha(el, p)
        if a <= 0:
            continue
        # 序号块
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dl = ImageDraw.Draw(lay)
        col = _col(it.get("color"), PRIMARY if i % 2 == 0 else PRIMARY2)
        rr(dl, (116, y - 44, 204, y + 44), 18, fill=col + (int(245 * a),))
        text_c(dl, (160, y), str(it.get("n") or f"{i + 1:02d}"), font(48, "hei"), WHITE)
        img = Image.alpha_composite(img, lay)
        d = ImageDraw.Draw(img)
        _t(img, (238, y - (16 if it.get("sub") else 0)), it.get("text", ""),
           font(50, "hei"), INK, a, anchor="lm", max_w=W - 330)
        if it.get("sub"):
            _t(img, (238, y + 46), it["sub"], font(34, "hei"), SUB, a, anchor="lm", max_w=W - 330)
    return img


def _c_nums(img, card, p, d):
    nums = card.get("nums", [])[:3]
    s = Seq(card.get("lines") or [n.get("label", "") for n in nums])
    if card.get("title"):
        el = s.add("header", at=0, dur=0.10)
        if _alpha(el, p) > 0:
            card_title(d, card["title"], 400)
    n = max(1, len(nums))
    y = 900
    seg = (W - 240) / n
    x0 = 120
    for i, nd in enumerate(nums):
        el = s.add("num", at=min(i, len(s.lines) - 1), dur=0.14)
        a = _alpha(el, p)
        if a <= 0:
            continue
        cx = x0 + seg * i + seg / 2
        if i:
            col = (214, 206, 190)
            _ln(img, (x0 + seg * i, y - 130), (x0 + seg * i, y + 150), col, 2)
        val = str(nd.get("v", ""))
        unit = nd.get("unit", "")
        ncol = _col(nd.get("color"), WARN)
        # 字号按实测宽度自适应, 避免长值(如"300万")横向溢出挤成一团
        maxw = seg - 56
        fs = 118
        while fs > 46 and d.textlength(val, font=font(fs, "hei")) > maxw:
            fs -= 6
        fv = font(fs, "hei")
        _t(img, (cx, y - 20), val, fv, ncol, a)
        if unit:
            wv = d.textlength(val, font=fv)
            el2 = s.add("num_u", at=el["at"], dur=el["dur"])
            aa = _alpha(el2, p)
            if aa > 0:
                _t(img, (cx + wv / 2 + d.textlength(unit, font=font(38, "hei")) / 2 + 10, y + 14),
                   unit, font(38, "hei"), ncol, aa)
        _t(img, (cx, y + 110), nd.get("label", ""), font(36, "hei"), SUB, a, max_w=int(seg) - 40)
    if card.get("note"):
        el = s.add("note", dur=0.12)
        a = _alpha(el, p)
        if a > 0:
            _t(img, (CX, 1180), card["note"], font(40, "hei"), INK, a, max_w=W - 300)
    return img


def _c_compare(img, card, p, d):
    L, R = card.get("left", {}), card.get("right", {})
    lsi, rsi = len(L.get("items", [])), len(R.get("items", []))
    lines = card.get("lines") or (L.get("items", []) + R.get("items", []))
    s = Seq(lines)
    if card.get("title"):
        el = s.add("header", at=0, dur=0.10)
        if _alpha(el, p) > 0:
            card_title(d, card["title"], 392)
    y0 = 500
    y1 = min(1420, y0 + 190 + max(lsi, rsi) * 96 + 70)
    colw = (W - 240 - 40) / 2
    for si, (side, x) in enumerate(((L, 120), (R, 120 + colw + 40))):
        col = _col(side.get("color"), WARN if si == 0 else GREEN)
        bgc = _col(side.get("bg"), WARN_BG if si == 0 else (238, 248, 243))
        el = s.add("col", at=(0 if si == 0 else max(lsi, 1)), dur=0.14)
        a = _alpha(el, p)
        if a <= 0:
            continue
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dl = ImageDraw.Draw(lay)
        rr(dl, (x, y0, x + colw, y1), 26, fill=bgc + (int(255 * a),), outline=col + (int(210 * a),), width=3)
        rr(dl, (x, y0, x + colw, y0 + 92), 26, fill=col + (int(235 * a),))
        rr(dl, (x, y0 + 60, x + colw, y0 + 92), 0, fill=col + (int(235 * a),))
        text_c(dl, (x + colw / 2, y0 + 46), side.get("head", ""), font(44, "hei"), WHITE)
        img = Image.alpha_composite(img, lay)
        d = ImageDraw.Draw(img)
        yy = y0 + 140
        for j, item in enumerate(side.get("items", [])):
            el2 = s.add("row", at=((j + 1) if si == 0 else lsi + j + 1), dur=0.12)
            a2 = _alpha(el2, p)
            nrow = max(1, len(wrap_px(d, item, font(38, "hei"), int(colw) - 100)))
            if a2 > 0:
                _t(img, (x + 30, yy + 4), "·", font(44, "hei"), col, a2, anchor="lt")
                _t(img, (x + 62, yy), item, font(38, "hei"), INK, a2, anchor="lt",
                   max_w=int(colw) - 100, line_gap=54)
            yy += 54 * nrow + 42
    if card.get("note"):
        el = s.add("note", dur=0.12)
        a = _alpha(el, p)
        if a > 0:
            _t(img, (CX, y1 + 70), card["note"], font(36, "hei"), SUB, a, max_w=W - 260)
    return img


def _c_warn(img, card, p, d):
    items = card.get("items", [])[:4]
    s = Seq(card.get("lines") or [card.get("text", "")])
    # 醒目警示图标
    el = s.base()
    a = _alpha(el, p)
    if a > 0:
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dl = ImageDraw.Draw(lay)
        dl.ellipse((CX - 62, 400 - 62, CX + 62, 400 + 62), fill=WARN + (int(240 * a),))
        text_c(dl, (CX, 396), "!", font(96, "hei"), WHITE)
        img = Image.alpha_composite(img, lay)
        d = ImageDraw.Draw(img)
    if card.get("title"):
        el = s.add("text", at=0, dur=0.13)
        a = _alpha(el, p)
        if a > 0:
            _t(img, (CX, 530), card["title"], font(58, "hei"), WARN, a, max_w=W - 260)
    if card.get("text"):
        el = s.add("text", at=0, dur=0.16)
        a = _alpha(el, p)
        if a > 0:
            _t(img, (CX, 680), card["text"], font(70, "hei"), INK, a, max_w=W - 300, line_gap=92)
    y = 1000 if card.get("text") else 860
    for i, it in enumerate(items):
        el = s.add("row", at=min(i, len(s.lines) - 1), dur=0.12)
        a = _alpha(el, p)
        if a <= 0:
            continue
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dl = ImageDraw.Draw(lay)
        rr(dl, (140, y - 42, W - 140, y + 42), 16, fill=WARN_BG + (int(240 * a),),
           outline=(226, 176, 172, int(240 * a)), width=2)
        text_c(dl, (CX, y), it, font(44, "hei"), (132, 30, 30))
        img = Image.alpha_composite(img, lay)
        d = ImageDraw.Draw(img)
        y += 104
    return img


def _c_law(img, card, p, d):
    """深色概念/法条卡: 文号标签 + 条文 + 要点。"""
    s = Seq(card.get("lines") or [card.get("text", "")])
    inner = card_plate(CARD_TOP, CARD_BOT, fill=DARK, outline=(18, 26, 40), radius=34)
    el = s.base()
    a = _alpha(el, p)
    img = Image.alpha_composite(img, inner)
    d = ImageDraw.Draw(img)
    y = 430
    if card.get("code"):
        el = s.add("code", at=0, dur=0.14)
        a = _alpha(el, p)
        if a > 0:
            tw = d.textlength(card["code"], font=font(38, "hei"))
            lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            dl = ImageDraw.Draw(lay)
            rr(dl, (CX - tw / 2 - 30, y - 46, CX + tw / 2 + 30, y + 46), 16,
               fill=ACCENT + (int(250 * a),))
            text_c(dl, (CX, y), card["code"], font(38, "hei"), (30, 26, 14))
            img = Image.alpha_composite(img, lay)
            d = ImageDraw.Draw(img)
        y += 130
    if card.get("title"):
        el = s.add("text", at=0, dur=0.15)
        a = _alpha(el, p)
        if a > 0:
            _t(img, (CX, y + 40), card["title"], font(62, "hei"), WHITE, a, max_w=W - 280)
        y += 170
    if card.get("text"):
        el = s.add("text", at=0, dur=0.17)
        a = _alpha(el, p)
        if a > 0:
            _t(img, (CX, y + 60), card["text"], font(46, "hei"), (216, 226, 240), a,
               max_w=W - 300, line_gap=70)
        y += 120 + 70 * (len(wrap_px(d, card["text"], font(46, "hei"), W - 300)) - 1)
    yy = max(y + 60, 1060)
    for i, it in enumerate(card.get("bullets", [])[:4]):
        el = s.add("row", at=min(i, len(s.lines) - 1), dur=0.12)
        a = _alpha(el, p)
        if a <= 0:
            continue
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dl = ImageDraw.Draw(lay)
        rr(dl, (150, yy - 40, 162, yy + 40), 6, fill=ACCENT + (int(250 * a),))
        text_c(dl, (196, yy), it, font(44, "hei"), (230, 236, 246), anchor="lm")
        img = Image.alpha_composite(img, lay)
        d = ImageDraw.Draw(img)
        yy += 108
    return img


def _c_cta(img, card, p, d):
    s = Seq(card.get("lines") or [card.get("text", "")])
    el = s.base()
    if _alpha(el, p) > 0:
        pass
    y = 600
    if card.get("title"):
        el = s.add("text", dur=0.13)
        a = _alpha(el, p)
        if a > 0:
            _t(img, (CX, y), card["title"], font(72, "hei"), PRIMARY, a, max_w=W - 280, line_gap=96)
            y += 96 * len(wrap_px(d, card["title"], font(72, "hei"), W - 280)) + 40
    if card.get("text"):
        el = s.add("text", dur=0.14)
        a = _alpha(el, p)
        if a > 0:
            _t(img, (CX, y + 60), card["text"], font(46, "hei"), INK, a, max_w=W - 320, line_gap=70)
            y += 60 + 70 * len(wrap_px(d, card["text"], font(46, "hei"), W - 320))
    if card.get("action"):
        el = s.add("btn", dur=0.16)
        a = _alpha(el, p)
        if a > 0:
            box = (CX - 330, y + 130, CX + 330, y + 262)
            lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            dl = ImageDraw.Draw(lay)
            rr(dl, box, 24, fill=ACCENT + (int(250 * a),), outline=(46, 40, 26, int(250 * a)), width=4)
            text_c(dl, (CX, (box[1] + box[3]) / 2 - 4), card["action"], font(50, "hei"), (32, 26, 12))
            img = Image.alpha_composite(img, lay)
            d = ImageDraw.Draw(img)
    return img


# ---------------------------- 文字/线段小工具(支持透明度 + 自动折行) ----------------------------
def _t(img, xy, s, f, color, a=1.0, anchor="mm", max_w=None, line_gap=None, stroke=0, stroke_fill=None):
    """绘制多行文字(带透明度 + 自动折行)。就地回写, 返回 img 便于链式调用。"""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    alpha = int(255 * max(0.0, min(1.0, a)))
    col = tuple(color) + (alpha,)
    lines = wrap_px(d, s, f, max_w) if max_w else s.split("\n")
    gap = line_gap or int(f.size * 1.35)
    y0 = xy[1] if (anchor and anchor.endswith("t")) else xy[1] - (len(lines) - 1) * gap / 2
    for i, ln in enumerate(lines):
        d.text((float(xy[0]), float(y0 + i * gap)), ln, font=f, fill=col, anchor=anchor,
               stroke_width=stroke, stroke_fill=stroke_fill)
    img.paste(Image.alpha_composite(img, lay))
    return img


def _ln(img, p1, p2, color, w, a=1.0):
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(lay).line([float(p1[0]), float(p1[1]), float(p2[0]), float(p2[1])],
                             fill=tuple(color) + (int(255 * max(0.0, min(1.0, a))),), width=w)
    img.paste(Image.alpha_composite(img, lay))
    return img


# ---------------------------- 底部字幕(跟口播) ----------------------------
def add_subtitle(img, text, ratio, t_in=0.0, t_all=1.0):
    if not text:
        return img.convert("RGB")
    # ★ 分屏器会把长句按逗号拆到相邻屏 → 偶有某屏字幕以「，」「。」等标点开头，
    #   观感像"半句话"。统一去掉前导标点与空白。
    text = text.lstrip("，。、；：,.;:　 ")
    if not text:
        return img.convert("RGB")
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    f = font(50, "hei")
    lines = wrap_px(d, text, f, W - 200)
    if len(lines) > 2:   # 最多两行, 超出按时间滚动截取
        span = max(1, len(lines) - 2)
        k = int(min(span, ratio * (span + 1)))
        lines = lines[k:k + 2]
    total = max(1, sum(len(l) for l in lines))
    shown = int(ratio * total)
    y = SUB_BASELINE
    cum = 0
    for ln in lines:
        w = d.textlength(ln, font=f)
        x = (W - w) / 2
        for ch in ln:
            done = cum < shown
            col = (255, 255, 255, 255) if done else (176, 180, 190, 255)
            d.text((x, y), ch, font=f, fill=col, anchor="lm", stroke_width=8, stroke_fill=(0, 0, 0, 255))
            x += d.textlength(ch, font=f)
            cum += 1
        y += 74
    return Image.alpha_composite(img.convert("RGBA"), lay).convert("RGB")


# ---------------------------- 一帧 ----------------------------
def render_card(card, p, sub_ratio, head_cache, sub_text=""):
    img = Image.new("RGBA", (W, H), BG + (255,))
    img = Image.alpha_composite(img, head_cache)
    if card.get("type") != "law":
        img = Image.alpha_composite(img, card_plate(CARD_TOP, CARD_BOT))
    img = draw_card(img, card, p)
    img = add_subtitle(img, sub_text, sub_ratio)
    return img


# ---------------------------- 多进程渲染(模块级, 供 spawn 序列化) ----------------------------
_W = {}


def _worker_init(cards, tl, head_cache, frames_dir):
    _W["cards"] = cards
    _W["tl"] = tl
    _W["head"] = head_cache
    _W["dir"] = frames_dir


def _render_one(item):
    fi, bi, t = item
    cards, tl = _W["cards"], _W["tl"]
    _, s0, s1 = tl[bi]
    c = cards[bi]
    seg = max(s1 - s0, 0.1)
    p = min(1.0, max(0.0, (t - s0) / (seg * 0.72)))    # 前 72% 语音时间内浮现完
    if bi == 0 and c.get("seg_head"):
        # ★ 分段长片的段首卡：起始就给足进度 → 头部/首条内容第 0 帧即清晰可见，
        #   否则拼接后新段开头约 1 秒只有空底板（非首段无 title 卡遮挡淡入过程）。
        p = max(p, 0.15)
    sub_ratio = min(1.0, max(0.0, (t - s0) / seg))
    txt = c.get("narration") or "".join(c.get("lines") or [])
    img = render_card(c, p, sub_ratio, _W["head"], txt)
    img.save(Path(_W["dir"]) / f"f_{fi:05d}.png", "PNG")
    return fi


# ---------------------------- 主流程 ----------------------------
def probe_dur(path):
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True, help="分屏方案 JSON")
    ap.add_argument("--out", required=True)
    ap.add_argument("--voice", default="")
    ap.add_argument("--no-audio", action="store_true", help="只验画面(静音, 每屏按字数估时)")
    ap.add_argument("--gap", type=float, default=0.35, help="屏与屏之间的停顿秒数")
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--intro", default="", help="片头 mp4(缺省自动找 covers/intro.mp4)")
    ap.add_argument("--no-intro", action="store_true",
                    help="不拼片头（长片分段渲染时用：段内不拼，避免拼接处出现片头静音；"
                         "整片由 concat_segments.py 统一补一次片头）")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    if plan.get("__bom__"):
        pass
    cards = plan["cards"]
    brand = plan.get("brand", "昆山老张讲财税")
    sub_head = plan.get("header_sub", "")

    # ---- 1) 每屏配音测时长 → 时间轴 ----
    tmp = Path(tempfile.mkdtemp(prefix="card_"))
    tl = []          # (wav|None, start, end)
    cur = 0.0
    if args.no_audio:
        print(f"[1/4] 静音模式: {len(cards)} 屏按字数估时 ...")
        for c in cards:
            txt = c.get("narration") or "".join(c.get("lines") or [])
            dur = max(2.2, len(txt) / 4.6)
            tl.append((None, cur, cur + dur))
            cur += dur + args.gap
    else:
        from model_providers import ensure_env
        ensure_env()
        from qwen_tts import synth_natural, synth, DEFAULT_VOICE_ID
        sys.path.insert(0, str(BASE))
        from tts_text_rules import normalize_for_tts
        voice = args.voice or DEFAULT_VOICE_ID
        print(f"[1/4] 配音 {len(cards)} 屏(音色 {voice[:28]}...) ...")
        for i, c in enumerate(cards):
            txt = c.get("narration") or "".join(c.get("lines") or [])
            # ★ 政策文号/年份按中文读法送 TTS：20121号文 → 二零一二年一号文
            #   （否则引擎把 20121 连读成「二万零一百二十一号」；见 tts_text_rules.py）
            speak = normalize_for_tts(txt)
            wav = str(tmp / f"c{i:02d}.wav")
            synth_natural(speak, voice, wav, base_rate=0.92, volume=55)
            dur = probe_dur(wav)
            tl.append((wav, cur, cur + dur))
            cur += dur + args.gap
            print(f"  屏{i+1:02d}[{c.get('type')}] {dur:5.1f}s  {txt[:26]}"
                  + ("" if speak == txt else "  (朗读已规范化)"))

    total = tl[-1][2] if tl else 10.0
    print(f"[2/4] 总时长 {total:.1f}s, 渲染 {int(total * FPS)} 帧 ...")

    # ---- 2) 渲染 ----
    head_cache = header_layer(brand, sub_head)
    frames_dir = tmp / "f"
    frames_dir.mkdir()
    n = int(total * FPS)

    # 建立"帧 → 屏"索引
    def frame_card(fi):
        t = fi / FPS
        bi = 0
        for i, (_, s0, s1) in enumerate(tl):
            if t >= s0:
                bi = i
            if t < s1:
                break
        if t > tl[-1][2]:
            bi = len(tl) - 1
        return bi, t

    workers = args.workers or max(1, min(10, (__import__("os").cpu_count() or 4) - 2))
    work = []
    for fi in range(n):
        bi, t = frame_card(fi)
        work.append((fi, bi, t))

    _worker_init(cards, tl, head_cache, str(frames_dir))
    if workers <= 1 or n < workers * 8:
        for i, it in enumerate(work):
            _render_one(it)
            if i % 90 == 0 or i == len(work) - 1:
                print(f"      渲染 {int(100 * (i + 1) / len(work))}%")
    else:
        import concurrent.futures
        import multiprocessing as mp
        try:
            mp.set_start_method("spawn", force=True)
        except Exception:
            pass
        init = (cards, tl, head_cache, str(frames_dir))
        done = 0
        with concurrent.futures.ProcessPoolExecutor(
                max_workers=workers, initializer=_worker_init, initargs=init) as ex:
            for _ in ex.map(_render_one, work, chunksize=8):
                done += 1
                if done % 120 == 0 or done == len(work):
                    print(f"      渲染 {int(100 * done / len(work))}%")

    # ---- 3) 合成音轨 ----
    print("[3/4] 合成音轨 + 视频 ...")
    mid = str(tmp / "mid.mp4")
    if args.no_audio:
        cmd = [FFMPEG, "-y", "-framerate", str(FPS), "-i", str(frames_dir / "f_%05d.png"),
               "-vf", "format=yuv420p", "-c:v", "libx264", "-crf", "19", "-an", mid]
    else:
        listf = tmp / "list.txt"
        with open(listf, "w", encoding="utf-8") as f:
            for wav, s0, s1 in tl:
                f.write(f"file '{str(wav).replace(chr(92), '/')}'\n")
        audio = str(tmp / "all.wav")
        rr = subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(listf),
                             "-c", "copy", audio], capture_output=True, text=True)
        if rr.returncode != 0:
            print("  音轨拼接告警:", rr.stderr[-300:])
        cmd = [FFMPEG, "-y", "-framerate", str(FPS), "-i", str(frames_dir / "f_%05d.png"),
               "-i", audio, "-vf", "format=yuv420p", "-profile:v", "high",
               "-c:v", "libx264", "-crf", "19", "-c:a", "aac", "-ar", "44100",
               "-shortest", mid]
    rr = subprocess.run(cmd, capture_output=True, text=True)
    if rr.returncode != 0:
        sys.exit("合成失败:\n" + rr.stderr[-1200:])

    # ---- 4) 片头 ----
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    intro = Path(args.intro) if args.intro else (BASE / "covers/intro.mp4")
    if args.no_intro:
        # 长片分段：段内一律不拼片头（否则段2 起会插片头静音），整片由 concat_segments.py 补一次
        _move_file(mid, out)
        print("[4/4] 分段模式: 跳过片头（整片拼接时统一补）")
    elif intro.exists() and not args.no_audio:
        fc = ("[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
              "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1[v0];"
              "[1:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
              "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1[v1];"
              "[0:a]aresample=44100[a0];[1:a]aresample=44100[a1];"
              "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]")
        rr = subprocess.run([FFMPEG, "-y", "-i", str(intro), "-i", mid, "-filter_complex", fc,
                             "-map", "[v]", "-map", "[a]", "-pix_fmt", "yuv420p", str(out)],
                            capture_output=True, text=True)
        if rr.returncode != 0:
            print("  片头拼接失败, 输出正片:", rr.stderr[-300:])
            _move_file(mid, out)
        else:
            print("[4/4] 已拼品牌片头")
    else:
        _move_file(mid, out)
        print("[4/4] 无片头, 直接输出")

    print(f"\n成品: {out}  ({out.stat().st_size // 1024} KB, {total:.1f}s)")
    print(f"中间帧: {frames_dir}")


if __name__ == "__main__":
    main()
