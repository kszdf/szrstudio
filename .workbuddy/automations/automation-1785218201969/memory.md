# 周更成片-下午(15:00) 执行记录

## 2026-08-16 (周日, 周号 2026-W33)
- 执行 `weekly_pipeline.py render --slot afternoon`
- 结果：**成功渲染 1 条**（非跳过）。本周 `output/weekly/2026-W33/queue.json` 存在；当前周日(day=6)下午档命中 seq13（老板的钱这样走出去，最省心，dialogue 双声）。
- 产出：`output/weekly/2026-W33/videos/2026-W33-13-老板的钱这样走出去_最省心.mp4`（约 25.6MB，1080x1920 竖屏 GIF动态背景大字逐字高亮）；同名 sidecar `2026-W33-13-老板的钱这样走出去_最省心.txt`（含发布文案+口播稿）。
- 队列 seq13 status 已置为 `done`、video_path 回填；per-week `output/weekly/2026-W33/README.md` 已刷新（seq13 显示 done+成片路径）。脚本自动确保 ffmpeg 与模型 key 就绪，无异常，EXIT=0。
- **下午档 7 条（seq1/3/5/7/9/11/13）现已全部 done**；待渲染下午选题列表为空。本周仅剩晚间档 seq14（金税四期下，小规模别再这样开票，monologue，day6 evening）待 20:00 自动化渲染。

## 2026-08-09 (周日, 周号 2026-W32)
- 执行 `weekly_pipeline.py render --slot afternoon`
- 结果：**成功渲染 1 条**（非跳过）。今日周日(day=6)下午档命中 seq13（老板给员工发工资的坑，dialogue 双声）。
- 产出：`videos/2026-W32-13-老板给员工发工资的坑.mp4`（约 18.8MB，1080x1920 竖屏 GIF动态背景大字逐字高亮）；同名 sidecar `2026-W32-13-老板给员工发工资的坑.txt`（含发布文案+口播稿）。
- 队列 seq13 status 已置为 `done`、video_path 回填；README.md 已刷新。**下午档 7 条（seq1/3/5/7/9/11/13）现已全部 done。**
- ⚠️ 修复项：首次运行报错 `voice_id 为空`（qwen_tts 抛错）。根因=今天 03:00 daily backup 提交(30abbb0)把 `make_scroll_video.py` 的 `MALE_VOICE/FEMALE_VOICE` 改成空（"新租户初始无自带声音"），且 `rewrite_studio.py` 的 `SCROLL_MALE/FEMALE_VOICE` 同样为空；`weekly_pipeline.render` 调 `make_scroll_video.py` 时未传 `--male-voice/--female-voice`，对话稿 TTS 拿不到音色。已在 `weekly_pipeline.py` 的 `cmd_render` 中显式传入老张/江老师克隆音色 id（优先读 env `QWEN_MALE/FEMALE_VOICE_ID`，缺则回退 documented 默认值），重跑成功 EXIT=0。注：工作台「一键出片」目前仍传空音色，存在同样隐患，待用户从「声音」页配置或恢复默认值。
- 晚间档(20:00 自动化)仍会渲染 seq14（税务局怎么发现你隐匿收入，monologue）；本档为本周最后一条待渲染。

## 2026-08-06 (周四, 周号 2026-W32)
- 执行 `weekly_pipeline.py render --slot afternoon`
- 结果：**成功渲染 1 条**（非跳过）。本周 `output/weekly/2026-W32/queue.json` 存在；当前周四(day=3)下午档命中 seq7（股东借款年底不还的后果，dialogue 双声）。
- 产出：`videos/2026-W32-07-股东借款年底不还的后果.mp4`（约 12.3MB，1080x1920 竖屏 GIF动态背景大字逐字高亮）；同名 sidecar `2026-W32-07-股东借款年底不还的后果.txt`（含发布文案+口播稿）。
- 队列 seq7 status 已置为 `done`、video_path 回填；README.md 已刷新（seq7 显示 done+成片路径）。脚本自动确保 ffmpeg 与模型 key 就绪，无异常，EXIT=0。
- 下午档已渲染完成计数=4（seq1/3/5/7）；剩余下午档待渲染：seq9/11/13（day 4-6）。

