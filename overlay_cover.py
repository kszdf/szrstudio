#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
封面文字叠加器 v2：以用户海马体职业照为底子，每条标题+品牌名叠在下方深蓝半透明色块上。
- 底图：covers/portrait_src.jpg (1078x1080 浅灰纯净背景)
- 上下延伸到 1080x1920，同色调 #F0F0F3
- 下方 640 区域加 #0E2A4A@0.78 半透明深蓝块
- 标题：白字，88px，居中
- 品牌名：金色 #E8C77D，42px，居中

用法:
  python overlay_cover.py
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path("D:/heygem_data/gpt_sovits")
BG = BASE / "covers/portrait_src.jpg"
PKG = BASE / "qwen_out/batch1_pkg"
OUT = BASE / "covers/final"
FONT = "fonts/simhei.ttf"
BRAND = "老张讲财税  ·  慧根堂税务风险咨询"
W, H = 1080, 1920
PAD_COLOR = "#F0F0F3"        # 上下延伸色（贴近浅灰背景）
BLOCK_COLOR = "#0E2A4A"     # 标题区深蓝
BLOCK_ALPHA = 0.78
TITLE_SIZE = 88
BRAND_SIZE = 42


def parse_title(publish_md: Path) -> str:
    txt = publish_md.read_text(encoding="utf-8")
    m = re.search(r"\*\*标题\*\*[：:]\s*(.+)", txt)
    return m.group(1).strip() if m else ""


def wrap_cn(text: str, max_chars: int = 10):
    """中文按标点+字数断行，最多3行。"""
    res, cur = [], ""
    for ch in text:
        cur += ch
        if ch in "，。！？、；" and len(cur) >= 6:
            res.append(cur)
            cur = ""
        elif len(cur) >= max_chars:
            res.append(cur)
            cur = ""
    if cur:
        res.append(cur)
    return res[:3]


def build_filter(title_lines):
    lines = [l.replace("'", "") for l in title_lines]
    title_text = "\\n".join(lines)  # 字面 \n 交给 ffmpeg 渲染多行
    n = len(lines)
    # 标题在色块内 y=1280-1920 区间居中，3行时第一行上移
    first_y = 1450 - (n - 1) * 52
    return (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},"
        f"pad={W}:{H}:0:420:{PAD_COLOR},"
        f"drawbox=x=0:y=1280:w={W}:h=640:color={BLOCK_COLOR}@{BLOCK_ALPHA}:t=fill,"
        f"drawtext=fontfile='{FONT}':fontsize={TITLE_SIZE}:fontcolor=white:"
        f"shadowcolor=#000000@0.6:shadowx=2:shadowy=2:line_spacing=22:"
        f"text='{title_text}':x=(w-text_w)/2:y={first_y},"
        f"drawtext=fontfile='{FONT}':fontsize={BRAND_SIZE}:fontcolor=#E8C77D:"
        f"shadowcolor=#000000@0.6:shadowx=2:shadowy=2:"
        f"text='{BRAND}':x=(w-text_w)/2:y=1820"
    )


def main():
    if not BG.exists():
        sys.exit(f"没找到底图 {BG}")
    OUT.mkdir(parents=True, exist_ok=True)
    dirs = sorted([d for d in PKG.iterdir() if d.is_dir() and re.match(r"\d+", d.name)])
    if not dirs:
        sys.exit(f"没找到素材包 {PKG}")
    for d in dirs:
        title = parse_title(d / "publish.md")
        if not title:
            print(f"  [skip] {d.name} 无标题")
            continue
        lines = wrap_cn(title)
        flt = build_filter(lines)
        out = OUT / f"{d.name}_cover.png"
        cmd = ["ffmpeg", "-y", "-i", str(BG), "-vf", flt, "-frames:v", "1", str(out)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  [ERR] {d.name} ffmpeg 失败:")
            print("       " + r.stderr.strip().splitlines()[-1] if r.stderr else "(no stderr)")
            continue
        shutil.copy(out, d / "cover.png")
        print(f"  -> {out.name}  标题: {title}")


if __name__ == "__main__":
    main()
