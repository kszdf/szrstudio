# -*- coding: utf-8 -*-
"""白板式布局生成器: 财税内容 → 白板布局 JSON(标题 + 要点卡片 + 警示)。

布局结构:
  {"title": "主标题(≤10字)", "warn": "底部警示(≤12字, 可空)",
   "items": [{"main": "要点(≤8字)", "sub": "说明(≤12字)", "num": "序号或关键数字(≤6字)"}]}

成熟度把关: 流程/概念/对比类 → 白板图解; 法条类不白板化(保精确)。
"""
import json
import sys

sys.path.insert(0, r"D:\heygem_data\gpt_sovits")
from model_providers import get_text_config, deepseek_chat

TEMPLATE = """你是财税科普「白板图解」导演。把下面的财税知识点提炼成一块教学白板的内容(线条手绘风格)。
要求:
- title: 主标题(≤10字, 一句话点题)
- items: 2~4 个要点卡片, 每个含:
    main: 要点主文字(≤8字)
    sub: 一行说明(≤12字, 讲清这个要点)
    num: 序号或关键数字(如"1"/"45天"/"2万", ≤6字)
    icon: 匹配内容的手绘图标(从 ledger账本/calendar日历/stamp印章/coin钱币/check对勾/warn警示/calculator计算器/flag旗帜 选一, 最贴合这个要点)
- warn: 底部一句警示/提醒(≤12字, 可空串)
- 内容忠实原稿, 数字精确(如"45天"不要写成"40多天")
- 只输出 JSON 对象, 不要多余文字:
{{"title":"...", "warn":"...", "items":[{{"main":"...","sub":"...","num":"...","icon":"..."}}]}}
内容: {text}"""


def generate(text):
    cfg = get_text_config()
    raw = deepseek_chat(TEMPLATE.format(text=text), cfg["model"], cfg["key"],
                        cfg.get("base_url"), timeout=90)
    content = (raw or "").strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content[:4].lower() == "json":
            content = content[4:]
    data = json.loads(content) if content.startswith("{") else None
    if not data:
        raise ValueError(f"白板布局解析失败: {content[:200]}")
    return {
        "title": (data.get("title") or "财税知识")[:10],
        "warn": (data.get("warn") or "")[:14],
        "items": [{"main": (it.get("main") or "")[:8],
                   "sub": (it.get("sub") or "")[:12],
                   "num": (it.get("num") or str(i + 1))[:6],
                   "icon": it.get("icon", "")}
                  for i, it in enumerate(data.get("items") or [])][:4],
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('用法: make_whiteboard_storyboard.py "内容"')
    print(json.dumps(generate(sys.argv[1]), ensure_ascii=False, indent=2))