## 2026-08-05 (周三, 周号 2026-W32)
- 执行 `weekly_pipeline.py render --slot afternoon`
- 结果：**成功渲染 1 条**（非跳过）。本周 `output/weekly/2026-W32/queue.json` 存在；当前周三(day=2)下午档命中 seq5。
- 产出：`videos/2026-W32-05-金税四期最怕老板什么.mp4`（约 15.1MB，1080x1920 竖屏 GIF动态背景大字逐字高亮）；同名 sidecar `2026-W32-05-金税四期最怕老板什么.txt`（含发布文案+口播稿）。
- 队列 seq5 status 已置为 `done`、video_path 回填；README.md 已刷新（seq5 显示 done+成片路径）。脚本自动确保 ffmpeg 与模型 key 就绪，无异常，EXIT=0。
- 下午档已渲染完成计数=3（seq1/3/5）；剩余下午档待渲染：seq7/9/11/13（day 3-6）。

## 2026-08-04 (周二, 周号 2026-W32)
- 执行 `weekly_pipeline.py render --slot afternoon`
- 结果：**成功渲染 1 条**（非跳过）。本周 `output/weekly/2026-W32/queue.json` 存在（14 选题，seq1 已 done）。今日周二(day=1)下午档命中 seq3。
- 产出：`videos/2026-W32-03-老板个人卡流水多大危险.mp4`（约 24.2MB，1080x1920 竖屏动态背景大字高亮）；同名 sidecar `2026-W32-03-老板个人卡流水多大危险.txt`（含发布文案+口播稿）。
- 队列 seq3 status 已置为 `done`、video_path 回填；README.md 已刷新（seq3 显示 done+成片路径）。脚本自动确保 ffmpeg 与模型 key 就绪，无异常，EXIT=0。
- 剩余下午档待渲染：seq5/7/9/11/13（day 2-6）。

## 2026-08-03 (周一, 周号 2026-W32)
- 执行 `weekly_pipeline.py render --slot afternoon`
- 结果：**成功渲染 1 条**（非跳过）。本周 `output/weekly/2026-W32/queue.json` 已生成（14 选题，全部 selected/scripted）。当前周一(day=0)下午档命中 seq1。
- 产出：`videos/2026-W32-01-公转私这样转才安全.mp4`（约 17.5MB，1080x1920 竖屏动态背景大字高亮）；同名 sidecar `2026-W32-01-公转私这样转才安全.txt`（含发布文案+口播稿）。
- 队列 seq1 status 已置为 `done`、video_path 回填；README.md 已刷新（seq1 显示 done+成片路径）。脚本自动确保 ffmpeg 与模型 key 就绪，无异常，EXIT=0。
- 剩余下午档待渲染：seq3/5/7/9/11/13（day 1-6）。

## 2026-08-01 (周六, 周号 2026-W31)
- 执行 `weekly_pipeline.py render --slot afternoon`
- 结果：**跳过**。本周 `output/weekly/2026-W31/queue.json` 仍不存在（目录仅 `_samples/`，gen-topics 队列从未生成/落盘），脚本打印「未找到 2026-W31 队列」后退出，EXIT=0，未触发 TTS/ffmpeg。
- 无待渲染选题，无异常。连续第 5 天（07-28→08-01）因队列缺失跳过——gen-topics 上游任务疑似从未成功执行或落盘，建议排查周日 23:30 gen-topics 自动化是否缺失/报错。

## 2026-07-29 (周三, 周号 2026-W31)
- 执行 `weekly_pipeline.py render --slot afternoon`
- 结果：**跳过**。本周 `output/weekly/2026-W31/queue.json` 仍不存在（gen-topics 队列未生成/未落盘），脚本打印「未找到 2026-W31 队列」后退出，EXIT=0，未触发 TTS/ffmpeg。
- 无待渲染选题，无异常。

