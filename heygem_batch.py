#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
heygem_batch.py —— 基于 HeyGem 本地数字人的短视频批量生成与发布编排器
=====================================================================
设计前提（用户定稿）：
  * 数字人生成模型 = HeyGem（本地 Docker 部署，文字口型同步近乎零失误，逼真度高，成本为零）。
  * 不拼头部云平台的"预制形象 + 按时计费"，本地真人克隆是我们的底盘优势。

单条任务流水线：
  [solo 模式 · 出镜]
      中文脚本 -> TTS(千问/阿里 CosyVoice 克隆音) -> 字幕 ASS
              -> HeyGem(8383 face2face 嘴型对齐) -> finalize(1080x1920 烧字幕拼片头) -> 成片 mp4
  [dialogue 模式 · 不出镜(双声)]
      男女对话稿 -> make_scroll_video(双声 TTS + 滚动字幕卡) -> 成片 mp4（无需 Docker/模特）

编排能力（本文件提供）：
  1) 批量脚本输入：JSON 任务清单 / 监听 inbox 目录自动入队
  2) SQLite 任务队列：状态机(pending/running/done/failed) + 断点续跑（崩溃的 running 重启后回滚 pending）
  3) 并发控制：HeyGem 出镜任务强制串行(=1)，滚动字幕卡可并行
  4) 失败重试：指数退避；区分"临时错误(重试)"与"永久错误(直接失败)"，避免无谓重试
  5) 本地存储管理：按分类归档 + 每片 manifest.json 元数据 + 批次汇总
  6) 分辨率保障：成片经 finalize 已是 1080x1920；额外 ffprobe 校验，低于 1080p 兜底升采样
  7) 发布适配层：可插拔 BasePublisher；默认 ManualPublisher(人工审核出库，离线可用)，
     平台 API 适配器按 publish_config.json 启用（抖音/快手/B站 骨架，需用户自备企业凭证）。

与现有网关的一致性：
  * 复用 make_avatar_video.py（PY310 跑）、make_scroll_video.py（PY313 跑）、qwen_tts、build_package。
  * 解释器与 rewrite_studio 网关保持一致，确保依赖(dashscope/Pillow/ffmpeg)齐备。

用法：
  python heygem_batch.py run --jobs jobs.json [--concurrency 1] [--dry-run]
  python heygem_batch.py run --watch            # 监听 inbox 目录，持续处理
  python heygem_batch.py status                 # 查看队列
  python heygem_batch.py retry --id <task_id>   # 重跑失败任务
  python heygem_batch.py clean --work           # 清理临时工作目录
