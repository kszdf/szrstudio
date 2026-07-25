#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
后处理合成器（Duix 出片后的本地精修）
输入: 数字人视频(video) + 字幕(subtitle.ass) + 可选品牌片头(intro)
处理:
  1) 用 libass 把 ASS 字幕烧录进视频（白字黑边，竖屏标准）
  2) 在开头拼接品牌片头（淡入淡出）
输出: 成品短视频（可直接分发）
用法:
  python finalize_video.py --video <数字人视频> --ass <字幕> --out <成品>
  # 可选: --intro covers/intro.mp4 (默认)  --no-intro (不加片头)
  # 可选: --replace-audio <wav> (用指定音频替换视频原声，测试/补漏用)
"""
import argparse
import subprocess
import sys
from pathlib import Path

BASE = Path("D:/heygem_data/gpt_sovits")
INTRO = BASE / "covers/intro.mp4"
TMP_MID = BASE / "_tmp_mid.mp4"


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("  [ERR] 命令失败:", " ".join(cmd[:6]), "...")
        print(r.stderr[-1000:])
        sys.exit(1)
    return r


def burn_subtitle(video, ass, mid_out, replace_audio=None):
    # ffmpeg ass 滤镜对 Windows 反斜杠路径不友好，统一转正斜杠
    ass_path = str(ass).replace("\\", "/")
    if replace_audio:
        # 替换音频：用新 wav 替代视频音轨
        cmd = [
            "ffmpeg", "-y", "-i", str(video), "-i", str(replace_audio),
            "-map", "0:v:0", "-map", "1:a:0",
            "-vf", f"ass={ass_path}", "-c:a", "aac", "-ar", "44100",
            "-pix_fmt", "yuv420p", str(mid_out),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-i", str(video),
            "-vf", f"ass={ass_path}",
            "-c:a", "copy", "-pix_fmt", "yuv420p", str(mid_out),
        ]
    run(cmd)
    print(f"  字幕烧录完成: {mid_out}")


def concat_intro(intro, mid, out):
    # 统一 scale 到 1080x1920 标准竖屏，避免分辨率不一致导致 concat 失败
    fc = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1[v0];"
        "[1:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1[v1];"
        "[0:a]aresample=44100[a0];[1:a]aresample=44100[a1];"
        "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]"
    )
    cmd = [
        "ffmpeg", "-y", "-i", str(intro), "-i", str(mid),
        "-filter_complex", fc,
        "-map", "[v]", "-map", "[a]", "-pix_fmt", "yuv420p", str(out),
    ]
    run(cmd)
    print(f"  片头拼接完成: {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--ass", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--intro", default=str(INTRO))
    ap.add_argument("--no-intro", action="store_true")
    ap.add_argument("--replace-audio", default=None)
    args = ap.parse_args()

    video = Path(args.video)
    ass = Path(args.ass)
    if not video.exists():
        sys.exit(f"视频不存在: {video}")
    if not ass.exists():
        sys.exit(f"字幕不存在: {ass}")

    burn_subtitle(video, ass, TMP_MID, replace_audio=args.replace_audio)

    if args.no_intro or not Path(args.intro).exists():
        # 不拼片头，直接 rename
        TMP_MID.replace(Path(args.out))
        print(f"  成品(无片头): {args.out}")
    else:
        concat_intro(Path(args.intro), TMP_MID, Path(args.out))
        TMP_MID.unlink(missing_ok=True)
        print(f"  成品: {args.out}")


if __name__ == "__main__":
    main()
