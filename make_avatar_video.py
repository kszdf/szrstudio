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
import hashlib
import io
import os
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
FFPROBE = r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin/ffprobe.exe"

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
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore")
    if r.returncode != 0:
        print("  [ERR] 命令失败:", " ".join(str(c) for c in cmd[:6]), "...")
        print((r.stderr or "")[-1000:])
        sys.exit(1)
    return r


def strip_audio(src, dst):
    """物理剥离 HEYGEM 自带音轨 -> 仅视频（去双声关键一步）"""
    run([FFMPEG, "-y", "-i", str(src), "-an", "-c:v", "copy", str(dst)])
    return dst


def probe_audio(path, timeout=30):
    """ffprobe 读取音频时长；失败/不可读返回 None。
    机制B2-1：渲染提交前必须确认音频完整可读（时长>0），
    否则 HEYGEM 会拿坏音频渲染 30 分钟才发现，昨晚「temp 清理丢音频」就是这样浪费的。"""
    try:
        r = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of",
             "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="ignore",
            timeout=timeout)
        dur = float((r.stdout or "").strip() or 0)
        if r.returncode == 0 and dur > 0:
            return dur
    except Exception:  # noqa: BLE001
        pass
    return None


def check_container_health(timeout=8):
    """机制B2-2：提交 HEYGEM 前探测容器是否活着（/easy/query 有响应即可）。
    容器未就绪/崩溃时立刻报错并给出 docker restart 提示，而不是在提交重试里空等。"""
    try:
        r = requests.get(VIDEO_API + "/easy/query", params={"code": "health_probe"},
                         timeout=timeout)
        # 200 且能解析出 code 字段即视为容器活着（任务不存在=10004 也是正常响应）
        r.raise_for_status()
        r.json()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  [B2] ⚠ HEYGEM 容器健康探测失败: {e}")
        return False


def _derive_code(name, audio_in_face_name, model, audio_dur, audio_size):
    """确定性派生 HEYGEM code：avatar_{name}_{md5(音频文件|模特|时长|大小)}。
    name 是 --name 参数（ASCII 短名，如 hgt_201f7a），决定 code 前缀；
    hash 部分由音频文件+模特+时长+大小派生 → 同一条稿+同音色重跑 → 同 code → 复用命中（机制A1）。"""
    raw = (name or '').split('/')[-1] or 'proj'
    ascii_tag = raw.encode('ascii', 'ignore').decode('ascii').strip() or 'proj'
    _seed = "%s|%s|%s|%s" % (audio_in_face_name, model, str(audio_dur), audio_size)
    _h = hashlib.md5(_seed.encode("utf-8")).hexdigest()[:6]
    return f"avatar_{ascii_tag}_{_h}"


