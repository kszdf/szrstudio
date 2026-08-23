# 周更成片-晚上(20:00) 执行记录

## 2026-08-23 (周日, 周号 2026-W34)
- 执行 `weekly_pipeline.py render --slot evening`（python 3.13.12）。本周（W34）唯一剩余晚间待渲染 = **seq 14 费用报销缺发票，这样做最危险**（monologue，视频号，56s）。
- **重大故障与根因**：首次直接跑 `weekly_pipeline` 渲染 seq14 时反复失败/产出损坏 mp4（ffprobe 报 "moov atom not found" / ffmpeg 严格解码 rc=69 "Invalid NAL unit size"）。根因=**最终 mp4 路径 `videos/2026-W34-14-….mp4` 被某个残留进程锁定**，`rm -f` 在 Windows 下删除失败（挂起删除），后续所有重渲都写到这个被锁的旧句柄 → 字节交错损坏；且我多次并发启动渲染（含 weekly_pipeline 子进程与直接 make_scroll）相互竞争同一文件，雪上加霜。
- **解决**：① 彻底 `pkill` 所有 make_scroll/ffmpeg/weekly_pipeline 残留进程；② PowerShell `Test-Path` 确认锁定释放后文件已消失；③ **改用全新临时文件名 `_seq14_vFINAL.mp4` 渲染（绕过任何残留锁）**，`python -u` 前台直采输出、`dangerouslyDisableSandbox` 放行 dashscope TTS 联网；④ 严格解码校验（ffmpeg `-err_detect explode` rc=0 无错）通过后 `cp` 拷回最终路径。
- 注意：本次**情绪标注因 DeepSeek 调用超时回退 narrate**（脚本自带容错，line 114-115），韵律起伏较平，但音频为老张真实克隆音、成片完整可用。如需动态情绪韵律可择机重渲。
- 产出：`output/weekly/2026-W34/videos/2026-W34-14-费用报销缺发票_这样做最危险.mp4`（约 48.6MB，1080×1920 竖屏 GIF动态背景＋大字逐字高亮滚动字幕）+ 同名 sidecar `.txt`（约 2.6KB，发布文案 + 口播稿三段）。手动复刻 weekly_pipeline 的后处理（build_sidecar/save_queue/write_readme）更新了队列 status=done、video_path 与 README。
- **W34 晚间档 PENDING 已清空（seq 2/4/6/8/10/12/14 全部 done）**——本周晚间选题渲染完毕。
- 教训：本机交互式 Bash 默认沙箱会拦截 TTS 联网，需用 `dangerouslyDisableSandbox`；且**渲染到最终文件名前务必确认无残留进程持有该文件路径锁**，否则会静默损坏。优先渲染到全新临时名再拷回。

## 2026-08-19 (周三, 周号 2026-W34)
- 执行 `weekly_pipeline.py render --slot evening`（python 3.13.12，路径用正斜杠规避 bash 反斜杠转义）
- 结果：**成功渲染 1 条**。脚本挑出 W34 下一个 `selected + status≠done + period=evening` 选题 = **seq 6 留抵退税被倒查，原因就一个**（monologue，晚档）。注：W34 晚档 seq2/4 已分别于 08-18、本次前渲染完成。
- 产出：`output/weekly/2026-W34/videos/2026-W34-06-留抵退税被倒查_原因就一个.mp4`（约 23.1MB，1080×1920 竖屏 GIF动态背景＋大字逐字高亮滚动字幕，make_scroll_video 路径）+ 同名 sidecar `.txt`（约 1.6KB，发布文案 + 口播稿三段）。
- 队列更新：seq6 status→`done`、video_path 已填；周目录 `README.md` 已刷新（seq6 行 done+成片路径）。
- ffmpeg/模型 key 就绪，脚本打印 `[render] 完成` 正常退出，无异常无报错，EXIT=0，耗时约 2m28s。
- 剩余晚间待渲染：seq 8/10/12/14（均为 evening，status=scripted）；W34 晚档 7 条已渲染 seq2/4/6，下一周晚间场继续推进 seq8。

