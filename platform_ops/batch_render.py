# -*- coding: utf-8 -*-
"""
批量出片（幕后音·动态画面）: 用 make_motion_video_v4.py 并行渲染多条视频。

用法:
  D:/heygem/py310/Scripts/python.exe batch_render.py jobs.json [--concurrency 2] [--workers 12] [--out-dir 输出目录]

jobs.json 示例:
  [
    {"script": "D:/heygem_data/gpt_sovits/_dlg_stock_v1.txt",  "title": "库存虚高怎么处理", "style": "财经严谨"},
    {"script": "D:/heygem_data/gpt_sovits/_dlg_stock_female.txt","title": "库存虚高怎么处理(女声)", "style": "财经严谨"}
  ]

并发模型: 同时渲染 --concurrency 条视频, 每条用 --workers 个渲染进程。
建议(16核/32G): --concurrency 2 --workers 6  或  --concurrency 3 --workers 4, 避免超订导致内存吃紧。
每条端到端约 2-3 分钟(并行TTS + 并行渲染), 出片文件名为 "<title>.mp4" 落在 --out-dir。
"""
import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

PY = r"D:/heygem/py310/Scripts/python.exe"
ENGINE = r"D:/heygem_data/gpt_sovits/make_motion_video_v4.py"
DEFAULT_OUT = r"C:\Users\lenovo\短视频自动化生产\演示输出"


def render_one(job, idx, out_dir, workers, tts_workers):
    script = job["script"]
    title = job.get("title") or os.path.splitext(os.path.basename(script))[0]
    style = job.get("style") or "财经严谨"
    if not os.path.exists(script):
        return {"idx": idx, "title": title, "ok": False, "error": f"稿子不存在: {script}"}
    out = os.path.join(out_dir, f"{title}.mp4")
    log = os.path.join(out_dir, f"_{title}.batch.log")
    t0 = time.time()
    cmd = [PY, ENGINE, "--script", script, "--out", out,
           "--title", title, "--style", style, "--dialogue",
           "--workers", str(workers), "--tts-workers", str(tts_workers)]
    with open(log, "w", encoding="utf-8") as f:
        rc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT).returncode
    el = (time.time() - t0) / 60.0
    return {"idx": idx, "title": title, "ok": rc == 0 and os.path.exists(out),
            "out": out, "elapsed_min": round(el, 2), "rc": rc, "log": log}


def main():
    ap = argparse.ArgumentParser(description="批量出片(幕后音·动态画面)")
    ap.add_argument("jobs", help="jobs.json: [{\"script\":..., \"title\":..., \"style\":...}]")
    ap.add_argument("--concurrency", type=int, default=1, help="同时渲染的视频数")
    ap.add_argument("--workers", type=int, default=0, help="每条视频的渲染进程数(0=自动: 12//并发)")
    ap.add_argument("--tts-workers", type=int, default=4, help="每条视频的并行TTS段数")
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    args = ap.parse_args()

    jobs = json.load(open(args.jobs, encoding="utf-8-sig"))
    os.makedirs(args.out_dir, exist_ok=True)
    workers = args.workers if args.workers > 0 else max(1, 12 // max(1, args.concurrency))
    print(f"[batch] {len(jobs)} 条视频, 并发 {args.concurrency}, 每条约 {workers} 渲染进程", flush=True)

    results = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = {ex.submit(render_one, j, i, args.out_dir, workers, args.tts_workers): i
                for i, j in enumerate(jobs)}
        for fut in as_completed(futs):
            r = fut.result()
            results.append(r)
            tag = "OK " if r["ok"] else "FAIL"
            print(f"  [{r['idx']+1}/{len(jobs)}] {tag} {r['title']}  {r.get('elapsed_min','?')}分"
                  + (f"  -> {r['out']}" if r.get("out") else f"  {r.get('error')}"), flush=True)

    results.sort(key=lambda x: x["idx"])
    report = os.path.join(args.out_dir, "_batch_report.json")
    json.dump({"elapsed_min": round((time.time() - t0) / 60.0, 2), "results": results},
              open(report, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    ok_n = sum(1 for r in results if r["ok"])
    print(f"[batch] 完成 {ok_n}/{len(jobs)}, 总耗时 {(time.time()-t0)/60:.1f} 分钟, 报告: {report}", flush=True)


if __name__ == "__main__":
    main()
