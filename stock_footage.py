#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_footage.py — 真实素材库接入（Pexels / Pixabay，双源互备）

作用:
  · 为「幕后音·动态画面」引擎提供**真实动态视频素材**作背景（风景/城市/内容相关场景），
    替代纯 AI 生图静态底图 + 模拟运镜 —— 画面从"仿真"升级为"真实"。
  · 商用授权: Pexels License / Pixabay Content License 均允许免费商用（无需署名），
    但**禁止转售/再分发原始素材**、禁止用素材生成近似素材再分发、禁止冒充素材来源。
    本引擎只把素材作为视频背景使用（叠加字幕/水印/调色），完全合规。
  · 免费额度: Pexels API 200 次/小时、20000 次/月；Pixabay API 100 次/分钟。
    本项目每视频约 3~12 幕 = 3~12 次请求，且按关键词做文件级缓存，重跑不重复消耗。

Key 配置: model_keys.env 里填（留空 = 不启用，引擎自动回退万相生图/照片库）:
    PEXELS_API_KEY=xxx   注册: https://www.pexels.com/api/
    PIXABAY_API_KEY=xxx  注册: https://pixabay.com/api/docs/
  任一可用即可；Pexels 优先，失败自动切 Pixabay。
"""
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
ENV_FILE = BASE / "model_keys.env"
CACHE_DIR = BASE / "storage" / "stock_videos"          # 在线素材下载缓存
LOCAL_DIR = BASE / "storage" / "stock_videos_local"    # 手动素材库(零注册): 文件名用英文关键词

_PROC_CACHE = {}          # query -> clip_path  (进程内缓存, 同次渲染不重复下载)
_FETCH_LOCK = threading.Lock()   # 多进程渲染时同进程内串行化下载, 防并发写同一缓存文件
_ENABLED = None           # 惰性判定: 是否任一 key 可用

# ============================================================
# Key 读取（环境变量优先 > model_keys.env），与 model_providers 同规则
# ============================================================
def _env_value(name):
    v = os.environ.get(name)
    if v and v.strip():
        return v.strip()
    try:
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, val = line.partition("=")
            if k.strip() == name and val.strip():
                return val.strip()
    except Exception:
        pass
    return ""


def is_enabled():
    """是否配置了任一素材库 key（惰性判定一次）。"""
    global _ENABLED
    if _ENABLED is None:
        _ENABLED = bool(_env_value("PEXELS_API_KEY") or _env_value("PIXABAY_API_KEY"))
    return _ENABLED


# ============================================================
# 场景 → 搜索词（英文，Pexels/Pixabay 以英文检索命中率最高）
# 财税内容词优先（内容相关 B-roll），无命中回退 tone 风景词（无人物）
# ============================================================
CONTENT_TERMS = [
    (("发票", "开票", "专票", "普票", "税票", "抵扣"), "invoice documents closeup"),
    (("仓库", "库存", "盘点", "存货", "仓储"), ["empty warehouse interior shelves", "warehouse aisle boxes"]),
    (("账", "记账", "台账", "报表", "核算", "对账"), ["accounting ledger documents", "calculator bookkeeping"]),
    (("工厂", "车间", "生产", "制造", "产线", "设备"), ["factory machinery production line", "manufacturing plant interior"]),
    (("物流", "运输", "货运", "货车", "快递", "配送"), ["highway trucks logistics aerial", "cargo containers port"]),
    (("门店", "店铺", "零售", "商场", "超市", "生意"), ["modern storefront retail", "shopping street store"]),
    (("建筑", "工地", "工程", "施工", "土建", "项目"), ["construction site crane", "building scaffolding"]),
    (("会议", "谈判", "合同", "签约", "客户"), ["business meeting conference room", "office handshake meeting"]),
    (("办公", "电脑", "系统", "申报", "软件", "网银"), ["modern office computer desk", "typing laptop office"]),
    (("现金", "银行", "资金", "融资", "贷款", "存款"), ["bank cash money closeup", "city bank building"]),
    (("税务", "稽查", "纳税", "税务局", "税局"), ["tax office documents", "government building documents"]),
    (("餐饮", "餐厅", "饭店", "厨房", "食材"), ["restaurant kitchen cooking", "cafe interior cozy"]),
    (("电商", "直播", "网店", "电商", "带货"), ["online shopping ecommerce boxes", "smartphone shopping online"]),
    (("医院", "诊所", "医疗", "药店", "医药"), ["hospital corridor clean", "pharmacy shelves medicine"]),
    (("学校", "培训", "教育", "课程", "学员"), ["school classroom desks", "lecture hall audience"]),
]

SCENIC_BY_TONE = {
    "risk":    ["stormy sea dark clouds", "night city skyline lights", "dark clouds dramatic sky"],
    "safe":    ["sunny beach ocean waves", "green mountain lake sunrise", "calm lake water reflection"],
    "neutral": ["city skyline blue sky", "forest morning light mist", "green meadow hills clouds"],
}

_QUERY_ROUND = {}   # 查询词 -> 已用次数（同一查询词轮换同义变体，避免全片同一段素材）


def scene_query(sc):
    """把分镜场景 sc 映射成英文搜索词；返回 None 表示不适用（如对话句无内容）。"""
    kw_text = " ".join([
        str(sc.get("title", "") or ""),
        " ".join(sc.get("keywords") or []),
        str(sc.get("sentence", "") or ""),
    ])
    for keys, q in CONTENT_TERMS:
        if any(k in kw_text for k in keys):
            pool = q if isinstance(q, list) else [q]
            n = _QUERY_ROUND.get(pool[0], 0)
            _QUERY_ROUND[pool[0]] = n + 1
            return pool[n % len(pool)]
    tone = sc.get("tone", "neutral")
    pool = SCENIC_BY_TONE.get(tone, SCENIC_BY_TONE["neutral"])
    h = hash(sc.get("title", "") or "x") % len(pool)
    return pool[h]


# ============================================================
# 素材搜索（Pexels 优先 → Pixabay 兜底）
# ============================================================
def _http_json(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _pick_pexels(data):
    """从 Pexels 搜索结果挑最合适的一段: 竖屏优先、宽≥720、时长 5~40s、mp4。"""
    best, best_score = None, -1
    for v in data.get("videos", []):
        dur = v.get("duration") or 0
        if dur < 5 or dur > 40:
            continue
        for f in v.get("video_files", []):
            w, h = f.get("width") or 0, f.get("height") or 0
            if w < 720 or h < 720:
                continue
            link = f.get("link", "") or ""
            if f.get("file_type") and "mp4" not in f["file_type"]:
                continue
            if not link.lower().endswith((".mp4", ".mov")):
                continue
            portrait = 1 if h >= w else 0.5          # 竖屏强优先
            score = portrait * (w * h) + dur * 10
            if score > best_score:
                best, best_score = link, score
    return best


def _pexels_search(query):
    key = _env_value("PEXELS_API_KEY")
    if not key:
        return None
    url = ("https://api.pexels.com/videos/search?" +
           urllib.parse.urlencode({"query": query, "orientation": "portrait",
                                   "per_page": 10, "size": "medium"}))
    data = _http_json(url, headers={"Authorization": key})
    return _pick_pexels(data)


def _pick_pixabay(data):
    best, best_score = None, -1
    for h in data.get("hits", []):
        dur = h.get("duration") or 0
        if dur < 5 or dur > 40:
            continue
        for size in ("large", "medium"):
            v = (h.get("videos") or {}).get(size)
            if not v:
                continue
            w, hh = v.get("width") or 0, v.get("height") or 0
            if w < 720 or hh < 720:
                continue
            portrait = 1 if hh >= w else 0.5
            score = portrait * (w * hh) + dur * 10
            if score > best_score:
                best, best_score = v.get("url"), score
    return best


def _pixabay_search(query):
    key = _env_value("PIXABAY_API_KEY")
    if not key:
        return None
    url = ("https://pixabay.com/api/videos/?" +
           urllib.parse.urlencode({"key": key, "q": query, "video_type": "film",
                                   "orientation": "portrait", "min_width": 720,
                                   "per_page": 10}))
    data = _http_json(url)
    return _pick_pixabay(data)


def _download(url, dest, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    # 唯一临时名: 多进程/多线程并发下载同一缓存文件时互不冲突, 原子替换
    tmp = dest.parent / (dest.stem + "." + uuid_hex() + ".part")
    with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
        shutil_copyfileobj(r, f)
    if tmp.stat().st_size < 50 * 1024:
        tmp.unlink(missing_ok=True)
        return False
    tmp.replace(dest)
    return True


def uuid_hex():
    import uuid
    return uuid.uuid4().hex


def shutil_copyfileobj(r, f):
    """纯 urllib 实现，避免依赖 shutil 权限差异。"""
    while True:
        chunk = r.read(1 << 20)
        if not chunk:
            break
        f.write(chunk)


# ============================================================
# 本地素材库（零注册方案）: 手动下载素材放进 storage/stock_videos_local/
# 文件名/子目录名用英文关键词(下划线分词), 如 beach_waves.mp4 / invoice_documents/
# 引擎按查询词分词取交集匹配。在线 key 可用时仍优先在线(素材更全), 本地作为兜底。
# ============================================================
_VIDEO_SUFFIXES = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v")

def has_local():
    """本地素材目录是否已有可用视频文件。"""
    try:
        return LOCAL_DIR.exists() and any(
            f.is_file() and f.suffix.lower() in _VIDEO_SUFFIXES for f in LOCAL_DIR.rglob("*"))
    except Exception:
        return False


def _local_match(query):
    """按查询词在本地素材目录匹配: 文件名+各级父目录名 分词后与查询词分词取交集,
    选交集命中词数最多的文件；完全无交集返回 None。"""
    if not has_local():
        return None
    q_tokens = set(re.findall(r"[a-z0-9]+", query.lower()))
    if not q_tokens:
        return None
    best, best_hits = None, 0
    for f in sorted(LOCAL_DIR.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in _VIDEO_SUFFIXES:
            continue
        parts = [f.stem] + [p.name for p in f.parents if p != LOCAL_DIR]
        name_tokens = set()
        for part in parts:
            name_tokens |= set(re.findall(r"[a-z0-9]+", part.lower()))
        hits = len(q_tokens & name_tokens)
        if hits > best_hits:
            best, best_hits = f, hits
    return str(best) if best else None


# ============================================================
# 对外主入口：按查询词取本地素材 / 在线缓存素材文件（全程不抛异常，引擎自动降级）
# ============================================================
def fetch_clip(query):
    """返回素材视频本地路径；失败返回 None。
    优先级: 本地素材目录(手动, 零注册) → 在线下载缓存(Pexels/Pixabay)。
    进程内加锁串行化：多进程渲染时各 worker 独立进程，跨进程靠文件缓存+原子替换兜底。"""
    with _FETCH_LOCK:
        if query in _PROC_CACHE:
            return _PROC_CACHE[query]
        local = _local_match(query)
        if local:
            _PROC_CACHE[query] = local
            return local
        if not is_enabled():
            return None
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        dest = CACHE_DIR / (hashlib.md5(query.encode("utf-8")).hexdigest() + ".mp4")
        if dest.exists() and dest.stat().st_size > 50 * 1024:
            _PROC_CACHE[query] = str(dest)
            return str(dest)
        # 1) Pexels → 2) Pixabay，各自失败静默继续
        url = None
        try:
            url = _pexels_search(query)
        except Exception as e:
            print(f"      [stock] Pexels 搜索失败({str(e)[:80]})")
        if not url:
            try:
                url = _pixabay_search(query)
            except Exception as e:
                print(f"      [stock] Pixabay 搜索失败({str(e)[:80]})")
        if not url:
            print(f"      [stock] 无可用素材({query}), 本次回退兜底底图")
            return None
        try:
            if not _download(url, dest):
                return None
        except Exception as e:
            print(f"      [stock] 素材下载失败({str(e)[:80]})")
            return None
        _PROC_CACHE[query] = str(dest)
        print(f"      [stock] 素材就绪: {query} -> {dest.name}")
        return str(dest)


if __name__ == "__main__":
    # 自检: python stock_footage.py "sunny beach ocean waves"
    sys.stdout.reconfigure(encoding="utf-8")
    q = sys.argv[1] if len(sys.argv) > 1 else "sunny beach ocean waves"
    print(f"is_enabled={is_enabled()}")
    p = fetch_clip(q)
    print(f"clip={p}")
