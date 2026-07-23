# 财税短视频批量生产线（Tax Short-Video Pipeline）

一套面向财税行业老板 / 代账公司的**全自动短视频生产系统**：从选题到数字人成片，一条龙跑完。

核心能力：
- **选题 → 二次改写**：内置财税违禁词红线（抖音 / 视频号 / 快手 / 小红书 / B站），自动规避封号风险；输出「开头 / 正文 / 结尾（钩子）」三段式可编辑文稿。
- **老张声音克隆配音**：阿里 CosyVoice 克隆的"老张"音色，自然度接近真人，财税专业词发音准。
- **本地数字人驱动**：本地部署的 HEYGEM 数字人，用配音音频直接驱动嘴型，去双声，速度快、数据不出域。
- **字幕 / 片头封装**：PIL 逐帧烧录白字黑边字幕 + 拼接品牌片头，产出可直接发布的竖屏短视频。
- **QC 质检**：ffprobe 硬指标把关（分辨率 / 编码 / 单音轨无双声 / 码率）。
- **一键发布文案**：按平台生成标题、话题、钩子文案，并做违禁词复核。
- **Web 控制台**：浏览器打开即用的三栏流水线（改写台 → 出音频 → 选模特 → 出片 → 字幕 → QC → 发布）。

---

## 架构 / 文件说明

| 文件 | 作用 |
|---|---|
| `rewrite_studio.py` / `rewrite_studio.html` | Web 控制台后端（纯标准库 http.server，端口 8385）+ 三栏前端 SPA |
| `content_pipeline.py` | 选题 → 逐字稿 → 二次改写（违禁词红线 + 三段式）全链路 |
| `qwen_tts.py` | 阿里 CosyVoice 老张音色配音 |
| `make_avatar_video.py` | 端到端：千问音频 → HEYGEM 数字人驱动 → 去双声 → 出片 |
| `finalize_v2_pil.py` | PIL 烧字幕 + 拼片头 |
| `forbidden_words.py` / `check_forbidden.py` | 违禁词词库 + 筛查门禁 |
| `build_package.py` | 生成素材包（字幕 / 文案 / 模特建议 / 封面上传位） |
| `daily_pipeline.py` | 每日批量出片 SOP 编排器 |
| `model_providers.py` | 多模型供应商切换（DeepSeek / 千问 文本；阿里 配音） |
| `qc_probe.py` / `qc_fast.py` / `qc_check.py` | QC 质检工具 |
| `make_intro.py` / `overlay_cover_pil.py` | 片头生成 / 封面（用户自传） |
| `clone_cjps.py` / `clone_cjps_v2.py` | 声音克隆脚本（参考样本 → CosyVoice 音色） |
| `SOP_每日出片.md` | 每日出片标准作业流程 |

---

## 依赖

- Docker Desktop（运行本地 HEYGEM 数字人容器，端口 8383）
- Python 3.13（Web 控制台 / 二创 / 配音）；Python 3.10（HEYGEM 网关线）
- 阿里云百炼 `DASHSCOPE_API_KEY`（老张声音克隆 + 配音）
- 文本模型 Key（DeepSeek 或 千问，可切换）
- 静音模特视频：`face2face/BGZSP20260721_t18_silent.mp4`（主力）、`szrsp_silent.mp4`（备选）

> ⚠️ **密钥管理**：所有 API Key 放在本地 `model_keys.env`，**绝不入库**（已在 .gitignore 排除）。部署时自行创建该文件。

---

## 快速开始

```bash
# 1. 安装依赖（受管 Python）
python -m venv venv && source venv/bin/activate
pip install dashscope requests

# 2. 准备密钥
cp model_keys.env.example model_keys.env   # 填入 DASHSCOPE_API_KEY / DEEPSEEK_API_KEY
# （仓库未提供示例，请自行创建，格式：KEY=VALUE，每行一条）

# 3. 启动 HEYGEM 数字人容器（Docker Desktop 先开）
#    heygem-gen-video :8383  /  heygem-tts-old :18180

# 4. 启动 Web 控制台
python rewrite_studio.py
#    浏览器打开 http://localhost:8385
```

---

## 生产流程

选题（生成初稿）→ 改写（实时标红 + 人工改 + 保存）→ 出音频（严格按界面三段文本）→ 选模特 → 出片（HEYGEM 驱动）→ 字幕 → QC → 发布。

- 出片进度在 Web 控制台「出片」步骤卡片实时显示（HEYGEM 真实百分比）。
- 视觉质量（嘴型 / 有无旧字幕残留）由人工肉眼验收，AI 负责音频层与流程正确。

---

## 安全 / 合规

- 本仓库**不含任何 API Key、音频、视频、模型权重**。
- 财税内容经违禁词红线把关，但仍需人工终审后再发布。
- 数字人模特与声音须获得本人授权。
