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
APP_FILE = BASE / "app.html"   # 商用化新前端（接真实接口，顶替老界面皮肤）
PY310 = r"D:/heygem/py310/Scripts/python.exe"      # 出片网关线用 py310
MAKE_AVATAR = BASE / "make_avatar_video.py"
MAKE_SCROLL = BASE / "make_scroll_video.py"          # 不出镜·滚动字幕卡（男女对话）
MAKE_MOTION = BASE / "make_motion_video_v4.py"       # 幕后音·动态画面（男声/女声/男女对话, 动态GIF+中部滚动字幕）
SCRIPT_EDIT = BASE / "auto_edit.py"                  # 成片后自动剪辑包装（fast/artistic/vlog/pip）
PY313 = r"C:/Users/lenovo/.workbuddy/binaries/python/versions/3.13.12/python.exe"  # 滚动字幕卡用 3.13（自带 dashscope+Pillow+numpy）
SCROLL_DEFAULT_GIF = r"C:/Users/lenovo/WorkBuddy/2026-07-27-09-14-15/videos/ocean_rolling_9x16_deepblue.gif"   # 用户默认 GIF 海景背景（已清理旧 20260721TP.gif 引用）
SCROLL_MALE_VOICE = ""   # 新租户初始无自带声音；须由租户克隆/选择后显式传入
SCROLL_FEMALE_VOICE = ""
SCROLL_MALE_MODEL = "cosyvoice-v3-plus"
SCROLL_FEMALE_MODEL = "cosyvoice-v3-plus"
# 双声 TTS 自然度规则：嵌入所有「男女对话稿」生成 prompt，约束 LLM 产出利于自然朗读的对话文本
TTS_NATURAL_RULE = (
    "【双声自然度与情感规范（TTS 合成前必读）】\n"
    "- 语气词适度：在句首/句中自然嵌入「嗯、其实、你看、说白了、对吧、咱们」等口语填充词增强对话感；"
    "每百字不超过 2~3 处，避免啰嗦或抢戏。\n"
    "- 情感克制真实：情绪随内容走（讲解时平实、提示风险时稍严肃、举例时略轻松），幅度轻不外露；"
    "严禁戏剧化腔调、喊麦式激昂、过度卖萌或夸张叹气。\n"
    "- 像日常对话：句子短促利落、一句一意，多用口语短句与自然的逗号/句号断句，"
    "便于 TTS 在标点处自然停顿；男女交接处留半句空隙感。\n"
    "- 语速语调：模拟真人聊天节奏，陈述句尾自然回落、疑问句尾上扬，避免全程平调或机械匀速朗读。\n"
    "- 双声衔接：男女声交替边界清晰，不混淆、不串频，语气过渡顺滑。\n"
)
FFPROBE = r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin/ffprobe"
FFMPEG = r"D:/ffmpeg/ffmpeg-8.1.2-full_build/bin/ffmpeg.exe"

for d in (AUDIO_DIR, VIDEO_DIR, PKG_DIR, THUMB_DIR, STATIC_DIR):
    d.mkdir(parents=True, exist_ok=True)

# 双声视频背景图管理（上传/替换/预览）：存于静态目录，前端走 /static/bg/ 直接访问
BG_DIR = STATIC_DIR / "bg"
BG_DIR.mkdir(parents=True, exist_ok=True)
BG_INDEX = BG_DIR / "bg_index.json"
BG_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
BG_MAX_BYTES = 30 * 1024 * 1024
ACCOUNT_PROFILE = BASE / "account_profile.json"  # weekly_pipeline 同读此文件 bg 字段（单一事实来源）


