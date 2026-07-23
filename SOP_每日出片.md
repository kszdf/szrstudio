# 每日批量出片 SOP（老张讲财税短视频矩阵）

> 目标：日更 5–10 条质量标准短视频，全链路闭环、违禁词门禁、先审改再出片。
> 主脚本：`gpt_sovits/daily_pipeline.py`（两段式：draft 草稿 → produce 生产）

## 前置条件（一次就绪）
- [x] Docker Desktop 已安装，HEYGEM 容器 `heygem-gen-video`(8383) 能起（先开 Docker 再跑）
- [x] 环境变量 `DASHSCOPE_API_KEY`（百炼 Key，文本+千问TTS 共用）
- [x] ffmpeg 在 `D:/ffmpeg/ffmpeg-8.1.2-full_build/bin`
- [x] 违禁词库 `forbidden_words.py` 已就位（建议每季度人工复核）

## 每日流程

### Phase A — 草稿 + 门禁（上午，产出可编辑定稿）
```bash
cd D:/heygem_data/gpt_sovits
python daily_pipeline.py draft --topics topics_当日.txt --date 20260723
```
- 自动：选题 → 逐字稿 → 二次改写（**违禁词红线+三段式**）→ 定稿 `.md` + 违禁词检查 `.md`
- 输出：`qwen_out/<date>/<NNN>/03_逐字稿定稿.md`（结构：`=== 开头 ===` / `=== 正文 ===` / `=== 结尾（钩子） ===`）
- **你来做**：打开每个 `03_逐字稿定稿.md` 审改（开头/正文/结尾），确认 `03_违禁词检查.md` 无高危

### Phase B — 生产（审改后，一键出片）
```bash
# 先预演（不需 Docker/Key，核对门禁与计划）
python daily_pipeline.py produce --date 20260723 --dry-run

# 实跑（需 Docker + Key）
python daily_pipeline.py produce --date 20260723
# 单条重出：python daily_pipeline.py produce --date 20260723 --item 003
```
- 自动逐条：TTS出音频 → 素材包(字幕/文案/封面位) → HEYGEM 出片 → QC
- **门禁**：定稿仍含高危违禁词 → 自动跳过该条，不生产
- 产出：
  - 视频：`output/<date>/avatar_<NNN>.mp4`
  - 素材包：`qwen_out/<date>/pkg/<NNN>/`（audio.wav / subtitle.ass / publish.md / model_hint.txt / cover_upload_here.txt）

### 发布前（你来做）
1. 肉眼验收成片：嘴型自然度 / 无绿嘴 / 无旧字幕残留（AI 看不到图）
2. 做封面 `cover.png` 放进各 `pkg/<NNN>/`（系统只留上传位，不自动生成）
3. 发布文案若有高危违禁词（produce 会报警），改 `publish.md` 的 caption 再发
4. 手动分发上传各平台（自动分发受合规限制）

## 复查 / 报告
```bash
python daily_pipeline.py report --date 20260723   # 对已有成品重跑 QC + 门禁
python check_forbidden.py qwen_out/<date>/pkg/<NNN>/publish.md   # 单查发布文案
```

## 文件布局
```
gpt_sovits/
  daily_pipeline.py        编排器（draft / produce / report）
  content_pipeline.py      选题→逐字稿→二次改写→TTS（三段式+红线）
  build_package.py         素材包生成（字幕/文案/封面位）
  make_avatar_video.py     HEYGEM 出片（千问音频驱动+去双声+字幕+片头）
  forbidden_words.py       违禁词库 + 筛查 + 红线提示词
  check_forbidden.py       筛查 CLI
  qwen_out/<date>/<NNN>/   03_逐字稿定稿.md + 03_违禁词检查.md + 04_音频.wav
  qwen_out/<date>/pkg/<NNN>/  发布素材包
output/<date>/avatar_<NNN>.mp4   成品
```

## 注意
- 002 历史片 `avatar_002.mp4` 仍含旧文本"最影响"（已改源头 03，需重渲 002 才彻底干净）
- 模特默认 `BGZSP20260721_t18_silent.mp4`（稳）；换模特用 `--model /code/data/xxx.mp4`
- 当前会话无 Key，draft/produce 实跑需在含 Key 环境执行；`--dry-run` 任何环境可预演
