#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 白板式全自动管线: 内容 → LLM 布局(标题/要点/警示, 智能配色) → 手绘逐笔动画 → 配音成片。
用法: python make_whiteboard_pipeline.py --text "内容" --voice <voice_id> --out out.mp4"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True, help="财税内容/文案")
    ap.add_argument("--voice", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--duration", type=float, default=20.0, help="成片时长(秒)")
    ap.add_argument("--title", default="", help="标题提示(可选, 布局生成参考)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    # 1) LLM 白板布局
    from make_whiteboard_storyboard import generate
    print("[1/3] 生成白板布局 ...")
    layout = generate(args.text if not args.title else f"{args.title}：{args.text}")
    print(f"  标题: {layout['title']} | 要点 {len(layout['items'])} 条 | 警示: {layout.get('warn') or '无'}")

    # 2) 渲染
    print("[2/3] 渲染白板动画 ...")
    tmp = Path(tempfile.mkdtemp(prefix="wbpipe_"))
    layout_file = tmp / "layout.json"
    layout_file.write_text(json.dumps(layout, ensure_ascii=False), encoding="utf-8")
    subprocess.run([sys.executable, str(BASE / "make_whiteboard_video.py"),
                    "--out", args.out, "--voice", args.voice,
                    "--duration", str(args.duration),
                    "--layout", str(layout_file)], cwd=str(BASE))
    print(f"成品: {args.out}")


if __name__ == "__main__":
    main()
