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
    ap.add_argument("--i2v", action="store_true",
                    help="AI 图生视频动效模式(每幕约0.24元/秒, 惊艳; 缺省用 Ken Burns 代码动效)")
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
        prompt = (f"{ROLE}，{s['shot']}，表情{s['emotion']}，竖版构图，"
                  f"明亮通透的背景，高调布光，整体画面明亮鲜艳，儿童绘本亮色系")
        print(f"[2/5] 生图 幕{i+1} ({s['emotion']}) ...")
        # 2026-08-31 修复：生图容错——①审核拦截(DataInspectionFailed)换措辞重试 2 次；②重试仍失败降级为渐变占位图，
        # 不让整条任务失败(此前单幕被审核拦截即整条failed, 浪费已生成图和费用)
        rsp = None
        for attempt in range(3):
            try:
                rsp = ImageSynthesis.call(model="wanx2.1-t2i-turbo", prompt=prompt,
                                          size="720*1280", n=1,
                                          api_key=os.environ.get("DASHSCOPE_API_KEY"))
                if rsp.status_code == 200:
                    break
                msg = str(getattr(rsp, "message", "") or "")
                if "DataInspectionFailed" in msg or "inappropriate" in msg.lower():
                    # 审核拦截：弱化措辞重试(去掉可能触发审核的敏感词, 用中性描述)
                    print(f"  幕{i+1} 审核拦截(尝试{attempt+2}/3): 换中性措辞重试")
                    prompt = (f"{ROLE}，{s['shot']}，表情{s['emotion']}，竖版构图，"
                              f"明亮背景，色彩明快，儿童绘本风格")
                else:
                    print(f"  幕{i+1} 生图失败(尝试{attempt+2}/3): {msg}")
            except Exception as e:  # noqa: BLE001
                print(f"  幕{i+1} 生图异常(尝试{attempt+2}/3): {e}")
            rsp = None
        if rsp is None or rsp.status_code != 200:
            # 降级：生成渐变占位图(纯色+文字提示该幕生成失败)，不阻断整条出片
            print(f"  ⚠ 幕{i+1} 生图重试仍失败，使用占位图(该幕画面为占位, 可重跑)")
            try:
                from PIL import Image, ImageDraw, ImageFont
                ph = Image.new("RGB", (720, 1280), (235, 238, 245))
                d = ImageDraw.Draw(ph)
                try:
                    f = ImageFont.truetype(str(BASE / "fonts/simhei.ttf"), 40)
                except Exception:  # noqa: BLE001
                    f = ImageFont.load_default()
                d.text((360, 640), f"该幕画面生成失败\n内容审核未通过或服务异常", font=f,
                       fill=(120, 128, 140), anchor="mm")
                dest = BASE / "cartoon_assets" / f"manga_{h}_{i}.jpg"
                ph.save(dest)
                shots.append(str(dest))
            except Exception:  # noqa: BLE001
                return 1
            continue
        dest = BASE / "cartoon_assets" / f"manga_{h}_{i}.jpg"
        urllib.request.urlretrieve(rsp.output.results[0].url, dest)
        shots.append(str(dest))

    # 3) 成片(复用 make_manga_video)
    narration = "|".join(s["narration"] for s in r["shots"])
    print(f"[3/5] 成片({len(shots)} 幕, {'i2v图生视频' if args.i2v else 'Ken Burns代码动效'}) ...")
    cmd = [sys.executable, str(BASE / "make_manga_video.py"),
           "--shots", ",".join(shots), "--narration", narration,
           "--voice", args.voice, "--out", args.out]
    if args.i2v:
        cmd += ["--i2v"]
        # 信息层字段: 优先 LLM 分镜输出, 缺省由 make_manga_video 自动派生
        tags = [s.get("tag", "") for s in r["shots"]]
        cards = [s.get("card", "") for s in r["shots"]]
        nums = [s.get("num", "") for s in r["shots"]]
        if any(tags):
            cmd += ["--tags", ",".join(tags)]
        if any(cards):
            cmd += ["--cards", ",".join(cards)]
        if any(nums):
            cmd += ["--nums", ",".join(nums)]
    if r.get("steps"):
        cmd += ["--steps", ",".join(r["steps"])]
    if args.title:
        cmd += ["--title", args.title]
    subprocess.run(cmd, cwd=str(BASE))
    print(f"成品: {args.out}")


if __name__ == "__main__":
    main()
