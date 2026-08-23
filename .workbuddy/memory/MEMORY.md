# 慧根堂短视频流水线 · 项目长期记忆

## 周更内容创作自动化（weekly_pipeline.py）
- 调度：周日23:30 gen-topics(14条) → 周一08:00 select+create-scripts → 每天15:00/20:00 render。
- 周号约定：周日触发的 gen-topics 必须显式 `--week` 落到"周一 week_key() 会命中的那一周"（周日属本周末日、周一入下周），否则周一 select 找不到队列。例：8/23(W34末日)→产出 W35；8/24起为 W35。
- gen-topics 解析：已实现 parse_topics_raw() 直接解析首次 LLM 返回，不再回灌 llm_json 二次生成（旧逻辑偶发只产出 1 条占位）。
- 产物：output/weekly/<W>/queue.json + topics/week_topics.md；确认前 selected=true/confirmed=false，待 `select --keep/--drop` 调整。
- 模型：DeepSeek（enable_search 联网），依赖 model_keys.env 有效 key。

## 账号定位（account_profile.json）
- 老张讲财税：人设张德富/实战派；受众中小企业老板/个体户/创业主；调性口语直给去AI痕迹。
- 痛点池：公转私、虚开发票、个人卡流水、暂估成本、金税四期稽查、社保入税、股东借款、留抵退税。