# ---------------------------------------------------------------- 背景图管理助手
def _bg_load_index():
    if BG_INDEX.exists():
        try:
            return json.loads(BG_INDEX.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _bg_save_index(items):
    BG_INDEX.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def _bg_account_bg():
    """读取账号定位里的当前背景路径（单一事实来源）。"""
    if ACCOUNT_PROFILE.exists():
        try:
            return json.loads(ACCOUNT_PROFILE.read_text(encoding="utf-8")).get("bg") or ""
        except Exception:
            return ""
    return ""


def _bg_set_account_bg(path):
    prof = {}
    if ACCOUNT_PROFILE.exists():
        try:
            prof = json.loads(ACCOUNT_PROFILE.read_text(encoding="utf-8"))
        except Exception:
            prof = {}
    prof["bg"] = path or ""
    ACCOUNT_PROFILE.write_text(json.dumps(prof, ensure_ascii=False, indent=2), encoding="utf-8")


def _bg_current_id():
    """由 account_profile.bg 反查当前背景在索引里的 id（供前端高亮）。"""
    cur = _bg_account_bg()
    if not cur:
        return None
    for it in _bg_load_index():
        if not it.get("deleted") and it.get("path") == cur:
            return it.get("id")
    return None

sys.path.insert(0, str(BASE))
import forbidden_words as fw
import qwen_tts  # 顶层仅常量；synth() 内部才 import dashscope
from model_providers import ensure_env, get_text_config, deepseek_chat, get_key, tavily_search
ensure_env()  # 让 model_keys.env 里的 key 自动生效
from content_pipeline import llm, STYLE_GUIDE  # 复用文本模型 + 老张叙事风
import build_package as bp
import thirdparty_avatar as tp   # 第三方数字人出片（与 HEYGEM 并列，可任选）
import studio_db   # 多租户数据层（账号/会话/形象/声音隔离）

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

def do_tts(name: str, segs: dict | None = None, tenant_id: int | None = None) -> dict:
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
        tid = tenant_id if tenant_id is not None else studio_db.get_default_tenant().get("id")
        voice_id = studio_db.get_tenant_voice_id(tid, "male")
        qwen_tts.synth(clean, voice_id, str(out),
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


def do_tts_dialogue(name: str, dialogue_text: str = "", tenant_id: int | None = None) -> dict:
    """用男女对话稿合成双声试听音频，保存 04_对话音频.wav + 复制到集中库。
    固定音色：男=张老师克隆音 zhangc2，女=江老师克隆音 jiangnv3，不再弹窗确认。"""
    p = project_path(name)
    if not dialogue_text or not dialogue_text.strip():
        # 未提供对话文本：回退到已有的对话稿文件
        dlg_p = p / "dialogue.txt"
        if not dlg_p.exists():
            return {"ok": False, "error": "对话稿为空，请先在「改写」填写男女对话稿"}
        dialogue_text = dlg_p.read_text(encoding="utf-8")
    dialogue_text = dialogue_text.strip()
    if not dialogue_text:
        return {"ok": False, "error": "对话稿为空，无法合成双声试听"}
    # 保存/更新对话稿
    dlg_p = p / "dialogue.txt"
    dlg_p.write_text(dialogue_text, encoding="utf-8")
    out = p / "04_对话音频.wav"
    try:
        import make_scroll_video as smv
        tid = tenant_id if tenant_id is not None else studio_db.get_default_tenant().get("id")
        female_v = studio_db.get_tenant_voice_id(tid, "female")
        male_v = studio_db.get_tenant_voice_id(tid, "male")
        smv.synth_dialogue_audio(dialogue_text, str(out), dry=False, gap=0.18,
                                 female_voice=female_v, male_voice=male_v)
    except SystemExit as e:
        return {"ok": False, "error": friendly_tts_error(str(e))}
    except Exception as e:  # noqa
        return {"ok": False, "error": friendly_tts_error(f"{type(e).__name__}: {e}")}
    dest = AUDIO_DIR / f"{name}_dialogue.wav"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(out, dest)
    return {"ok": True, "out": str(out), "audio_url": f"/api/audio/{name}_dialogue"}


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

# ---- 每日热点·双题材（daily_hot）后台任务状态 ----
_HOT_STATE = {"running": False, "error": None, "done_at": None}


def _run_hot_daily():
    try:
        import daily_hot
        daily_hot.run_daily(finance_top=4, event_top=4, per_source=30)
        with JOB_LOCK:
            _HOT_STATE.update({"running": False, "error": None,
                               "done_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    except Exception as e:  # noqa
        with JOB_LOCK:
            _HOT_STATE.update({"running": False, "error": str(e)[:300]})


def start_hot_daily() -> dict:
    """触发每日热点·双题材后台刷新（2-4 分钟）；已在跑则直接返回。"""
    with JOB_LOCK:
        if _HOT_STATE.get("running"):
            return {"ok": True, "running": True, "error": None}
        _HOT_STATE.update({"running": True, "error": None})
    threading.Thread(target=_run_hot_daily, daemon=True).start()
    return {"ok": True, "running": True, "error": None}


def hot_daily_result() -> dict:
    """读取最近一次每日热点结果（daily_hot.json）。"""
    import json as _json
    p = Path(os.environ.get("HOT_DAILY_OUT", r"D:\heygem_data\runtime-logs\daily_hot.json"))
    with JOB_LOCK:
        state = dict(_HOT_STATE)
    result = None
    if p.exists():
        try:
            result = _json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa
            result = None
    return {"running": state.get("running", False),
            "error": state.get("error"),
            "done_at": state.get("done_at"),
            "result": result}


def start_render(name: str, model_id: str = "", provider: str = "heygem",
                 avatar_id: str | None = None, voice_mode: str = "official",
                 bg: str | None = None, title: str | None = None,
                 subtitle: str | None = None, bg_fit: str | None = None,
                 subtitle_style: str = "dynamic", subtitle_size: int | None = None,
                 subtitle_outline: int | None = None, subtitle_position: str = "bottom",
                 subtitle_lines: int | None = None, edit_style: str | None = None,
                 bgm: str | None = None, tenant_id: int | None = None,
                 motion_style: str = "财经严谨") -> dict:
    """出片入口。provider 默认 heygem（原本地流程，零改动）；
    provider=thirdparty 走第三方官方数字人，不动 HEYGEM 任何逻辑；
    provider=scroll 走不出镜·滚动字幕卡（男女对话），输出同一 VIDEO_DIR/<name>.mp4，下游无缝复用；
    provider=motion 走幕后音·动态画面（男声/女声/男女对话 + 动态GIF/生图 + 中部滚动字幕）。"""
    if provider == "thirdparty":
        return _start_render_thirdparty(name, avatar_id, voice_mode)
    if provider == "motion":
        return _start_render_motion(name, title, motion_style=motion_style, tenant_id=tenant_id)
    if provider == "scroll":
        return _start_render_scroll(name, bg, title, subtitle, bg_fit=bg_fit,
                                     tenant_id=tenant_id, subtitle_style=subtitle_style,
                                     subtitle_size=subtitle_size, subtitle_outline=subtitle_outline,
                                     subtitle_position=subtitle_position, subtitle_lines=subtitle_lines,
                                     bgm=bgm, edit_style=edit_style)
    models = {m["id"]: m for m in list_models()}
    if model_id in models:
        model_container = models[model_id]["container"]
    else:
        # 未指定/无效模特 → 回退该租户默认形象（多租户隔离：各租户用各自上传的静音驱动视频）
        tid = tenant_id if tenant_id is not None else studio_db.get_default_tenant().get("id")
        model_container = studio_db.get_tenant_avatar_path(tid)
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
    # 关键：-u 强制子进程无缓冲输出，否则 make_avatar_video.py 的 print 被管道 block 缓冲，
    # 父进程读不到 (code=...) 与 [N] 步骤，导致 heygem_code 抓不到、进度条卡 0%。
    cmd = [PY310, "-u", str(MAKE_AVATAR), "--audio", str(audio), "--ass", str(ass),
           "--model", model_container, "--out", str(out), "--name", name]
    if subtitle_style in ("dynamic", "minimal", "bubble"):
        cmd += ["--subtitle-style", subtitle_style]
    job_id = "job_" + os.urandom(4).hex()
    with JOB_LOCK:
        JOBS[job_id] = {"status": "running", "step": "准备提交 HEYGEM",
                        "progress": 0, "video_url": None, "error": None}
    threading.Thread(target=_run_render, args=(job_id, cmd, out),
                     kwargs={"edit_style": edit_style, "title": title, "subtitle": subtitle},
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


def _start_render_scroll(name: str, bg: str | None = None,
                          title: str | None = None,
                          subtitle: str | None = None,
                          bg_fit: str | None = None,
                          tenant_id: int | None = None,
                          subtitle_style: str = "dynamic",
                          subtitle_size: int | None = None,
                          subtitle_outline: int | None = None,
                          subtitle_position: str = "bottom",
                          subtitle_lines: int | None = None,
                          bgm: str | None = None,
                          edit_style: str | None = None) -> dict:
    """不出镜·滚动字幕卡（男女对话）出片：调 make_scroll_video.py，输出到 VIDEO_DIR/<name>.mp4。
    下游（预览/字幕/质检/发布/队列）只认这个 mp4，与数字人出片零差别复用。"""
    p = project_path(name)
    dlg = p / "dialogue.txt"
    # 优雅回退：无对话稿时用独白三段稿生成单声对话，避免平台死路（提示已注明）
    if not dlg.exists() or not dlg.read_text(encoding="utf-8-sig").strip():
        md = p / "03_逐字稿定稿.md"
        if not md.exists():
            return {"ok": False, "error": "请先在「改写」步骤填写内容（独白或男女对话稿）"}
        clean = fw.clean_script(md.read_text(encoding="utf-8"))
        if not clean.strip():
            return {"ok": False, "error": "定稿为空，无法出片"}
        dlg = p / "_auto_dialogue.txt"
        dlg.write_text("男：" + clean.replace("\n", " ") + "\n", encoding="utf-8")
    out = VIDEO_DIR / f"{name}.mp4"
    tid = tenant_id if tenant_id is not None else studio_db.get_default_tenant().get("id")
    female_v = studio_db.get_tenant_voice_id(tid, "female")
    male_v = studio_db.get_tenant_voice_id(tid, "male")
    cmd = [PY313, "-u", str(MAKE_SCROLL), "--dialogue", str(dlg), "--out", str(out),
           "--female-voice", female_v, "--male-voice", male_v]
    # 背景：seaside(默认，省略)/gif(用户GIF)/其他=自定义路径
    if bg == "gif":
        cmd += ["--bg", SCROLL_DEFAULT_GIF]
    elif bg and bg not in ("seaside", ""):
        cmd += ["--bg", bg]
    if title and title.strip():
        cmd += ["--title", title.strip()[:20]]
    if subtitle and subtitle.strip():
        cmd += ["--subtitle", subtitle.strip()[:40]]
    if bg_fit and bg_fit in ("fill", "contain", "stretch"):
        cmd += ["--bg-fit", bg_fit]
    # 字幕风格深度参数化（make_scroll_video.py 已支持）
    if subtitle_style in ("dynamic", "minimal", "bubble"):
        cmd += ["--subtitle-style", subtitle_style]
    if subtitle_size:
        cmd += ["--subtitle-size", str(int(subtitle_size))]
    if subtitle_outline is not None:
        cmd += ["--subtitle-outline", str(int(subtitle_outline))]
    if subtitle_position in ("bottom", "center"):
        cmd += ["--subtitle-position", subtitle_position]
    if subtitle_lines:
        cmd += ["--subtitle-lines", str(int(subtitle_lines))]
    # BGM：bgm 为情绪 key（light/inspire/calm/tech/suspense），映射到 bgm_library/<key>.mp3；缺失则跳过不报错
    if bgm:
        bgm_file = BASE / "bgm_library" / f"{bgm}.mp3"
        if bgm_file.exists():
            cmd += ["--bgm", str(bgm_file)]
        else:
            print(f"[WARN] BGM 库缺失 {bgm_file}，跳过背景音乐（请在 bgm_library/ 放入授权音乐）")
    job_id = "job_" + os.urandom(4).hex()
    with JOB_LOCK:
        JOBS[job_id] = {"status": "running", "step": "滚动字幕卡渲染中（TTS+合成）",
                        "progress": 10, "video_url": None, "error": None,
                        "provider": "scroll"}
    threading.Thread(target=_run_render_scroll, args=(job_id, cmd, out),
                     daemon=True).start()
    return {"ok": True, "job_id": job_id,
            "hint": "不出镜·滚动字幕卡（男女对话），无需 Docker 与模特"}


def _apply_edit_style(job_id, src_mp4, name, edit_style, title="", subtitle="", name_tag="慧根堂 · 财税军师"):
    """成片后自动剪辑包装：调用 auto_edit.py 按 edit_style 生成最终片；失败/不支持则回退原片。"""
    if not edit_style or edit_style not in ("fast", "artistic", "vlog", "pip"):
        return src_mp4
    tmp_out = VIDEO_DIR / f"{name}_edited.mp4"
    cmd = [PY313, "-u", str(SCRIPT_EDIT), "--input", str(src_mp4),
           "--output", str(tmp_out), "--edit-style", edit_style,
           "--title", (title or "")[:20], "--subtitle", (subtitle or "")[:40],
           "--name-tag", name_tag]
    _update_job(job_id, f"🎬 自动剪辑包装（{edit_style}）中…", 96)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=600)
        if proc.returncode == 0 and tmp_out.exists():
            # 用剪辑包装片覆盖原片，保持下游（字幕/质检/发布）按 name.mp4 复用
            os.replace(tmp_out, src_mp4)
            return src_mp4
        _update_job(job_id, f"⚠️ 剪辑包装失败，保留原片（{edit_style}）", 99)
        return src_mp4
    except Exception as e:  # noqa
        _update_job(job_id, f"⚠️ 剪辑包装异常，保留原片：{str(e)[:60]}", 99)
        return src_mp4


def _run_render_scroll(job_id: str, cmd: list, out: Path, edit_style=None, title=None, subtitle=None):
    """运行 make_scroll_video.py，解析「成品」行标记完成。"""
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace")
        tail: list[str] = []
        for line in proc.stdout:
            line = line.strip()
            if line:
                tail.append(line)
                if len(tail) > 60:
                    tail.pop(0)
                if "成品" in line:
                    _update_job(job_id, "✅ 滚动字幕卡已生成", 100)
        rc = proc.wait()
        if rc == 0 and out.exists():
            final = _apply_edit_style(job_id, out, out.stem, edit_style, title, subtitle)
            rel = final.relative_to(VIDEO_DIR).as_posix()
            with JOB_LOCK:
                JOBS[job_id].update({"status": "done", "progress": 100,
                                     "video_url": f"/api/video/{rel}"})
        else:
            err = "\n".join(tail[-20:])
            with JOB_LOCK:
                JOBS[job_id].update({"status": "error",
                                     "error": f"出片失败(rc={rc}): {err[:400]}"})
    except Exception as e:  # noqa
        with JOB_LOCK:
            JOBS[job_id].update({"status": "error", "error": str(e)[:400]})


def _start_render_motion(name: str, title: str | None = None,
                         motion_style: str = "财经严谨",
                         tenant_id: int | None = None) -> dict:
    """幕后音·动态画面出片：调 make_motion_video_v4.py（--dialogue 自动双声TTS + 动态GIF/生图 + 中部滚动字幕）。
    输出 VIDEO_DIR/<name>.mp4，下游（预览/字幕/质检/发布/队列）与数字人/滚动字幕卡零差别复用。"""
    p = project_path(name)
    dlg = p / "dialogue.txt"
    if not dlg.exists() or not dlg.read_text(encoding="utf-8-sig").strip():
        md = p / "03_逐字稿定稿.md"
        if not md.exists():
            return {"ok": False, "error": "请先在「改写」步骤填写内容（独白或男女对话稿）"}
        clean = fw.clean_script(md.read_text(encoding="utf-8"))
        if not clean.strip():
            return {"ok": False, "error": "定稿为空，无法出片"}
        dlg = p / "_auto_dialogue.txt"
        dlg.write_text(clean.replace("\n", " ") + "\n", encoding="utf-8")
    out = VIDEO_DIR / f"{name}.mp4"
    tid = tenant_id if tenant_id is not None else studio_db.get_default_tenant().get("id")
    female_v = studio_db.get_tenant_voice_id(tid, "female")
    male_v = studio_db.get_tenant_voice_id(tid, "male")
    cmd = [PY310, "-u", str(MAKE_MOTION), "--script", str(dlg), "--out", str(out),
           "--style", motion_style or "财经严谨", "--dialogue",
           "--male-voice", male_v, "--female-voice", female_v]
    if title and title.strip():
        cmd += ["--title", title.strip()[:20]]
    job_id = "job_" + os.urandom(4).hex()
    with JOB_LOCK:
        JOBS[job_id] = {"status": "running", "step": "幕后音·动态画面渲染中（并行TTS+动态画面）",
                        "progress": 10, "video_url": None, "error": None,
                        "provider": "motion"}
    threading.Thread(target=_run_render_motion, args=(job_id, cmd, out),
                     daemon=True).start()
    return {"ok": True, "job_id": job_id,
            "hint": "幕后音·动态画面（男声/女声/男女对话），无需 Docker 与模特，约 2-4 分钟"}


def _run_render_motion(job_id: str, cmd: list, out: Path):
    """运行 make_motion_video_v4.py，解析「成品」行标记完成。"""
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace")
        tail: list[str] = []
        for line in proc.stdout:
            line = line.strip()
            if line:
                tail.append(line)
                if len(tail) > 80:
                    tail.pop(0)
                if "成品" in line:
                    _update_job(job_id, "✅ 幕后音·动态画面已生成", 100)
        rc = proc.wait()
        if rc == 0 and out.exists():
            rel = out.relative_to(VIDEO_DIR).as_posix()
            with JOB_LOCK:
                JOBS[job_id].update({"status": "done", "progress": 100,
                                     "video_url": f"/api/video/{rel}"})
        else:
            err = "\n".join(tail[-25:])
            with JOB_LOCK:
                JOBS[job_id].update({"status": "error",
                                     "error": f"出片失败(rc={rc}): {err[:400]}"})
    except Exception as e:  # noqa
        with JOB_LOCK:
            JOBS[job_id].update({"status": "error", "error": str(e)[:400]})


def _run_render(job_id: str, cmd: list, out: Path, edit_style=None, title=None, subtitle=None):
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
        # —— 出片前先探测 HEYGEM(8383) 是否真的在听：不可达直接给明确提示，避免白跑子进程 ——
        import urllib.error as _ue
        _heygem_ready = False
        for _probe in range(3):
            try:
                urllib.request.urlopen(HEYGEM_API + "/", timeout=4).read()
                _heygem_ready = True
                break
            except _ue.HTTPError:
                # 404 等也说明服务在监听，端口活着
                _heygem_ready = True
                break
            except Exception:
                time.sleep(2)
        if not _heygem_ready:
            with JOB_LOCK:
                JOBS[job_id].update({"status": "error", "step": "HEYGEM 未就绪",
                    "error": "HEYGEM 数字人服务(8383) 不可达。请先启动 Docker Desktop 并确认容器 heygem-gen-video 处于 Up（docker ps）；容器启动后等待约 10~30 秒再出片。"})
            return
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace")
        tail: list[str] = []  # 保留最近若干行输出，失败时拼进 error
        for line in proc.stdout:
            line = line.strip()
            tail.append(line)
            if len(tail) > 80:
                tail.pop(0)
            # 解析 make_avatar_video.py 的 [N] / finalize_v2_pil.py 的 [N/M] step 描述
            m = re.search(r"\[(\d+)(?:/(\d+))?\]\s*(.*)", line)
            if m:
                step = int(m.group(1))
                total = m.group(2) or "1"
                desc = m.group(3)
                with JOB_LOCK:
                    JOBS[job_id]["step"] = f"[{step}/{total}] {desc[:50]}"
                    pm = re.search(r"(\d+)%", desc)
                    if pm:
                        # finalize 烧字幕阶段：90% 基准 + 帧进度(0-100)*0.09 → 90~99
                        JOBS[job_id]["progress"] = min(99, 90 + int(int(pm.group(1)) * 0.09))
                    elif any(k in desc for k in ("抽帧", "字幕", "合成", "片头", "烧字幕")):
                        # finalize 各阶段粗略推进（HEYGEM 完成后才进入，不会和 HEYGEM 进度冲突）
                        JOBS[job_id]["progress"] = min(99, 90 + step * 2)
                    else:
                        # make_avatar 阶段（提交/生成/剥离/合成嘴型），交给 HEYGEM 轮询推进，不覆盖
                        pass
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
            final = _apply_edit_style(job_id, out, out.stem, edit_style, title, subtitle)
            with JOB_LOCK:
                # 用相对 VIDEO_DIR 的完整相对路径（含子目录，如 batch1/001.mp4），否则前端 GET 会丢子目录 404
                rel = final.relative_to(VIDEO_DIR).as_posix()
                JOBS[job_id].update({"status": "done", "progress": 100,
                                     "video_url": f"/api/video/{rel}",
                                     "step": "完成"})
        else:
            tail_msg = " | ".join(tail[-6:]) if tail else "无输出"
            err = "成品未生成（rc=%s）；末段输出：%s" % (proc.returncode, tail_msg[:600])
            # 网络中断类错误：给出明确的 HEYGEM 服务排查引导，而非只甩底层堆栈
            if any(k in tail_msg for k in ("RemoteDisconnected", "Connection aborted",
                                           "ConnectionError", "Connection refused",
                                           "远程主机强迫关闭", "Max retries")):
                err = ("HEYGEM 数字人服务连接中断（rc=%s）。请确认 Docker 容器 heygem-gen-video 处于 Up；"
                       "容器刚启动需等约 10~30 秒再出片；仍失败执行 docker restart heygem-gen-video 后重试。"
                       "底层末段：%s" % (proc.returncode, tail_msg[:400]))
            with JOB_LOCK:
                JOBS[job_id].update({"status": "error", "error": err})
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


def add_to_queue(name: str, model_id: str, tenant_id: int | None = None) -> dict:
    models = {m["id"]: m for m in list_models()}
    p = project_path(name)
    if not (p / "04_音频.wav").exists():
        return {"ok": False, "error": "请先在「出音频」步骤生成 04_音频.wav 再入队"}
    with QUEUE_LOCK:
        # 关键修复：去重——同项目已在 waiting/rendering 直接返回旧 id，不重复入队
        for x in QUEUE:
            if x["name"] == name and x["status"] in ("waiting", "rendering"):
                return {"ok": False, "error": f"该项目已在队列中（状态：{x['status']}），点「批量队列」看进度",
                        "duplicate": True, "queue": get_queue()["queue"]}
        if len([x for x in QUEUE if x["status"] in ("waiting", "rendering")]) >= QUEUE_MAX:
            return {"ok": False, "error": f"队列已满（最多 {QUEUE_MAX} 个），先处理完几个再入队"}
        valid = model_id in models
        item = {
            "id": _queue_next_id(),
            "name": name,
            "model_id": model_id if valid else "",
            "model_label": models[model_id].get("label", model_id) if valid else "租户默认形象",
            "tenant_id": tenant_id,
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
            r = start_render(next_item["name"], next_item.get("model_id", ""),
                             tenant_id=next_item.get("tenant_id"))
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
# ============ P0-1 阶段0 局域网访问令牌鉴权 ============
# 说明：本服务原无鉴权，局域网内任何人可触发出片/上传。此处加一层最简令牌门禁，
# 仅 gate 数据面 /api/*，放行 / 与 /static/（首页与 LOGO），避免影响现有前端 fetch。
# 令牌读取优先级：.access_token 文件(.access_token 文件优先) > 环境变量 HGTV2_TOKEN > 自动生成持久化。
def _load_access_token() -> str:
    tok_file = ROOT / ".access_token"
    try:
        if tok_file.exists():
            t = tok_file.read_text(encoding="utf-8").strip()
            if t:
                return t
    except Exception:
        pass
    env = os.environ.get("HGTV2_TOKEN", "").strip()
    if env:
        return env
    # 兜底：首次启动自动生成并落盘（重启后不变）
    try:
        import secrets
        t = "hgt-" + secrets.token_hex(24)
        tok_file.write_text(t, encoding="utf-8")
        try:
            os.chmod(tok_file, 0o600)
        except Exception:
            pass
        return t
    except Exception:
        return "hgt-insecure-fallback"

ACCESS_TOKEN = _load_access_token()

def _auth_ok(self) -> bool:
    # 支持 Cookie(hgtv2_token) 或 Authorization: Bearer，兼容浏览器与脚本
    c = self.headers.get("Cookie", "")
    if "hgtv2_token=" in c:
        for part in c.split(";"):
            part = part.strip()
            if part.startswith("hgtv2_token="):
                if part[len("hgtv2_token="):].strip() == ACCESS_TOKEN:
                    return True
    auth = self.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        if auth[7:].strip() == ACCESS_TOKEN:
            return True
    return False

# ───────────────────────── 多租户：鉴权开关 / 会话 / 租户解析 ─────────────────────────
def _auth_enabled() -> bool:
    """AUTH_ENABLED=1 时才强制账号登录；默认 0（局域网试用）维持原单一令牌放行。"""
    return os.environ.get("AUTH_ENABLED", "0") == "1"

def _session_token_from(self) -> str | None:
    """从 Cookie(hgtv2_session) 或 Authorization: Bearer 取会话令牌。"""
    c = self.headers.get("Cookie", "")
    if "hgtv2_session=" in c:
        for part in c.split(";"):
            part = part.strip()
            if part.startswith("hgtv2_session="):
                return part[len("hgtv2_session="):].strip() or None
    auth = self.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None

def _resolve_tenant_id(self) -> int:
    """解析当前请求所属租户 id；鉴权关闭时一律回退默认(创建者)租户。"""
    dt = studio_db.get_default_tenant()
    dtid = dt.get("id")
    if not _auth_enabled():
        return dtid
    tok = _session_token_from(self)
    s = studio_db.get_session(tok) if tok else None
    return s["tenant_id"] if s else dtid

def _auth_gate(self) -> bool:
    """返回 True 表示已放行；返回 False 表示已发送 401 并应 return。
    AUTH_ENABLED=1 时：单一 ACCESS_TOKEN（管理员后门）或有效会话令牌均可放行。"""
    if _auth_ok(self):
        return True
    if _auth_enabled():
        tok = _session_token_from(self)
        if tok and studio_db.get_session(tok):
            return True
    self.send_response(401)
    self.send_header("Content-Type", "application/json; charset=utf-8")
    self.send_header("WWW-Authenticate", 'Bearer realm="hgtv2"')
    self.send_header("Cache-Control", "no-store")
    self.end_headers()
    self.wfile.write(json.dumps({"error": "unauthorized", "hint": "请先调用 /api/login 获取会话令牌"}, ensure_ascii=False).encode("utf-8"))
    return False

def _auth_state(self) -> dict:
    """公开状态接口（/api/auth/state）：前端据此决定走令牌模式还是账号登录模式。"""
    if not _auth_enabled():
        return {"auth_enabled": False, "authenticated": True,
                "username": None, "role": None, "tenant_id": None}
    tok = _session_token_from(self)
    s = studio_db.get_session(tok) if tok else None
    if s:
        u = studio_db.get_user_by_id(s["user_id"])
        if u:
            return {"auth_enabled": True, "authenticated": True,
                    "username": u["username"], "role": u["role"], "tenant_id": u["tenant_id"]}
    return {"auth_enabled": True, "authenticated": False,
            "username": None, "role": None, "tenant_id": None}
# =====================================================


class Handler(BaseHTTPRequestHandler):
    server_version = "RewriteStudio/2.1"

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
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(data)

    def _send_html_app(self):
        if not APP_FILE.exists():
            self._send_json({"error": "app.html 未部署"}, 404)
            return
        html = APP_FILE.read_text(encoding="utf-8")
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
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
        # 公开状态接口（不受网关限制，供前端判断鉴权模式）
        if path == "/api/auth/state":
            return self._send_json(_auth_state(self))
        # P0-1 数据面鉴权：仅 gate /api/*，放行首页与静态资源
        if path.startswith("/api/") and not _auth_gate(self):
            return
        if path == "/api/hot_daily_result":
            return self._send_json(hot_daily_result())
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
        # 商用化新前端入口（同域 serve，令牌 cookie 自动生效，零跨域）
        if path in ("/app", "/app/"):
            return self._send_html_app()
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
        if path == "/api/bg_list":
            items = [it for it in _bg_load_index() if not it.get("deleted")]
            return self._send_json({"items": items, "current_id": _bg_current_id()})
        if path == "/api/bg_current":
            return self._send_json({"path": _bg_account_bg(), "current_id": _bg_current_id()})
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
        if m:  # 读三段 + 男女对话稿
            name = unquote(m.group(1))
            p = project_path(name)
            md = (p / "03_逐字稿定稿.md").read_text(encoding="utf-8")
            dlg = ""
            dp = p / "dialogue.txt"
            if dp.exists():
                dlg = dp.read_text(encoding="utf-8-sig")
            return self._send_json({"name": name, "segs": parse_three(md),
                                    "raw": md, "dialogue": dlg})
        self._send_json({"error": "not found"}, 404)

    def do_DELETE(self):
        # http.server 默认不支持 DELETE，复用 POST 处理逻辑
        self.do_POST()

    def _handle_login(self):
        """POST /api/login {username,password} → 校验 studio_db.users，下发会话 Cookie。"""
        body = self._body()
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""
        u = studio_db.verify_user(username, password)
        if not u:
            return self._send_json({"ok": False, "error": "账号或密码错误"}, 401)
        token = studio_db.create_session(u["tenant_id"], u["id"])
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie",
            f"hgtv2_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(json.dumps({
            "ok": True, "token": token,
            "tenant_id": u["tenant_id"], "username": u["username"], "role": u["role"]
        }, ensure_ascii=False).encode("utf-8"))

    # —— 商用 SaaS：自助注册（试用租户）——
    _REG_IP = {}
    def _handle_register(self):
        """POST /api/register {username,password,company?} → 新建试用租户+账号，并自动登录。"""
        import ipaddress as _ipa
        ip = self.client_address[0] if self.client_address else "?"
        now = int(time.time())
        hit = self._REG_IP.get(ip, [])
        hit = [t for t in hit if now - t < 3600]      # 1 小时窗口
        if len(hit) >= 8:
            return self._send_json({"ok": False, "error": "注册过于频繁，请稍后再试"}, 429)
        hit.append(now); self._REG_IP[ip] = hit
        body = self._body()
        r = studio_db.register_trial(body.get("username", ""),
                                     body.get("password", ""),
                                     body.get("company", ""))
        if not r.get("ok"):
            return self._send_json(r, 400)
        # 自动登录
        u = studio_db.verify_user(body.get("username", "").strip(), body.get("password", ""))
        if not u:
            return self._send_json({"ok": False, "error": "注册成功但登录失败，请手动登录"}, 200)
        token = studio_db.create_session(u["tenant_id"], u["id"])
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie",
            f"hgtv2_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(json.dumps({
            "ok": True, "token": token, "trial": True,
            "tenant_id": u["tenant_id"], "username": u["username"], "role": u["role"],
            "hint": "试用账号已创建，可直接使用"
        }, ensure_ascii=False).encode("utf-8"))

    def _session_role(self) -> str | None:
        """当前会话用户的角色（超管判断用）。"""
        tok = _session_token_from(self)
        if not tok:
            return None
        s = studio_db.get_session(tok)
        if not s:
            return None
        u = studio_db.get_user_by_id(s["user_id"])
        return u["role"] if u else None

    def _handle_admin(self, action: str, body: dict | None = None):
        """超管管理：users/tenants 列表 + 建试用号/重置密码/禁用启用。仅 role=super。"""
        if self._session_role() != "super":
            return self._send_json({"ok": False, "error": "仅超级管理员可操作"}, 403)
        body = body or {}
        if action == "users":
            return self._send_json({"ok": True, "users": studio_db.list_users_all()})
        if action == "tenants":
            return self._send_json({"ok": True, "tenants": studio_db.list_tenants()})
        if action == "user_create":
            r = studio_db.register_trial(body.get("username", ""),
                                         body.get("password", ""),
                                         body.get("company", ""))
            return self._send_json(r, 200 if r.get("ok") else 400)
        if action == "user_reset":
            try:
                uid = int(body.get("user_id", 0))
            except (TypeError, ValueError):
                return self._send_json({"ok": False, "error": "user_id 非法"}, 400)
            if not body.get("password") or len(str(body.get("password"))) < 6:
                return self._send_json({"ok": False, "error": "新密码至少 6 位"}, 400)
            r = studio_db.set_user_password(uid, str(body.get("password")))
            return self._send_json(r, 200 if r.get("ok") else 400)
        if action == "user_status":
            try:
                uid = int(body.get("user_id", 0))
            except (TypeError, ValueError):
                return self._send_json({"ok": False, "error": "user_id 非法"}, 400)
            r = studio_db.set_user_status(uid, str(body.get("status", "")))
            return self._send_json(r, 200 if r.get("ok") else 400)
        return self._send_json({"ok": False, "error": "未知操作"}, 400)

    def do_POST(self):
        u = urlparse(self.path)
        path = u.path
        # P0-1 数据面鉴权：仅 gate /api/*，放行首页与静态资源
        # 登录/注册接口本身免网关（否则永远登不进）
        if path.startswith("/api/") and path not in ("/api/login", "/api/register") and not _auth_gate(self):
            return
        if path == "/api/login":
            return self._handle_login()
        if path == "/api/register":
            return self._handle_register()
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
        # 背景图上传（multipart/form-data，与模特上传同机制）
        if path == "/api/bg_upload":
            try:
                return self._handle_upload_bg()
            except Exception as e:  # noqa
                import traceback as _tb
                err = f"{type(e).__name__}: {e}\n{_tb.format_exc()}"
                return self._send_json({"ok": False, "error": err}, 200)
        body = self._body()
        if path == "/api/bg_set":
            bg_id = (body.get("id") or "").strip()
            items = _bg_load_index()
            rec = next((it for it in items if it.get("id") == bg_id and not it.get("deleted")), None)
            if not rec:
                return self._send_json({"ok": False, "error": "背景不存在或已删除"}, 400)
            _bg_set_account_bg(rec["path"])
            return self._send_json({"ok": True, "path": rec["path"]})
        if path == "/api/bg_delete":
            bg_id = (body.get("id") or "").strip()
            items = _bg_load_index()
            rec = next((it for it in items if it.get("id") == bg_id), None)
            if not rec:
                return self._send_json({"ok": False, "error": "背景不存在"}, 400)
            rec["deleted"] = True
            # 若删除的正是当前背景，则清空当前（需求6：旧背景不再引用）
            if _bg_account_bg() == rec.get("path"):
                _bg_set_account_bg("")
            _bg_save_index(items)
            return self._send_json({"ok": True, "deleted": bg_id})
        if path == "/api/check":
            text = body.get("text", "")
            platform = body.get("platform") or None
            hits = fw.scan(text, platform=platform)
            return self._send_json({"hits": hits})
        if path == "/api/tts_preview":
            text = (body.get("text") or "").strip()
            if not text:
                return self._send_json({"ok": False, "error": "文本为空，无法试听"}, 400)
            try:
                import make_scroll_video as smv
                import tempfile as _tf, os as _os
                fd, tmp = _tf.mkstemp(suffix=".wav", prefix="tts_pv_")
                _os.close(fd)
                try:
                    smv.synth_dialogue_audio(text, tmp, dry=False, gap=0.18)
                except SystemExit as se:
                    try:
                        _os.remove(tmp)
                    except Exception:
                        pass
                    return self._send_json({"ok": False, "error": friendly_tts_error(str(se))})
                with open(tmp, "rb") as _f:
                    data = _f.read()
                _os.remove(tmp)
                if not data:
                    return self._send_json({"ok": False, "error": "合成结果为空"})
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            except SystemExit as e:
                return self._send_json({"ok": False, "error": friendly_tts_error(str(e))})
            except Exception as e:  # noqa
                return self._send_json({"ok": False, "error": friendly_tts_error(f"{type(e).__name__}: {e}")})
        if path == "/api/generate":
            r = generate_from_source(body.get("source", ""),
                                     body.get("direction", ""),
                                     body.get("length", ""),
                                     body.get("keep_core", ""),
                                     float(body.get("target_seconds", 0) or 0))
            return self._send_json(r)
        if path == "/api/rewrite":
            r = rewrite_with_duration(
                opening=body.get("opening", ""),
                body=body.get("body", ""),
                ending=body.get("ending", ""),
                source=body.get("source", ""),
                topic=body.get("topic", ""),
                target_seconds=float(body.get("target_seconds", 60) or 60),
                extra_prompt=body.get("extra_prompt", ""),
                account_type=body.get("account_type", "财税IP打造类"),
            )
            return self._send_json(r)
        if path == "/api/topic_search":
            try:
                _tc = body.get("topics_cache")
                # 防御：前端可能双重 JSON 序列化（JSON.stringify + fetch自动序列化）
                if isinstance(_tc, str) and _tc.strip():
                    import json as _json
                    try:
                        _tc = _json.loads(_tc)
                    except Exception:
                        _tc = None
                r = search_and_create(body.get("category", ""),
                                      body.get("period", ""),
                                      body.get("direction", ""),
                                      body.get("length", ""),
                                      body.get("keep_core", ""),
                                      float(body.get("target_seconds", 0) or 0),
                                      body.get("mode", "list"),
                                      int(body.get("topic_index", -1)),
                                      _tc or None,
                                      output_mode=body.get("output_mode", "solo"))
            except Exception as e:  # noqa
                import traceback as _tb
                return self._send_json({"ok": False,
                    "error": f"联网检索异常: {type(e).__name__}: {e}"})
            if not isinstance(r, dict):
                r = {"ok": False, "error": "检索返回格式异常"}
            return self._send_json(r)
        if path == "/api/new":
            return self._send_json(do_new(body.get("title", ""), body.get("account_type", "")))
        # —— 批量渲染队列 ——
        if path == "/api/queue" and self.command == "POST":
            tid = self._resolve_tenant_id()
            return self._send_json(add_to_queue(body.get("name", ""),
                                                 body.get("model", ""),
                                                 tenant_id=tid))
        if path == "/api/queue" and self.command == "DELETE":
            return self._send_json(queue_remove(body.get("id", "")))
        if path == "/api/queue/remove":
            return self._send_json(queue_remove(body.get("id", "")))
        if path == "/api/queue/move":
            return self._send_json(queue_move(body.get("id", ""),
                                              body.get("direction", "")))
        if path == "/api/queue":
            return self._send_json({"error": "method not allowed"}, 405)
        if path == "/api/generate_title":
            return self._send_json(generate_title_for_video(
                body.get("opening", ""), body.get("body", ""),
                body.get("ending", ""), body.get("dialogue", "")))
        if path == "/api/hot_daily":
            return self._send_json(start_hot_daily())
        # ---- 商用 SaaS 账号体系：自助注册 + 超管管理 ----
        if path == "/api/register":
            return self._handle_register()
        if path == "/api/admin/users":
            return self._handle_admin("users")
        if path == "/api/admin/tenants":
            return self._handle_admin("tenants")
        if path == "/api/admin/user_create":
            return self._handle_admin("user_create", body)
        if path == "/api/admin/user_reset":
            return self._handle_admin("user_reset", body)
        if path == "/api/admin/user_status":
            return self._handle_admin("user_status", body)
        m = re.match(r"^/api/project/(.+?)/(save|tts|tts_dialogue|publish-check|render|publish|account)$", path)
        if m:
            name = unquote(m.group(1))
            action = m.group(2)
            tid = self._resolve_tenant_id()
            if action == "save":
                return self._send_json(do_save(name, body.get("opening", ""),
                                               body.get("body", ""),
                                               body.get("ending", ""),
                                               dialogue=body.get("dialogue", "")))
            if action == "account":
                return self._send_json(do_set_account(name, body.get("account_type", "")))
            if action == "tts":
                return self._send_json(do_tts(name, {
                    "opening": body.get("opening", ""),
                    "body": body.get("body", ""),
                    "ending": body.get("ending", ""),
                }, tenant_id=tid))
            if action == "tts_dialogue":
                return self._send_json(do_tts_dialogue(name, body.get("dialogue", ""), tenant_id=tid))
            if action == "render":
                return self._send_json(start_render(
                    name, body.get("model", ""),
                    provider=body.get("provider", "heygem"),
                    avatar_id=body.get("avatar_id"),
                    voice_mode=body.get("voice_mode", "official"),
                    bg=body.get("bg"),
                    title=body.get("title"),
                    subtitle=body.get("subtitle"),
                    bg_fit=body.get("bg_fit"),
                    subtitle_style=body.get("subtitle_style", "dynamic"),
                    subtitle_size=body.get("subtitle_size"),
                    subtitle_outline=body.get("subtitle_outline"),
                    subtitle_position=body.get("subtitle_position", "bottom"),
                    subtitle_lines=body.get("subtitle_lines"),
                    edit_style=body.get("edit_style") or None,
                    bgm=body.get("bgm"),
                    motion_style=body.get("motion_style", "财经严谨"),
                    tenant_id=tid))
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
        safe_stem = re.sub(r"[^A-Za-z0-9_\-]", "_", stem)[:40]
        # 若原名全是非字母数字（如纯中文），转义后会是一串下划线，给个可读 fallback
        if not re.search(r"[A-Za-z0-9]", safe_stem):
            safe_stem = "model"

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
        # 注意：-f lavfi 必须紧挨 -i anullsrc；所有 -c:v/-c:a 等输出选项须放在全部 -i 之后；
        #       用 -map 明确「视频取输入0、音频取 anullsrc 静音」，既去原声又满足 HEYGEM 需音频流。
        cmd = [FFMPEG, "-y", "-loglevel", "error",
               "-i", str(raw_path),
               "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
               "-map", "0:v:0", "-map", "1:a:0",
               "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
               "-pix_fmt", "yuv420p",
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


    def _handle_upload_bg(self):
        """POST /api/bg_upload — multipart/form-data, 字段 file。
        校验图片格式(jpg/png/gif/webp)，保存到 static/bg/，登记索引并返回可访问 URL。"""
        ct = self.headers.get("Content-Type", "")
        if not ct.startswith("multipart/form-data"):
            return self._send_json({"ok": False, "error": "需 multipart/form-data"}, 400)
        m = re.search(r"boundary=([^;]+)", ct)
        if not m:
            return self._send_json({"ok": False, "error": "缺 boundary"}, 400)
        boundary = m.group(1).strip().strip('"').encode("utf-8")
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            parts = _parse_multipart(raw, boundary)
        except Exception as e:  # noqa
            return self._send_json({"ok": False, "error": f"解析失败: {type(e).__name__}: {e}"}, 400)
        if "file" not in parts or parts["file"][1] is None:
            return self._send_json({"ok": False, "error": "缺字段 file"}, 400)
        orig_name, data = parts["file"]
        if orig_name is None:
            return self._send_json({"ok": False, "error": "缺文件"}, 400)
        base = os.path.basename(orig_name)
        stem, ext = os.path.splitext(base)
        ext = ext.lower()
        if ext not in BG_ALLOWED_EXT:
            return self._send_json({"ok": False, "error": "仅支持 JPG/PNG/GIF/WebP 图片"}, 400)
        if len(data) > BG_MAX_BYTES:
            return self._send_json({"ok": False, "error": "文件超过 30MB"}, 400)
        if len(data) < 512:
            return self._send_json({"ok": False, "error": "文件过小/可能损坏"}, 400)
        safe_stem = re.sub(r"[^A-Za-z0-9_\u4e00-\u9fa5-]", "_", stem)[:40] or "bg"
        bid = "bg_" + os.urandom(4).hex()
        fname = f"{bid}_{safe_stem}{ext}"
        try:
            (BG_DIR / fname).write_bytes(data)
        except Exception as e:
            return self._send_json({"ok": False, "error": f"写入失败: {e}"}, 500)
        items = _bg_load_index()
        rec = {"id": bid, "name": base, "filename": fname,
               "url": f"/static/bg/{fname}", "path": str(BG_DIR / fname),
               "created": int(time.time()), "fit": "fill", "deleted": False}
        items.append(rec)
        _bg_save_index(items)
        was_empty = not _bg_account_bg()
        if was_empty:
            _bg_set_account_bg(rec["path"])
        return self._send_json({"ok": True, "item": rec, "auto_set": was_empty})


# ------------------------------------------------------------------ 业务处理（生成初稿/保存/新建 放末尾避免循环依赖问题）
def do_save(name: str, opening: str, body: str, ending: str, dialogue: str = "") -> dict:
    p = project_path(name)
    p.mkdir(parents=True, exist_ok=True)  # 关键：先建项目目录，否则写03定稿会 FileNotFoundError
    md = serialize_three({"opening": opening, "body": body, "ending": ending})
    (p / "03_逐字稿定稿.md").write_text(md, encoding="utf-8")
    hits = fw.scan(fw.clean_script(md))
    high = sum(1 for h in hits if h["level"] == "high" and not h.get("need_human"))
    med = sum(1 for h in hits if h["level"] == "medium")
    (p / "03_违禁词检查.md").write_text(fw.format_report(hits), encoding="utf-8")
    # 男女对话稿（用于滚动字幕卡出片）；留空则删除旧对话稿，避免脏数据
    dlg_p = p / "dialogue.txt"
    if dialogue and dialogue.strip():
        dlg_p.write_text(dialogue, encoding="utf-8")
    elif dlg_p.exists():
        try:
            dlg_p.unlink()
        except Exception:
            pass
    return {"ok": True, "high": high, "med": med,
            "saved": str(p / "03_逐字稿定稿.md")}


def generate_title_for_video(opening: str, body: str, ending: str, dialogue: str) -> dict:
    """根据口播稿为视频号生成标题与副标题。
    标题≤10字、简洁有力、口语化、适配视频号竖屏首屏；
    副标题≤40字、与标题语义衔接、补充钩子或场景，避免生硬截断。"""
    # 优先用对话稿，否则拼接三段稿
    if dialogue and dialogue.strip():
        source = dialogue.strip()
    else:
        source = "\n".join([opening or "", body or "", ending or ""]).strip()
    if not source:
        return {"ok": False, "error": "稿件为空，无法生成标题"}
    # 取前 400 字避免 prompt 过长
    source = source[:400]
    prompt = f"""你是一名熟悉视频号平台规则的短视频文案。请根据以下口播稿，为视频号生成一条标题和一条副标题。

要求：
1. 标题控制在 10 个字以内（含标点），必须简洁、有力、口语化、去 AI 痕迹，能在一秒内抓住老板眼球。
2. 副标题控制在 40 个字以内（含标点），放在标题下方作为补充说明；要与标题语义连贯、断句自然，不能出现生硬换行或被截断的感觉。
3. 风格：财税干货、风险警示、面向中小企业老板，可用「老板」「注意」「千万别」「一查一个准」等视频号高点击词汇，但避免夸张恐吓。
4. 输出必须是纯 JSON，不要任何解释、不要 markdown 代码块，格式：{{"title":"...","subtitle":"..."}}

口播稿：
{source}
"""
    try:
        raw = llm(prompt, retries=2)
        candidate = raw.strip()
        # 去 markdown 代码围栏（模型偶尔会包一层 ```json）
        if candidate.startswith("```"):
            candidate = re.sub(r"^```[a-zA-Z]*\s*", "", candidate)
            candidate = re.sub(r"\s*```$", "", candidate).strip()
        try:
            obj = json.loads(candidate)
        except Exception:
            # 兜底：宽松正则抓 {..."title"... "subtitle"...}（re.S 含换行）
            m = re.search(r"\{.*?\"title\".*?\"subtitle\".*?\}", candidate, re.S)
            if not m:
                return {"ok": False, "error": "模型返回格式异常", "raw": raw[:200]}
            try:
                obj = json.loads(m.group(0))
            except Exception:
                return {"ok": False, "error": "模型返回格式异常", "raw": raw[:200]}
        title = (obj.get("title") or "").strip()
        subtitle = (obj.get("subtitle") or "").strip()
        # 硬截断兜底（按字符数，防止 LLM 超长）
        if len(title) > 10:
            title = title[:10]
        if len(subtitle) > 40:
            subtitle = subtitle[:40]
        return {"ok": True, "title": title, "subtitle": subtitle}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}


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


