#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用已审批的千问音频驱动本地数字人(HEYGEM)生成嘴型对齐视频，
并套用 [去双声] 结构修复：物理剥离 HEYGEM 自带音轨，只用千问音频。
最后烧字幕 + 拼片头出发布级成品。

流程:
  1) 复制千问 audio.wav 到 face2face/ (容器可见 /code/data/xxx.wav)
  2) POST HEYGEM /easy/submit {audio_url, video_url, code}
  3) 轮询 /easy/query?code= 直到 success/cleaned
  4) 取 face2face/temp/{code}-r.mp4
  5) strip_audio(): ffmpeg -an 物理剥离自带音轨 -> 仅视频
  6) mux: 仅视频 + 千问 audio -> 嘴型对齐(音频=千问)的 synced.mp4
  7) finalize_v2_pil 烧字幕 + 拼片头 -> 成品

用法:
  python make_avatar_video.py --audio qwen_out/batch1_pkg/001/audio.wav \
      --ass qwen_out/batch1_pkg/001/subtitle.ass \
      --model /code/data/BGZSP20260721_t18_silent.mp4 \
      --out output/avatar_001.mp4 --name 001
"""
import argparse
import io
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import requests

# Windows 控制台默认 GBK，强制 stdout/stderr 用 UTF-8，避免 ✅ 等符号在最后打印时 UnicodeEncodeError
if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 强制使用 full 版 ffmpeg（含 libx264），绕开系统 essentials 版缺编码器的坑
FFMPEG = r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin/ffmpeg.exe"

BASE = Path("D:/heygem_data")
FACE = BASE / "face2face"
TEMP = FACE / "temp"
OUT = BASE / "output"
OUT.mkdir(parents=True, exist_ok=True)
VIDEO_API = "http://localhost:8383"
GATEWAY = BASE / "gpt_sovits"          # finalize_v2_pil.py 所在
FINALIZE = GATEWAY / "finalize_v2_pil.py"

# 数字人出镜场景 -> HEYGEM 容器内模特视频路径（容器 /code/data 映射到宿主 face2face/）
# office_a 复用现有稳定模特；office_b 为待用户投放的第二张办公桌前场景视频。
SCENE_MODELS = {
    "office_a": "/code/data/BGZSP20260721_t18_silent.mp4",
    "office_b": "/code/data/office_b_silent.mp4",
}


def model_host_path(container_path: str) -> Path:
    """容器 /code/data/X.mp4 -> 宿主 D:/heygem_data/face2face/X.mp4（用于存在性检查）。"""
    return FACE / Path(container_path).name


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("  [ERR] 命令失败:", " ".join(str(c) for c in cmd[:6]), "...")
        print(r.stderr[-1000:])
        sys.exit(1)
    return r


def strip_audio(src, dst):
    """物理剥离 HEYGEM 自带音轨 -> 仅视频（去双声关键一步）"""
    run([FFMPEG, "-y", "-i", str(src), "-an", "-c:v", "copy", str(dst)])
    return dst


def mux(video_noaudio, audio, dst):
    """仅视频 + 千问音频 -> 嘴型对齐(音频=千问)"""
    run([FFMPEG, "-y", "-i", str(video_noaudio), "-i", str(audio),
         "-c:v", "copy", "-c:a", "aac", "-ar", "44100", "-shortest", str(dst)])
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True, help="千问生成的音频 wav(宿主路径)")
    ap.add_argument("--ass", required=True, help="字幕 ass(宿主路径)")
    ap.add_argument("--model", default="/code/data/BGZSP20260721_t18_silent.mp4",
                    help="模特视频在容器内的路径 /code/data/xxx.mp4")
    ap.add_argument("--out", required=True, help="成品 mp4 路径")
    ap.add_argument("--name", default="test", help="标识，用于临时文件命名")
    ap.add_argument("--scene", default=None,
                    help="出镜场景：office_a(办公桌前·正面) / office_b(办公桌前·侧面)；"
                         "未显式指定 --model 时由场景决定模特视频")
    args = ap.parse_args()

    # —— 场景解析（安全接线：素材未就位时回退默认模特，绝不因未知参数崩溃）——
    if args.scene:
        if args.scene not in SCENE_MODELS:
            print(f"  [WARN] 未知场景 '{args.scene}'，忽略场景参数")
        elif args.model == ap.get_default("model"):
            target = SCENE_MODELS[args.scene]
            if model_host_path(target).exists():
                args.model = target
                print(f"  [scene] 已选用场景 {args.scene} -> {target}")
            else:
                print(f"  [WARN] 场景视频未就位（{model_host_path(target)}），"
                      f"回退默认模特 {args.model}；投放素材后将自动生效")
        else:
            print(f"  [scene] 已显式指定 --model，场景参数 {args.scene} 被忽略")

    audio = Path(args.audio)
    ass = Path(args.ass)
    if not audio.exists():
        sys.exit(f"音频不存在: {audio}")
    if not ass.exists():
        sys.exit(f"字幕不存在: {ass}")

    # 1) 复制音频到 face2face (容器可见)
    audio_in_face = FACE / f"audio_{args.name}.wav"
    # name 可能含子目录（如 batch1/001），必须确保父目录存在，否则 shutil.copy 报 No such file
    audio_in_face.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(audio, audio_in_face)
    audio_container = f"/code/data/audio_{args.name}.wav"
    print(f"[1] 音频已桥接: {audio_in_face.name} -> {audio_container}")

    # 关键修复：code 之前拼 args.name（含中文）→ 落到 PowerShell 5.1 终端被 GBK 解码成乱字符
    # 改成 ASCII 短名+UUID：HEYGEM/容器/PowerShell/WebSocket 端到处都干净。
    # 用 name 末段当"人类可读标识"（已经够用，不需要全名），不是英文时回退到 'proj'
    raw = (args.name or '').split('/')[-1] or 'proj'
    ascii_tag = raw.encode('ascii', 'ignore').decode('ascii').strip() or 'proj'
    code = f"avatar_{ascii_tag}_{uuid.uuid4().hex[:6]}"
    # 2) 提交 HEYGEM（遇到 code=10001「忙碌中」自动重试 3 次，间隔递增）
    # HEYGEM 的 TransDhTask.run_flag 是内存标志，偶发异常退出后没复位会一直 busy
    print(f"[2] 提交 HEYGEM 视频生成 (code={code}) ...")
    submit_data = None
    # 提交阶段：对「HEYGEM 忙碌(10001)」与「网络中断/连接被拒」两类临时错误都做递增重试，
    # 给刚启动或偶发崩溃的容器留出就绪时间，避免一上来就 rc=1 直接失败。
    waits = [0, 5, 10, 15, 20, 25]
    last_net_err = None
    for attempt in range(1, len(waits) + 1):
        wait_s = waits[attempt - 1]
        if wait_s:
            print("    等待 %ds 后重试（第 %d/%d 次）..." % (wait_s, attempt, len(waits)))
            time.sleep(wait_s)
        try:
            r = requests.post(VIDEO_API + "/easy/submit", json={
                "audio_url": audio_container,
                "video_url": args.model,
                "code": code,
            }, timeout=30)
            submit_data = r.json()
        except requests.exceptions.RequestException as e:
            last_net_err = e
            print("    网络异常（连接中断/被拒）：%s — 视为临时错误，重试" % e)
            continue
        if submit_data.get("code") == 10000:
            break
        # 10001=忙碌中 → 重试；其它错（参数错等）→ 立即失败
        if submit_data.get("code") != 10001:
            sys.exit("提交失败: " + str(submit_data))
    if not submit_data or submit_data.get("code") != 10000:
        if last_net_err:
            hint = ("\n    底层网络错误: " + str(last_net_err) +
                    "\n    多半是 HEYGEM 容器刚启动未就绪或已崩溃——请确认 Docker 容器 heygem-gen-video 处于 Up，"
                    "启动后等待约 10~30 秒再重试，或执行 `docker restart heygem-gen-video`。")
            sys.exit("提交失败（%d 次重试后仍失败）：%s%s" % (len(waits), submit_data, hint))
        sys.exit("提交失败（%d 次重试后仍忙碌）：%s。前面可能还有长任务在跑，请稍后手动重试，或执行 `docker restart heygem-gen-video` 清掉内存锁。" % (len(waits), submit_data))
    print("    提交成功，开始渲染...")

    # 3) 轮询
    result = None
    start = time.time()
    max_wait = 480
    while time.time() - start < max_wait:
        time.sleep(4)
        try:
            q = requests.get(f"{VIDEO_API}/easy/query", params={"code": code}, timeout=10).json()
        except Exception as e:
            print(f"    query 异常: {e}")
            continue
        st = q.get("code")
        if st == 10000:
            d = q.get("data", {})
            s = d.get("status")
            print(f"    [{time.time()-start:.0f}s] status={s} progress={d.get('progress')}")
            if s == "success":
                result = TEMP / f"{code}-r.mp4"
                break
            if s == "error":
                sys.exit(f"渲染失败: {d.get('msg')}")
        elif st == 10004:
            result = TEMP / f"{code}-r.mp4"
            print(f"    [{time.time()-start:.0f}s] 任务已清理(完成)，取文件")
            break
        else:
            print(f"    [{time.time()-start:.0f}s] query code={st} (继续等)")

    if not result or not result.exists():
        cands = sorted(TEMP.glob(f"{code}*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        if cands:
            result = cands[0]
        else:
            sys.exit(f"未找到生成结果: {TEMP}/{code}-r.mp4")

    print(f"[3] 生成结果: {result}  ({result.stat().st_size//1024} KB)")

    # 4) 去双声: 剥离自带音轨
    noa = TEMP / f"{code}_noa.mp4"
    strip_audio(result, noa)
    print("[4] 已剥离 HEYGEM 自带音轨 (去双声)")

    # 5) 用千问音频 mux
    synced = TEMP / f"{code}_synced.mp4"
    mux(noa, audio, synced)
    print(f"[5] 已合成嘴型对齐视频(音频=千问): {synced}")

    # 6) 烧字幕 + 片头
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    print("[6] PIL 烧字幕 + 拼片头 ...")
    # 关键：finalize_v2_pil 抽帧重编码会丢原音轨，必须 --replace-audio 重新注入千问音频
    run([sys.executable, str(FINALIZE), "--video", str(synced),
         "--ass", str(ass), "--replace-audio", str(audio), "--out", str(out)])
    print(f"\n✅ 成品: {out}  ({out.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
