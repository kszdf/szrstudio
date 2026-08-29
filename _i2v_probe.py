# -*- coding: utf-8 -*-
"""图生视频探针: 验证 wanx2.1-i2v-turbo 可用性 + 余额 + 效果。
用法: python _i2v_probe.py <分镜图路径> <输出mp4路径> [--prompt 动作描述]
失败(欠费/限流)会打印明确错误; 生成失败不扣费。"""
import argparse
import os
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("img")
    ap.add_argument("out")
    ap.add_argument("--prompt", default="", help="动作描述(留空=轻微镜头推拉)")
    ap.add_argument("--duration", type=int, default=5, help="秒(1-5)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    from model_providers import ensure_env
    ensure_env()
    from dashscope import VideoSynthesis

    prompt = args.prompt or "画面缓慢推近镜头，人物轻微点头，自然呼吸，背景微动，电影质感，动作平滑自然"
    print(f"[i2v] 输入: {args.img}")
    print(f"[i2v] prompt: {prompt}")
    print(f"[i2v] 时长: {args.duration}s, 预计费用: {0.24 * args.duration:.2f} 元")
    t0 = time.time()
    try:
        rsp = VideoSynthesis.call(
            model="wanx2.1-i2v-turbo",
            prompt=prompt,
            img_url=args.img,
            size="720*1280",
            duration=args.duration,
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
        )
    except Exception as e:  # noqa: BLE001
        print(f"[i2v] 调用异常: {e}")
        return 2
    if rsp.status_code != 200:
        print(f"[i2v] 失败: HTTP {rsp.status_code} {getattr(rsp, 'message', '')}")
        print(f"[i2v] 若提示欠费/余额不足/arrearage → 余额不足；若限流 → 稍后重试")
        return 1
    # 异步任务: 轮询结果
    task_id = rsp.output.task_id
    print(f"[i2v] 任务已提交: {task_id}, 轮询中 ...")
    url = None
    for _ in range(60):
        time.sleep(10)
        q = VideoSynthesis.fetch(task=task_id, api_key=os.environ.get("DASHSCOPE_API_KEY"))
        st = q.output.task_status
        print(f"  {time.time()-t0:.0f}s status={st}")
        if st == "SUCCEEDED":
            url = q.output.video_url
            break
        if st in ("FAILED", "CANCELED", "UNKNOWN"):
            print(f"[i2v] 任务失败: {getattr(q.output, 'message', '')}")
            return 1
    if not url:
        print("[i2v] 超时未完成")
        return 1
    import urllib.request
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)
    print(f"[i2v] 完成: {dest} ({dest.stat().st_size/1024/1024:.1f}MB, 耗时{time.time()-t0:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