# 选题相关度过滤用的停用词（与主题无关的通用词、后缀）
_TOPIC_STOPWORDS = {
    "的", "了", "和", "与", "或", "等", "在", "是", "有", "及", "而", "但", "为", "对",
    "关于", "如何", "怎么", "什么", "为什么", "多少", "哪些", "好处", "优势", "劣势",
    "风险", "影响", "实务", "解读", "案例", "政策", "热点", "讨论", "相关", "最新", "最近"
}

# 字符重叠兜底时忽略的高频虚字/标点
_TOPIC_CHAR_STOP = set("  \t\n的了吗呢吧啊哦了在和与或等是有及而为但对关于如何怎么什么为什么多少哪些")


def _topic_keywords(category: str) -> list:
    """从用户输入的类别/主题中提取有效关键词，用于后续相关度过滤。"""
    if not category:
        return []
    # 把停用词作为分隔符切分，去掉通用后缀/前缀
    pattern = "|".join(re.escape(w) for w in _TOPIC_STOPWORDS)
    parts = [p.strip() for p in re.split(pattern, category) if p.strip()]
    keywords = []
    for part in parts:
        if len(part) < 2:
            continue
        keywords.append(part)
        # 对纯中文长片段再提取前缀，提高命中（如“小微企业身份”可拆出“小微企业”）
        if re.match(r"^[\u4e00-\u9fa5]+$", part):
            if len(part) >= 2:
                keywords.append(part[:2])
            if len(part) >= 4:
                keywords.append(part[:4])
            if len(part) >= 6:
                keywords.append(part[:6])
    # 去重并按长度降序（越长越具体，优先匹配）
    seen = set()
    result = []
    for k in keywords:
        kl = k.lower()
        if kl not in seen:
            seen.add(kl)
            result.append(k)
    return sorted(result, key=lambda x: len(x), reverse=True)


