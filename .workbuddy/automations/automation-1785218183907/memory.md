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