def _post_process(result, audio, ass, args, code):
    """后期阶段（去双声 → mux → 烧字幕+片头）。render/post/full 三模式共用。
    result: 已就绪的 HEYGEM 产物（-r.mp4/-t.mp4）。"""
    noa = TEMP / f"{code}_noa.mp4"
    strip_audio(result, noa)
    print("[4] 已剥离 HEYGEM 自带音轨 (去双声)")

    synced = TEMP / f"{code}_synced.mp4"
    mux(noa, audio, synced)
    print(f"[5] 已合成嘴型对齐视频(音频=千问): {synced}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    print("[6] PIL 烧字幕 + 拼片头 ...")

    # 6.1) 图解浮层自适应：渲染后抽各图解段起始帧，检测数字人主脸位置，
    #      写回 graphics JSON，供 finalize 半透明叠加时避让/变尺寸（人动浮层跟着动）
    if args.graphics and os.path.exists(args.graphics):
        try:
            import json as _json
            gfx = _json.loads(Path(args.graphics).read_text(encoding="utf-8"))
            gfx = annotate_face_positions(gfx, str(synced))
            Path(args.graphics).write_text(_json.dumps(gfx, ensure_ascii=False), encoding="utf-8")
            print(f"[6.1] 图解浮层定位完成: {len(gfx)} 段")
        except Exception as e:  # noqa: BLE001
            print(f"  [WARN] 人脸定位失败({e})，浮层退化为固定底部")

    # 关键：finalize_v2_pil 抽帧重编码会丢原音轨，必须 --replace-audio 重新注入千问音频
    fin_args = [sys.executable, str(FINALIZE), "--video", str(synced),
                "--ass", str(ass), "--replace-audio", str(audio), "--out", str(out),
                "--subtitle-style", args.subtitle_style]
    if getattr(args, "no_intro", False):
        fin_args += ["--no-intro"]
    if args.karaoke:
        fin_args += ["--karaoke", str(args.karaoke)]
    if args.graphics:
        fin_args += ["--graphics", str(args.graphics)]
    if args.font:
        fin_args += ["--font", args.font]
    run(fin_args)
    print(f"\n✅ 成品: {out}  ({out.stat().st_size//1024} KB)")


# HEYGEM 标 success 时 -r.mp4 常尚未完全 flush（moov 写在文件末尾）。
# 若立即处理会读到半截文件 -> ffmpeg 报 "moov atom not found" 导致整任务失败。
# 故取结果后必须等到文件真正落盘完整再继续。
WAIT_READY_TIMEOUT = 180  # 最多等 180s


def wait_file_ready(path, timeout=WAIT_READY_TIMEOUT):
    """等待 HEYGEM 产物真正落盘完整：文件存在 + ffprobe 可读(moov 完整) + 连续两次大小稳定。"""
    waited = 0
    last = -1
    stable = 0
    while waited < timeout:
        if not path.exists():
            time.sleep(2); waited += 2; continue
        try:
            sz = path.stat().st_size
        except OSError:
            time.sleep(2); waited += 2; continue
        r = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                            "format=nb_streams", "-of", "default=nw=1:nk=1", str(path)],
                           capture_output=True, text=True, encoding="utf-8", errors="ignore")
        ok = (r.returncode == 0 and r.stdout.strip().isdigit()
              and int(r.stdout.strip()) >= 1)
        if ok and sz > 0 and sz == last:
            stable += 1
        else:
            stable = 0
        last = sz
        if stable >= 2:   # 连续两次(约4s)大小不变且可读 = 落盘完成
            return True
        time.sleep(2); waited += 2
    return False


def mux(video_noaudio, audio, dst):
    """仅视频 + 千问音频 -> 嘴型对齐(音频=千问)"""
    run([FFMPEG, "-y", "-i", str(video_noaudio), "-i", str(audio),
         "-c:v", "copy", "-c:a", "aac", "-ar", "44100", "-shortest", str(dst)])
    return dst