def _topic_text_chars(category: str) -> set:
    """返回 category 中有区分度的字符集合（用于兜底字符重叠计算）。"""
    return set(category) - _TOPIC_CHAR_STOP


def _filter_relevant_topics(topics: list, category: str, min_keep: int = 1) -> list:
    """按关键词命中 + 字符重叠兜底，确保返回选题与 category 相关；过滤后无命中则回退原始。"""
    keywords = _topic_keywords(category)
    cat_chars = _topic_text_chars(category)
    if not topics or (not keywords and not cat_chars):
        return topics
    kept = []
    for t in topics:
        text = " ".join([
            str(t.get("title", "")), str(t.get("why_hot", "")),
            str(t.get("risk_point", "")), str(t.get("audience", ""))
        ])
        text_lower = text.lower()
        # 1) 关键词命中（支持拆分后的前缀/片段）
        keyword_hit = any(k.lower() in text_lower for k in keywords) if keywords else False
        # 2) 字符重叠兜底（处理“个人所得税”→“个税”等同义/缩略）
        text_chars = set(text) - _TOPIC_CHAR_STOP
        union = len(cat_chars | text_chars)
        overlap = (len(cat_chars & text_chars) / union) if union else 0
        if keyword_hit or overlap >= 0.18:
            kept.append(t)
    # 若过滤太狠（只剩 0/1/2 条且原始足够多），说明关键词可能过严，回退原始列表
    if len(kept) < min_keep and len(topics) >= min_keep:
        return topics
    return kept


