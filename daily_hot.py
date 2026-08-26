# -*- coding: utf-8 -*-
"""
daily_hot.py — 每日热点·双题材选题 + 爆款方案创作

题材（只保留两类）:
  1) finance 财税直接相关（政策/稽查/申报/合规/案例/税率等）
  2) event   重大热点事件（社会/经济/民生热点，附"财税切入角度"）

数据源（公开接口，失败自动跳过）:
  - 微博热搜 weibo.com/ajax/side/hotSearch
  - 百度热搜 top.baidu.com/board?tab=realtime
  - 今日头条热榜 toutiao.com/hot-event/hot-board
  - 兜底: tavily 检索（抖音热榜等需签名，不可靠，用检索替代）

流程: 抓榜 → LLM 双题材分类过滤 → 对入选选题生成爆款方案 → 落盘 JSON 缓存

用法:
  D:/heygem/py310/Scripts/python.exe daily_hot.py [--finance-top 4] [--event-top 4] [--out 文件.json]
  import daily_hot; daily_hot.run_daily()   # 返回结构化 dict
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
DEFAULT_OUT = Path(os.environ.get("HOT_DAILY_OUT", r"D:\heygem_data\runtime-logs\daily_hot.json"))


def _http_json(url, headers=None, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _http_text(url, headers=None, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


# ============================== 抓榜 ==============================
def fetch_weibo(limit=30):
    """微博热搜: data.realtime[].word/num。"""
    j = _http_json("https://weibo.com/ajax/side/hotSearch",
                   headers={"Referer": "https://weibo.com/"})
    out = []
    for it in (j.get("data") or {}).get("realtime", [])[:limit]:
        w = (it.get("word") or "").strip()
        if w:
            out.append({"title": w, "heat": str(it.get("num") or ""),
                        "source": "微博热搜", "url": "https://s.weibo.com/weibo?q=" + urllib.parse.quote(w)})
    return out


def fetch_baidu(limit=30):
    """百度热搜: 页面 JSON 里 'word':'...'（'topContent'）。"""
    html = _http_text("https://top.baidu.com/board?tab=realtime")
    words = re.findall(r'"word":"([^"]+)"', html)
    seen, out = set(), []
    for w in words:
        if w and w not in seen:
            seen.add(w)
            out.append({"title": w, "heat": "", "source": "百度热搜",
                        "url": "https://www.baidu.com/s?wd=" + urllib.parse.quote(w)})
        if len(out) >= limit:
            break
    return out


def fetch_toutiao(limit=30):
    """今日头条热榜: data[].Title。"""
    j = _http_json("https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc")
    out = []
    for it in (j.get("data") or [])[:limit]:
        t = (it.get("Title") or "").strip()
        if t:
            out.append({"title": t, "heat": str(it.get("HotValue") or ""),
                        "source": "头条热榜", "url": it.get("Url") or ""})
    return out


def fetch_all(per_source=30):
    """汇总多平台热榜, 按标题去重(保留首个来源)。"""
    agg, seen = [], set()
    fetchers = [("微博热搜", fetch_weibo), ("百度热搜", fetch_baidu), ("头条热榜", fetch_toutiao)]
    for name, fn in fetchers:
        try:
            items = fn(per_source)
            for it in items:
                key = re.sub(r"[\s【】\[\]\(\)（）·•，,。！!？?]", "", it["title"])
                if key and key not in seen:
                    seen.add(key)
                    agg.append(it)
            print(f"  [抓榜] {name}: {len(items)} 条", flush=True)
        except Exception as e:
            print(f"  [抓榜] {name} 失败: {str(e)[:80]}", flush=True)
    return agg


# ============================== LLM ==============================
def _llm(prompt, timeout=120, retries=2):
    from model_providers import ensure_env, get_text_config, deepseek_chat
    ensure_env()
    cfg = get_text_config()
    last = None
    for _ in range(retries + 1):
        try:
            return deepseek_chat(prompt, cfg["model"], cfg["key"], cfg.get("base_url"), timeout=timeout)
        except Exception as e:
            last = e
            time.sleep(2)
    raise RuntimeError(f"LLM 调用失败: {last}")


def _parse_json(raw):
    raw = (raw or "").strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.M).strip()
    try:
        return json.loads(raw)
    except Exception:
        # 兜底: 从夹杂文本中抽出 JSON 对象/数组
        for pat in (r"\{.*\}", r"\[.*\]"):
            m = re.search(pat, raw, flags=re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except Exception:
                    continue
        raise


def classify(items):
    """LLM 双题材分类: 只保留 finance / event, 过滤无关, 附财税切入角度。"""
    listing = "\n".join(f"{i}. [{it['source']}] {it['title']}" for i, it in enumerate(items))
    prompt = (
        "你是财税短视频的选题主编。下面是今天各大平台热榜汇总(含来源)。\n"
        "请把每条分类，只保留两类题材：\n"
        "1) category=\"finance\"：财税**直接相关**（税收/发票/稽查/申报/汇算/社保/会计处理/企业合规/税率/政策等）\n"
        "2) category=\"event\"：重大热点事件（社会/经济/民生/财经大事），并给出**财税切入角度**（该事件能引出什么财税话题）\n"
        "其余一律 category=\"other\"（不入选）。\n"
        "要求：宁可少选，不要硬凑；event 必须真是有热度的大事件且能自然切到财税。\n"
        "严格输出 JSON 数组（不要解释、不要 markdown 代码块）：\n"
        '[{"title":"原题","source":"微博热搜","category":"finance|event|other","reason":"分类理由(≤20字)","angle":"财税切入角度(仅event需要, ≤30字; finance可空)"}]\n\n'
        f"【今日热榜汇总】\n{listing}\n"
    )
    raw = _llm(prompt)
    data = _parse_json(raw)
    if not isinstance(data, list):
        raise ValueError("分类结果不是数组")
    kept = [d for d in data if isinstance(d, dict) and d.get("category") in ("finance", "event")]
    print(f"  [分类] 汇总 {len(items)} 条 -> 财税 {sum(1 for d in kept if d['category']=='finance')} 条, "
          f"重大热点 {sum(1 for d in kept if d['category']=='event')} 条", flush=True)
    return kept


def viral_plan(topic, category):
    """爆款方案: 标题/角度/钩子/结构/留资/发布建议。"""
    prompt = (
        "你是财税行业短视频爆款策划。基于选题生成一份可直接出片的爆款方案。\n"
        f"题材类型: {'财税直接相关' if category == 'finance' else '重大热点事件的财税切入'}\n"
        f"选题: {topic.get('title')}（来源: {topic.get('source')}）\n"
        f"财税切入角度(如有): {topic.get('angle') or ''}\n"
        "要求：口语化、戳老板痛点、不写绝对化违禁词（最/第一/保证/稳赚等）、结尾留资自然。\n"
        "严格输出 JSON（不要解释、不要 markdown 代码块）：\n"
        '{"title":"成片标题(≤18字,戳痛点)","hook_type":"痛点直击/悬念提问/反常识/数据冲击/身份共鸣/蹭热点",'
        '"hook_line":"开头钩子(1句,口语化)","structure":["段1要点","段2要点","段3要点","段4收口"],'
        '"cta":"结尾留资钩子(1句,引导评论/私信)","publish_tip":"发布建议(标题党克制, 挂什么话题标签, ≤30字)"}\n'
    )
    raw = _llm(prompt)
    # 解析失败重试一次(LLM 自纠错), 避免偶发未转义引号导致整条丢
    plan = None
    last_err = ""
    for attempt in range(2):
        try:
            plan = _parse_json(raw if attempt == 0 else _llm(prompt))
            break
        except Exception as e:
            last_err = str(e)[:80]
            time.sleep(1)
    if not isinstance(plan, dict):
        raise ValueError(f"方案不是对象: {last_err}")
    return plan


def fetch_finance_supplement(need=4):
    """热榜里财税直接相关不足时, 用 tavily 检索最新财税政策/稽查案例补齐。"""
    try:
        from model_providers import get_key, tavily_search
    except Exception:
        return []
    key = get_key("TAVILY_API_KEY")
    if not key:
        return []
    queries = ["税务总局 政策 公告 最新", "税务稽查 案例 最新", "金税四期 企业 风险 最新",
               "增值税 汇算清缴 新政", "企业 涉税 风险 新闻"]
    out, seen = [], set()
    for q in queries:
        try:
            rsp = tavily_search(q, key, topic="general", days=7, max_results=4, timeout=10) or {}
            for r in (rsp.get("results") or []):
                t = (r.get("title") or "").strip()
                # 标题是网址/过短时, 用摘要前段代替
                if not t or t.startswith("http") or len(t) < 6:
                    c = re.sub(r"\s+", " ", (r.get("content") or "")).strip()
                    t = c[:36]
                key2 = re.sub(r"[\s【】\[\]\(\)（）·•，,。！!？?]", "", t)
                if t and key2 not in seen and not any(x in t for x in ("直播", "课程", "培训", "报名")):
                    seen.add(key2)
                    out.append({"title": t, "heat": "", "source": "财税检索",
                                "url": r.get("url") or ""})
        except Exception:
            continue
        if len(out) >= need:
            break
    if out:
        print(f"  [补充] 财税检索 {len(out)} 条", flush=True)
    return out[:need]


def run_daily(finance_top=4, event_top=4, per_source=30, out=None):
    """抓榜 -> 分类(+财税检索补充) -> 爆款方案 -> 落盘。返回结构化 dict。"""
    print("[1/3] 抓取平台热榜 ...", flush=True)
    items = fetch_all(per_source)
    if not items:
        raise RuntimeError("所有平台热榜抓取失败")
    print(f"[2/3] LLM 双题材分类过滤({len(items)} 条) ...", flush=True)
    kept = classify(items)
    finance = [k for k in kept if k["category"] == "finance"][:finance_top]
    event = [k for k in kept if k["category"] == "event"][:event_top]
    # 财税直接相关不足 → tavily 检索补齐
    if len(finance) < finance_top:
        for it in fetch_finance_supplement(finance_top - len(finance)):
            finance.append({"title": it["title"], "source": it["source"], "category": "finance",
                            "reason": "财税最新动态检索", "angle": "", "url": it.get("url", "")})
    print(f"[3/3] 爆款方案创作({len(finance)} 财税 + {len(event)} 重大热点) ...", flush=True)
    plans = []
    for k in finance + event:
        try:
            plan = viral_plan(k, k["category"])
            plans.append({**k, "plan": plan})
            print(f"  [方案] [{k['category']}] {k['title'][:24]} -> {plan.get('title', '')[:24]}", flush=True)
        except Exception as e:
            print(f"  [方案] 失败({k['title'][:20]}): {str(e)[:80]}", flush=True)
    result = {
        "date": time.strftime("%Y-%m-%d"),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sources": ["微博热搜", "百度热搜", "头条热榜"],
        "raw_count": len(items),
        "finance": [p for p in plans if p["category"] == "finance"],
        "event": [p for p in plans if p["category"] == "event"],
    }
    out_path = Path(out) if out else DEFAULT_OUT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已落盘: {out_path}", flush=True)
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="每日热点·双题材选题 + 爆款方案")
    ap.add_argument("--finance-top", type=int, default=4, help="财税直接相关条数")
    ap.add_argument("--event-top", type=int, default=4, help="重大热点事件条数")
    ap.add_argument("--per-source", type=int, default=30, help="每平台取前N条")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    res = run_daily(args.finance_top, args.event_top, args.per_source, args.out)
    print(json.dumps(res, ensure_ascii=False, indent=2))