def annotate_face_positions(gfx, video_path, fps=30):
    """数字人图解浮层自适应：抽每段起始帧，Haar 检测数字人主脸位置，
    写 face=[x,y,w,h] 进每段 graphics，finalize 叠加时避让/变尺寸。
    cv2 不可用则跳过（finalize 退化为底部固定浮层）。"""
    try:
        import cv2  # noqa: F401
        import numpy as np  # noqa: F401
    except Exception:  # noqa: BLE001
        print("  [WARN] cv2 不可用，图解浮层退化为固定底部位置")
        return gfx
    cascade = cv2.CascadeClassifier(
        str(Path(__file__).resolve().parent / "haarcascade_frontalface_default.xml"))
    import tempfile as _tf
    for g in gfx or []:
        sec = float(g.get("start", 0))
        frame = os.path.join(_tf.gettempdir(), "face_%s.png" % uuid.uuid4().hex[:8])
        try:
            subprocess.run(
                [FFMPEG, "-y", "-ss", str(max(0, sec - 0.3)), "-i", str(video_path),
                 "-frames:v", "1", frame],
                capture_output=True, timeout=30, check=True)
            if os.path.exists(frame):
                img = cv2.imread(frame)
                if img is not None:
                    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                    faces = cascade.detectMultiScale(gray, scaleFactor=1.1,
                                                    minNeighbors=5, minSize=(80, 80))
                    big = [f for f in faces if f[2] >= 200]
                    if big:
                        fx, fy, fw, fh = max(big, key=lambda f: f[2] * f[3])
                        g["face"] = [int(fx), int(fy), int(fw), int(fh)]
        except Exception as e:  # noqa: BLE001
            print(f"  [WARN] 人脸检测失败 @{sec}s: {e}")
        finally:
            try:
                os.remove(frame)
            except OSError:
                pass
    return gfx


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
    ap.add_argument("--subtitle-style", default="minimal",
                    choices=["dynamic", "minimal", "bubble"],
                    help="字幕风格：dynamic=逐字高亮 / minimal=纯净白字 / bubble=气泡底衬"
                         "（finalize_v2_pil 用）")
    ap.add_argument("--font", default=None, help="字幕主字体路径（透传 finalize_v2_pil）")
    ap.add_argument("--karaoke", default=None,
                    help="逐字高亮时间轴 sidecar JSON（dynamic 风格使用）")
    ap.add_argument("--graphics", default=None,
                    help="智能图解时间轴 JSON（数字人出镜时按内容穿插图解卡）")
    ap.add_argument("--stage", default="full", choices=["full", "render", "post"],
                    help="full=渲染+后期(默认); render=只渲染到产物就绪并输出路径(--result-out); "
                         "post=从 --result 已就绪产物开始只做后期（流水线并行用，不碰容器）")
    ap.add_argument("--result", default=None,
                    help="post 模式：已就绪的 HEYGEM 产物（-r.mp4/-t.mp4）路径")
    ap.add_argument("--result-out", default=None,
                    help="render 模式：产物就绪后把产物路径写入该文件（供流水线编排读取）")
    ap.add_argument("--no-intro", action="store_true",
                    help="本段不拼品牌片头（分段流水线用：每段不拼片头，拼接后整片拼一次，"
                         "否则段2 开头片头会在拼接处造成 3s 静音）")
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

    # 机制B2-1：渲染提交前自检 —— 音频必须完整可读（时长>0）
    # 否则 HEYGEM 拿坏音频渲染 30 分钟才发现（昨晚「temp 清理丢音频」事故的根因）
    audio_dur = probe_audio(audio)
    if audio_dur is None:
        sys.exit(
            f"[B2] 音频完整性自检失败: {audio}\n"
            f"      ffprobe 无法读取（文件缺失/半截/损坏）。"
            f"      请检查 TTS 产物是否被临时目录清理，重新生成音频后再提交渲染，"
            f"      不要带病提交（否则 HEYGEM 会白跑 30 分钟）。")
    print(f"[B2] 音频自检通过: {audio.name} 时长 {audio_dur:.1f}s")

    # —— 流水线拆分点：post 模式从已就绪产物直接做后期，不碰容器/不桥接 ——
    if args.stage == "post":
        if not args.result or not Path(args.result).exists():
            sys.exit(f"[post] --result 产物不存在: {args.result}")
        result = Path(args.result)
        print(f"[post] 从已就绪产物开始后期: {result.name} "
              f"({result.stat().st_size//1024} KB)")
        # 兼容：post 模式无容器参与，但 code 用于中间文件命名，仍按确定性派生
        audio_in_face_name = f"audio_{args.name}.wav"
        code = _derive_code(args.name, audio_in_face_name, args.model, audio_dur, audio.stat().st_size)
        _post_process(result, audio, ass, args, code)
        return

    # 机制B2-2：提交 HEYGEM 前探测容器健康，未就绪立刻给出提示而非空等重试
    if not check_container_health():
        print("  [B2] ⚠ 容器探测失败——确认 Docker 容器 heygem-gen-video 处于 Up 状态，"
              "启动后等待约 10~30 秒再重试，或执行 `docker restart heygem-gen-video`。")
        # 网络级失败（连接被拒）直接终止；仅任务级 busy 走下方提交重试
        try:
            requests.get(VIDEO_API + "/easy/query", params={"code": "health_probe"}, timeout=5)
        except requests.exceptions.RequestException:
            sys.exit("[B2] HEYGEM 容器无响应（连接被拒）——请确认容器已启动。")
        print("  [B2] 容器可达（可能正忙），继续提交重试逻辑...")

    # 1) 复制音频到 face2face (容器可见)
    audio_in_face = FACE / f"audio_{args.name}.wav"
    # name 可能含子目录（如 batch1/001），必须确保父目录存在，否则 shutil.copy 报 No such file
    audio_in_face.parent.mkdir(parents=True, exist_ok=True)
    if os.path.abspath(audio) == os.path.abspath(audio_in_face):
        print(f"[1] 音频已在 face2face（跳过复制）: {audio_in_face.name}")
    else:
        shutil.copy(audio, audio_in_face)
    audio_container = f"/code/data/audio_{args.name}.wav"
    print(f"[1] 音频已桥接: {audio_in_face.name} -> {audio_container}")

    # 机制B2-3：桥接后校验复制产物完整（大小一致 + 时长可读），
    # 防「temp 清理丢音频 / 复制半截」在渲染 30 分钟后才暴露
    if audio_in_face.stat().st_size != audio.stat().st_size:
        sys.exit(f"[B2] 音频桥接校验失败：大小不一致 "
                 f"({audio.stat().st_size} -> {audio_in_face.stat().st_size})")
    bridged_dur = probe_audio(audio_in_face)
    if bridged_dur is None or abs(bridged_dur - audio_dur) > 0.5:
        sys.exit(f"[B2] 音频桥接校验失败：容器侧时长不可读/不一致 "
                 f"(源 {audio_dur:.1f}s -> 桥接 {bridged_dur}s)")
    print(f"[B2] 音频桥接校验通过（{audio_in_face.stat().st_size} bytes, {bridged_dur:.1f}s）")

    # 关键修复：code 之前拼 args.name（含中文）→ 落到 PowerShell 5.1 终端被 GBK 解码成乱字符
    # 改成 ASCII 短名 + 确定性 hash（机制一：产物复用）
    # code 基于「音频内容+模特+时长」的 md5 —— 同一条稿重跑得到相同 code，
    # 渲染前检查同 code 产物是否完整，完整则跳过 HEYGEM 渲染直接后期（省 30 分钟）。
    code = _derive_code(args.name, audio_in_face.name, args.model, audio_dur, audio.stat().st_size)

    # 2) 产物复用检查：同 code 的 -t.mp4 已完整可读（moov 完整 + 时长≈音频）→ 跳过渲染
    cand_t = TEMP / f"{code}-t.mp4"
    cand_r = TEMP / f"{code}-r.mp4"
    reused = None
    for cand in (cand_t, cand_r):
        if cand.exists() and cand.stat().st_size > 1024 * 1024:
            _pr = subprocess.run(
                [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of",
                 "default=nw=1:nk=1", str(cand)],
                capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=30)
            _dur = 0.0
            try:
                _dur = float(_pr.stdout.strip() or 0)
            except ValueError:
                pass
            if _pr.returncode == 0 and _dur > 0 and abs(_dur - audio_dur) < 3.0:
                reused = cand
                break
    if reused is not None:
        print(f"[2] 命中产物复用: {reused.name}（时长 {reused.stat().st_size//1024//1024}MB，"
              f"跳过 HEYGEM 渲染，直接后期）")
        result = reused
        # 跳到后期阶段（去双声前的产物就绪点）
        print(f"[3] 生成结果: {result}  ({result.stat().st_size//1024} KB)")
        print("[3+] 文件已确认完整落盘，开始后期处理")
        _skip_render = True
    else:
        _skip_render = False
        # 2b) 提交 HEYGEM（遇到 code=10001「忙碌中」自动重试 3 次，间隔递增）
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

    if _skip_render:
        # 产物复用：跳过轮询/落盘等待，result 已就绪
        pass
    else:
        # 3) 轮询
        result = None
        start = time.time()
        # 渲染等待按音频时长动态：HEYGEM 渲染速度约 10~12s 视频/分钟，
        # 306s 口播需 30 分钟；短视频也有 8 分钟下限兜底（修复：长口播被 480s 掐死）
        max_wait = max(480, int(audio_dur * 6) + 120)
        print(f"    [轮询] 渲染等待上限 {max_wait}s（音频 {audio_dur:.0f}s）")
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
    # 关键修复：HEYGEM 标 success 时文件可能尚未完全落盘，必须等真正写完再处理，
    # 否则 ffmpeg 读半截文件会报 "moov atom not found" 导致整任务失败。
    # 动态超时：按音频时长自适应（长口播 300s 音频 → 等 720s），短音频仍 180s 兜底。
    ready_timeout = max(WAIT_READY_TIMEOUT, int(audio_dur * 2.4) + 120)
    print(f"[3] 等待 HEYGEM 产物落盘（音频 {audio_dur:.0f}s → 超时 {ready_timeout}s）")
    if not wait_file_ready(result, timeout=ready_timeout):
        sys.exit(f"HEYGEM 产物 {result.name} 等待落盘超时（{ready_timeout}s）——"
                 f"文件未完整写入，可能容器卷同步延迟或渲染异常。"
                 f"请重试，或 `docker restart heygem-gen-video` 后重跑。")
    print("[3+] 文件已确认完整落盘，开始后期处理")

    # —— 流水线拆分点：render 模式只到产物就绪，写路径后返回（后期由编排方并行做）——
    if args.stage == "render":
        if args.result_out:
            Path(args.result_out).write_text(str(result), encoding="utf-8")
            print(f"[render] 产物就绪路径已写入: {args.result_out}")
        else:
            print(f"[render] 产物就绪: {result}")
        return

    _post_process(result, audio, ass, args, code)


if __name__ == "__main__":
    main()