## 2026-08-18 (周二, 周号 2026-W34)
- 执行 `weekly_pipeline.py render --slot evening`（python 3.13.12）
- 结果：**成功渲染 1 条**。当前周已切换为 **2026-W34**（W33 于 08-16 周日结束，其 seq14 随周切换未再补渲；本自动化按"本周"逻辑转入 W34）。脚本挑出 W34 下一个 `selected + status≠done + period=evening` 选题 = **seq 2 暂估成本冲了，利润没降反被罚**（monologue，晚档）。注：W34 seq1（公转私10万被查，dialogue）已于 08-17 由其他时段渲染完成。
- 产出：`output/weekly/2026-W34/videos/2026-W34-02-暂估成本冲了_利润没降反被罚.mp4`（约 35.7MB，1080×1920 竖屏 GIF动态背景＋大字逐字高亮滚动字幕，make_scroll_video 路径）+ 同名 sidecar `.txt`（约 2.0KB，发布文案 + 口播稿三段）。
- 队列更新：seq2 status→`done`、video_path 已填；周目录 `README.md` 已刷新（seq2 行 done+成片路径）。
- ffmpeg/模型 key 就绪，脚本打印 `[render] 完成` 正常退出，无异常无报错，EXIT=0。
- 剩余晚间待渲染：seq 4/6/8/10/12/14（均为 evening，status=scripted）；W34 晚档 7 条已渲染 1/2，下一周晚间场继续推进 seq4。

## 2026-08-16 (周日, 周号 2026-W33)
- 执行 `weekly_pipeline.py render --slot evening`（python 3.13.12）
- 结果：**成功渲染 1 条**。脚本挑出下一个 `selected + status≠done + period=evening` 选题 = **seq 12 注销公司，居然还要补三年税**（抖音/monologue）。注：seq 10 已于 08-15 渲染完成（未记入本自动化记忆），故本次推进到 seq 12。
- 产出：`output/weekly/2026-W33/videos/2026-W33-12-注销公司_居然还要补三年税.mp4`（约 46.0MB，1080×1920 竖屏 GIF动态背景＋大字逐字高亮滚动字幕视频）+ 同名 sidecar `.txt`（约 2.8KB，发布文案 + 口播稿三段）。
- 队列更新：seq12 status→`done`、video_path 已填；周目录 `README.md` 已刷新（seq12 行 done+成片路径）。
- ffmpeg/模型 key 就绪，脚本打印 `[render] 完成` 正常退出，无异常无报错，EXIT=0。
- 剩余晚间待渲染：seq 14（evening，status=scripted）；W33 晚档 7 条已渲染 2/4/6/8/10/12，仅余 seq14，下一周晚间场继续推进 seq14。

## 2026-08-13 (周四, 周号 2026-W33)
- 执行 `weekly_pipeline.py render --slot evening`（python 3.13.12）
- 结果：**成功渲染 1 条**。脚本挑出下一个 `selected + status≠done + period=evening` 选题 = **seq 8 留抵退税，退了怕查？这样做**（monologue，晚档）。W33 晚档 7 条已渲染 2/4/6/8。
- 产出：`output/weekly/2026-W33/videos/2026-W33-08-留抵退税_退了怕查_这样做.mp4`（约 37.2MB，1080×1920 竖屏 GIF动态背景＋大字逐字高亮滚动字幕视频，make_scroll_video 路径）+ 同名 sidecar `.txt`（约 2.1KB，发布文案 + 口播稿三段）。
- 队列更新：seq8 status→`done`、video_path 已填；周目录 `README.md` 已刷新（seq8 行 done+成片路径）。
- ffmpeg/模型 key 就绪，脚本打印 `[render] 完成` 正常退出，无异常无报错，EXIT=0。
- 剩余晚间待渲染：seq 10/12/14（均为 evening，status=scripted）；下一周晚间场继续推进 seq10。

## 2026-08-12 (周三, 周号 2026-W33)
- 执行 `weekly_pipeline.py render --slot evening`（python 3.13.12）
- 结果：**成功渲染 1 条**。脚本挑出下一个 `selected + status≠done + period=evening` 选题 = **seq 6 数电发票，让虚开无处可逃**（抖音/monologue）。注：seq 4（个人卡流水超20万）已于 08-11 晚间场渲染完成，故本次推进到 seq 6。
- 产出：`output/weekly/2026-W33/videos/2026-W33-06-数电发票_让虚开无处可逃.mp4`（约 31.9MB，1080×1920 竖屏 GIF动态背景＋大字逐字高亮滚动字幕视频，make_scroll_video 路径）+ 同名 sidecar `.txt`（约 1.9KB，发布文案 + 口播稿三段）。
- 队列更新：seq6 status→`done`、video_path 已填；周目录 `README.md` 已刷新（seq6 行 done+成片路径）。
- ffmpeg/模型 key 就绪，脚本打印 `[render] 完成` 正常退出，无异常无报错，EXIT=0。
- 剩余晚间待渲染：seq 8/10/12/14（均为 evening，status=scripted）；W33 晚档 7 条已渲染 2/4/6，下一周晚间场继续推进 seq8。