"""
import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

# ----------------------------- 路径与解释器（与网关保持一致） -----------------------------
BASE = Path("D:/heygem_data")
GPT = BASE / "gpt_sovits"
PY310 = r"D:/heygem/py310/Scripts/python.exe"                 # 出片网关线（make_avatar_video / qwen_tts）
PY313 = r"C:/Users/lenovo/.workbuddy/binaries/python/versions/3.13.12/python.exe"  # 滚动字幕卡（自带 dashscope+Pillow+numpy）
FFMPEG = r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin/ffmpeg.exe"
FFPROBE = r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin/ffprobe.exe"
MAKE_AVATAR = GPT / "make_avatar_video.py"
MAKE_SCROLL = GPT / "make_scroll_video.py"
QWEN_TTS = GPT / "qwen_tts.py"

WORK = BASE / "batch_work"          # 临时工作目录（每条任务一个子目录）
OUT = BASE / "batch_output"         # 成品归档根
INBOX = BASE / "batch_inbox"        # 监听入队目录
QUEUE_DB = BASE / "batch_queue.db"  # 任务队列表

DEFAULT_MODEL = "BGZSP20260721_t18_silent.mp4"   # HeyGem 主力静音模特（容器内 /code/data/...）
DEFAULT_VOICE = "cosyvoice-v3-plus-zhangc2-28a7c3541e1c45518a03046c11baeb1d"  # 老张克隆音
DEFAULT_FEMALE_VOICE = "cosyvoice-v3-plus-jiangnv3-991b204c1d564ac7a60f0cb9a8fd78bd"  # 江老师克隆音

HEYGEM_API = "http://localhost:8383"
MAX_ATTEMPTS = 3                     # 单任务最大尝试次数（含首次）
BACKOFF = [0, 5, 12, 30, 60]         # 重试间隔（秒），索引=已尝试次数

# 永久错误关键字：命中后不再重试（多为鉴权/参数/欠费）
PERMANENT_ERR = ("API Key", "authorization", "invalid", "permission", "forbidden",
                 "欠费", "额度", "参数错误", "模特不存在", "音频不存在", "字幕不存在")


# ============================== 数据库 / 队列 ==============================
def db_conn():
    cx = sqlite3.connect(str(QUEUE_DB))
    cx.row_factory = sqlite3.Row
    return cx


def init_db():
    cx = db_conn()
    cx.execute(
        """CREATE TABLE IF NOT EXISTS batch_tasks (
            id TEXT PRIMARY KEY,
            title TEXT,
            category TEXT DEFAULT '未分类',
            mode TEXT DEFAULT 'solo',
            status TEXT DEFAULT 'pending',
            attempts INTEGER DEFAULT 0,
            max_attempts INTEGER DEFAULT 3,
            error TEXT,
            out_path TEXT,
            meta TEXT,
            created_at REAL,
            updated_at REAL,
            finished_at REAL
        )"""
    )
    cx.commit()
    # 断点续跑：把崩溃遗留的 running 回滚为 pending
    n = cx.execute("UPDATE batch_tasks SET status='pending' WHERE status='running'").rowcount
    cx.commit()
    cx.close()
    if n:
        print(f"[init] 回滚 {n} 个崩溃遗留的 running 任务为 pending")
    return n


def enqueue_jobs(spec: dict):
    """把 jobs 清单插入队列。返回新增数量。"""
    cx = db_conn()
    now = time.time()
    cnt = 0
    for j in spec.get("jobs", []):
        tid = j.get("id") or ("job_" + uuid.uuid4().hex[:10])
        # 同 id 已存在且已完成则跳过，避免重复生成
        row = cx.execute("SELECT status FROM batch_tasks WHERE id=?", (tid,)).fetchone()
        if row and row["status"] in ("done", "running"):
            print(f"  [skip] {tid} 已存在(status={row['status']})，跳过")
            continue
        meta = {
            "script": j.get("script", ""),
            "dialogue": j.get("dialogue", ""),
            "voice": j.get("voice", DEFAULT_VOICE),
            "model": j.get("model", DEFAULT_MODEL),
            "bg": j.get("bg"),
            "title_override": j.get("title"),
            "publish": j.get("publish", {}),
        }
        cx.execute(
            "INSERT OR REPLACE INTO batch_tasks"
            "(id,title,category,mode,status,attempts,max_attempts,error,out_path,meta,created_at,updated_at) "
            "VALUES(?,?,?,?, 'pending',0,?,NULL,NULL,?,?,?)",
            (tid, j.get("title", tid), j.get("category", "未分类"),
             j.get("mode", "solo"), MAX_ATTEMPTS,
             json.dumps(meta, ensure_ascii=False), now, now),
        )
        cnt += 1
    cx.commit()
    cx.close()
    return cnt


def list_tasks():
    cx = db_conn()
    rows = cx.execute("SELECT * FROM batch_tasks ORDER BY created_at").fetchall()
    cx.close()
    return [dict(r) for r in rows]


# ============================== 工具：TTS / 字幕 / 出片 ==============================
def ffprobe_duration(wav_path: Path) -> float:
    try:
        out = subprocess.check_output(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(wav_path)],
            text=True, timeout=30)
        return float(out.strip())
    except Exception:
        return 0.0


def tts_solo(script: str, out_wav: Path, voice: str):
    """中文脚本 -> 克隆音音频（千问/CosyVoice）。"""
    import qwen_tts
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    qwen_tts.synth(script, voice, str(out_wav),
                   model=getattr(qwen_tts, "DEFAULT_MODEL", "cosyvoice-v3-plus"))


def gen_subtitle(script: str, wav_path: Path, ass_path: Path):
    """脚本 + 音频时长 -> 字幕 ASS（1080x1920 竖屏标准，纯函数不调 LLM）。"""
    import build_package as bp
    dur = ffprobe_duration(wav_path) if wav_path.exists() else 0.0
    ass = bp.gen_ass(script, dur)
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path.write_text(ass, encoding="utf-8")


def ascii_code(name: str) -> str:
    """HEYGEM code 必须用 ASCII，避免中文经 PowerShell/容器 GBK 乱码（与 make_avatar_video 一致）。"""
    raw = (name or "").split("/")[-1] or "proj"
    tag = raw.encode("ascii", "ignore").decode("ascii").strip() or "proj"
    return f"batch_{tag}_{uuid.uuid4().hex[:6]}"


def run_heygem(audio: Path, ass: Path, model: str, out: Path, name: str) -> dict:
    """solo 模式：调用 make_avatar_video.py（PY310），复用现有单条出片全链路。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [PY310, "-u", str(MAKE_AVATAR),
           "--audio", str(audio), "--ass", str(ass),
           "--model", f"/code/data/{model}", "--out", str(out), "--name", name]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "HEYGEM 出片超时(>600s)"}
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "")[-1500:]
        return {"ok": False, "error": f"make_avatar_video 失败(rc={r.returncode}): {err}"}
    if not out.exists():
        return {"ok": False, "error": "成片未生成"}
    return {"ok": True, "out": str(out)}


