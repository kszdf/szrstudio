#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日批量出片编排器（老张讲财税短视频矩阵）
========================================
把分散的脚本串成两段式流水线，符合"先审查再确认"工作流：

  Phase A  草稿+门禁  : daily_pipeline.py draft --topics 选题.txt --date 20260723
            -> 选题→逐字稿→二次改写(违禁词红线+三段式)→定稿 .md + 违禁词检查 .md
            -> 【停】你打开 qwen_out/<date>/<NNN>/03_逐字稿定稿.md 审改（开头/正文/结尾）

  Phase B  生产       : daily_pipeline.py produce --date 20260723 [--item 003] [--dry-run]
            -> 对每个已审改条目：tts出音频 → 素材包(字幕/文案/封面位) → HEYGEM出片 → QC
            -> 违禁词高危未清的条目自动跳过（门禁），直到你改干净

依赖：DASHSCOPE_API_KEY（改写/配音）+ Docker(HEYGEM 8383) + ffmpeg。
      当前若没有 Key / Docker，可用 --dry-run 预演流程、核对门禁结果。

用法：
  python daily_pipeline.py draft  --topics topics.txt --date 20260723
  python daily_pipeline.py produce --date 20260723 --dry-run      # 预演
  python daily_pipeline.py produce --date 20260723               # 真出片
  python daily_pipeline.py produce --date 20260723 --item 003    # 单条重出
  python daily_pipeline.py report  --date 20260723               # 对已有成品重跑 QC+门禁