## 2026-07-28 (周二, 周号 2026-W31)
- 执行 `weekly_pipeline.py render --slot afternoon`
- 结果：**跳过**。本周 `output/weekly/2026-W31/queue.json` 不存在（gen-topics 队列未生成），脚本打印「未找到 2026-W31 队列」后退出，EXIT=0，未触发 TTS/ffmpeg。
- 无待渲染选题，无异常。

## 2026-07-30 (周四, 周号 2026-W31)
- 执行 `weekly_pipeline.py render --slot afternoon`
- 结果：**跳过**。本周 `output/weekly/2026-W31/queue.json` 仍不存在（目录仅有 `_samples/`，gen-topics 队列未生成/未落盘），脚本打印「未找到 2026-W31 队列」后退出，EXIT=0，未触发 TTS/ffmpeg。
- 无待渲染选题，无异常。

## 2026-07-31 (周五, 周号 2026-W31)
- 执行 `weekly_pipeline.py render --slot afternoon`
- 结果：**跳过**。本周 `output/weekly/2026-W31/queue.json` 仍不存在（目录仅 `_samples/`，gen-topics 队列未生成/未落盘），脚本打印「未找到 2026-W31 队列」后退出，EXIT=0，未触发 TTS/ffmpeg。
- 无待渲染选题，无异常。连续第 4 天（07-28→07-31）因队列缺失跳过，建议排查 gen-topics 是否需手动触发或上游任务缺失。

## 2026-08-02 (周日, 周号 2026-W31)
- 执行 `weekly_pipeline.py render --slot afternoon`
- 结果：**跳过**。本周 `output/weekly/2026-W31/queue.json` 仍不存在（目录仅 `_samples/`，gen-topics 队列从未生成/落盘），脚本打印「未找到 2026-W31 队列」后退出，EXIT=0，未触发 TTS/ffmpeg。
- 无待渲染选题，无异常。连续第 6 天（07-28→08-02）因队列缺失跳过——gen-topics 上游任务疑似从未成功执行或落盘，建议排查周日 23:30 gen-topics 自动化是否缺失/报错，或手动触发一次 gen-topics 生成本周队列。

## 2026-08-10 (周一, 周号 2026-W33)
- 执行 `weekly_pipeline.py render --slot afternoon`
- 结果：**成功渲染 1 条**（非跳过）。本周 `output/weekly/2026-W33/queue.json` 已生成（14 选题，全部 selected/scripted）。当前周一(day=0)下午档命中 seq1（老板这样发工资，等于自杀，dialogue 双声）。
- 产出：`output/weekly/2026-W33/videos/2026-W33-01-老板这样发工资_等于自杀.mp4`（约 24.5MB，1080x1920 竖屏 GIF动态背景大字逐字高亮）；同名 sidecar `2026-W33-01-老板这样发工资_等于自杀.txt`（含发布文案+口播稿）。
- 队列 seq1 status 已置为 `done`、video_path 回填；per-week `output/weekly/2026-W33/README.md` 已刷新（脚本 write_readme 写的是周目录内的 README，非项目根 handbook）。脚本自动确保 ffmpeg 与模型 key 就绪，无异常，EXIT=0。
- 剩余下午档待渲染：seq3/5/7/9/11/13（day 1-6）。注：脚本视频落盘路径为 `output/weekly/<wk>/videos/`，非根 `videos/` 目录（任务描述"videos/ 目录"为泛称，以脚本实际路径为准）。

## 2026-08-11 (周二, 周号 2026-W33)
- 执行 `weekly_pipeline.py render --slot afternoon`
- 结果：**成功渲染 1 条**（非跳过）。当前周二(day=1)下午档命中 seq3（暂估成本，金税四期一查一个准，dialogue 双声）。
- 产出：`output/weekly/2026-W33/videos/2026-W33-03-暂估成本_金税四期一查一个准.mp4`（约 19.1MB，1080x1920 竖屏 GIF动态背景大字逐字高亮）；同名 sidecar `2026-W33-03-暂估成本_金税四期一查一个准.txt`（含发布文案+口播稿）。
- 队列 seq3 status 已置为 `done`、video_path 回填；per-week `output/weekly/2026-W33/README.md` 已刷新。脚本自动确保 ffmpeg 与模型 key 就绪，无异常，EXIT=0。
- 剩余下午档待渲染：seq5/7/9/11/13（day 2-6）。