def run_scroll(dialogue_text: str, out: Path, name: str, bg=None) -> dict:
    """dialogue 模式：调用 make_scroll_video.py（PY313），双声 TTS + 滚动字幕卡，不出镜。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    dlg_file = WORK / f"{name}_dlg.txt"
    dlg_file.parent.mkdir(parents=True, exist_ok=True)
    dlg_file.write_text(dialogue_text, encoding="utf-8")
    cmd = [PY313, "-u", str(MAKE_SCROLL),
           "--dialogue", str(dlg_file), "--out", str(out), "--name", name]
    if bg:
        cmd += ["--bg", str(bg)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "滚动字幕卡生成超时(>600s)"}
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "")[-1500:]
        return {"ok": False, "error": f"make_scroll_video 失败(rc={r.returncode}): {err}"}
    if not out.exists():
        return {"ok": False, "error": "成片未生成"}
    return {"ok": True, "out": str(out)}


def verify_resolution(video: Path) -> dict:
    """校验成品分辨率 >=1080p；不足则兜底升采样到 1080x1920（竖屏）。"""
    try:
        r = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json",
                            "-show_streams", str(video)], capture_output=True, text=True, timeout=30)
        info = json.loads(r.stdout)
        v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
        if not v:
            return {"ok": True, "w": 0, "h": 0, "note": "无视频流，跳过校验"}
        w, h = int(v.get("width", 0)), int(v.get("height", 0))
        if min(w, h) >= 1080:
            return {"ok": True, "w": w, "h": h, "note": "分辨率达标"}
        # 兜底升采样
        up = video.with_name(video.stem + "_upscaled.mp4")
        subprocess.run([FFMPEG, "-y", "-i", str(video),
                        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
                        "-c:a", "copy", "-c:v", "libx264", "-crf", "20", str(up)],
                       capture_output=True, text=True, timeout=120)
        if up.exists():
            shutil.move(str(up), str(video))
            return {"ok": True, "w": 1080, "h": 1920, "note": f"已升采样 {w}x{h} -> 1080x1920"}
        return {"ok": True, "w": w, "h": h, "note": "升采样失败，保留原片"}
    except Exception as e:
        return {"ok": True, "w": 0, "h": 0, "note": f"校验异常(忽略): {e}"}


# ============================== 发布适配层 ==============================
class BasePublisher:
    """发布适配器基类。子类实现 publish(video_path, meta) -> dict(ok, status, detail)。"""
    name = "base"

    def publish(self, video_path: Path, meta: dict) -> dict:
        raise NotImplementedError


class ManualPublisher(BasePublisher):
    """默认发布器：离线可用。把成片 + 发布元数据打包进 outbox，状态置 pending_review，
    等待人工审核后在各平台手动上传。这是当前最稳妥的"自动发布"落地方式（平台 API 受限）。"""
    name = "manual"

    def publish(self, video_path: Path, meta: dict) -> dict:
        outbox = OUT / "publish_outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        dest = outbox / f"{meta.get('id','video')}.mp4"
        try:
            shutil.copy(str(video_path), str(dest))
        except Exception as e:
            return {"ok": False, "status": "error", "detail": f"拷贝失败: {e}"}
        pmeta = {
            "id": meta.get("id"),
            "title": meta.get("title"),
            "category": meta.get("category"),
            "platforms": meta.get("publish", {}).get("platforms", []),
            "schedule": meta.get("publish", {}).get("schedule"),
            "video": str(dest),
            "status": "pending_review",
            "created_at": time.time(),
        }
        (outbox / f"{meta.get('id','video')}_publish.json").write_text(
            json.dumps(pmeta, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "status": "pending_review",
                "detail": f"已入人工审核出库: {dest}"}


class ConfigPublisher(BasePublisher):
    """按 publish_config.json 启用的平台 API 适配器。
    真实平台自动发布需要企业资质 + OAuth 凭证（抖音开放平台/快手/B站），本类提供骨架：
    读取凭证 -> 若齐全则调用对应 _publish_<platform>；否则优雅降级到 ManualPublisher。
    不会伪造成功，也不会在缺凭证时空跑。"""
    name = "config"

    def __init__(self, cfg_path: Path = None):
        self.cfg = {}
        if cfg_path and Path(cfg_path).exists():
            try:
                self.cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
            except Exception:
                self.cfg = {}

    def _platform_adapter(self, platform: str):
        # 预留各平台真实上传实现位置（需用户填入凭证 + 通过平台审核）。
        # 例：抖音开放平台 /video/create + /video/upload（需 access_token + 应用白名单）。
        # 当前统一返回 None -> 走人工出库，避免伪成功。
        return None

    def publish(self, video_path: Path, meta: dict) -> dict:
        platforms = meta.get("publish", {}).get("platforms", [])
        manual = ManualPublisher()
        results = []
        for p in platforms:
            adapter = self._platform_adapter(p)
            if adapter is None:
                r = manual.publish(video_path, {**meta, "publish": {"platforms": [p],
                                                                   "schedule": meta.get("publish", {}).get("schedule")}})
                results.append({"platform": p, **r, "mode": "manual_fallback"})
            else:
                try:
                    results.append({"platform": p, **adapter(video_path, meta)})
                except Exception as e:
                    results.append({"platform": p, "ok": False, "status": "error", "detail": str(e)})
        if not platforms:
            results.append({**(manual.publish(video_path, meta)), "platform": "none"})
        return {"ok": True, "status": "dispatched", "detail": results}


def get_publisher(cfg_path=None) -> BasePublisher:
    cp = ConfigPublisher(cfg_path)
    if cp.cfg.get("enabled") and cp.cfg.get("platforms"):
        return cp
    return ManualPublisher()


# ============================== 单任务流水线 ==============================
def process_task(task: dict, dry_run: bool = False) -> dict:
    """执行单条任务全流程。返回 dict(ok, out_path?, error?)。"""
    tid = task["id"]
    meta = json.loads(task["meta"]) if isinstance(task["meta"], str) else task["meta"]
    mode = task.get("mode", "solo")
    title = task.get("title") or tid
    category = task.get("category") or "未分类"
    model = meta.get("model", DEFAULT_MODEL)
    voice = meta.get("voice", DEFAULT_VOICE)
    bg = meta.get("bg")
    script = meta.get("script", "")
    dialogue = meta.get("dialogue", "")

    work = WORK / tid
    work.mkdir(parents=True, exist_ok=True)

    if dry_run:
        # 干跑：不调 TTS/HEYGEM，直接落一个占位成片 + manifest，验证队列/存储逻辑
        out = OUT / _safe(category) / f"{tid}_{_safe(title, 20)}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("DRY_RUN_PLACEHOLDER", encoding="utf-8")
        _write_manifest(out, task, meta, mode, model, voice, 0, 1080, 1920)
        return {"ok": True, "out_path": str(out), "dry": True}

    try:
        if mode == "dialogue":
            if not dialogue.strip():
                return {"ok": False, "error": "dialogue 模式缺少对话稿"}
            out = OUT / _safe(category) / f"{tid}_{_safe(title, 20)}.mp4"
            r = run_scroll(dialogue, out, ascii_code(tid), bg=bg)
            if not r["ok"]:
                return r
        else:
            if not script.strip():
                return {"ok": False, "error": "solo 模式缺少脚本"}
            audio = work / "04_音频.wav"
            tts_solo(script, audio, voice)
            if not audio.exists():
                return {"ok": False, "error": "TTS 未产出音频"}
            ass = work / "subtitle.ass"
            gen_subtitle(script, audio, ass)
            if not ass.exists():
                return {"ok": False, "error": "字幕生成失败"}
            out = OUT / _safe(category) / f"{tid}_{_safe(title, 20)}.mp4"
            r = run_heygem(audio, ass, model, out, ascii_code(tid))
            if not r["ok"]:
                return r

        # 分辨率保障
        res = verify_resolution(out)
        # 存储 manifest
        dur = ffprobe_duration(audio) if (mode != "dialogue" and audio.exists()) else 0.0
        _write_manifest(out, task, meta, mode, model, voice, dur, res.get("w", 0), res.get("h", 0))
        # 发布（默认人工出库；配置启用则按平台分发）
        pub = get_publisher(GPT / "publish_config.json").publish(out, {
            "id": tid, "title": title, "category": category, "publish": meta.get("publish", {})})
        return {"ok": True, "out_path": str(out), "resolution": res, "publish": pub}
    except SystemExit as e:
        return {"ok": False, "error": f"子进程退出: {e}"}
    except Exception as e:  # noqa
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _safe(name: str, limit: int = 40) -> str:
    """文件名安全化（去非法字符 + 截断）。"""
    s = re.sub(r"[\\/:*?\"<>|]", "_", name or "video").strip()
    s = s[:limit].strip()
    return s or "video"


def _write_manifest(out: Path, task: dict, meta: dict, mode: str, model: str,
                    voice: str, dur: float, w: int, h: int):
    m = {
        "id": task["id"],
        "title": task.get("title"),
        "category": task.get("category"),
        "mode": mode,
        "model": model,
        "voice": voice,
        "duration_sec": round(dur, 1),
        "resolution": f"{w}x{h}",
        "video": str(out),
        "created_at": time.time(),
    }
    (out.parent / f"{out.stem}_manifest.json").write_text(
        json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================== 重试分类 ==============================
def is_retryable(err: str) -> bool:
    """永久错误直接失败；其余视为临时错误可重试。"""
    if not err:
        return True
    for kw in PERMANENT_ERR:
        if kw.lower() in err.lower():
            return False
    return True


# ============================== 队列执行器 ==============================
def run_queue(concurrency: int = 1, dry_run: bool = False, stop_event=None):
    """处理队列中所有 pending 任务。HeyGem(solo) 强制串行；dialogue 可并行。"""
    init_db()
    heygem_lock = threading.Lock()   # solo 出镜必须串行（HEYGEM 内存 busy 锁）
    sem = threading.Semaphore(max(1, concurrency))
    threads = []

    def worker(task):
        tid = task["id"]
        with sem:
            # solo 占用 HeyGem 锁
            if task.get("mode", "solo") == "solo":
                heygem_lock.acquire()
            try:
                _set_status(tid, "running")
                res = process_task(task, dry_run=dry_run)
                if res.get("ok"):
                    _set_status(tid, "done", out_path=res.get("out_path"),
                                error=None, meta_patch={"publish": res.get("publish"),
                                                        "resolution": res.get("resolution")})
                    print(f"  ✅ {tid} 完成 -> {res.get('out_path')}")
                else:
                    err = res.get("error", "未知错误")
                    attempts = task["attempts"] + 1
                    if attempts >= task["max_attempts"] or not is_retryable(err):
                        _set_status(tid, "failed", attempts=attempts, error=err)
                        print(f"  ❌ {tid} 失败(终态): {err}")
                    else:
                        _set_status(tid, "pending", attempts=attempts, error=err)
                        wait = BACKOFF[min(attempts, len(BACKOFF) - 1)]
                        print(f"  🔁 {tid} 第{attempts}次失败，{wait}s 后重试: {err[:80]}")
                        if wait:
                            time.sleep(wait)
            finally:
                if task.get("mode", "solo") == "solo":
                    heygem_lock.release()

    while True:
        if stop_event and stop_event.is_set():
            break
        cx = db_conn()
        row = cx.execute(
            "SELECT * FROM batch_tasks WHERE status='pending' "
            "ORDER BY created_at LIMIT 1").fetchone()
        cx.close()
        if not row:
            break
        task = dict(row)
        t = threading.Thread(target=worker, args=(task,), daemon=True)
        threads.append(t)
        t.start()
        # 控制并发：等出队速度，避免瞬间拉满
        time.sleep(0.2)

    for t in threads:
        t.join(timeout=900)


def _set_status(tid, status, attempts=None, error=None, out_path=None, meta_patch=None):
    cx = db_conn()
    now = time.time()
    sql = "UPDATE batch_tasks SET status=?, updated_at=? "
    params = [status, now]
    if attempts is not None:
        sql += ", attempts=? "; params.append(attempts)
    if error is not None:
        sql += ", error=? "; params.append(error)
    if out_path is not None:
        sql += ", out_path=? "; params.append(out_path)
    if status == "done":
        sql += ", finished_at=? "; params.append(now)
    if meta_patch:
        row = cx.execute("SELECT meta FROM batch_tasks WHERE id=?", (tid,)).fetchone()
        meta = json.loads(row["meta"]) if row and row["meta"] else {}
        meta.update(meta_patch)
        sql += ", meta=? "; params.append(json.dumps(meta, ensure_ascii=False))
    sql += " WHERE id=?"
    params.append(tid)
    cx.execute(sql, params)
    cx.commit()
    cx.close()


def retry_task(tid: str):
    cx = db_conn()
    row = cx.execute("SELECT * FROM batch_tasks WHERE id=?", (tid,)).fetchone()
    cx.close()
    if not row:
        print(f"未找到任务 {tid}")
        return
    if row["status"] == "done":
        print(f"{tid} 已完成，无需重试")
        return
    _set_status(tid, "pending", attempts=0, error=None)
    print(f"已将 {tid} 置为 pending，准备重试")


def watch_inbox(stop_event=None):
    """监听 inbox 目录：发现 *.json 任务清单即入队并处理。"""
    INBOX.mkdir(parents=True, exist_ok=True)
    print(f"[watch] 监听入队目录: {INBOX}")
    seen = set()
    while True:
        if stop_event and stop_event.is_set():
            break
        for f in INBOX.glob("*.json"):
            if f.name in seen:
                continue
            try:
                spec = json.loads(f.read_text(encoding="utf-8"))
                n = enqueue_jobs(spec)
                print(f"[watch] 入队 {n} 条来自 {f.name}")
                seen.add(f.name)
                # 处理完归档
                f.rename(f.with_suffix(".done.json"))
            except Exception as e:
                print(f"[watch] 解析 {f.name} 失败: {e}")
        run_queue(concurrency=1)
        time.sleep(5)


# ============================== CLI ==============================
def print_status():
    tasks = list_tasks()
    if not tasks:
        print("队列为空")
        return
    print(f"{'ID':<16}{'模式':<9}{'状态':<10}{'尝试':<5}{'分类':<12}{'标题'}")
    print("-" * 90)
    for t in tasks:
        print(f"{t['id']:<16}{t['mode']:<9}{t['status']:<10}{t['attempts']:<5}"
              f"{str(t['category'])[:11]:<12}{str(t['title'])[:30]}")


def main():
    ap = argparse.ArgumentParser(description="HeyGem 本地数字人短视频批量生成与发布编排器")
    sub = ap.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="处理队列")
    p_run.add_argument("--jobs", help="任务清单 JSON 路径")
    p_run.add_argument("--concurrency", type=int, default=1, help="并发数(默认1；solo 强制串行)")
    p_run.add_argument("--dry-run", action="store_true", help="干跑：不调 TTS/HEYGEM，验证队列与存储")
    p_run.add_argument("--watch", action="store_true", help="持续监听 inbox 目录")

    p_st = sub.add_parser("status", help="查看队列")
    p_rt = sub.add_parser("retry", help="重跑失败任务")
    p_rt.add_argument("--id", required=True)
    p_cl = sub.add_parser("clean", help="清理")
    p_cl.add_argument("--work", action="store_true", help="清理 batch_work 临时目录")

    args = ap.parse_args()
    init_db()

    if args.cmd == "run":
        if args.jobs:
            spec = json.loads(Path(args.jobs).read_text(encoding="utf-8"))
            n = enqueue_jobs(spec)
            print(f"已入队 {n} 条任务")
        if args.watch:
            watch_inbox()
        else:
            run_queue(concurrency=args.concurrency, dry_run=args.dry_run)
        print_status()
    elif args.cmd == "status":
        print_status()
    elif args.cmd == "retry":
        retry_task(args.id)
        run_queue(concurrency=args.concurrency if hasattr(args, "concurrency") else 1)
        print_status()
    elif args.cmd == "clean":
        if args.work:
            if WORK.exists():
                shutil.rmtree(str(WORK))
                print(f"已清理 {WORK}")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