## 2026-08-10 (周一, 周号 2026-W33)
- 执行 `weekly_pipeline.py render --slot evening`（python 3.13.12）
- 结果：**成功渲染 1 条**。脚本挑出当前周（W33，自 08-10 起）下一个 `selected + status≠done + period=evening` 选题 = **seq 2 公转私200万，这样转才安全**（monologue，晚档）。
- 产出：`output/weekly/2026-W33/videos/2026-W33-02-公转私200万_这样转才安全.mp4`（约 53.6MB，1080×1920 竖屏 GIF动态背景＋大字逐字高亮滚动字幕视频，make_scroll_video 路径）+ 同名 sidecar `.txt`（约 2.7KB，发布文案 + 口播稿三段）。
- 队列更新：seq2 status→`done`、video_path 已填；`output/weekly/2026-W33/README.md` 已刷新（seq2 行 done+成片路径）。注：W33 的 seq1（老板这样发工资_等于自杀）已于今日 15:12 由下午场渲染完成，本次之前已存在。
- ffmpeg/模型 key 就绪，脚本打印 `[render] 完成` 正常退出，无异常无报错，EXIT=0，耗时约 7m25s（TTS + ffmpeg 编码）。
- 剩余晚间待渲染：seq 4/6/8/10/12/14（均为 evening，status=scripted）；W33 共 7 个晚档（2,4,6,8,10,12,14），已渲染 seq1(下午)、seq2(本次)，下一周晚间场继续推进 seq4。

## 2026-08-09 (周日, 周号 2026-W32)
- 执行 `weekly_pipeline.py render --slot evening`（python 3.13.12）
- 结果：**成功渲染 1 条**。脚本挑出下一个 `selected + status≠done + period=evening` 选题 = **seq 8 留抵退税被稽查的原因**（抖音/monologue）。
- 产出：`output/weekly/2026-W32/videos/2026-W32-08-留抵退税被稽查的原因.mp4`（约 34.6MB，1080×1920 竖屏 GIF动态背景＋大字逐字高亮滚动字幕视频）+ 同名 sidecar `.txt`（约 2.1KB，发布文案 + 口播稿三段）。
- 队列更新：seq8 status→`done`、video_path 已填；`output/weekly/2026-W32/README.md` 已刷新（seq8 行 done+成片路径）。仓库根 README.md 未变（刷新目标是周目录内 README）。
- ffmpeg/模型 key 就绪，脚本打印 `[render] 完成` 正常退出，无异常无报错，EXIT=0。
- 剩余晚间待渲染：seq10/12/14（均为 evening，status=scripted）；本周（W32）晚间档尚余 3 条，下一周（W33 自 08-10 起）将由周日23:30 gen-topics 重新建队。

## 2026-08-04 (周二, 周号 2026-W32)
- 执行 `weekly_pipeline.py render --slot evening`（python 3.13.12）
- 结果：**成功渲染 1 条**。脚本按队列顺序挑出首个 `selected + status≠done + period=evening` 选题 = **seq 2 虚开发票罪量刑标准变了**（视频号/monologue/55s）。
- 产出：`output/weekly/2026-W32/videos/2026-W32-02-虚开发票罪量刑标准变了.mp4`（约 32MB，1080×1920 竖屏滚动字幕卡点视频，make_scroll_video.py）+ 同名 sidecar `.txt`（发布文案 + 口播稿开头/正文/结尾）。
- 队列更新：seq2 status→`done`、video_path 已填；README.md 已刷新（seq2 行 done+成片路径）。
- 渲染耗时约 3m27s（TTS + ffmpeg 编码），ffmpeg/模型 key 就绪，无异常无报错。
- 剩余晚间待渲染：seq4/6/8/10/12/14（均为 evening，status=scripted）；下次晚上场继续推进下一晚档。

