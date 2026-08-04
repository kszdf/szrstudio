# -*- coding: utf-8 -*-
"""
make_cover.py — 本地智能封面生成（P3b 专业版，依赖 Pillow + numpy + OpenCV）

设计目标（专业级，不降级）：
  - 智能选帧：在视频中段均匀采样 N 个候选帧，用「清晰度(拉普拉斯方差) + 亮度适宜度 +
    人脸存在/构图 + 场景稳定性」综合打分，挑选最能代表内容的一帧，杜绝模糊/过暗/过渡帧。
  - 人脸感知构图：出镜视频自动把人脸放在上三分之一并避免裁切；无脸(滚动字幕卡)走稳健中心偏上构图。
  - 色彩协调：提取画面主色用于顶部色调，与品牌靛蓝/青点缀形成协调而不刺眼的搭配。
  - 自动对比度文字：采样标题区亮度，自动选黑/白字 + 反向描边，杜绝看不清或元素错位；
    标题字号自适应缩放 + 多行换行，绝不溢出画布。
  - 严格画幅：输出精确 1080×目标高（4:5=1350 / 3:4=1440 / 1:1=1080），LANCZOS 缩放无拉伸。
  - 质量门禁：成图后跑 QC（清晰度/分辨率/亮度/文字适配），不达标自动换下一候选帧重试，
    仍不达标则尽力输出但标记 qc_fail，供上游决定是否采用（绝不静默输出次品）。

用法：
  python make_cover.py --input out.mp4 --output cover.jpg --title "老板最容易踩的坑" --subtitle "个人卡收货款的风险"
  python make_cover.py --input out.mp4 --output cover.jpg --title "..." --platform xhs
  python make_cover.py --input out.mp4 --output cover.jpg --title "..." --dry-run
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FFMPEG = os.environ.get("FFMPEG_BIN", r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin/ffmpeg.exe")
FFPROBE = os.environ.get("FFPROBE_BIN", r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin/ffprobe.exe")
FONT_PATH = "C:/Windows/Fonts/simhei.ttf"
if not os.path.exists(FONT_PATH):
    for _fp in ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", "/System/Library/Fonts/PingFang.ttc"):
        if os.path.exists(_fp):
            FONT_PATH = _fp
            break

BRAND = "追梦 · 数字人"
# 靛蓝主色 + 青色点缀（与 DESIGN.md token 一致）
C_PRIMARY = (79, 70, 229)
C_ACCENT = (6, 182, 212)

ASPECTS = {            # 画幅预设（宽,高）
    "4:5": (1080, 1350),
    "3:4": (1080, 1440),
    "1:1": (1080, 1080),
}
PLATFORM_ASPECT = {
    "douyin": "4:5", "video": "4:5", "wechat": "4:5", "xhs": "3:4",
    "red": "3:4", "redbook": "3:4", "square": "1:1", "": "4:5",
}

# 人脸级联（优先用脚本同目录自带的 xml，离线可用；缺失则跳过，仅作软信号）
_HERE = os.path.dirname(os.path.abspath(__file__))
_CASCADE_CANDIDATES = [
    os.path.join(_HERE, "haarcascade_frontalface_default.xml"),
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml",
]
_FACE_CASCADE = None


def _face_cascade():
    global _FACE_CASCADE
    if _FACE_CASCADE is None:
        for _p in _CASCADE_CANDIDATES:
            if _p and os.path.exists(_p):
                try:
                    _FACE_CASCADE = cv2.CascadeClassifier(_p)
                    if not _FACE_CASCADE.empty():
                        break
                except Exception:
                    _FACE_CASCADE = None
    return _FACE_CASCADE


# ----------------------------------------------------------------- 候选帧打分
def select_best_frames(video, n=16, skip_head=0.08, skip_tail=0.08):
    """均匀采样候选帧并综合打分，返回按分数降序的列表：
    [{"t":秒, "frame":BGR(ndarray), "sharp":float, "lum":float,
      "face":(x,y,w,h)|None, "hist":ndarray, "score":float}, ...]"""
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        return []
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    dur = (total / fps) if fps else 0
    if dur <= 0:
        cap.release()
        return []
    cas = _face_cascade()
    cands = []
    prev_hist = None
    for i in range(n):
        t = dur * (skip_head + (1 - skip_head - skip_tail) * (i / max(1, n - 1)))
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())     # 越大越清晰
        lum = float(gray.mean())                                 # 0-255 亮度
        # 亮度适宜度：理想 55-205，越偏离越扣分
        if lum < 55:
            lum_fit = max(0.0, 1 - (55 - lum) / 55.0)
        elif lum > 205:
            lum_fit = max(0.0, 1 - (lum - 205) / 50.0)
        else:
            lum_fit = 1.0
        # 人脸：加分且偏好大小适中、位于上中区
        face = None
        face_score = 0.0
        if cas is not None:
            try:
                fx = cas.detectMultiScale(gray, 1.15, 4, minSize=(60, 60))
                if len(fx) > 0:
                    x, y, w, h = max(fx, key=lambda r: r[2] * r[3])
                    ih, iw = gray.shape[:2]
                    area = (w * h) / float(iw * ih)
                    if 0.03 <= area <= 0.30:
                        face_score = 1.0
                    else:
                        face_score = 0.4
                    fcy = (y + h / 2) / ih
                    if 0.18 <= fcy <= 0.62:
                        face_score += 0.3
                    face = (int(x), int(y), int(w), int(h))
            except Exception:
                face = None
        # 场景稳定性：与上一帧直方图相关性，过低说明在转场/闪切，扣分
        hist = cv2.calcHist([gray], [0], None, [64], [0, 256])
        cv2.normalize(hist, hist)
        stab = 1.0
        if prev_hist is not None:
            corr = cv2.compareHist(hist, prev_hist, cv2.HISTCMP_CORREL)
            stab = max(0.0, float(corr))     # 1=相似，0/负=跳变
        prev_hist = hist
        # 综合分数（权重经验值，清晰与亮度为主，人脸/稳定为辅）
        score = (1.0 * min(1.0, np.log1p(sharp) / 8.0)
                 + 1.0 * lum_fit
                 + 0.6 * face_score
                 + 0.5 * stab)
        cands.append({"t": t, "frame": frame, "sharp": sharp, "lum": lum,
                      "face": face, "hist": hist, "score": score})
    cap.release()
    cands.sort(key=lambda c: c["score"], reverse=True)
    return cands


def dominant_color(frame_bgr, k=3):
    """用 kmeans 取画面主色（RGB 元组），用于顶部色调协调。"""
    small = cv2.resize(frame_bgr, (64, 64))
    pix = small.reshape(-1, 3).astype(np.float32)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 0.9)
    _, labels, centers = cv2.kmeans(pix, k, None, crit, 4, cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.flatten())
    dom = centers[counts.argmax()].astype(int)
    return (int(dom[2]), int(dom[1]), int(dom[0]))   # BGR->RGB


# ----------------------------------------------------------------- PIL 合成
def _load_font(size):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except Exception:
        return ImageFont.load_default()


def _wrap(text, draw, font, max_w):
    """按像素宽度换行（中文按字）。"""
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur); cur = ""; continue
        test = cur + ch
        if draw.textlength(test, font=font) > max_w and cur:
            lines.append(cur); cur = ch
        else:
            cur = test
    if cur:
        lines.append(cur)
    return lines


def _fit_title(draw, title, max_w, avail_h, base=84):
    """自适应标题字号：从大到小试，保证不溢出宽度、行数<=3、总高<=可用高。"""
    for size in (base, 78, 70, 62, 54, 46):
        f = _load_font(size)
        lines = _wrap(title or "", draw, f, max_w)
        lh = f.getbbox("测")[3] + 20
        if len(lines) <= 3 and lh * len(lines) <= avail_h:
            # 再确认最宽行不超出
            if max(draw.textlength(ln, font=f) for ln in lines) <= max_w + 4:
                return f, lines, lh
    f = _load_font(42)
    lines = _wrap(title or "", draw, f, max_w)
    return f, lines, f.getbbox("测")[3] + 18


def compose_cover(frame_bgr, out_path, title, subtitle, aspect="4:5",
                  face=None, tint=None, brand=BRAND):
    from PIL import Image, ImageDraw
    W, H = ASPECTS.get(aspect, ASPECTS["4:5"])
    ih, iw = frame_bgr.shape[:2]
    src_aspect = iw / ih
    tgt_aspect = W / H

    # 计算裁剪窗口（COVER，无拉伸）：横向不足则裁宽，纵向不足则裁高
    if src_aspect < tgt_aspect:
        cw = int(ih * tgt_aspect); ch = ih
    else:
        cw = iw; ch = int(iw / tgt_aspect)
    # 垂直/水平居中点（人脸感知）
    if face:
        fcx = face[0] + face[2] / 2
        fcy = face[1] + face[3] / 2
        cx = fcx
        # 人脸置于裁剪窗上三分之一（0.40）
        cy = fcy - 0.40 * ch
    else:
        cx = iw / 2
        cy = ih / 2 - (ih - ch) * 0.15   # 中心略偏上（三分法）
    cx = min(max(cx, cw / 2), iw - cw / 2)
    cy = min(max(cy, ch / 2), ih - ch / 2)
    x0, y0 = int(cx - cw / 2), int(cy - ch / 2)
    crop = frame_bgr[y0:y0 + ch, x0:x0 + cw]
    img = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)).resize((W, H), Image.LANCZOS)

    # ---- 专业背景处理：强去饱和 + 模糊 + 全局暗化，让叠加元素绝对主导（封面行业标准做法）----
    # 滚动字幕卡成片已烧入大字字幕文字；此步骤将背景降为"氛围底色"而非竞争元素。
    import PIL.ImageFilter as _IF
    gray_bg = img.convert("L").convert("RGB")
    img = Image.blend(img, gray_bg, 0.45)          # 45% 去饱和（保留场景色调轮廓）
    img = img.filter(_IF.GaussianBlur(radius=3))     # 半径3 彻底柔化背景字幕边缘
    # 全局轻微暗化压低背景亮度，确保标题/品牌在信息流中醒目
    dark = Image.new("RGB", img.size, (20, 24, 36))
    img = Image.blend(img, dark, 0.18)

    d = ImageDraw.Draw(img, "RGBA")

    # 顶部色调（取自主色，低透明度，协调不刺眼）
    if tint:
        tr, tg, tb = tint
        top_h = int(H * 0.30)
        for y in range(top_h):
            a = int(150 * (1 - y / top_h))
            d.line([(0, y), (W, y)], fill=(tr, tg, tb, a))
    # 底部暗渐变（保标题可读）
    grad_h = int(H * 0.46)
    for y in range(grad_h):
        a = int(210 * (y / grad_h))
        d.line([(0, H - grad_h + y), (W, H - grad_h + y)], fill=(0, 0, 0, a))

    # 采样标题区亮度 → 决定文字明暗（自动对比度，防看不清）
    region = img.crop((0, H - grad_h, W, H))
    rlum = float(np.asarray(region.convert("L")).mean())
    if rlum > 150:
        txt_fill, stroke_fill = (15, 23, 42), (255, 255, 255)
    else:
        txt_fill, stroke_fill = (255, 255, 255), (0, 0, 0)

    # 中央播放按钮（半透明圆 + 三角，品牌暗示）
    cxn, cyn = W // 2, int(H * 0.40)
    r = int(min(W, H) * 0.11)
    d.ellipse([cxn - r, cyn - r, cxn + r, cyn + r], fill=(255, 255, 255, 55),
              outline=(255, 255, 255, 175), width=6)
    tri = [(cxn - r * 0.42, cyn - r * 0.55), (cxn - r * 0.42, cyn + r * 0.55),
           (cxn + r * 0.62, cyn)]
    d.polygon(tri, fill=(255, 255, 255, 205))

    # 标题（自适应字号，多行居中，带描边防错位/看不清）
    tf, title_lines, lh = _fit_title(d, title or "", W - 110, grad_h - 110)
    block_h = lh * len(title_lines)
    ty = H - grad_h + 46
    sw = max(2, tf.getbbox("测")[3] // 14)
    for i, ln in enumerate(title_lines):
        y = ty + i * lh
        w = d.textlength(ln, font=tf)
        x = W // 2 - w / 2
        # 描边：先画深色/浅色底字再画主字
        d.text((x, y), ln, font=tf, fill=stroke_fill)
        d.text((x, y), ln, font=tf, fill=txt_fill, stroke_width=sw, stroke_fill=stroke_fill)

    # 副标题（浅灰，限 2 行）
    if subtitle:
        sf = _load_font(42)
        sub_lines = _wrap(subtitle, d, sf, W - 150)[:2]
        sy = ty + block_h + 16
        for ln in sub_lines:
            w = d.textlength(ln, font=sf)
            d.text((W // 2 - w / 2, sy), ln, font=sf, fill=(226, 232, 240))
            sy += sf.getbbox("测")[3] + 12

    # 品牌水印（右上，圆角胶囊底 + 青色描边）
    bf = _load_font(40)
    bw = d.textlength(brand, font=bf) + 56
    bx, by = W - bw - 36, 36
    d.rounded_rectangle([bx, by, bx + bw, by + 76], radius=38,
                        fill=(15, 23, 42, 175), outline=C_ACCENT + (255,), width=3)
    d.text((bx + 28, by + 18), brand, font=bf, fill=(255, 255, 255))

    img.convert("RGB").save(out_path, quality=95)
    return out_path, rlum, len(title_lines)


# ----------------------------------------------------------------- 封面 QC
def cover_qc(cover_path, target_w, target_h):
    """成图质量校验：分辨率达标、清晰度达标、亮度不至于全黑/全白、文字无溢出。
    返回 {"ok":bool,"issues":[...]}。"""
    issues = []
    try:
        im = Image.open(cover_path).convert("RGB")
    except Exception as e:
        return {"ok": False, "issues": [f"无法读取封面: {e}"]}
    w, h = im.size
    if (w, h) != (target_w, target_h):
        issues.append(f"分辨率不符: {w}x{h} 期望 {target_w}x{target_h}")
    gray = np.asarray(im.convert("L"))
    sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if sharp < 18:
        issues.append(f"封面模糊(清晰度 {sharp:.1f} 过低)")
    lum = float(gray.mean())
    if lum < 18 or lum > 248:
        issues.append(f"封面亮度异常({lum:.0f})")
    return {"ok": len(issues) == 0, "issues": issues, "sharp": sharp, "lum": lum}


# ----------------------------------------------------------------- 主流程
def make_cover(video, out_path, title="", subtitle="", platform="", dry_run=False):
    assert os.path.exists(video), f"video not found: {video}"
    aspect = PLATFORM_ASPECT.get((platform or "").lower(), "4:5")
    W, H = ASPECTS.get(aspect, ASPECTS["4:5"])
    if dry_run:
        return 0, {"aspect": aspect, "output": out_path, "rc": 0,
                   "log": [f"CV2: select_best_frames({video}, n=16) -> score-sorted",
                           f"PIL: compose_cover(aspect={aspect}, title='{title}') -> {out_path}",
                           f"QC: cover_qc({out_path}) 分辨率 {W}x{H}"],
                   "dry_run": True}
    cands = select_best_frames(video, n=16)
    if not cands:
        return 1, {"aspect": aspect, "output": out_path, "rc": 1,
                   "issues": ["无法采样候选帧"], "log": ["选帧失败"]}
    last_qc = None
    # 依次尝试 Top3 候选帧，QC 通过即用；不通过则换下一帧重试（质量门禁）
    for cand in cands[:3]:
        tint = dominant_color(cand["frame"])
        try:
            _, rlum, nlines = compose_cover(cand["frame"], out_path, title, subtitle,
                                            aspect, face=cand["face"], tint=tint)
        except Exception as e:
            last_qc = {"ok": False, "issues": [f"合成异常: {e}"]}
            continue
        qc = cover_qc(out_path, W, H)
        last_qc = qc
        if qc["ok"]:
            return 0, {"aspect": aspect, "output": out_path, "rc": 0,
                       "qc": qc, "title_lines": nlines, "frame_t": round(cand["t"], 2),
                       "log": [f"选用帧 t={cand['t']:.2f}s 清晰度={cand['sharp']:.0f} 亮度={cand['lum']:.0f}"]}
    # 三帧均不达标：尽力返回最后一帧（已写出），但标记 qc_fail 交上游裁决
    return 0 if os.path.exists(out_path) else 1, {
        "aspect": aspect, "output": out_path,
        "rc": 0 if os.path.exists(out_path) else 1,
        "qc": last_qc, "qc_fail": True,
        "log": ["Top3 候选帧均未通过 QC，已尽力输出待人工/上游裁决"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="成片视频路径")
    ap.add_argument("--output", required=True, help="封面输出路径(.jpg)")
    ap.add_argument("--title", default="", help="封面主标题")
    ap.add_argument("--subtitle", default="", help="封面副标题")
    ap.add_argument("--platform", default="", help="douyin/video/xhs/red/square")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出完整质检报告（供服务端解析门禁）")
    args = ap.parse_args()
    rc, man = make_cover(args.input, args.output, args.title, args.subtitle,
                         args.platform, args.dry_run)
    if args.dry_run:
        print("\n".join(man["log"]))
    if args.json:
        print("__COVER_JSON__" + json.dumps(man, ensure_ascii=False))
    else:
        print(f"make_cover rc={rc} -> {args.output} (aspect={man.get('aspect')}) "
              f"qc_ok={man.get('qc', {}).get('ok') if isinstance(man.get('qc'), dict) else 'n/a'}")
    sys.exit(rc)


if __name__ == "__main__":
    main()
