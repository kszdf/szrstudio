#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
发布素材包生成器（内容生产链的下游）
  读 batch 目录里每条的 03_逐字稿定稿.md + 04_音频.wav
  -> 1) 自动字幕 subtitle.ass（按标点切句+字数权重分配时间轴，竖屏1080x1920标准）
  -> 2) 发布文案 publish.md（标题/话题/钩子，LLM生成）
  -> 3) 模特场景建议 model_hint.txt
  -> 4) 拷贝音频、整理成一条标准素材包，供上传 Duix 选模特出片 + 各平台分发

用法:
  python build_package.py --src qwen_out/batch1 --dst qwen_out/batch1_pkg
"""
import os
import re
import sys
import time
import shutil
import argparse
import subprocess
from pathlib import Path

# 剥离二创稿的三段标记（=== 开头/正文/结尾 ===），得到纯净口播正文
try:
    from forbidden_words import clean_script
except Exception:
    def clean_script(t):
        return t

SRC_DEFAULT = "qwen_out/batch1"
DST_DEFAULT = "qwen_out/batch1_pkg"


# ---------- 字幕生成 ----------
def ffprobe_duration(wav_path):
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(wav_path)],
            text=True)
        return float(out.strip())
    except Exception:
        return 0.0


def split_sentences(text):
    """按句末标点切大句；大句内按逗号细分，过短片段合并，保证每屏一句完整语义。"""
    big = re.split(r"(?<=[。！？])", text)
    result = []
    for b in big:
        b = b.strip()
        if not b:
            continue
        subs = re.split(r"(?<=，)", b)  # 逗号切分（保留逗号），顿号连接的词算一个短语
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


def wrap_line(seg, max_chars=12):
    """长句按 max_chars 强制换行。"""
    if len(seg) <= max_chars:
        return seg
    lines = []
    for i in range(0, len(seg), max_chars):
        lines.append(seg[i:i + max_chars])
    return "\\N".join(lines)


def gen_ass(text, duration, resx=1080, resy=1920):
    segs = split_sentences(text)
    total_chars = sum(len(s) for s in segs) or 1
    # 首尾留 0.3s 余量
    usable = max(duration - 0.6, 0.5)
    start = 0.3
    events = []
    for s in segs:
        sd = usable * len(s) / total_chars
        sd = max(sd, 1.0)  # 单句最短 1s，避免一闪而过
        end = start + sd
        body = wrap_line(s)
        events.append(
            f"Dialogue: 0,{start:.2f},{end:.2f},Default,,0,0,0,,{body}")
        start = end
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {resx}
PlayResY: {resy}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, Outline, Shadow, Bold, Alignment, MarginL, MarginR, MarginV
Style: Default,SimHei,64,&H00FFFFFF,&H00000000,4,1,0,2,60,60,80

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    return header + "\n".join(events) + "\n"


# ---------- 发布文案（LLM） ----------
def llm(prompt, model="qwen-turbo", retries=3):
    from dashscope import Generation
    last = None
    for i in range(retries):
        try:
            r = Generation.call(model=model, prompt=prompt, result_format="message")
            return r.output.choices[0].message.content.strip()
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(2 * (i + 1))
    print(f"  [warn] LLM 失败: {last}")
    return ""


def gen_publish(text):
    p = (
        "为这条财税口播短视频生成发布文案。\n口播稿：\n" + text + "\n\n"
        "输出三部分，严格按下面格式（不要多余解释）：\n"
        "标题：一句话吸睛标题（18字内，说清痛点或好处，不标题党）\n"
        "话题：3个#话题标签，用空格隔开（如 #虚开发票 #金税四期 #老板必看）\n"
        "文案：一句发布文案（含自然留资钩子，30字内）"
    )
    raw = llm(p)
    title, topics, body = "", "", ""
    for line in raw.splitlines():
        if line.startswith("标题"):
            title = line.split("：", 1)[-1].strip()
        elif line.startswith("话题"):
            topics = line.split("：", 1)[-1].strip()
        elif line.startswith("文案"):
            body = line.split("：", 1)[-1].strip()
    return title, topics, body


def model_hint(topic_dirname, text):
    """简单场景映射，给出 Duix 选模特建议。"""
    hints = {
        "001": "虚开发票/公转私类：建议『办公讲解』场景模特（正装、背景书架/办公桌）；备选『茶桌闲聊』缓和说教感",
        "002": "暂估成本类：建议『办公讲解』场景模特，偏专业严谨",
        "003": "公转私类：建议『办公讲解』场景模特，可配图表背景",
        "004": "股东借款类：建议『办公讲解』或『茶桌闲聊』，叙事感更亲和",
        "005": "个人卡发工资类：建议『茶桌闲聊』场景模特，拉家常讲风险更自然",
    }
    return hints.get(topic_dirname, "默认『办公讲解』场景模特；叙事类内容可换『茶桌闲聊』")


def build_one(src_dir, dst_dir):
    src_dir = Path(src_dir)
    dst_dir = Path(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)

    raw = (src_dir / "03_逐字稿定稿.md").read_text(encoding="utf-8").strip()
    script = clean_script(raw)  # 兼容三段式标记与旧纯文本
    audio = src_dir / "04_音频.wav"
    dur = ffprobe_duration(audio) if audio.exists() else 0.0

    # 1) 字幕
    ass = gen_ass(script, dur)
    (dst_dir / "subtitle.ass").write_text(ass, encoding="utf-8")

    # 2) 发布文案
    print(f"  [publish] 生成标题/话题...")
    title, topics, body = gen_publish(script)
    publish_md = f"# 发布文案\n\n**标题**：{title}\n\n**话题**：{topics}\n\n**文案**：{body}\n"
    (dst_dir / "publish.md").write_text(publish_md, encoding="utf-8")

    # 3) 模特建议
    hint = model_hint(src_dir.name, script)
    (dst_dir / "model_hint.txt").write_text("Duix 选模特建议：\n" + hint + "\n", encoding="utf-8")

    # 4) 拷贝音频 + 文案
    if audio.exists():
        shutil.copy(audio, dst_dir / "audio.wav")
    (dst_dir / "script.md").write_text(script, encoding="utf-8")

    # 5) 封面：预留上传位（用户自行设计，系统不自动生成）
    cover_slot = (
        "封面自行上传说明\n"
        "====================\n"
        "本系统不自动生成封面，请用户自行设计后上传。\n\n"
        "操作：将做好的封面图命名为 cover.png，放到本目录即可。\n"
        "要求：竖版 1080x1920 PNG（9:16），与视频号/抖音封面规格一致。\n"
        "出片后分发脚本会自动读取本目录的 cover.png（若存在）。\n\n"
        "（本文件为占位说明，上传 cover.png 后可删除。）\n"
    )
    (dst_dir / "cover_upload_here.txt").write_text(cover_slot, encoding="utf-8")

    print(f"  -> {dst_dir.name}: 字幕/文案/建议/音频 已打包 (时长 {dur:.1f}s)；封面位已预留")
    return title


def main():
    ap = argparse.ArgumentParser(description="生成发布素材包（字幕+文案+模特建议）")
    ap.add_argument("--src", default=SRC_DEFAULT)
    ap.add_argument("--dst", default=DST_DEFAULT)
    args = ap.parse_args()

    src = Path(args.src)
    dirs = sorted([d for d in src.iterdir() if d.is_dir() and re.match(r"\d+", d.name)])
    if not dirs:
        sys.exit(f"在 {src} 没找到带数字的子目录")
    print(f"共 {len(dirs)} 条，生成素材包到 {args.dst}")
    for d in dirs:
        print(f"\n===== {d.name} =====")
        build_one(d, Path(args.dst) / d.name)


if __name__ == "__main__":
    if not os.environ.get("DASHSCOPE_API_KEY"):
        sys.exit("请先设置 DASHSCOPE_API_KEY")
    main()