## 2026-08-14 (周五, 周号 2026-W33)
- 执行 `weekly_pipeline.py render --slot afternoon`
- 结果：**成功渲染 1 条**（非跳过）。本周 `output/weekly/2026-W33/queue.json` 存在；当前周五(day=4)下午档命中 seq9（老板不懂法，公司钱就是私钱，dialogue 双声）。
- 产出：`output/weekly/2026-W33/videos/2026-W33-09-老板不懂法_公司钱就是私钱.mp4`（约 16.7MB，1080x1920 竖屏 GIF动态背景大字逐字高亮）；同名 sidecar `2026-W33-09-老板不懂法_公司钱就是私钱.txt`（含发布文案+口播稿）。
- 队列 seq9 status 已置为 `done`、video_path 回填；per-week `output/weekly/2026-W33/README.md` 已刷新（seq9 显示 done+成片路径）。脚本自动确保 ffmpeg 与模型 key 就绪，无异常，EXIT=0。
- 剩余下午档待渲染：seq11/13（day 5-6，周六/周日 15:00 自动化继续）。

## 2026-08-15 (周六, 周号 2026-W33)
- 执行 `weekly_pipeline.py render --slot afternoon`
- 结果：**成功渲染 1 条**（非跳过）。本周 `output/weekly/2026-W33/queue.json` 存在；当前周六(day=5)下午档命中 seq11（个税汇算3月启动，这些老板要补税，dialogue 双声）。
- 产出：`output/weekly/2026-W33/videos/2026-W33-11-个税汇算3月启动_这些老板要补税.mp4`（约 17.8MB / 18,694,353字节，1080x1920 竖屏 GIF动态背景大字逐字高亮）；同名 sidecar `2026-W33-11-个税汇算3月启动_这些老板要补税.txt`（1342字节，含发布文案+口播稿）。
- 队列 seq11 status 已置为 `done`、video_path 回填；per-week `output/weekly/2026-W33/README.md` 已刷新（脚本 write_readme 写周目录内 README）。脚本自动确保 ffmpeg 与模型 key 就绪，无异常，EXIT=0。
- 剩余下午档待渲染：seq13（day 6，周日 15:00 自动化继续）。注：seq5/7 此前已 done（queue 已确认），故今日直接命中 seq11。

## 2026-08-17 (周一, 周号 2026-W34)
- 执行 `weekly_pipeline.py render --slot afternoon`（EXIT=0）。
- 结果：**成功渲染 1 条**（非跳过）。本周W34队列已生成（14选题，全部selected）。当前周一(day=0)下午档命中 seq1（公转私10万被查，错在哪，dialogue 双声）。
- 产出：`output/weekly/2026-W34/videos/2026-W34-01-公转私10万被查_错在哪.mp4`（16,408,998字节≈16MB，1080x1920竖屏 GIF动态背景大字逐字高亮）；同名 sidecar `videos/2026-W34-01-公转私10万被查_错在哪.txt`（1259字节，含发布文案+口播稿）。
- 队列 seq1 status 已置为 `done`、`video_path` 回填；per-week `output/weekly/2026-W34/README.md` 已刷新（seq1 显示 done+成片路径）。脚本自动确保 ffmpeg 与模型 key 就绪，无异常。
- 剩余下午档待渲染：seq3/5/7/9/11/13（day 1-6，周二至周日 15:00 自动化继续）。

