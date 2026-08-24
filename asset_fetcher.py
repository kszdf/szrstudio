#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
asset_fetcher.py — 政策原文素材采集器（财税短视频 · 证据卡）

把文案里引用的政策（文号 / 名称 / 关键词），自动变成一张可插入视频的
「政策原文证据卡」高清图片，增强财税内容的权威感与可信度。

三级采集策略（按可用性自动降级，绝不静默失败）：
  L1  playwright headless 真实截屏（官网原样截图，段落级定位 + 高亮）
  L2  urllib 抓取官方 HTML 正文 → PIL 渲染「红头文件风」高清图（标题 + 文号 + 条款 + 高亮 + 来源）
  L3  仅返回官方原文 URL（供手动截图）

只采官方白名单域名（版权安全 + 权威）：
  chinatax.gov.cn / mof.gov.cn / gov.cn / 其它 *.gov.cn

设计：
  - 网络用纯标准库 urllib（与 model_providers / server.py 风格一致）
  - playwright / PyMuPDF 为可选依赖，缺失自动降级，不阻塞主流程
  - 联网搜索 soft-import 复用 model_providers（deepseek 联网 / tavily），缺失则降级内置搜索
  - 结果缓存（policy_ref hash），重复引用直接复用
  - 高亮渲染用 PIL（权威源已有）

用法：
  命令行:
    python asset_fetcher.py "国家税务总局公告2024年第5号" --out p.png
  库调用:
    from asset_fetcher import fetch_policy_asset
    asset = fetch_policy_asset("财税〔2024〕15号")
    asset.image_path   # 高清素材图路径
    asset.source_url   # 官方原文 URL
    asset.title        # 政策标题
    asset.clause       # 定位到的条款文本
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path

# ------------------------------------------------------------------ 字体 fallback
def _find_font(size: int):
    """多字体 fallback，保证中文可渲染。"""
    from PIL import ImageFont
    candidates = [
        "C:/Windows/Fonts/msyhbd.ttc",   # 微软雅黑 Bold
        "C:/Windows/Fonts/msyh.ttc",     # 微软雅黑
        "C:/Windows/Fonts/simhei.ttf",   # 黑体
        "C:/Windows/Fonts/simsun.ttc",   # 宋体
    ]
    for fp in candidates:
        if Path(fp).exists():
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    # 沙箱/其它平台兜底：PIL 默认字体（可能无中文，仅用于不崩）
    return ImageFont.load_default()


# ------------------------------------------------------------------ 白名单
OFFICIAL_DOMAINS = (
    "chinatax.gov.cn",   # 国家税务总局
    "mof.gov.cn",        # 财政部
    "gov.cn",            # 中国政府网 + 各级 gov.cn
)

_DOMAIN_RE = re.compile(r"^https?://([^/?#]+)")


def is_official(url: str) -> bool:
    """URL 是否属于官方白名单域名（含各级 gov.cn 子域）。"""
    m = _DOMAIN_RE.match((url or "").strip())
    if not m:
        return False
    host = m.group(1).lower()
    return any(host == d or host.endswith("." + d) for d in OFFICIAL_DOMAINS)


# ------------------------------------------------------------------ 政策引用解析
_DOC_NO_PATTERNS = [
    re.compile(r"(?:国家税务总局|财政部|税务总局|国务院)[^\d]{0,12}?(?:公告|通知|公告|令|发|税总|财)[^\d]{0,6}?\d{4}年第?\d+号"),
    re.compile(r"财税[〔\[]\s*\d{4}\s*[〕\]]\s*\d+\s*号"),
    re.compile(r"税总(?:函|发|公告)?[〔\[]?\s*\d{4}\s*[〕\]]?\s*\d+\s*号"),
    re.compile(r"(?:国发|财关税|财会|财政部|税务总局)[〔\[]?\s*\d{4}\s*[〕\]]?\s*\d+\s*号"),
]


def parse_policy_ref(text: str) -> dict:
    """从文案片段解析政策引用，返回 {doc_no, keywords}。
    未识别出文号时，退化为整段关键词。"""
    t = (text or "").strip()
    doc_no = ""
    for pat in _DOC_NO_PATTERNS:
        m = pat.search(t)
        if m:
            doc_no = m.group(0).strip()
            break
    # 关键词：去掉语气词/标点，取有信息量的片段
    kw = re.sub(r"[，。！？；：、\s]+", " ", t)[:80].strip()
    if doc_no:
        kw = doc_no + " " + kw
    return {"doc_no": doc_no, "keywords": kw or (doc_no or t[:40])}