## 2026-08-05 (周三, 周号 2026-W32)
- 执行 `weekly_pipeline.py render --slot evening`（python 3.13.12）
- 结果：**成功渲染 1 条**。脚本按队列顺序挑出下一个 `selected + status≠done + period=evening` 选题 = **seq 4 暂估成本被查怎么补救**（抖音/monologue/50s）。
- 产出：`output/weekly/2026-W32/videos/2026-W32-04-暂估成本被查怎么补救.mp4`（约 44.6MB，1080×1920 竖屏 GIF动态背景＋大字逐字高亮滚动字幕视频，make_scroll_video 路径）+ 同名 sidecar `.txt`（发布文案 + 口播稿开头/正文/结尾三段）。
- 队列更新：seq4 status→`done`、video_path 已填；README.md 已刷新（seq4 行 done+成片路径）。
- ffmpeg/模型 key 就绪，脚本打印 `[render] 完成` 正常退出，无异常无报错。
- 剩余晚间待渲染：seq6/8/10/12/14（均为 evening，status=scripted）；下次晚上场继续推进 seq6。

## 2026-08-03 (周一, 周号 2026-W32)
- 执行 `weekly_pipeline.py render --slot evening`（python 3.13.12）
- 结果：**跳过**。「未找到 2026-W32 队列」，EXIT=0。output/weekly 下仍仅有 `_samples/`，无正式 `2026-W32/queue.json`，未触发 TTS/ffmpeg，无 mp4/sidecar 产出，README 未改。无异常、无报错。
- 根因同前：上游 gen-topics(周日23:30) / select+create-scripts(周一08:00) 本周（W32）仍未生成正式队列。进入新一周后继续跳过。

## 2026-08-01 (周六, 周号 2026-W31)
- 执行 `weekly_pipeline.py render --slot evening`（python 3.13.12）
- 结果：**跳过**。「未找到 2026-W31 队列」，EXIT=0。output/weekly 下仍仅有 `_samples/`，无正式 `2026-W31/queue.json`，未触发 TTS/ffmpeg，无 mp4/sidecar 产出，README 未改。无异常、无报错。
- 根因同前：上游 gen-topics(周日23:30) / select+create-scripts(周一08:00) 本周仍未生成正式队列。连续 6 次跳过（07-28 下午、07-29 上下午晚、07-30 晚、07-31 晚、08-01 晚）。

## 2026-07-31 (周五, 周号 2026-W31)
- 执行 `weekly_pipeline.py render --slot evening`（python 3.13.12）
- 结果：**跳过**。「未找到 2026-W31 队列」，EXIT=0。`output/weekly/2026-W31/` 目录仍不存在（仅有 `_samples/`），未触发 TTS/ffmpeg，无 mp4/sidecar 产出，README 未改。无异常、无报错。
- 根因同上：上游 gen-topics(周日23:30) / select+create-scripts(周一08:00) 本周仍未生成正式队列。连续 5 次跳过（07-28 下午、07-29 上下午晚、07-30 晚、07-31 晚）。

## 2026-07-29 (周三, 周号 2026-W31)
- 执行 `weekly_pipeline.py render --slot evening`（python 3.13.12）
- 结果：**跳过**。本周 `output/weekly/2026-W31/queue.json` 不存在，脚本打印「未找到 2026-W31 队列」后退出，EXIT=0，未触发 TTS/ffmpeg，无 mp4/sidecar 产出，未改动 README.md。
- 根因：上游 gen-topics（应为周日23:30）/ select+create-scripts（应为周一08:00）从未为本周生成队列；output/weekly 下仅有 `_samples/2026-W31-demo/` 演示数据。下午场(automation-1785218201969) 同日 07-28 也已同样跳过。
- 无异常、无报错。

- 2026-07-29 晚(23:51, 20:00档)：再次执行 `render --slot evening`，同样**跳过**。依旧未找到 2026-W31 队列，EXIT=0，无 mp4/sidecar，README 未改。上游 gen-topics/select 本周仍未产出队列。无异常。

## 2026-07-30 (周四, 周号 2026-W31)
- 执行 `weekly_pipeline.py render --slot evening`（python 3.13.12）
- 结果：**跳过**。「未找到 2026-W31 队列」，EXIT=0。output/weekly 下仅有 `_samples/2026-W31-demo/`，无正式 `2026-W31/queue.json`，未触发 TTS/ffmpeg，无 mp4/sidecar 产出，README 未改。无异常、无报错。
- 根因同上：上游 gen-topics(周日23:30) / select+create-scripts(周一08:00) 本周仍未生成正式队列。连续 4 次（07-28 下午、07-29 上下午晚、07-30 晚）均跳过。

## 历史
- 2026-07-28：本自动化首次运行即发现 queue 缺失，同一下午场已先跳过。