## 2026-08-18 (周二, 周号 2026-W34)
- 执行 `weekly_pipeline.py render --slot afternoon`（EXIT=0）。
- 结果：**成功渲染 1 条**（非跳过）。本周W34队列已生成（14选题，seq1/2/3 已 done）。当前周二(day=1)下午档命中 seq3（个人卡流水过大，这样解释没用，dialogue 双声）。
- 产出：`output/weekly/2026-W34/videos/2026-W34-03-个人卡流水过大_这样解释没用.mp4`（16,954,575字节≈16.9MB，1080x1920竖屏 GIF动态背景大字逐字高亮）；同名 sidecar `videos/2026-W34-03-个人卡流水过大_这样解释没用.txt`（1345字节，含发布文案+口播稿）。
- 队列 seq3 status 已置为 `done`、`video_path` 回填；per-week `output/weekly/2026-W34/README.md` 已刷新。脚本自动确保 ffmpeg 与模型 key 就绪，无异常。
- 剩余下午档待渲染：seq5/7/9/11/13（day 2-6，周三至周日 15:00 自动化继续）。

## 2026-08-20 (周四, 周号 2026-W34)
- 执行 `weekly_pipeline.py render --slot afternoon`（EXIT=0）。
- 结果：**成功渲染 1 条**（非跳过）。本周W34队列已生成（14选题，seq1/3 已 done）。当前周四(day=3)下午档命中 seq7（社保入税后，按最低基数缴行吗，dialogue 双声）。
- 产出：`output/weekly/2026-W34/videos/2026-W34-07-社保入税后_按最低基数缴行吗.mp4`（25,735,043字节≈25.7MB，1080x1920竖屏 GIF动态背景大字逐字高亮）；同名 sidecar `videos/2026-W34-07-社保入税后_按最低基数缴行吗.txt`（1665字节，含发布文案+口播稿）。
- 队列 seq7 status 已置为 `done`、`video_path` 回填；per-week `output/weekly/2026-W34/README.md` 已刷新。脚本自动确保 ffmpeg 与模型 key 就绪，无异常。
- 剩余下午档待渲染：seq9/11/13（day 4-6，周五至周日 15:00 自动化继续）。

## 2026-08-21 (周五, 周号 2026-W34)
- 执行 `weekly_pipeline.py render --slot afternoon`（EXIT=0）。
- 结果：**成功渲染 1 条**（非跳过）。本周W34队列已生成（14选题，seq1/3/5/7 已 done）。当前周五(day=4)下午档命中 seq9（企业注销了，税务还能查你，dialogue 双声）。
- 产出：`output/weekly/2026-W34/videos/2026-W34-09-企业注销了_税务还能查你.mp4`（25,012,456字节≈25.0MB，1080x1920竖屏 GIF动态背景大字逐字高亮）；同名 sidecar `videos/2026-W34-09-企业注销了_税务还能查你.txt`（1676字节，含发布文案+口播稿）。
- 队列 seq9 status 已置为 `done`、`video_path` 回填；per-week `output/weekly/2026-W34/README.md` 已刷新（seq9 显示 done+成片路径）。脚本自动确保 ffmpeg 与模型 key 就绪，无异常。
- 剩余下午档待渲染：seq11/13（day 5-6，周六/周日 15:00 自动化继续）。

## 2026-08-22 (周六, 周号 2026-W34)
- 执行 `weekly_pipeline.py render --slot afternoon`（EXIT=0）。
- 结果：**成功渲染 1 条**（非跳过）。本周W34队列已生成（14选题，seq1/3/5/7/9 已 done）。当前周六(day=5)下午档命中 seq11（老板低价卖房给公司，有风险吗，dialogue 双声）。
- 产出：`output/weekly/2026-W34/videos/2026-W34-11-老板低价卖房给公司_有风险吗.mp4`（18,521,054字节≈18.5MB，1080x1920竖屏 GIF动态背景大字逐字高亮）；同名 sidecar `videos/2026-W34-11-老板低价卖房给公司_有风险吗.txt`（1241字节，含发布文案+口播稿）。
- 队列 seq11 status 已置为 `done`、`video_path` 回填；per-week `output/weekly/2026-W34/README.md` 已刷新（seq11 显示 done+成片路径）。脚本自动确保 ffmpeg 与模型 key 就绪，无异常。
- 剩余下午档待渲染：seq13（往来款挂账三年，税务盯上了，day 6，周日 15:00 自动化继续）。

## 历史
- （暂无更早记录）
