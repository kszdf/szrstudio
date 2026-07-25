#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
二创改写台 v2 —— 全链路短视频生产控制台
========================================
三栏结构：左步骤导航 / 中工作区 / 右产物预览。
覆盖：选题→改写(违禁词标红)→出音频(可播放)→选模特→一键出片→视频预览→字幕→QC→发布文案。

纯标准库 http.server 后端（不依赖 Flask），运行在 3.13 环境
（dashscope / qwen_tts / build_package 所在环境）。

启动：
  C:/Users/lenovo/.workbuddy/binaries/python/versions/3.13.12/python.exe rewrite_studio.py
访问： http://localhost:8385
"""
from __future__ import annotations
import os
import re
import sys
import json
import time
import shutil
import threading
import subprocess
import urllib.request
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent                      # D:/heygem_data
QWEN_OUT = BASE / "qwen_out"
OUTPUT = ROOT / "output"

# —— 集中后台库（可改：指定统一存放音频/视频，方便备份分发）——
AUDIO_DIR = OUTPUT / "audio"           # 音频集中库：audio/<name>.wav
VIDEO_DIR = OUTPUT / "video"           # 视频集中库：video/<name>.mp4
PKG_DIR = OUTPUT / "pkg"               # 每条素材包：pkg/<name>/subtitle.ass, publish.md
THUMB_DIR = OUTPUT / "model_thumbs"    # 模特缩略图：model_thumbs/<name>.jpg
STATIC_DIR = BASE / "static"           # 静态资源（LOGO 等）：static/logo.jpg
FACE = ROOT / "face2face"

PORT = 8385
HTML_FILE = BASE / "rewrite_studio.html"
PY310 = r"D:/heygem/py310/Scripts/python.exe"      # 出片网关线用 py310
MAKE_AVATAR = BASE / "make_avatar_video.py"
FFPROBE = r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin/ffprobe"
FFMPEG = r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin/ffmpeg.exe"

for d in (AUDIO_DIR, VIDEO_DIR, PKG_DIR, THUMB_DIR, STATIC_DIR):
    d.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE))
import forbidden_words as fw
import qwen_tts  # 顶层仅常量；synth() 内部才 import dashscope
from model_providers import ensure_env, get_text_config, deepseek_chat, get_key, tavily_search
ensure_env()  # 让 model_keys.env 里的 key 自动生效
from content_pipeline import llm, STYLE_GUIDE  # 复用文本模型 + 老张叙事风
import build_package as bp
import thirdparty_avatar as tp   # 第三方数字人出片（与 HEYGEM 并列，可任选）

# ------------------------------------------------------------------ 三段解析
MARKER = re.compile(r"^\s*={3,}\s*(.+?)\s*={3,}\s*$", re.M)


def parse_three(text: str) -> dict:
    segs = {"opening": "", "body": "", "ending": ""}
    cur = None
    for ln in text.splitlines():
        m = MARKER.match(ln)
        if m:
            name = m.group(1)
            if "开头" in name:
                cur = "opening"
            elif "正文" in name:
                cur = "body"
            elif "结尾" in name or "钩子" in name:
                cur = "ending"
            else:
                cur = None
            continue
        if cur is not None:
            segs[cur] += ln + "\n"
    return {k: v.strip() for k, v in segs.items()}


def serialize_three(segs: dict) -> str:
    return (
        "=== 开头 ===\n" + (segs.get("opening") or "").strip() + "\n\n"
        "=== 正文 ===\n" + (segs.get("body") or "").strip() + "\n\n"
        "=== 结尾（钩子） ===\n" + (segs.get("ending") or "").strip() + "\n"
    )


def project_path(name: str) -> Path:
    return QWEN_OUT / name.replace("/", os.sep)


def list_projects() -> list:
    res = []
    for f in sorted(QWEN_OUT.glob("**/03_逐字稿定稿.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        rel = str(f.parent.relative_to(QWEN_OUT)).replace(os.sep, "/")
        try:
            text = f.read_text(encoding="utf-8")
            hits = fw.scan(fw.clean_script(text))
        except Exception:
            hits = []
        high = sum(1 for h in hits if h["level"] == "high" and not h.get("need_human"))
        med = sum(1 for h in hits if h["level"] == "medium")
        audio = (f.parent / "04_音频.wav").exists() or (AUDIO_DIR / f"{rel}.wav").exists()
        video = (VIDEO_DIR / f"{rel}.mp4").exists()
        mtime = int(f.stat().st_mtime)
        # 账号定位：读 00_meta.json，无则归「未分类」
        account_type = "未分类"
        meta_p = f.parent / "00_meta.json"
        if meta_p.exists():
            try:
                meta = json.loads(meta_p.read_text(encoding="utf-8"))
                account_type = meta.get("account_type") or "未分类"
            except Exception:
                pass
        res.append({"name": rel, "high": high, "med": med,
                    "audio": audio, "video": video, "mtime": mtime,
                    "account_type": account_type})
    return res


def _parse_multipart(body: bytes, boundary: bytes) -> dict:
    """极简 multipart/form-data 解析 -> {field: (filename_or_None, bytes)}。
    避免 cgi 模块（Python 3.13 已移除）。"""
    parts = {}
    delim = b"--" + boundary
    for seg in body.split(delim):
        if seg in (b"--\r\n", b"--", b"\r\n", b""):
            continue
        if seg.startswith(b"\r\n"):
            seg = seg[2:]
        if b"\r\n\r\n" not in seg:
            continue
        head, _, content = seg.partition(b"\r\n\r\n")
        if content.endswith(b"\r\n"):
            content = content[:-2]
        cd = ""
        for line in head.decode("utf-8", "replace").split("\r\n"):
            if line.lower().startswith("content-disposition"):
                cd = line
        name = None
        filename = None
        m = re.search(r'name="([^"]*)"', cd)
        if m:
            name = m.group(1)
        m = re.search(r'filename="([^"]*)"', cd)
        if m:
            filename = m.group(1)
        if name is not None:
            parts[name] = (filename, content)
    return parts


def list_models() -> list:
    """扫描 face2face 下 _silent.mp4 作为可用模特（容器可见路径 /code/data/）。
    递归扫，但排除 temp/ 工作目录；自定义上传的放 custom_models/。"""
    models = []
    for f in sorted(FACE.rglob("*_silent.mp4")):
        rel = f.relative_to(FACE)
        # 排除 temp 工作目录（HEYGEM 中间产物，不能当模特）
        if rel.parts and rel.parts[0] == "temp":
            continue
        if re.search(r"(stab|test|_raw)", f.name):
            continue
        sz = f.stat().st_size / 1024 / 1024
        label = re.sub(r"_?silent\.mp4$", "", f.name)
        # 自定义上传的标签加前缀
        is_custom = rel.parts and rel.parts[0] == "custom_models"
        if is_custom:
            label = "🆕 " + label
        try:
            thumb = get_model_thumb(f)
        except Exception:
            # 渲染期间 ffmpeg 可能被 HEYGEM 占用，抽帧失败不应阻断列表
            thumb = None
        models.append({
            "id": f.name,
            "filename": rel.as_posix(),
            "container": f"/code/data/{rel.as_posix()}",
            "label": label,
            "size_mb": round(sz, 1),
            "thumb_url": f"/api/model_thumb/{f.stem}.jpg" if thumb else None,
        })
    return models


def get_model_thumb(model_path: Path) -> Path | None:
    """从模特视频中抽一帧做缩略图（取 0.5s 处，宽 240px），结果缓存到 model_thumbs/。"""
    thumb = THUMB_DIR / f"{model_path.stem}.jpg"
    if thumb.exists() and thumb.stat().st_size > 1000:
        return thumb
    try:
        subprocess.run([FFMPEG, "-y", "-loglevel", "error",
                        "-ss", "0.5", "-i", str(model_path),
                        "-frames:v", "1", "-q:v", "5",
                        "-vf", "scale=240:-2", str(thumb)],
                       capture_output=True, timeout=20)
    except Exception:
        return None
    return thumb if thumb.exists() and thumb.stat().st_size > 1000 else None


# ------------------------------------------------------------------ 业务处理
def friendly_tts_error(raw: str) -> str:
    """把 dashscope 合成异常翻译成用户可读中文，区分欠费/密钥/网络。"""
    r = raw.lower()
    if ("返回内容异常" in raw) or ("nonetype" in r) or ("len=0" in r) or ("none" in r and "return" in r):
        return ("阿里云（百炼/DashScope）账户欠费或免费额度已用完，导致语音合成返回空。"
                "请到百炼控制台充值后重试——充值即时生效，无需重启本服务。")
    if ("authorization" in r) or ("api key" in r) or ("invalid" in r) or ("permission" in r) or ("forbidden" in r):
        return "阿里云 API Key 无效或已失效，请检查 gpt_sovits/model_keys.env 里的 DASHSCOPE_API_KEY 是否正确。"
    if ("timeout" in r) or ("connect" in r) or ("connection" in r) or ("网络" in raw):
        return "网络异常，无法连接阿里云语音合成服务，请检查网络后重试。"
    return raw

def do_tts(name: str, segs: dict | None = None) -> dict:
    """用三段定稿（去标记）出音频，保存 04_音频.wav + 复制到集中库。
    优先用界面实时文本 segs（保证音频==界面文字）；缺则回退磁盘 03_逐字稿定稿.md。"""
    p = project_path(name)
    if segs and any(segs.get(k) for k in ("opening", "body", "ending")):
        clean = fw.clean_script(serialize_three({
            "opening": segs.get("opening") or "",
            "body": segs.get("body") or "",
            "ending": segs.get("ending") or "",
        }))
    else:
        md = (p / "03_逐字稿定稿.md").read_text(encoding="utf-8")
        clean = fw.clean_script(md)
    if not clean.strip():
        return {"ok": False, "error": "定稿为空，无法合成"}
    out = p / "04_音频.wav"
    try:
        qwen_tts.synth(clean, qwen_tts.DEFAULT_VOICE_ID, str(out),
                       model=qwen_tts.DEFAULT_MODEL)
    except SystemExit as e:
        return {"ok": False, "error": friendly_tts_error(str(e))}
    except Exception as e:  # noqa
        return {"ok": False, "error": friendly_tts_error(f"{type(e).__name__}: {e}")}
    # 复制到集中后台库（name 可能含子目录，如 batch1/005，先建目录防拷贝失败）
    dest = AUDIO_DIR / f"{name}.wav"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(out, dest)
    return {"ok": True, "out": str(out), "audio_url": f"/api/audio/{name}"}


def subtitle_preview(ass_path: Path) -> list:
    if not ass_path.exists():
        return []
    txt = ass_path.read_text(encoding="utf-8")
    res = []
    for line in txt.splitlines():
        if line.startswith("Dialogue:"):
            parts = line.split(",", 9)
            if len(parts) >= 10:
                start, end, body = parts[1], parts[2], parts[9]
                res.append({"start": start, "end": end,
                            "text": body.replace("\\N", "\n")})
    return res


def do_publish(name: str, generate: bool = False) -> dict:
    p = project_path(name)
    md = (p / "03_逐字稿定稿.md").read_text(encoding="utf-8")
    script = fw.clean_script(md)
    pkg = PKG_DIR / name
    pkg.mkdir(parents=True, exist_ok=True)
    pub = pkg / "publish.md"
    if generate or not pub.exists():
        try:
            title, topics, body = bp.gen_publish(script)
        except Exception as e:  # noqa
            return {"ok": False, "error": f"生成失败: {e}"}
        pub.write_text(
            f"# 发布文案\n\n**标题**：{title}\n\n**话题**：{topics}\n\n**文案**：{body}\n",
            encoding="utf-8")
    else:
        t = pub.read_text(encoding="utf-8")
        title = topics = body = ""
        for ln in t.splitlines():
            if ln.startswith("**标题**"):
                title = ln.split("：", 1)[-1].strip()
            elif ln.startswith("**话题**"):
                topics = ln.split("：", 1)[-1].strip()
            elif ln.startswith("**文案**"):
                body = ln.split("：", 1)[-1].strip()
    return {"ok": True, "title": title, "topics": topics, "body": body}


def qc_report(video_path: Path) -> dict:
    if not video_path.exists():
        return {"exists": False}
    try:
        r = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json",
                            "-show_format", "-show_streams", str(video_path)],
                           capture_output=True, text=True, timeout=30)
    except Exception as e:  # noqa
        return {"exists": True, "error": f"ffprobe 失败: {e}"}
    if r.returncode != 0:
        return {"exists": True, "error": r.stderr[:300]}
    info = json.loads(r.stdout)
    fmt = info["format"]
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    auds = [s for s in info["streams"] if s["codec_type"] == "audio"]
    if not v:
        return {"exists": True, "error": "无视频流"}
    W, H = int(v["width"]), int(v["height"])
    dur = float(fmt.get("duration", 0))
    vb = int(fmt.get("bit_rate", 0)) // 1000
    res_ok = (W, H) == (1080, 1920)
    enc_ok = v["codec_name"] == "h264" and bool(auds) and auds[0]["codec_name"] == "aac"
    # 时长只检查"不低于 7 秒"（太短不像有效口播），不限上限——长视频同样达标
    dur_ok = dur >= 7
    a_ok = len(auds) == 1
    vb_ok = vb >= 1000  # 口播竖屏视频 1.4-1.8M 为正常（源720p上采样封顶），非缺陷
    checks = {"res_ok": res_ok, "enc_ok": enc_ok, "dur_ok": dur_ok,
              "audio_ok": a_ok, "bitrate_ok": vb_ok}
    return {"exists": True,
            "resolution": f"{W}x{H}",
            "codec": v["codec_name"],
            "audio_codec": auds[0]["codec_name"] if auds else "无",
            "duration": round(dur, 1),
            "bitrate_k": vb,
            "audio_tracks": len(auds),
            "checks": checks,
            "pass": all(checks.values())}


# ------------------------------------------------------------------ 出片长任务
JOBS = {}
JOB_LOCK = threading.Lock()


def start_render(name: str, model_id: str, provider: str = "heygem",
                 avatar_id: str | None = None, voice_mode: str = "official") -> dict:
    """出片入口。provider 默认 heygem（原本地流程，零改动）；
    provider=thirdparty 走第三方官方数字人，不动 HEYGEM 任何逻辑。"""
    if provider == "thirdparty":
        return _start_render_thirdparty(name, avatar_id, voice_mode)
    models = {m["id"]: m for m in list_models()}
    if model_id not in models:
        return {"ok": False, "error": "模特不存在，请刷新模特列表"}
    p = project_path(name)
    audio = p / "04_音频.wav"
    if not audio.exists():
        return {"ok": False, "error": "请先在「出音频」步骤生成 04_音频.wav"}
    pkg = PKG_DIR / name
    try:
        bp.build_one(p, pkg)  # 生成字幕 + 发布文案 + 模特建议
    except Exception as e:  # noqa
        return {"ok": False, "error": f"素材包生成失败: {e}"}
    ass = pkg / "subtitle.ass"
    if not ass.exists():
        return {"ok": False, "error": "字幕生成失败"}
    out = VIDEO_DIR / f"{name}.mp4"
    model_container = models[model_id]["container"]
    # 关键：-u 强制子进程无缓冲输出，否则 make_avatar_video.py 的 print 被管道 block 缓冲，
    # 父进程读不到 (code=...) 与 [N] 步骤，导致 heygem_code 抓不到、进度条卡 0%。
    cmd = [PY310, "-u", str(MAKE_AVATAR), "--audio", str(audio), "--ass", str(ass),
           "--model", model_container, "--out", str(out), "--name", name]
    job_id = "job_" + os.urandom(4).hex()
    with JOB_LOCK:
        JOBS[job_id] = {"status": "running", "step": "准备提交 HEYGEM",
                        "progress": 0, "video_url": None, "error": None}
    threading.Thread(target=_run_render, args=(job_id, cmd, out),
                     daemon=True).start()
    return {"ok": True, "job_id": job_id}


# ------------------------------------------------------------------ 第三方数字人出片（与 HEYGEM 并列，零侵入）
def _script_text(p: Path) -> str:
    """从定稿拼出口播纯文本（开头+正文+结尾），供第三方数字人念稿。"""
    md = p / "03_逐字稿定稿.md"
    if not md.exists():
        return ""
    segs = parse_three(md.read_text(encoding="utf-8"))
    return "\n".join(x for x in (segs.get("opening"), segs.get("body"), segs.get("ending")) if x).strip()


def _update_job(job_id: str, step: str, progress: int) -> None:
    with JOB_LOCK:
        j = JOBS.get(job_id)
        if j:
            j["step"] = step
            j["progress"] = progress


def _start_render_thirdparty(name: str, avatar_id: str | None,
                             voice_mode: str) -> dict:
    """第三方官方数字人出片：提交→轮询→下载，落盘到 VIDEO_DIR/<name>.mp4。
    下游（字幕/质检/发布/预览）只认这个 mp4，不感知引擎是谁。"""
    p = project_path(name)
    md = p / "03_逐字稿定稿.md"
    if not md.exists():
        return {"ok": False, "error": "请先在「改写」步骤生成定稿（03_逐字稿定稿.md）"}
    script = _script_text(p)
    if not script:
        return {"ok": False, "error": "定稿文本为空，无法生成第三方数字人视频"}
    # 品牌主播音模式：用本地 04_音频.wav 驱动官方形象嘴型（平台支持 audio 时）
    brand_audio = str(p / "04_音频.wav") if voice_mode == "brand" else None
    out = VIDEO_DIR / f"{name}.mp4"
    job_id = "job_" + os.urandom(4).hex()
    with JOB_LOCK:
        JOBS[job_id] = {"status": "running", "step": "准备提交第三方数字人",
                        "progress": 0, "video_url": None, "error": None,
                        "provider": "thirdparty"}
    try:
        cfg = tp.get_avatar_config()
    except Exception:
        cfg = {}
    av_name = next((a["name"] for a in cfg.get("avatars", []) if a["id"] == avatar_id),
                   avatar_id or "默认形象")
    voice_name = "品牌克隆音" if voice_mode == "brand" else "官方音色"

    def _run():
        try:
            tp.run(script, avatar_id, voice_mode, brand_audio, out,
                   on_progress=lambda step, prog: _update_job(job_id, step, prog))
            with JOB_LOCK:
                JOBS[job_id].update({"status": "done", "progress": 100,
                                     "video_url": f"/api/video/{name}.mp4"})
        except Exception as e:  # noqa
            with JOB_LOCK:
                JOBS[job_id].update({"status": "error", "error": str(e)})

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "job_id": job_id,
            "hint": f"已选第三方数字人：{av_name} / {voice_name}"}


def _run_render(job_id: str, cmd: list, out: Path):
    # 后端主动轮询 HEYGEM /easy/query 拿真实进度（HEYGEM 渲染中 stdout 不会打 progress=）
    HEYGEM_API = "http://localhost:8383"
    last_heygem_poll = [0.0]   # 上次轮询时间
    heygem_code = [None]       # HEYGEM task code
    heygem_progress = [0]      # 最新 HEYGEM 进度（0-100）
    def update_step(s, p=None):
        with JOB_LOCK:
            JOBS[job_id]["step"] = s
            if p is not None:
                JOBS[job_id]["progress"] = max(JOBS[job_id].get("progress",0), p)
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace")
        tail: list[str] = []  # 保留最近若干行输出，失败时拼进 error
        for line in proc.stdout:
            line = line.strip()
            tail.append(line)
            if len(tail) > 80:
                tail.pop(0)
            # 解析 make_avatar_video.py 的 [N] step 描述
            m = re.search(r"\[(\d+)\]\s*(.*)", line)
            if m:
                step = int(m.group(1))
                desc = m.group(2)
                with JOB_LOCK:
                    JOBS[job_id]["step"] = f"[{step}] {desc[:60]}"
                    JOBS[job_id]["progress"] = min(90, 5 + step * 14)
            # 抓 HEYGEM task_code（make_avatar_video.py 提交时打印 (code=avatar_<name>_<uuid>)；name 可能含中文，故用 [^)\s]+ 抓到闭括号/空白为止）
            cm = re.search(r"\(code=([^)\s]+)\)", line)
            if cm and heygem_code[0] is None:
                heygem_code[0] = cm.group(1)
                with JOB_LOCK:
                    JOBS[job_id]["heygem_code"] = heygem_code[0]  # 让前端可见
                update_step(f"🔄 HEYGEM 任务已提交 ({heygem_code[0][:18]}…)", 12)
            # 抓 make_avatar_video.py 内部的 progress= 字段（不太稳定但留着）
            pm = re.search(r"progress=([\d.]+)", line)
            if pm:
                try:
                    p = float(pm.group(1))
                    heygem_progress[0] = max(heygem_progress[0], p)
                    with JOB_LOCK:
                        JOBS[job_id]["progress"] = min(90, max(JOBS[job_id]["progress"], p))
                except Exception:
                    pass
            if "成品:" in line:
                with JOB_LOCK:
                    JOBS[job_id]["progress"] = 98

            # 每 2s 主动查 HEYGEM 真实任务进度（核心：HEYGEM stdout 不打进度，要靠 query）
            now = time.time()
            if heygem_code[0] and now - last_heygem_poll[0] >= 2.0:
                last_heygem_poll[0] = now
                try:
                    qr = urllib.request.urlopen(
                        f"{HEYGEM_API}/easy/query?code={heygem_code[0]}",
                        timeout=4).read()
                    qd = json.loads(qr.decode("utf-8", errors="replace"))
                    rc = qd.get("code")
                    d = qd.get("data") or {}
                    if rc == 10000:
                        st = d.get("status")
                        pr = float(d.get("progress") or 0)
                        heygem_progress[0] = max(heygem_progress[0], pr)
                        # HEYGEM 返回数字 status：1=queued, 2=processing, 3=success, 4=error
                        if st == 1:
                            msg = f"⏳ HEYGEM 排队中（{pr:.0f}%）"
                            pct = min(90, max(15, pr))
                        elif st == 2:
                            msg = f"🎬 HEYGEM 渲染中（{pr:.0f}%）"
                            pct = min(90, max(20, pr))
                        elif st == 3:
                            msg = f"✅ HEYGEM 渲染完成（{pr:.0f}%），收尾中"
                            pct = 92
                        elif st == 4:
                            msg = f"❌ HEYGEM 渲染失败（{d.get('msg','') or '未知'}）"
                            pct = 90
                        else:
                            msg = f"HEYGEM status={st} progress={pr:.0f}%"
                            pct = min(90, max(15, pr))
                        update_step(msg, pct)
                    elif rc == 10004:
                        update_step("✅ HEYGEM 已清理任务（即将收尾）", 95)
                    elif rc == 10001:
                        # HEYGEM 忙碌/限流：明确告诉用户 GPU 排队中
                        update_step(f"⏳ HEYGEM GPU 忙碌：{d.get('msg','等待 GPU 空闲')}", 15)
                    else:
                        # 其它 HEYGEM 返回码：直接展示出来
                        update_step(f"⚠ HEYGEM 返回 {rc}：{qd.get('msg','')}", 12)
                except Exception as e:
                    pass  # 轮询失败不致命，下次再问
            elif not heygem_code[0] and now - last_heygem_poll[0] >= 2.0:
                # 还没拿到 HEYGEM task_code（提交前的素材包生成/音频桥接），给个保底爬升
                last_heygem_poll[0] = now
                with JOB_LOCK:
                    cur = JOBS[job_id].get("progress", 0)
                    if cur < 8:
                        JOBS[job_id]["progress"] = min(8, cur + 1)
                        JOBS[job_id]["step"] = "📦 准备素材包 + 桥接音频到 HEYGEM…"
        proc.wait()
        if out.exists():
            with JOB_LOCK:
                # 用相对 VIDEO_DIR 的完整相对路径（含子目录，如 batch1/001.mp4），否则前端 GET 会丢子目录 404
                rel = out.relative_to(VIDEO_DIR).as_posix()
                JOBS[job_id].update({"status": "done", "progress": 100,
                                     "video_url": f"/api/video/{rel}",
                                     "step": "完成"})
        else:
            tail_msg = " | ".join(tail[-6:]) if tail else "无输出"
            with JOB_LOCK:
                JOBS[job_id].update({"status": "error",
                                     "error": f"成品未生成（rc={proc.returncode}）；末段输出：{tail_msg[:600]}"})
    except Exception as e:  # noqa
        with JOB_LOCK:
            JOBS[job_id].update({"status": "error",
                                 "error": f"{type(e).__name__}: {e}"})


# ------------------------------------------------------------------ 批量渲染队列
# HEYGEM 一次只能渲一个任务，用队列把多个出片请求串行化，等待时可手动调序。
QUEUE: list[dict] = []          # {"id","name","model_id","model_label","status","job_id","added_at","error"}
QUEUE_LOCK = threading.Lock()
QUEUE_MAX = 10
_queue_seq = 0


def _queue_next_id() -> str:
    global _queue_seq
    _queue_seq += 1
    return f"q{_queue_seq}_{int(time.time()*1000)}"


def add_to_queue(name: str, model_id: str) -> dict:
    models = {m["id"]: m for m in list_models()}
    if model_id not in models:
        return {"ok": False, "error": "模特不存在，请刷新模特列表"}
    p = project_path(name)
    if not (p / "04_音频.wav").exists():
        return {"ok": False, "error": "请先在「出音频」步骤生成 04_音频.wav 再入队"}
    with QUEUE_LOCK:
        if len([x for x in QUEUE if x["status"] in ("waiting", "rendering")]) >= QUEUE_MAX:
            return {"ok": False, "error": f"队列已满（最多 {QUEUE_MAX} 个），先处理完几个再入队"}
        item = {
            "id": _queue_next_id(),
            "name": name,
            "model_id": model_id,
            "model_label": models[model_id].get("label", model_id),
            "status": "waiting",
            "pos": (QUEUE[-1]["pos"] + 1) if QUEUE else 0,
            "job_id": None,
            "added_at": time.strftime("%H:%M:%S"),
            "error": None,
        }
        QUEUE.append(item)
    return {"ok": True, "queue": get_queue()["queue"]}


def _resort():
    """按 pos 升序重排 QUEUE，保证显示与调度顺序一致。"""
    QUEUE.sort(key=lambda x: x.get("pos", 0))


def queue_move(item_id: str, direction: str) -> dict:
    """direction: up/down，仅在 waiting 项中调整顺序（渲染中/已完成不可动）。"""
    with QUEUE_LOCK:
        wait = [x for x in QUEUE if x["status"] == "waiting"]
        idx = next((i for i, x in enumerate(wait) if x["id"] == item_id), None)
        if idx is None:
            return {"ok": False, "error": "该项不在等待队列中（可能已在渲染或已完成）"}
        if direction == "up" and idx > 0:
            wait[idx - 1]["pos"], wait[idx]["pos"] = wait[idx]["pos"], wait[idx - 1]["pos"]
        elif direction == "down" and idx < len(wait) - 1:
            wait[idx + 1]["pos"], wait[idx]["pos"] = wait[idx]["pos"], wait[idx + 1]["pos"]
        else:
            return {"ok": False, "error": "已是端点，无法移动"}
        _resort()
    return {"ok": True, "queue": get_queue()["queue"]}


def queue_remove(item_id: str) -> dict:
    with QUEUE_LOCK:
        it = next((x for x in QUEUE if x["id"] == item_id), None)
        if not it:
            return {"ok": False, "error": "队列项不存在"}
        if it["status"] == "rendering":
            return {"ok": False, "error": "正在渲染中，不能移除（可等它完成）"}
        QUEUE[:] = [x for x in QUEUE if x["id"] != item_id]
        # 重排剩余项 pos，保持紧凑
        for i, x in enumerate(QUEUE):
            x["pos"] = i
        _resort()
    return {"ok": True, "queue": get_queue()["queue"]}


def get_queue() -> dict:
    with QUEUE_LOCK:
        q = [dict(x) for x in QUEUE]
        # 把当前渲染项的 job 实时进度并入
        for it in q:
            if it["job_id"] and it["job_id"] in JOBS:
                j = JOBS[it["job_id"]]
                it["progress"] = j.get("progress")
                it["step"] = j.get("step")
                it["video_url"] = j.get("video_url")
                it["error"] = j.get("error") or it.get("error")
        q.sort(key=lambda x: x.get("pos", 0))
    active = next((x for x in q if x["status"] == "rendering"), None)
    return {"queue": q, "active": active is not None,
            "max": QUEUE_MAX}


def queue_worker():
    """后台守护线程：队列非空且当前无渲染项时，自动取下个 waiting 项提交 HEYGEM。"""
    while True:
        try:
            with QUEUE_LOCK:
                rendering = any(x["status"] == "rendering" for x in QUEUE)
                waiting = sorted([x for x in QUEUE if x["status"] == "waiting"],
                                 key=lambda x: x.get("pos", 0))
                next_item = waiting[0] if (not rendering and waiting) else None
            if next_item is None:
                time.sleep(2)
                continue
            # 取出队首等待项，标记为渲染中并提交
            with QUEUE_LOCK:
                next_item["status"] = "rendering"
            r = start_render(next_item["name"], next_item["model_id"])
            if not r.get("ok"):
                with QUEUE_LOCK:
                    next_item["status"] = "error"
                    next_item["error"] = r.get("error", "提交失败")
                time.sleep(1)
                continue
            job_id = r["job_id"]
            with QUEUE_LOCK:
                next_item["job_id"] = job_id
            # 轮询该 job 直到结束
            while True:
                time.sleep(3)
                with JOB_LOCK:
                    j = JOBS.get(job_id)
                if not j:
                    break
                st = j.get("status")
                if st == "done":
                    with QUEUE_LOCK:
                        next_item["status"] = "done"
                        next_item["video_url"] = j.get("video_url")
                    break
                if st == "error":
                    with QUEUE_LOCK:
                        next_item["status"] = "error"
                        next_item["error"] = j.get("error")
                    break
            time.sleep(1)
        except Exception:
            time.sleep(3)


threading.Thread(target=queue_worker, daemon=True).start()


# ------------------------------------------------------------------ HTTP Handler
class Handler(BaseHTTPRequestHandler):
    server_version = "RewriteStudio/2.0"

    def log_message(self, *args):  # 安静日志
        pass

    def handle_error(self):
        # 捕获未处理异常，写文件便于排查（不影响其他连接）
        import traceback as _tb
        try:
            with open("D:/heygem_data/server_err.log", "a", encoding="utf-8") as _f:
                _f.write(f"[{time.strftime('%H:%M:%S')}] {self.command} {self.path}\n{_tb.format_exc()}\n")
        except Exception:
            pass
        try:
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'{"error":"internal"}')
        except Exception:
            pass

    def _send_json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self):
        html = HTML_FILE.read_text(encoding="utf-8")
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path: Path, mime: str):
        if not path.exists():
            self._send_json({"error": "not found"}, 404)
            return
        size = path.stat().st_size
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            try:
                rs = rng[6:].split("-")
                start = int(rs[0]) if rs[0] else 0
                end = int(rs[1]) if len(rs) > 1 and rs[1] else size - 1
                end = min(end, size - 1)
                length = end - start + 1
                with open(path, "rb") as f:
                    f.seek(start)
                    data = f.read(length)
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Type", mime)
                self.end_headers()
                self.wfile.write(data)
                return
            except Exception:
                pass
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        if path in ("/", "/index.html"):
            return self._send_html()
        # 静态资源（LOGO 等）
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            # 防穿越
            if ".." in rel or rel.startswith("/"):
                return self._send_json({"error": "bad path"}, 400)
            fp = STATIC_DIR / rel
            if fp.exists() and fp.is_file():
                ext = fp.suffix.lower()
                mime = {"jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png",
                        "gif":"image/gif","webp":"image/webp","svg":"image/svg+xml",
                        "ico":"image/x-icon"}.get(ext.lstrip("."), "application/octet-stream")
                return self._send_file(fp, mime)
            return self._send_json({"error": "not found"}, 404)
        if path == "/api/projects":
            return self._send_json(list_projects())
        if path == "/api/guidance":
            return self._send_json({"text": fw.build_guidance()})
        if path == "/api/models":
            return self._send_json(list_models())
        if path == "/api/thirdparty/info":
            return self._send_json(tp.info())
        m = re.match(r"^/api/model_thumb/(.+)$", path)
        if m:
            name = unquote(m.group(1))
            fp = THUMB_DIR / name
            if not fp.exists():
                # 第一次访问兜底：缺图时现场抽一张，避免前端破图
                src = next((f for f in FACE.rglob(f"{Path(name).stem}_*.mp4") if f.exists()), None)
                if src:
                    get_model_thumb(src)
            return self._send_file(fp, "image/jpeg")
        m = re.match(r"^/api/audio/(.+)$", path)
        if m:
            name = unquote(m.group(1))
            fp = AUDIO_DIR / f"{name}.wav"
            if not fp.exists():
                fp = project_path(name) / "04_音频.wav"
            return self._send_file(fp, "audio/wav")
        m = re.match(r"^/api/video/(.+)$", path)
        if m:
            name = unquote(m.group(1))
            return self._send_file(VIDEO_DIR / name, "video/mp4")
        m = re.match(r"^/api/job/(.+)$", path)
        if m:
            with JOB_LOCK:
                job = JOBS.get(m.group(1))
            return self._send_json(job or {"status": "not found"})
        if path == "/api/queue":
            return self._send_json(get_queue())
        m = re.match(r"^/api/project/(.+?)/(qc|subtitle|publish)$", path)
        if m:
            name = unquote(m.group(1))
            action = m.group(2)
            if action == "qc":
                return self._send_json(qc_report(VIDEO_DIR / f"{name}.mp4"))
            if action == "subtitle":
                return self._send_json(
                    {"items": subtitle_preview(PKG_DIR / name / "subtitle.ass")})
            if action == "publish":
                return self._send_json(do_publish(name))
        m = re.match(r"^/api/project/(.+)$", path)
        if m:  # 读三段
            name = unquote(m.group(1))
            p = project_path(name)
            md = (p / "03_逐字稿定稿.md").read_text(encoding="utf-8")
            return self._send_json({"name": name, "segs": parse_three(md), "raw": md})
        self._send_json({"error": "not found"}, 404)

    def do_DELETE(self):
        # http.server 默认不支持 DELETE，复用 POST 处理逻辑
        self.do_POST()

    def do_POST(self):
        u = urlparse(self.path)
        path = u.path
        # 上传模特（multipart/form-data，需在 _body 之前直接读流）
        if path == "/api/models/upload":
            try:
                return self._handle_upload_model()
            except Exception as e:  # noqa
                import traceback as _tb
                err = f"{type(e).__name__}: {e}\n{_tb.format_exc()}"
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": False, "error": err},
                                               ensure_ascii=False).encode("utf-8"))
                except Exception:
                    pass
                return
        body = self._body()
        if path == "/api/check":
            text = body.get("text", "")
            platform = body.get("platform") or None
            hits = fw.scan(text, platform=platform)
            return self._send_json({"hits": hits})
        if path == "/api/generate":
            r = generate_from_source(body.get("source", ""),
                                     body.get("direction", ""),
                                     body.get("length", ""),
                                     body.get("keep_core", ""))
            return self._send_json(r)
        if path == "/api/topic_search":
            r = search_and_create(body.get("category", ""),
                                  body.get("period", ""),
                                  body.get("direction", ""),
                                  body.get("length", ""),
                                  body.get("keep_core", ""),
                                  body.get("mode", "list"),
                                  int(body.get("topic_index", -1)),
                                  body.get("topics_cache") or None)
            return self._send_json(r)
        if path == "/api/new":
            return self._send_json(do_new(body.get("title", ""), body.get("account_type", "")))
        # —— 批量渲染队列 ——
        if path == "/api/queue" and self.command == "POST":
            return self._send_json(add_to_queue(body.get("name", ""),
                                                 body.get("model", "")))
        if path == "/api/queue" and self.command == "DELETE":
            return self._send_json(queue_remove(body.get("id", "")))
        if path == "/api/queue/remove":
            return self._send_json(queue_remove(body.get("id", "")))
        if path == "/api/queue/move":
            return self._send_json(queue_move(body.get("id", ""),
                                              body.get("direction", "")))
        if path == "/api/queue":
            return self._send_json({"error": "method not allowed"}, 405)
        m = re.match(r"^/api/project/(.+?)/(save|tts|publish-check|render|publish|account)$", path)
        if m:
            name = unquote(m.group(1))
            action = m.group(2)
            if action == "save":
                return self._send_json(do_save(name, body.get("opening", ""),
                                               body.get("body", ""),
                                               body.get("ending", "")))
            if action == "account":
                return self._send_json(do_set_account(name, body.get("account_type", "")))
            if action == "tts":
                return self._send_json(do_tts(name, {
                    "opening": body.get("opening", ""),
                    "body": body.get("body", ""),
                    "ending": body.get("ending", ""),
                }))
            if action == "render":
                return self._send_json(start_render(
                    name, body.get("model", ""),
                    provider=body.get("provider", "heygem"),
                    avatar_id=body.get("avatar_id"),
                    voice_mode=body.get("voice_mode", "official")))
            if action == "publish":
                return self._send_json(do_publish(name, generate=True))
            if action == "publish-check":
                p = project_path(name)
                pub = p / "publish.md"
                if not pub.exists():
                    return self._send_json({"hits": [], "exists": False})
                hits = fw.scan(pub.read_text(encoding="utf-8"),
                               platform=body.get("platform") or None)
                return self._send_json({"hits": hits, "exists": True})
        self._send_json({"error": "not found"}, 404)

    def _handle_upload_model(self):
        """POST /api/models/upload — multipart/form-data, 字段 file。
        保存到 face2face/custom_models/，自动转码为静音模板 <stem>_<ts>_silent.mp4
        （HEYGEM 铁律：模板必须有音频流但内容为静音，否则会和驱动音频叠加成双声）。
        """
        ct = self.headers.get("Content-Type", "")
        if not ct.startswith("multipart/form-data"):
            return self._send_json({"ok": False, "error": "需 multipart/form-data"}, 400)
        m = re.search(r"boundary=([^;]+)", ct)
        if not m:
            return self._send_json({"ok": False, "error": "缺 boundary"}, 400)
        boundary = m.group(1).strip().strip('"').encode("utf-8")
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            parts = _parse_multipart(body, boundary)
        except Exception as e:  # noqa
            return self._send_json({"ok": False, "error": f"解析失败: {type(e).__name__}: {e}"}, 400)

        if "file" not in parts or parts["file"][1] is None:
            return self._send_json({"ok": False, "error": "缺字段 file"}, 400)
        orig_name, data = parts["file"]
        if orig_name is None:
            return self._send_json({"ok": False, "error": "缺文件"}, 400)
        base = os.path.basename(orig_name)
        stem, ext = os.path.splitext(base)
        if ext.lower() != ".mp4":
            return self._send_json({"ok": False, "error": "只支持 .mp4"}, 400)
        safe_stem = re.sub(r"[^A-Za-z0-9_\-]", "_", stem)[:40] or "model"

        max_size = 500 * 1024 * 1024
        if len(data) > max_size:
            return self._send_json({"ok": False, "error": "文件超过 500MB"}, 400)
        if len(data) < 1024:
            return self._send_json({"ok": False, "error": "文件过小/可能损坏"}, 400)

        CUSTOM_DIR = FACE / "custom_models"
        CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        raw_path = CUSTOM_DIR / f"_raw_{ts}_{safe_stem}.mp4"
        silent_name = f"{safe_stem}_{ts}_silent.mp4"
        silent_path = CUSTOM_DIR / silent_name

        try:
            raw_path.write_bytes(data)
        except Exception as e:
            return self._send_json({"ok": False, "error": f"写入失败: {e}"}, 500)

        # ffprobe 取时长
        FFPROBE = FFMPEG.replace("ffmpeg.exe", "ffprobe.exe")
        duration = 0.0
        try:
            probe = subprocess.run([FFPROBE, "-v", "error",
                                    "-show_entries", "format=duration",
                                    "-of", "default=nw=1:nk=1", str(raw_path)],
                                   capture_output=True, text=True, timeout=30)
            duration = float(probe.stdout.strip() or "0")
        except Exception:
            duration = 0.0

        # 转码：去原声 + 加静音音轨，libx264 重编码确保 HEYGEM 兼容
        cmd = [FFMPEG, "-y", "-loglevel", "error",
               "-i", str(raw_path),
               "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
               "-pix_fmt", "yuv420p",
               "-an",
               "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
               "-c:a", "aac", "-b:a", "128k",
               "-shortest"]
        if duration > 0:
            cmd += ["-t", str(duration)]
        cmd += ["-movflags", "+faststart", str(silent_path)]

        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                err_tail = (r.stderr or "")[-300:]
                try: raw_path.unlink()
                except Exception: pass
                return self._send_json({"ok": False, "error": f"转码失败: {err_tail}"}, 500)
        except subprocess.TimeoutExpired:
            try: raw_path.unlink()
            except Exception: pass
            return self._send_json({"ok": False, "error": "转码超时（>10min）"}, 500)
        except Exception as e:
            try: raw_path.unlink()
            except Exception: pass
            return self._send_json({"ok": False, "error": f"转码异常: {e}"}, 500)

        try:
            raw_path.unlink()
        except Exception:
            pass

        return self._send_json({
            "ok": True,
            "file": f"custom_models/{silent_name}",
            "label": safe_stem,
            "duration": duration,
            "size_mb": round(silent_path.stat().st_size / 1024 / 1024, 1),
            "message": "上传并转码为静音模板完成"
        })


# ------------------------------------------------------------------ 业务处理（生成初稿/保存/新建 放末尾避免循环依赖问题）
def do_save(name: str, opening: str, body: str, ending: str) -> dict:
    p = project_path(name)
    p.mkdir(parents=True, exist_ok=True)  # 关键：先建项目目录，否则写03定稿会 FileNotFoundError
    md = serialize_three({"opening": opening, "body": body, "ending": ending})
    (p / "03_逐字稿定稿.md").write_text(md, encoding="utf-8")
    hits = fw.scan(fw.clean_script(md))
    high = sum(1 for h in hits if h["level"] == "high" and not h.get("need_human"))
    med = sum(1 for h in hits if h["level"] == "medium")
    (p / "03_违禁词检查.md").write_text(fw.format_report(hits), encoding="utf-8")
    return {"ok": True, "high": high, "med": med,
            "saved": str(p / "03_逐字稿定稿.md")}


def do_set_account(name: str, account_type: str) -> dict:
    """修改已有项目的账号定位（不依赖 save 三段稿件）。
    用于选题面板下拉框随时切换分类，立即持久化到 00_meta.json。"""
    p = project_path(name)
    if not (p / "03_逐字稿定稿.md").exists():
        return {"ok": False, "error": f"项目不存在：{name}"}
    meta_p = p / "00_meta.json"
    try:
        meta = {}
        if meta_p.exists():
            try: meta = json.loads(meta_p.read_text(encoding="utf-8"))
            except Exception: meta = {}
        meta["account_type"] = account_type or "未分类"
        meta["updated_at"] = int(time.time())
        meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "account_type": meta["account_type"]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}


def do_new(title: str, account_type: str = "") -> dict:
    safe = re.sub(r"[^\w一-鿿-]", "_", title or "untitled")[:40]
    idx = 1
    name = safe
    while (QWEN_OUT / name).exists():
        name = f"{safe}_{idx}"
        idx += 1
    p = QWEN_OUT / name
    p.mkdir(parents=True, exist_ok=True)
    (p / "03_逐字稿定稿.md").write_text(
        serialize_three({"opening": "", "body": "", "ending": ""}), encoding="utf-8")
    # 账号定位元数据（用于首页筛选 + 平台上传归类）
    atype = account_type or "财税IP打造类"
    meta = {"account_type": atype, "created_at": int(time.time())}
    (p / "00_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "name": name, "account_type": atype}


def _parse_topics_json(text: str) -> list:
    """从模型返回里抠出 JSON 数组（容忍 ```json 围栏 / 前后废话）。"""
    if not text:
        return []
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except Exception:  # noqa
        return []
    return arr if isinstance(arr, list) else []


def search_and_create(category: str, period: str, direction: str = "",
                      length: str = "约60秒", keep_core: str = "",
                      mode: str = "list", topic_index: int = -1,
                      topics_cache: list = None) -> dict:
    """智能选题两阶段：
      mode="list"  → 仅联网检索+提炼 10 个候选选题（不二创），用户挑选后再走 create
      mode="create" → 基于 topics_cache[topic_index] 那一条做二创，返回三段稿
    返回 dict 含 ok / topics / source_label / segs(仅 create) / raw(仅 create)
    """
    key = get_key("DEEPSEEK_API_KEY")
    if not key:
        return {"ok": False,
                "error": "智能选题需要配置 DEEPSEEK_API_KEY（二创用），请在 model_keys.env 填写后重试。"}
    now = "2026 年 7 月"
    period_days = {"近7天": 7, "近30天": 30, "近3个月": 90,
                   "2026年以来": 200, "近1年": 365}
    days = period_days.get(period, 30)

    # ——— 第一阶段：检索 + 提炼选题 ———
    if mode == "list" or topics_cache is None:
        real_material = ""
        source_label = "知识库"
        tavily_key = get_key("TAVILY_API_KEY")
        if tavily_key:
            try:
                sq = (f"最近 {period}（截至{now}）关于「{category}」的财税热点、税务稽查案例、"
                      f"老板财务合规痛点、最新政策变化讨论")
                sr = tavily_search(sq, tavily_key, topic="finance", days=days, max_results=10)
                items = sr["results"]
                if items:
                    real_material = "\n\n".join(
                        f"【来源】{r.get('title','')}\n链接：{r.get('url','')}\n"
                        f"摘要：{(r.get('content') or '')[:300]}"
                        for r in items
                    )
                    source_label = "联网检索"
            except Exception:
                real_material = ""
                source_label = "知识库"

        if real_material:
            brief_intro = (
                f"以下是联网检索到的「{category}」最近（{period}）真实财税热点素材（含来源链接）：\n"
                f"{real_material}\n\n"
                "请基于以上真实素材，提炼出 10 个最适合「老张讲财税」做的爆款选题，"
            )
        else:
            brief_intro = (
                f"未启用联网检索（未配置 TAVILY_API_KEY 或检索失败），请基于你的财税知识，"
                f"给出最近关于「{category}」最值得做的 10 个爆款选题。"
            )
        topic_prompt = (
            brief_intro +
            "每个选题严格按 JSON 结构：\n"
            '{"title":"口语化、带钩子感的选题标题","why_hot":"为什么火（老板痛点/社会情绪）",'
            '"risk_point":"可切入的专业风险点或争议点","audience":"目标人群（如个体户/企业主/财务）"}\n'
            "只输出 JSON 数组（[...]），不要任何多余解释、不要 markdown 代码块标记。"
        )
        try:
            raw_topics = deepseek_chat(topic_prompt, model="deepseek-v4-flash", key=key, timeout=120)
        except Exception as e:  # noqa
            return {"ok": False, "error": f"选题生成失败: {type(e).__name__}: {e}"}
        topics = _parse_topics_json(raw_topics)
        return {"ok": True, "topics": topics, "source_label": source_label,
                "stage": "list"}

    # ——— 第二阶段：基于用户挑的 1 条做二创 ———
    if mode == "create":
        topics = topics_cache or []
        if not topics or topic_index < 0 or topic_index >= len(topics):
            return {"ok": False, "error": "未提供有效选题索引，请回到上一步重新选题。"}
        t = topics[topic_index]
        length_map = {
            "约30秒": "约 30 秒口播量，60-90 字，精炼",
            "约60秒": "约 60 秒口播量，100-150 字",
            "约90秒": "约 90 秒口播量，150-220 字",
            "约3分钟": "约 3 分钟口播量（450-700 字），结构清晰：开头 30 字钩子 / 正文 400-500 字分段讲解 / 结尾 50 字留资钩子",
            "约5分钟": "约 5 分钟口播量（800-1200 字），结构清晰：开头 50 字钩子 / 正文 700-900 字分 3-4 段，每段一个小主题 / 结尾 80 字留资钩子",
            "约10分钟": "约 10 分钟口播量（1500-2200 字），结构清晰：开头 80 字钩子 / 正文 1200-1800 字分 5-7 段，每段一个小主题并配案例 / 结尾 100 字留资钩子。",
        }
        lr = length_map.get(length, "约 60 秒口播量，100-150 字")
        keep = keep_core or "保留核心知识点与关键判断，不编造数字与政策条文"
        fb = fw.build_guidance()
        topic_brief = (
            f"题目：{t.get('title','')}\n"
            f"为什么火：{t.get('why_hot','')}\n"
            f"专业切入点：{t.get('risk_point','')}\n"
            f"目标人群：{t.get('audience','')}"
        )
        p = (
            "你是「老张讲财税」短视频账号的资深编剧。主讲人张德富，苏州实战派财税专家，"
            "风格像朋友聊天叙事、不居高临下说教。\n\n"
            "【用户已选定这条选题（基于近期真实财税热点）】\n"
            f"{topic_brief}\n\n"
            f"【用户创作方向】{direction or '围绕这条选题最契合老张人设的切入点做原创二创'}\n\n"
            "【创作要求】\n"
            f"- 篇幅：{lr}\n"
            f"- 重点保留：{keep}\n"
            "- 原创优先：改写成老张第一人称口播，融合该真实热点的痛点，不照搬、不泛泛而谈\n"
            "- 深挖这条选题背后的专业风险点，形成一条完整口播\n"
            "- 财税术语准确，概念不混淆（如个人卡收营业款≠公转私）\n\n"
            f"{fb}\n\n"
            f"风格：\n{STYLE_GUIDE}\n\n"
            "【输出格式（严格按此，不要多余解释）】\n"
            "=== 开头 ===\n（抓眼球 / 痛点引入，1-2句）\n"
            "=== 正文 ===\n（核心讲解，3-5句，一句一意、节奏清晰）\n"
            "=== 结尾（钩子） ===\n（留资引导 / 关注，自然不生硬，1-2句，严禁加微信/扫码等导流词）\n\n"
            "直接输出三段式内容（含 === 标记），不要额外解释。"
        )
        try:
            raw = deepseek_chat(p, model="deepseek-v4-flash", key=key, timeout=120)
        except Exception as e:  # noqa
            return {"ok": False, "error": f"二创生成失败: {type(e).__name__}: {e}",
                    "topics": topics, "source_label": "联网检索"}
        segs = parse_three(raw)
        return {"ok": True, "segs": segs, "raw": raw,
                "topics": topics, "source_label": "联网检索",
                "chosen_index": topic_index, "chosen_title": t.get("title", ""),
                "stage": "create"}

    return {"ok": False, "error": f"未知 mode: {mode}（仅支持 list/create）"}


def generate_from_source(source: str, direction: str = "",
                         length: str = "约60秒", keep_core: str = "") -> dict:
    if not source or not source.strip():
        return {"ok": False, "error": "请先粘贴爆款链接或文案"}
    length_map = {
        "约30秒": "约 30 秒口播量，60-90 字，精炼",
        "约60秒": "约 60 秒口播量，100-150 字",
        "约90秒": "约 90 秒口播量，150-220 字",
        "约3分钟": "约 3 分钟口播量（450-700 字），结构清晰：开头 30 字钩子 / 正文 400-500 字分段讲解 / 结尾 50 字留资钩子",
        "约5分钟": "约 5 分钟口播量（800-1200 字），结构清晰：开头 50 字钩子 / 正文 700-900 字分 3-4 段，每段一个小主题 / 结尾 80 字留资钩子",
        "约10分钟": "约 10 分钟口播量（1500-2200 字），结构清晰：开头 80 字钩子 / 正文 1200-1800 字分 5-7 段，每段一个小主题并配案例 / 结尾 100 字留资钩子。允许长达数十分钟，只要节奏不拖沓。",
    }
    lr = length_map.get(length, "约 60 秒口播量，100-150 字")
    keep = keep_core or "保留原文核心知识点与关键判断，不编造数字与政策条文"
    fb = fw.build_guidance()
    p = (
        "你是「老张讲财税」短视频账号的资深编剧。主讲人张德富，苏州实战派财税专家，"
        "风格像朋友聊天叙事、不居高临下说教。\n\n"
        "【爆款原文/素材】（可能含链接或逐字稿，请提取其中可借鉴的选题与知识点，不要照搬原句）\n"
        f"{source}\n\n"
        f"【用户创作方向】{direction or '围绕原文核心痛点做原创二创，形成老张自己的解读'}\n\n"
        "【创作要求】\n"
        f"- 篇幅：{lr}\n"
        f"- 重点保留：{keep}\n"
        "- 原创优先：改写成老张第一人称口播，不能只是洗稿/搬运，避免与原文高度相似\n"
        "- 财税术语准确，概念不混淆（如个人卡收营业款≠公转私）\n\n"
        f"{fb}\n\n"
        f"风格：\n{STYLE_GUIDE}\n\n"
        "【输出格式（严格按此，不要多余解释）】\n"
        "=== 开头 ===\n（抓眼球 / 痛点引入，1-2句）\n"
        "=== 正文 ===\n（核心讲解，3-5句，一句一意、节奏清晰）\n"
        "=== 结尾（钩子） ===\n（留资引导 / 关注，自然不生硬，1-2句，严禁加微信/扫码等导流词）\n\n"
        "直接输出三段式内容（含 === 标记），不要额外解释。"
    )
    try:
        get_text_config()
    except RuntimeError as e:
        return {"ok": False, "error": f"{e} —— 请用记事本打开 model_keys.env，把 DEEPSEEK_API_KEY（推荐）或 DASHSCOPE_API_KEY 等号右边填上真实 key 保存（不要把 key 发到对话里）。"}
    try:
        raw = llm(p)
    except SystemExit as e:
        return {"ok": False, "error": f"生成失败（检查 KEY 或网络）: {e}"}
    except Exception as e:  # noqa
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    segs = parse_three(raw)
    return {"ok": True, "segs": segs, "raw": raw}


def main():
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"二创改写台 v2 已启动: http://localhost:{PORT}  (Ctrl+C 停止)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        httpd.shutdown()


if __name__ == "__main__":
    main()
