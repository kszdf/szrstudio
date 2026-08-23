# 周更选题生成 (automation-1785218183907) 执行记忆

## 2026-08-03 (周一 01:34, 实际触发晚于周日23:30约1.5h)
- 周号：2026-W32（8/3–8/9）
- 结果：成功生成 14 条爆款选题，写入 output/weekly/2026-W32/queue.json + topics/week_topics.md
- dialogue/monologue 各 7 条均衡；相关度 85–98；全部 selected=true, confirmed=false（待周一08:00前 keep/drop）
- 异常/改动：发现 account_profile.json 历来只有 bg 字段，缺失 persona/audience/tone/hit_topics 等账号定位字段，导致选题提示词注入 None。已补全（保留原 bg），使 gen-topics 基于真实「老张讲财税」定位产出。
- 无运行时报错。用 3.13.12 python + deepseek-v4-flash(enable_search) 联网检索生成。

## 2026-08-09 (周日 23:25 触发)
- 周号：2026-W33（8/10–8/16，下一周）。
- 关键决策：周日 23:30 落在 W32 末日（8/9 仍属 W32），但周一 08:00 的 select/create-scripts 用 week_key() 会落在 W33。故本步显式 `--week 2026-W33` 产出，避免与上周 W32 冲突/覆盖，且保证周一步骤能找到队列。
- 结果：成功生成 14 条，dialogue 7 / monologue 7 均衡；相关度 90–99；全部 selected=true, confirmed=false（待周一 08:00 前 keep/drop）。
- 产物：output/weekly/2026-W33/queue.json + topics/week_topics.md。无运行时异常。
- 模型：deepseek-v4-flash（enable_search 联网），key 有效。

## 2026-08-16 (周日 23:25 触发)
- 周号：2026-W34（8/17–8/23，下一周）。
- 关键决策：周日 8/16 落 W33 末日，周一 8/17 起为 W34；为让周一 08:00 select/create-scripts 用 week_key() 能命中队列，显式 `--week 2026-W34` 产出（沿用 8/9 同款决策）。
- 结果：成功生成 14 条，dialogue 7 / monologue 7 均衡；相关度 80–100；全部 selected=true, confirmed=false（待周一 08:00 前 keep/drop）。
- 产物：output/weekly/2026-W34/queue.json + topics/week_topics.md。无运行时异常。
- 模型：deepseek（enable_search 联网），key 有效。

## 2026-08-23 (周日 23:25 触发)
- 周号：2026-W35（8/24–8/30，下一周）。
- 关键决策：周日 8/23 落 W34 末日，周一 8/24 起为 W35；沿用历史，显式 `--week 2026-W35` 产出，保证周一 08:00 的 select/create-scripts(week_key()=W35) 能命中队列。
- 异常修复：首次 gen-topics 仅产出 1 条占位（title="选题1"），根因为 cmd_gen_topics 把首次 LLM 回复回灌 llm_json 二次生成，本次模型对二次输入只回吐 1 条残缺对象。已在脚本新增 parse_topics_raw() 直接解析首次返回（不再回灌），重跑后稳定产出 14 条。
- 结果：14 条，dialogue 7（1,3,5,7,9,11,13）/ monologue 7（2,4,6,8,10,12,14）均衡；相关度 86–99；全部 selected=true, confirmed=false（待周一 08:00 前 keep/drop）。
- 产物：output/weekly/2026-W35/queue.json + topics/week_topics.md（覆盖旧的 1 条占位）。