"""
import os
import sys
import time
import json
import argparse
import subprocess
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from content_pipeline import run_one, tts_from
from build_package import build_one
from forbidden_words import scan, format_report

BASE = Path("D:/heygem_data")
QWEN_OUT = BASE / "gpt_sovits" / "qwen_out"
OUTPUT = BASE / "output"
FFPROBE = r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin/ffprobe"
DEFAULT_MODEL_VIDEO = "/code/data/BGZSP20260721_t18_silent.mp4"  # 容器内路径


# ------------------------------------------------------------------ Phase A
def phase_draft(topics_file, date, rewrite_req=""):
    topics = [l.strip() for l in Path(topics_file).read_text(encoding="utf-8").splitlines() if l.strip()]
    if not topics:
        sys.exit("选题文件为空")
    day_dir = QWEN_OUT / date
    print(f"=== Phase A 草稿+门禁：{len(topics)} 条，输出到 {day_dir} ===")
    for i, t in enumerate(topics, 1):
        print(f"\n----- [{i}/{len(topics)}] {t[:30]} -----")
        run_one(t, day_dir / f"{i:03d}", rewrite_req=rewrite_req, no_audio=True)
    print("\n✅ 草稿完成。请审阅并修改各条 03_逐字稿定稿.md（开头/正文/结尾），")
    print("   确认 03_违禁词检查.md 无高危后，再跑：")
    print(f"   python daily_pipeline.py produce --date {date}")


# ------------------------------------------------------------------ 门禁
def gate_ok(script_md: Path) -> tuple[bool, str]:
    """返回 (是否可生产, 报告文本)。高危命中则拦截。"""
    txt = script_md.read_text(encoding="utf-8")
    hits = scan(txt)
    real = [h for h in hits if h["level"] == "high" and not h.get("need_human")]
    if real:
        return False, format_report(real)
    return True, ""


# ------------------------------------------------------------------ Phase B
def produce_one(date, item, model_video, dry_run):
    day_dir = QWEN_OUT / date
    src = day_dir / item
    pkg = day_dir / "pkg" / item
    out = OUTPUT / date / f"avatar_{item}.mp4"

    log = [f"[{item}]"]

    # 门禁
    script_md = src / "03_逐字稿定稿.md"
    if not script_md.exists():
        return False, [f"[{item}] ❌ 缺定稿 {script_md}（先跑 draft）"]
    ok, rep = gate_ok(script_md)
    if not ok:
        return False, [f"[{item}] 🔴 门禁拦截：定稿仍含高危违禁词，已跳过生产。请改干净后重跑。",
                       rep]

    if dry_run:
        log.append("  [dry-run] 门禁通过，计划：tts → 素材包 → HEYGEM出片 → QC")
        return True, log

    # 1) TTS
    audio = src / "04_音频.wav"
    try:
        tts_from(str(script_md), str(audio))
        log.append("  ✅ 音频")
    except Exception as e:
        return False, log + [f"  ❌ TTS 失败: {e}"]

    # 2) 素材包
    try:
        build_one(src, pkg)
        log.append("  ✅ 素材包(字幕/文案/封面位)")
        # 发布文案门禁提醒（caption 可发前手改，故只报警不阻断）
        pub = pkg / "publish.md"
        if pub.exists():
            ph = [h for h in scan(pub.read_text(encoding="utf-8")) if h["level"] == "high" and not h.get("need_human")]
            if ph:
                log.append("  ⚠️ 发布文案含高危违禁词，发前请改 caption：")
                log.append("     " + "、".join(h["word"] for h in ph))
    except Exception as e:
        return False, log + [f"  ❌ 素材包失败: {e}"]

    # 3) HEYGEM 出片
    ass = pkg / "subtitle.ass"
    audio_wav = pkg / "audio.wav"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [sys.executable, str(Path(__file__).with_name("make_avatar_video.py")),
             "--audio", str(audio_wav), "--ass", str(ass),
             "--model", model_video, "--out", str(out), "--name", f"{date}_{item}"],
            check=True)
        log.append(f"  ✅ 出片 {out.name}")
    except Exception as e:
        return False, log + [f"  ❌ 出片失败: {e}"]

    # 4) QC
    qc = qc_one(out)
    log.append("  " + qc)
    return True, log


def qc_one(mp4: Path) -> str:
    if not mp4.exists():
        return "❌ 成品缺失"
    try:
        r = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json",
                            "-show_format", "-show_streams", str(mp4)],
                           capture_output=True, text=True, timeout=30)
        info = json.loads(r.stdout)
        fmt = info["format"]
        v = next(s for s in info["streams"] if s["codec_type"] == "video")
        auds = [s for s in info["streams"] if s["codec_type"] == "audio"]
        W, H = int(v["width"]), int(v["height"])
        dur = float(fmt["duration"])
        vb = int(fmt.get("bit_rate", 0)) // 1000
        res_ok = (W, H) == (1080, 1920)
        enc_ok = v["codec_name"] == "h264" and auds and auds[0]["codec_name"] == "aac"
        dur_ok = 7 <= dur <= 60
        a_ok = len(auds) == 1
        ok = res_ok and enc_ok and dur_ok and a_ok
        return ("✅ QC通过 " if ok else "⚠️ QC需修 ") + \
            f"{W}x{H}/{'h264' if enc_ok else v['codec_name']}/单音轨{'✅' if a_ok else '❌'}/{dur:.0f}s/码率{vb}k"
    except Exception as e:
        return f"⚠️ QC异常: {e}"


# ------------------------------------------------------------------ report
def phase_report(date):
    day_dir = QWEN_OUT / date
    out_dir = OUTPUT / date
    print(f"=== 报告 {date} ===")
    items = sorted([d.name for d in day_dir.iterdir() if d.is_dir() and d.name.isdigit()],
                   key=lambda x: int(x))
    ok_all = True
    for it in items:
        sc = day_dir / it / "03_逐字稿定稿.md"
        gate, rep = gate_ok(sc) if sc.exists() else (False, "缺定稿")
        mp4 = out_dir / f"avatar_{it}.mp4"
        qc = qc_one(mp4) if mp4.exists() else "成品缺失"
        flag = "✅" if (gate and mp4.exists()) else "⚠️"
        if not (gate and mp4.exists()):
            ok_all = False
        print(f"\n{flag} [{it}] 门禁{'✅' if gate else '🔴'} | {qc}")
        if not gate:
            print(rep)
    print("\n汇总:", "✅ 全部就绪" if ok_all else "⚠️ 有条目待处理（看上）")


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description="每日批量出片编排器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("draft", help="Phase A 草稿+违禁词门禁（暂停待审改）")
    a.add_argument("--topics", required=True)
    a.add_argument("--date", required=True)
    a.add_argument("--rewrite", default="")

    b = sub.add_parser("produce", help="Phase B 生产（tts→素材包→出片→QC）")
    b.add_argument("--date", required=True)
    b.add_argument("--item", default=None, help="只处理某条，如 003")
    b.add_argument("--model", default=DEFAULT_MODEL_VIDEO, help="HEYGEM 容器内模特路径")
    b.add_argument("--dry-run", action="store_true")

    c = sub.add_parser("report", help="对已有成品重跑 QC+门禁")
    c.add_argument("--date", required=True)

    args = ap.parse_args()

    if args.cmd == "draft":
        phase_draft(args.topics, args.date, args.rewrite)
    elif args.cmd == "produce":
        day_dir = QWEN_OUT / args.date
        if args.item:
            items = [args.item]
        else:
            items = sorted([d.name for d in day_dir.iterdir() if d.is_dir() and d.name.isdigit()],
                           key=lambda x: int(x))
        print(f"=== Phase B 生产 {args.date} （{'预演' if args.dry_run else '实跑'}） ===")
        results = []
        for it in items:
            ok, log = produce_one(args.date, it, args.model, args.dry_run)
            results.append((it, ok))
            print("\n".join(log))
            time.sleep(0.2)
        done = sum(1 for _, ok in results if ok)
        print(f"\n=== 完成 {done}/{len(items)} 条 ===")
    elif args.cmd == "report":
        phase_report(args.date)


if __name__ == "__main__":
    if not os.environ.get("DASHSCOPE_API_KEY"):
        print("[提示] 当前未检测到 DASHSCOPE_API_KEY；Phase A/B 实跑需要它。")
        print("       可用 --dry-run 预演流程、或先 set DASHSCOPE_API_KEY 再跑。")
    main()
