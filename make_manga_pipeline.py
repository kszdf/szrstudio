#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 漫剧全自动管线: 内容 → 类型判断 → LLM分镜 → 生图(固定角色) → 动效配音成片。
法条/政策类 → 提示走口播(不漫剧化)。"""
import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True, help="财税内容/文案")
    ap.add_argument("--voice", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    # 1) 分镜
    from make_manga_storyboard import generate, ROLE
    r = generate(args.text)
    print(f"[1/5] 分镜: {r['form']} ({r['reason']}) {len(r['shots'])} 幕")
    if r["form"] == "lecture":
        print("→ 法条/政策类: 不漫剧化, 请走幕后音/数字人口播(保准确性)")
        return 1
    if not r["shots"]:
        print("→ 无分镜, 中止")
        return 1

    # 2) 逐幕生图(固定角色 + 分镜场景 + 表情)
    from model_providers import ensure_env
    ensure_env()
    from dashscope import ImageSynthesis
    import urllib.request
    shots = []
    h = hashlib.md5(args.text.encode("utf-8")).hexdigest()[:8]
    for i, s in enumerate(r["shots"]):
        prompt = f"{ROLE}，{s['shot']}，表情{s['emotion']}，竖版构图"
        print(f"[2/5] 生图 幕{i+1} ({s['emotion']}) ...")
        rsp = ImageSynthesis.call(model="wanx2.1-t2i-turbo", prompt=prompt,
                                  size="720*1280", n=1,
                                  api_key=os.environ.get("DASHSCOPE_API_KEY"))
        if rsp.status_code != 200:
            print(f"  生图失败: {rsp.message}")
            return 1
        dest = BASE / "cartoon_assets" / f"manga_{h}_{i}.jpg"
        urllib.request.urlretrieve(rsp.output.results[0].url, dest)
        shots.append(str(dest))

    # 3) 成片(复用 make_manga_video)
    narration = "|".join(s["narration"] for s in r["shots"])
    print(f"[3/5] 成片({len(shots)} 幕) ...")
    cmd = [sys.executable, str(BASE / "make_manga_video.py"),
           "--shots", ",".join(shots), "--narration", narration,
           "--voice", args.voice, "--out", args.out]
    if args.title:
        cmd += ["--title", args.title]
    subprocess.run(cmd, cwd=str(BASE))
    print(f"成品: {args.out}")


if __name__ == "__main__":
    main()