def _score_topic_relevance(t: dict, category: str) -> int:
    """返回 0-100 的相关度分：综合「关键词命中」+「字符重叠」，用于排序（不用于硬过滤）。

    排序标准说明：Tavily 返回的是网页文章而非视频，没有播放/评论/转发量，
    因此排序仅依据「与用户输入主题的相关度」+「LLM 判定的爆款潜力」，不引入社交指标。
    """
    keywords = _topic_keywords(category)
    cat_chars = _topic_text_chars(category)
    text = " ".join([
        str(t.get("title", "")), str(t.get("why_hot", "")),
        str(t.get("risk_point", "")), str(t.get("audience", ""))
    ])
    text_lower = text.lower()
    # 关键词命中得分（每个命中 +18，封顶 60）
    kwhits = sum(1 for k in keywords if k.lower() in text_lower)
    kw_score = min(60, kwhits * 18)
    # 字符重叠得分（0-40）
    text_chars = set(text) - _TOPIC_CHAR_STOP
    union = len(cat_chars | text_chars)
    overlap = (len(cat_chars & text_chars) / union) if union else 0
    char_score = int(overlap * 40)
    return min(100, kw_score + char_score)


def search_and_create(category: str, period: str, direction: str = "",
                      length: str = "约60秒", keep_core: str = "",
                      target_seconds: float = 0.0,
                      mode: str = "list", topic_index: int = -1,
                      topics_cache: list = None, max_topics: int = 10,
                      output_mode: str = "solo") -> dict:
    """智能选题两阶段：
      mode="list"  → 仅联网检索+提炼最多 max_topics 个候选选题（不二创），用户挑选后再走 create
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
                # 搜索词聚焦用户输入主题，不再硬塞「稽查/痛点/政策变化」等宽泛词，避免结果跑偏
                sq = f"最近 {period}（截至{now}）「{category}」财税实务、政策解读、热点案例讨论"
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
                f"以下是联网检索到的「{category}」最近（{period}）真实财税素材（含来源链接）：\n"
                f"{real_material}\n\n"
                f"请基于以上真实素材，提炼与「{category}」高度相关、适合「老张讲财税」做的爆款选题。"
            )
        else:
            brief_intro = (
                f"未启用联网检索（未配置 TAVILY_API_KEY 或检索失败），请基于你的财税知识，"
                f"给出与「{category}」高度相关的爆款选题。"
            )
        topic_prompt = (
            brief_intro +
            f"注意：所有选题必须紧紧围绕「{category}」这一主题；"
            "如果检索素材与该主题直接相关的内容不足，宁可少选也不要生成无关的泛财税热点凑数。"
            f"请按相关性和爆款潜力从高到低排序，最多给出 {max_topics} 个选题。\n"
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
        # 强制相关度过滤：确保返回的选题与 category 真正有关，宁可少也不要滥竽充数
        topics = _filter_relevant_topics(topics, category)
        # —— 相关度优先重排 ——
        # 给每条打相关度分(0-100)，按「相关度降序」重排；相关度并列时保留 LLM 原排序。
        # 排序标准：仅依据与用户输入主题的相关度（Tavily 返回网页文章、无播放/评论/转发量，
        # 故不引入社交指标），相关度低的选题自然沉底，避免泛财税热点挤占前排。
        for _i, _t in enumerate(topics):
            _t["relevance"] = _score_topic_relevance(_t, category)
            _t["_order"] = _i
        topics.sort(key=lambda _t: (_t.get("relevance", 0), -_t.get("_order", 0)),
                    reverse=True)
        for _t in topics:
            _t.pop("_order", None)
        topics = topics[:max_topics]
        return {"ok": True, "topics": topics, "source_label": source_label,
                "stage": "list"}

    # ——— 第二阶段：基于用户挑的 1 条做二创 ———
    if mode == "create":
        topics = topics_cache or []
        if not topics or topic_index < 0 or topic_index >= len(topics):
            return {"ok": False, "error": "未提供有效选题索引，请回到上一步重新选题。"}
        t = topics[topic_index]
        # 篇幅映射：key 兼容前端两种格式（"约X" / "X"），value 为 LLM 字数指令
        _lm = {
            "30秒": "约 30 秒口播量，60-90 字，精炼",
            "约30秒": "约 30 秒口播量，60-90 字，精炼",
            "1分钟": "约 60 秒口播量，100-150 字",
            "约60秒": "约 60 秒口播量，100-150 字",
            "约90秒": "约 90 秒口播量，150-220 字",
            "2分钟": "约 2 分钟口播量（480-540 字），结构清晰：开头 40 字钩子 / 正文 380-430 字分 3 段讲解 / 结尾 60 字留资钩子",
            "约2分钟": "约 2 分钟口播量（480-540 字），结构清晰：开头 40 字钩子 / 正文 380-430 字分 3 段讲解 / 结尾 60 字留资钩子",
            "3分钟": "约 3 分钟口播量（450-700 字），结构清晰：开头 30 字钩子 / 正文 400-500 字分段讲解 / 结尾 50 字留资钩子",
            "约3分钟": "约 3 分钟口播量（450-700 字），结构清晰：开头 30 字钩子 / 正文 400-500 字分段讲解 / 结尾 50 字留资钩子",
            "5分钟": "约 5 分钟口播量（800-1200 字），结构清晰：开头 50 字钩子 / 正文 700-900 字分 3-4 段，每段一个小主题 / 结尾 80 字留资钩子",
            "约5分钟": "约 5 分钟口播量（800-1200 字），结构清晰：开头 50 字钩子 / 正文 700-900 字分 3-4 段，每段一个小主题 / 结尾 80 字留资钩子",
            "约10分钟": "约 10 分钟口播量（1500-2200 字），结构清晰：开头 80 字钩子 / 正文 1200-1800 字分 5-7 段，每段一个小主题并配案例 / 结尾 100 字留资钩子。",
        }
        lr = _lm.get(length, _lm.get("约"+length, "约 60 秒口播量，100-150 字"))
        keep = keep_core or "保留核心知识点与关键判断，不编造数字与政策条文"
        if target_seconds and target_seconds > 0:
            dlg_hint = f"约 {target_seconds:.0f} 秒，{int(target_seconds*CHARS_PER_SECOND)} 字左右"
        else:
            dlg_hint = f"按上面同样的知识点与篇幅「{lr}」"
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
            "- 财税术语准确，概念不混淆（如个人卡收营业款≠公转私）\n"
            "- **开头设计（重要）**：① 严禁自我介绍式开头：不写「张德富/老张今天跟您聊」「我张德富」「老张我」等以人名/人称起头；② 优先用疑问句或痛点场景切入制造悬念，例如「老板们，虚开发票这事，您真觉得查不到您头上？」；③ 每次开场根据内容重新设计，拒绝固定套路，不要寒暄铺垫，直接戳痛点/抛钩子。\n\n"
            f"{fb}\n\n"
            f"风格：\n{STYLE_GUIDE}\n\n"
        )
        # 根据输出模式动态组装格式要求
        if output_mode == "dialogue":
            _fmt_block = (
                "【输出格式（严格按此，不要多余解释）】\n"
                "=== 开头 ===\n（抓眼球 / 痛点引入，1-2句）\n"
                "=== 正文 ===\n（核心讲解，3-5句，一句一意、节奏清晰）\n"
                "=== 结尾（钩子） ===\n（留资引导 / 关注，自然不生硬，1-2句，严禁加微信/扫码等导流词）\n\n"
                "=== 男女对话稿 ===\n"
                f"（{dlg_hint}，改写成女问男答的对话：每行以 女： 或 男： 开头；"
                "女为提问/引发好奇，男为张老师解答；称呼男为「张老师」，女用「我/您」自然对话；"
                "整体口语化、节奏与三段稿一致，覆盖同样的核心风险点）"
            )
        else:
            _fmt_block = (
                "【输出格式（严格按此，不要多余解释）】\n"
                "=== 开头 ===\n（抓眼球 / 痛点引入，1-2句）\n"
                "=== 正文 ===\n（核心讲解，3-5句，一句一意、节奏清晰）\n"
                "=== 结尾（钩子）===\n（留资引导 / 关注，自然不生硬，1-2句，严禁加微信/扫码等导流词）\n\n"
                "【注意】只需输出上述三段式逐字稿即可，不要输出对话稿或男女问答格式。"
            )
        p = p + f"{_fmt_block}\n\n{TTS_NATURAL_RULE}\n直接输出（含 === 标记），不要额外解释。"
        try:
            raw = deepseek_chat(p, model="deepseek-v4-flash", key=key, timeout=120)
        except Exception as e:  # noqa
            return {"ok": False, "error": f"二创生成失败: {type(e).__name__}: {e}",
                    "topics": topics, "source_label": "联网检索"}
        segs = parse_three(raw)
        dialogue = extract_dialogue(raw)
        # 兜底：LLM 没给对话稿时，用三段稿轻量拆出男女对话
        if not dialogue:
            dialogue = auto_dialogue_from_segs(segs)
        return {"ok": True, "segs": segs, "dialogue": dialogue, "raw": raw,
                "topics": topics, "source_label": "联网检索",
                "chosen_index": topic_index, "chosen_title": t.get("title", ""),
                "stage": "create"}

    return {"ok": False, "error": f"未知 mode: {mode}（仅支持 list/create）"}


# ------------------------------------------------------------------ 时长/字数估算与对话稿解析
# 实测老张/江老师克隆音口播节奏约 4.2 字/秒（滚动字幕卡已验证）
CHARS_PER_SECOND = 4.2


def estimate_chars(seconds: float) -> int:
    return int(seconds * CHARS_PER_SECOND)


def extract_dialogue(text: str) -> str:
    """从 LLM 输出中抓取 === 男女对话稿 === 区域。"""
    m = re.search(r"={2,}\s*男女对话稿\s*={2,}\s*\n?(.*?)(?:\n?={2,}|$)", text, re.S)
    if not m:
        return ""
    lines = []
    for ln in m.group(1).splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("女：") or ln.startswith("男："):
            lines.append(ln)
    return "\n".join(lines)


def _llm_with_timeout(prompt: str, seconds: int = 110):
    """独立线程跑 llm，超时返回 None，避免后端永久挂起导致前端「改写中」卡死。"""
    import concurrent.futures as _cf
    with _cf.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(llm, prompt)
        try:
            return fut.result(timeout=seconds)
        except _cf.TimeoutError:
            return None


def rewrite_with_duration(opening: str = "", body: str = "", ending: str = "",
                          source: str = "", topic: str = "",
                          target_seconds: float = 60.0,
                          extra_prompt: str = "",
                          account_type: str = "财税IP打造类") -> dict:
    """按目标时长（秒）重新改写三段稿，并同步生成男女对话稿。"""
    try:
        get_text_config()
    except RuntimeError as e:
        return {"ok": False, "error": f"{e} —— 请用记事本打开 model_keys.env，把 DEEPSEEK_API_KEY（推荐）或 DASHSCOPE_API_KEY 等号右边填上真实 key 保存（不要把 key 发到对话里）。"}

    ref_text = ""
    if opening or body or ending:
        ref_text = serialize_three({"opening": opening, "body": body, "ending": ending})
    elif source:
        ref_text = source
    elif topic:
        ref_text = f"选题：{topic}"
    else:
        return {"ok": False, "error": "请先在改写区填写初稿，或提供原始素材/选题"}

    target_chars = estimate_chars(target_seconds)
    fb = fw.build_guidance()
    prompt = (
        "你是「老张讲财税」短视频账号的资深编剧。主讲人张德富，苏州实战派财税专家，"
        "风格像朋友聊天叙事、不居高临下说教。\n\n"
        f"【账号定位】{account_type}\n"
        f"【参考文案】\n{ref_text}\n\n"
        f"【目标时长】约 {target_seconds} 秒（按口播节奏约 {target_chars} 字）。"
        f"请严格控制总字数在 {max(20, target_chars - 10)} 到 {target_chars + 15} 字之间。\n"
        f"【补充要求】{extra_prompt or '自然口语化、一句一意、节奏清晰、专业可信'}\n\n"
        "【创作要求】\n"
        "- 基于参考文案重新组织，不要照搬原句；优先用疑问句或痛点场景开头，严禁自我介绍式开头\n"
        "- 财税术语准确，概念不混淆；定性稳妥，不绝对化、不编造数字与政策条文\n"
        "- 输出两段内容：\n"
        "  1) 三段式独白稿（=== 开头 === / === 正文 === / === 结尾（钩子） ===）\n"
        "  2) 男女对话稿（每行以 女： 或 男： 开头；女为提问者/引发好奇，男为张老师解答；"
        "整体覆盖同样知识点，保持目标时长，称呼男为「张老师」，女用「我/您」自然对话）\n\n"
        f"{TTS_NATURAL_RULE}"
        f"{fb}\n\n"
        f"风格：\n{STYLE_GUIDE}\n\n"
        "【输出格式（严格按此，不要多余解释）】\n"
        "=== 开头 ===\n...\n=== 正文 ===\n...\n=== 结尾（钩子） ===\n...\n\n"
        "=== 男女对话稿 ===\n女：...\n男：...\n女：...\n男：...\n\n"
        "直接输出，不要额外解释。"
    )
    try:
        raw = _llm_with_timeout(prompt, seconds=110)
        if raw is None:
            return {"ok": False, "error": "改写超时（>110 秒），大模型未响应，请重试或缩短目标时长"}
    except SystemExit as e:
        return {"ok": False, "error": f"改写失败（检查 KEY 或网络）: {e}"}
    except Exception as e:  # noqa
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    segs = parse_three(raw)
    dialogue = extract_dialogue(raw)
    # 如果没解析到对话稿，按三段稿兜底拆几句简单对话
    if not dialogue:
        dialogue = auto_dialogue_from_segs(segs)
    return {
        "ok": True,
        "segs": segs,
        "dialogue": dialogue,
        "raw": raw,
        "target_seconds": target_seconds,
        "target_chars": target_chars,
    }


def auto_dialogue_from_segs(segs: dict) -> str:
    """LLM 没返回对话稿时的轻量兜底：把开头/正文/结尾拆成女问男答。"""
    parts = [segs.get("opening", "").strip(), segs.get("body", "").strip(), segs.get("ending", "").strip()]
    parts = [p for p in parts if p]
    lines = []
    for i, p in enumerate(parts):
        # 简单按句号/问号/叹号切第一句给女，剩余给男
        first, _, rest = re.split(r"([。！？])", p, maxsplit=1) if re.search(r"[。！？]", p) else (p, "", "")
        delim = _ if _ else ""
        if i == 0:
            lines.append(f"女：张老师，{first}{delim}" if not first.startswith("张老师") else f"女：{first}{delim}")
            if rest.strip():
                lines.append(f"男：{rest.strip()}")
        elif i == len(parts) - 1:
            lines.append(f"女：{first}{delim}" if first else f"女：那张老师，最后再提醒一句？")
            if rest.strip():
                lines.append(f"男：{rest.strip()}")
        else:
            lines.append(f"女：{first}{delim}")
            if rest.strip():
                lines.append(f"男：{rest.strip()}")
    return "\n".join([ln for ln in lines if ln.strip()])


def generate_from_source(source: str, direction: str = "",
                         length: str = "约60秒", keep_core: str = "",
                         target_seconds: float = 0.0) -> dict:
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
    if target_seconds and target_seconds > 0:
        dlg_hint = f"约 {target_seconds:.0f} 秒，{int(target_seconds*CHARS_PER_SECOND)} 字左右"
    else:
        dlg_hint = f"按上面同样的知识点与篇幅「{lr}」"
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
        "- 财税术语准确，概念不混淆（如个人卡收营业款≠公转私）\n"
        "- **开头设计（重要）**：① 严禁自我介绍式开头：不写「张德富/老张今天跟您聊」「我张德富」「老张我」等以人名/人称起头；② 优先用疑问句或痛点场景切入制造悬念，例如「老板们，虚开发票这事，您真觉得查不到您头上？」；③ 每次开场根据内容重新设计，拒绝固定套路，不要寒暄铺垫，直接戳痛点/抛钩子。\n\n"
        f"{fb}\n\n"
        f"风格：\n{STYLE_GUIDE}\n\n"
        "【输出格式（严格按此，不要多余解释）】\n"
        "=== 开头 ===\n（抓眼球 / 痛点引入，1-2句）\n"
        "=== 正文 ===\n（核心讲解，3-5句，一句一意、节奏清晰）\n"
        "=== 结尾（钩子） ===\n（留资引导 / 关注，自然不生硬，1-2句，严禁加微信/扫码等导流词）\n\n"
        "=== 男女对话稿 ===\n"
        f"（{dlg_hint}，改写成女问男答的对话：每行以 女： 或 男： 开头；"
        "女为提问/引发好奇，男为张老师解答；称呼男为「张老师」，女用「我/您」自然对话；"
        "整体口语化、节奏与三段稿一致，覆盖同样的核心风险点）\n\n"
        f"{TTS_NATURAL_RULE}"
        "直接输出（含 === 标记），不要额外解释。"
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
    dialogue = extract_dialogue(raw)
    if not dialogue:
        dialogue = auto_dialogue_from_segs(segs)
    return {"ok": True, "segs": segs, "dialogue": dialogue, "raw": raw}


def init_default_bg():
    """确保默认「滚动海浪」模板存在并可被选。"""
    default_name = "default_rolling_seas.gif"
    default_path = BG_DIR / default_name
    # 如果默认文件不存在但源文件存在，则复制一份
    src = Path(SCROLL_DEFAULT_GIF)
    if not default_path.exists() and src.exists():
        try:
            shutil.copy2(str(src), str(default_path))
        except Exception:
            pass
    if not default_path.exists():
        return
    items = _bg_load_index()
    # 查找是否已注册默认模板
    rec = next((it for it in items if it.get("is_default") and it.get("filename") == default_name), None)
    if not rec:
        rec = {
            "id": "default_rolling_seas",
            "name": "滚动海浪（默认模板）",
            "filename": default_name,
            "url": f"/static/bg/{default_name}",
            "path": str(default_path),
            "created": int(time.time()),
            "fit": "fill",
            "deleted": False,
            "is_default": True,
        }
        items.append(rec)
        _bg_save_index(items)
    # 若账号尚未设置背景，默认使用海浪模板
    if not _bg_account_bg():
        _bg_set_account_bg(str(default_path))


class StudioServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # 吞掉客户端断开/断管等连接级异常，避免刷 stderr 且不致命
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionAbortedError, BrokenPipeError, ConnectionResetError)):
            return
        # 其它异常仍落盘便于排查
        try:
            import traceback
            with open("D:/heygem_data/server_err.log", "a", encoding="utf-8") as _f:
                _f.write(f"[{time.strftime('%H:%M:%S')}] server error {client_address}: {traceback.format_exc()}\n")
        except Exception:
            pass


def main():
    # 多租户数据层初始化：建表 + 预置默认租户形象/声音/管理员账号（幂等，可重复调用）
    try:
        studio_db.init_all()
    except Exception as e:  # noqa
        print("⚠️ studio_db.init_all 失败（非致命，继续启动）:", e)
    init_default_bg()
    httpd = StudioServer(("0.0.0.0", PORT), Handler)
    print(f"二创改写台 v2 已启动: http://localhost:{PORT}  (Ctrl+C 停止)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        httpd.shutdown()


if __name__ == "__main__":
    main()