# ------------------------------------------------------------------ 联网搜索官方 URL
def _search_deepseek(policy_ref: dict, timeout: int = 40) -> list[dict]:
    """复用 model_providers 的 DeepSeek 联网搜索，返回官方 URL 候选。"""
    try:
        from model_providers import deepseek_chat, get_text_config  # soft import
        cfg = get_text_config()
        doc_no = policy_ref.get("doc_no", "")
        search_q = (doc_no + " " + policy_ref["keywords"]) if doc_no else policy_ref["keywords"]
        prompt = (
            f"你是一名政府政策检索专家。请联网搜索中国官方政府网站（国家税务总局 chinatax.gov.cn / "
            f"财政部 mof.gov.cn / 中国政府网 gov.cn），找到「{search_q}」的**政策正文原文页**。\n"
            f"要求：\n"
            f"1. URL 必须指向政策正文页（页面含具体发文文号、条款正文），严禁返回首页、新闻列表、政策目录页；\n"
            f"2. 页面标题必须含政策名称或文号，与检索主题直接相关；\n"
            f"3. 只返回 3 个最相关的正文页。\n"
            f'严格只输出 JSON 数组，每项 {{"url":"...","title":"..."}}，不要其它内容。'
        )
        raw = deepseek_chat(prompt, cfg["model"], cfg["key"], cfg.get("base_url"),
                            timeout=timeout, enable_search=True, return_meta=True)
        if isinstance(raw, dict):
            content = raw.get("content") or ""
            search_results = raw.get("search_results") or []
        else:
            content = raw or ""
            search_results = []
        # 优先用 content 里模型判断的「最相关」URL，search_results 作兜底
        out = []
        arr = _extract_json(content)
        if isinstance(arr, list):
            out = [{"url": it["url"], "title": it.get("title", "")}
                   for it in arr if isinstance(it, dict) and is_official(it.get("url", ""))]
        for sr in search_results:
            if isinstance(sr, dict) and sr.get("url") and is_official(sr["url"]):
                out.append({"url": sr["url"], "title": sr.get("title", "")})
        # 去重（按 url）
        seen, dedup = set(), []
        for it in out:
            if it["url"] not in seen:
                seen.add(it["url"])
                dedup.append(it)
        return dedup
    except Exception:
        return []


def _search_tavily(policy_ref: dict, timeout: int = 30) -> list[dict]:
    try:
        from model_providers import tavily_search, get_key  # soft import
        key = get_key("TAVILY_API_KEY")
        if not key:
            return []
        res = tavily_search(policy_ref["keywords"] + " 政策 原文 site:gov.cn",
                            key, topic="finance", days=0, max_results=6, timeout=timeout)
        out = []
        for r in (res.get("results") or []):
            if is_official(r.get("url", "")):
                out.append({"url": r["url"], "title": r.get("title", "")})
        return out
    except Exception:
        return []


def search_official(policy_ref: dict | str, timeout: int = 40) -> list[dict]:
    """搜索政策官方原文 URL。合并 DeepSeek 联网 + Tavily 两个来源（都收集、去重）。
    修复：此前只取第一个有结果的来源就返回，DeepSeek 常给出已失效的旧链
    （www.chinatax.gov.cn 旧 content.html 多已 404），导致所有候选全挂降级 L3；
    合并后 Tavily 的实时活链可作为兜底候选。"""
    ref = parse_policy_ref(policy_ref) if isinstance(policy_ref, str) else policy_ref
    out: list[dict] = []
    seen: set[str] = set()
    for fn in (_search_deepseek, _search_tavily):
        try:
            hits = fn(ref, timeout)
        except Exception:
            continue
        for h in hits or []:
            url = (h or {}).get("url") or ""
            if url and url not in seen:
                seen.add(url)
                out.append(h)
    return out


