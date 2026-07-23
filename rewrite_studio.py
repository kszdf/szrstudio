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
FACE = ROOT / "face2face"

PORT = 8385
HTML_FILE = BASE / "rewrite_studio.html"
PY310 = r"D:/heygem/py310/Scripts/python.exe"      # 出片网关线用 py310
MAKE_AVATAR = BASE / "make_avatar_video.py"
FFPROBE = r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin/ffprobe"
FFMPEG = r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin/ffmpeg.exe"

for d in (AUDIO_DIR, VIDEO_DIR, PKG_DIR, THUMB_DIR):
    d.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE))
import forbidden_words as fw
import qwen_tts  # 顶层仅常量；synth() 内部才 import dashscope
from model_providers import ensure_env, get_text_config
ensure_env()  # 让 model_keys.env 里的 key 自动生效
from content_pipeline import llm, STYLE_GUIDE  # 复用文本模型 + 老张叙事风
import build_package as bp

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
    for f in sorted(QWEN_OUT.glob("**/03_逐字稿定稿.md")):
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
        res.append({"name": rel, "high": high, "med": med,
                    "audio": audio, "video": video})
    return res


def list_models() -> list:
    """扫描 face2face 下 *_silent.mp4 作为可用模特（容器可见路径 /code/data/）。"""
    models = []
    for f in sorted(FACE.glob("*_silent.mp4")):
        if re.search(r"(stab|test|_raw)", f.name):  # 排除防抖/测试残留，不当模特
            continue
        sz = f.stat().st_size / 1024 / 1024
        label = re.sub(r"_?silent\.mp4$", "", f.name)
        thumb = get_model_thumb(f)
        models.append({
            "id": f.name,
            "filename": f.name,
            "container": f"/code/data/{f.name}",
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
        return {"ok": False, "error": f"合成进程退出: {e}"}
    except Exception as e:  # noqa
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
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
    dur_ok = 7 <= dur <= 60
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


def start_render(name: str, model_id: str) -> dict:
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
                JOBS[job_id].update({"status": "done", "progress": 100,
                                     "video_url": f"/api/video/{out.name}",
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


# ------------------------------------------------------------------ HTTP Handler
class Handler(BaseHTTPRequestHandler):
    server_version = "RewriteStudio/2.0"

    def log_message(self, *args):  # 安静日志
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
        if path == "/api/projects":
            return self._send_json(list_projects())
        if path == "/api/guidance":
            return self._send_json({"text": fw.build_guidance()})
        if path == "/api/models":
            return self._send_json(list_models())
        m = re.match(r"^/api/model_thumb/(.+)$", path)
        if m:
            name = unquote(m.group(1))
            fp = THUMB_DIR / name
            if not fp.exists():
                # 第一次访问兜底：缺图时现场抽一张，避免前端破图
                src = next((f for f in FACE.glob(f"{Path(name).stem}_*.mp4") if f.exists()), None)
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

    def do_POST(self):
        u = urlparse(self.path)
        path = u.path
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
        if path == "/api/new":
            return self._send_json(do_new(body.get("title", "")))
        m = re.match(r"^/api/project/(.+?)/(save|tts|publish-check|render|publish)$", path)
        if m:
            name = unquote(m.group(1))
            action = m.group(2)
            if action == "save":
                return self._send_json(do_save(name, body.get("opening", ""),
                                               body.get("body", ""),
                                               body.get("ending", "")))
            if action == "tts":
                return self._send_json(do_tts(name, {
                    "opening": body.get("opening", ""),
                    "body": body.get("body", ""),
                    "ending": body.get("ending", ""),
                }))
            if action == "render":
                return self._send_json(start_render(name, body.get("model", "")))
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


# ------------------------------------------------------------------ 业务处理（生成初稿/保存/新建 放末尾避免循环依赖问题）
def do_save(name: str, opening: str, body: str, ending: str) -> dict:
    p = project_path(name)
    md = serialize_three({"opening": opening, "body": body, "ending": ending})
    (p / "03_逐字稿定稿.md").write_text(md, encoding="utf-8")
    hits = fw.scan(fw.clean_script(md))
    high = sum(1 for h in hits if h["level"] == "high" and not h.get("need_human"))
    med = sum(1 for h in hits if h["level"] == "medium")
    (p / "03_违禁词检查.md").write_text(fw.format_report(hits), encoding="utf-8")
    return {"ok": True, "high": high, "med": med,
            "saved": str(p / "03_逐字稿定稿.md")}


def do_new(title: str) -> dict:
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
    return {"ok": True, "name": name}


def generate_from_source(source: str, direction: str = "",
                         length: str = "约60秒", keep_core: str = "") -> dict:
    if not source or not source.strip():
        return {"ok": False, "error": "请先粘贴爆款链接或文案"}
    length_map = {
        "约30秒": "约 30 秒口播量，60-90 字，精炼",
        "约60秒": "约 60 秒口播量，100-150 字",
        "约90秒": "约 90 秒口播量，150-220 字",
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
        return {"ok": False, "error": f"LLM 调用失败（检查 KEY 或网络，供应商={get_text_config()['provider']}）: {e}"}
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