# ------------------------------------------------------------------ 抓取 + 正文提取
def _http_get(url: str, timeout: int = 30) -> tuple[str, str]:
    """返回 (html, final_url)。http 失败时自动重试 https（gov 站点常强制 https）。
    编码：优先 Content-Type charset，其次 HTML meta charset，兜底 utf-8。
    修复：国税总局等站点为 GBK/GB2312 编码，硬编码 utf-8 会把标题解成乱码。"""
    candidates = [url]
    if url.startswith("http://"):
        candidates.append("https://" + url[len("http://"):])
    last_err = None
    for u in candidates:
        try:
            req = urllib.request.Request(
                u, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                charset = None
                ct = resp.headers.get("Content-Type") or ""
                m = re.search(r"charset=([\w-]+)", ct, re.I)
                if m:
                    charset = m.group(1).strip().strip('"').strip("'")
                if not charset:
                    m = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', raw[:4096], re.I)
                    if m:
                        charset = m.group(1).decode("ascii", "ignore")
                enc = (charset or "utf-8").lower()
                if enc in ("gb2312", "gbk", "gb18030"):
                    enc = "gb18030"   # gb18030 是 gbk/gb2312 的超集，兼容三者
                return raw.decode(enc, errors="replace"), resp.geturl()
        except Exception as e:
            last_err = e
    raise last_err


def extract_article(html_text: str) -> dict:
    """从 HTML 提取 {title, paragraphs}。零依赖，宽容解析。"""
    t = html_text or ""
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", t, re.S | re.I)
    if m:
        title = html_lib.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
    # 去除脚本/样式
    t = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", t, flags=re.S | re.I)
    # 段落：<p>/<div> 文本
    _NAV_NOISE = ("首页", "返回", "版权", "登录", "注册", "导航", "联系我们",
                  "网站地图", "无障碍", "English", "繁体", "邮箱", "电话")
    paras = []
    for pm in re.finditer(r"<(?:p|li|div)[^>]*>(.*?)</(?:p|li|div)>", t, re.S | re.I):
        seg = html_lib.unescape(re.sub(r"<[^>]+>", "", pm.group(1))).strip()
        seg = re.sub(r"[\u3000\s]+", " ", seg)
        if len(seg) >= 8 and not any(n in seg for n in _NAV_NOISE):   # 过滤导航/页脚
            paras.append(seg)
    if not paras:
        # 兜底：全文去标签
        body = html_lib.unescape(re.sub(r"<[^>]+>", " ", t))
        body = re.sub(r"[\u3000\s]+", " ", body).strip()
        if body:
            paras = [body]
    return {"title": title, "paragraphs": paras}


def locate_clause(paragraphs: list[str], keywords: str) -> str:
    """在段落中定位最相关的条款段落。无任何关键词/条款特征匹配时返回空（判定页面不相关）。"""
    if not paragraphs:
        return ""
    kws = [k for k in re.split(r"[\s，。；、]+", keywords or "") if len(k) >= 2]
    best, best_score = "", 0
    for p in paragraphs:
        score = sum(1 for k in kws if k in p)
        # 条款特征：带"第X条/第X款/公告"或数字，加分
        if re.search(r"第[一二三四五六七八九十百\d]+条|第[一二三四五六七八九十百\d]+款|自\d{4}", p):
            score += 1
        if score > best_score:
            best, best_score = p, score
    if best_score == 0:
        return ""   # 页面内容与政策无关，让上游换候选 URL
    # 太长截断（证据卡只展示核心一条）
    if len(best) > 180:
        best = best[:180] + "……"
    return best


_GENERIC_WORDS = {"公告", "通知", "文件", "政策", "关于", "规定", "办法", "条例", "法规", "解读", "公告"}
# 官方行文用词差异：检索口语（如"小微"）与原文（如"小型微利"）的宽松等价对
_FUZZY_PAIRS = (("小微", "小型微利"),)


def _kw_hit(kw: str, text: str) -> bool:
    """宽松关键词命中：精确包含，或按官方行文差异对宽松匹配。"""
    if kw in text:
        return True
    for short, full in _FUZZY_PAIRS:
        if short in kw and full in text:
            return True
        if full in kw and short in text:
            return True
    return False


def _is_relevant(title: str, ref: dict) -> bool:
    """判断抓到的页面标题是否与政策引用相关（文号精确匹配，或 ≥min(2, n) 个非通用关键词命中）。
    修复：单关键词（如"小微企业所得税优惠政策"）要求 ≥2 命中永远失败 → 降级为 ≥1；
    并支持"小微/小型微利"等官方行文差异的宽松匹配。"""
    t = title or ""
    doc_no = ref.get("doc_no", "")
    if doc_no and doc_no in t:
        return True
    kws = [k for k in re.split(r"[\s，。；、]+", ref.get("keywords", ""))
           if len(k) >= 2 and k not in _GENERIC_WORDS]
    if not kws:
        return True   # 无有效关键词时不拦截
    need = min(2, len(kws))
    return sum(1 for k in kws if _kw_hit(k, t)) >= need


# ------------------------------------------------------------------ PIL 渲染「政策条款证据卡」
def render_clause_card(title: str, doc_no: str, clause: str, source_url: str,
                       keywords: str = "", size: tuple = (1080, 1920),
                       out_path: str | None = None) -> str:
    """PIL 渲染一张「红头文件风」政策条款证据卡，返回图片路径。
    布局：白底 + 红字标题 + 灰色文号 + 分隔线 + 条款正文（关键词高亮）+ 底部来源。"""
    from PIL import Image, ImageDraw, ImageFont

    W, H = size
    MARGIN = 80
    RED = (192, 32, 32)
    DARK = (40, 40, 40)
    GRAY = (120, 120, 120)
    HILITE_BG = (255, 244, 179)
    HILITE_EDGE = (255, 170, 40)

    img = Image.new("RGB", (W, H), (250, 250, 248))
    d = ImageDraw.Draw(img)

    def wrap(text: str, font, max_w: int) -> list[str]:
        lines, cur = [], ""
        for ch in text:
            if ch == "\n":
                lines.append(cur); cur = ""; continue
            test = cur + ch
            if d.textbbox((0, 0), test, font=font)[2] > max_w:
                lines.append(cur); cur = ch
            else:
                cur = test
        if cur:
            lines.append(cur)
        return lines

    y = 140
    # 标题（红，居中，最多 3 行）
    f_title = _find_font(72)
    for ln in wrap(title or "政策原文", f_title, W - MARGIN * 2)[:3]:
        d.text(((W - d.textbbox((0, 0), ln, font=f_title)[2]) // 2, y), ln,
               font=f_title, fill=RED)
        y += 90
    # 文号（灰）
    if doc_no:
        f_no = _find_font(44)
        d.text(((W - d.textbbox((0, 0), doc_no, font=f_no)[2]) // 2, y), doc_no,
               font=f_no, fill=GRAY)
        y += 70
    # 分隔线
    y += 30
    d.rectangle([MARGIN, y, W - MARGIN, y + 3], fill=RED)
    y += 60

    # 条款正文（高亮关键词）
    f_body = _find_font(52)
    body_lines = wrap(clause or "（未能定位到条款原文，请见来源链接）", f_body, W - MARGIN * 2)
    # 高亮命中关键词的行
    kws = [k for k in re.split(r"[\s，。；、]+", keywords or "") if len(k) >= 2]
    for ln in body_lines[:16]:
        # 画整行淡黄底 + 命中词进一步标红边
        bb = d.textbbox((MARGIN, y), ln, font=f_body)
        hit = any(k in ln for k in kws)
        if hit:
            d.rectangle([MARGIN - 12, y - 6, bb[2] + 12, bb[3] + 6], fill=HILITE_BG, outline=HILITE_EDGE, width=2)
        d.text((MARGIN, y), ln, font=f_body, fill=DARK)
        y += 82
        if y > H - 300:
            break

    # 底部来源
    f_src = _find_font(36)
    domain = _DOMAIN_RE.match(source_url or "")
    src_txt = f"来源：{source_url or '—'}   （{domain.group(1) if domain else '官方' }）"
    for ln in wrap(src_txt, f_src, W - MARGIN * 2)[:2]:
        d.text((MARGIN, H - 140), ln, font=f_src, fill=GRAY)
        break

    if out_path is None:
        out_path = str(Path(tempfile_dir()) / "policy_card.png")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path


def tempfile_dir() -> Path:
    return Path(__file__).resolve().parent / "_asset_cache"


# ------------------------------------------------------------------ 结果对象
@dataclass
class PolicyAsset:
    image_path: str | None = None
    source_url: str = ""
    title: str = ""
    clause: str = ""
    doc_no: str = ""
    cached: bool = False
    level: str = "L3"            # L1 截屏 / L2 渲染 / L3 仅URL
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ------------------------------------------------------------------ 主入口
def fetch_policy_asset(policy_ref: str, size: tuple = (1080, 1920),
                       cache_dir: str | None = None, timeout: int = 40) -> PolicyAsset:
    """根据政策引用抓取官方原文素材。三级降级，带缓存。"""
    ref = parse_policy_ref(policy_ref)
    cache_dir = Path(cache_dir) if cache_dir else tempfile_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.md5(ref["keywords"].encode("utf-8")).hexdigest()[:16]
    meta_p = cache_dir / f"policy_{key}.json"
    img_p = cache_dir / f"policy_{key}.png"

    # 命中缓存
    if meta_p.exists() and img_p.exists():
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            asset = PolicyAsset(**{k: meta[k] for k in meta if k in PolicyAsset.__dataclass_fields__})
            asset.cached = True
            asset.image_path = str(img_p)
            return asset
        except Exception:
            pass

    # 搜索官方 URL
    hits = search_official(ref, timeout)
    if not hits:
        return PolicyAsset(level="L3", note="未检索到官方原文链接，请提供政策文号或更准确名称",
                           source_url="")

    top = hits[0]
    url, title = top["url"], top.get("title", "") or ref["keywords"]

    asset = PolicyAsset(source_url=url, title=title, doc_no=ref.get("doc_no", ""))
    last_err = ""

    # 逐个尝试候选 URL（搜索返回的 URL 可能已失效/404，依次降级到下一条）
    for cand in hits:
        url = cand.get("url", "")
        if not url:
            continue
        asset.title = cand.get("title", "") or title
        asset.source_url = url

        # L1 尝试 playwright 真实截屏（可选依赖）
        img = _try_playwright_capture(url, ref["keywords"], cache_dir, key)
        if img:
            asset.image_path = img
            asset.level = "L1"
            break
        # L2 urllib 抓正文 + PIL 渲染
        try:
            html_text, final_url = _http_get(url, timeout)
            art = extract_article(html_text)
            if not _is_relevant(art["title"], ref):
                last_err = f"页面不相关：{art['title'][:40]}"
                continue
            asset.title = art["title"] or asset.title
            asset.source_url = final_url or url
            clause = locate_clause(art["paragraphs"], ref["keywords"])
            asset.clause = clause
            if clause:
                asset.image_path = render_clause_card(asset.title, ref.get("doc_no", ""),
                                                      clause, asset.source_url, ref["keywords"],
                                                      size=size, out_path=str(img_p))
                asset.level = "L2"
                break
        except Exception as e:
            last_err = str(e)
            continue

    if not asset.image_path:
        asset.level = "L3"
        asset.note = last_err or "仅返回官方原文链接，请手动打开截图"

    meta_p.write_text(json.dumps(asset.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return asset


def _try_playwright_capture(url: str, keywords: str, cache_dir: Path, key: str) -> str | None:
    """L1：playwright headless 真实截屏（可选，缺失返回 None）。"""
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception:
        return None
    out = cache_dir / f"policy_{key}_shot.png"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 1600})
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            # 尝试滚到含关键词的文本，再整页截屏
            if keywords:
                try:
                    page.get_by_text(re.compile(re.escape(keywords.split()[0][:6])), exact=False).first.scroll_into_view_if_needed(timeout=3000)
                except Exception:
                    pass
            page.screenshot(path=str(out), full_page=False)
            browser.close()
        if Path(out).exists():
            return str(out)
    except Exception:
        pass
    return None


def _extract_json(text: str):
    """宽容提取 JSON（对象或数组）。"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    m = re.search(r"(\[.*\]|\{.*\})", t, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    return None


# ------------------------------------------------------------------ CLI
def main():
    import argparse
    ap = argparse.ArgumentParser(description="政策原文素材采集器")
    ap.add_argument("policy", help="政策引用（文号/名称/关键词）")
    ap.add_argument("--out", default=None, help="输出图片路径（默认自动命名）")
    ap.add_argument("--cache-dir", default=None)
    args = ap.parse_args()
    asset = fetch_policy_asset(args.policy, cache_dir=args.cache_dir)
    print(json.dumps(asset.to_dict(), ensure_ascii=False, indent=2))
    if asset.image_path and args.out:
        import shutil
        shutil.copy(asset.image_path, args.out)
        print(f"已复制到 {args.out}")


if __name__ == "__main__":
    main()
