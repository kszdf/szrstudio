# 慧根堂·财税智造 SaaS 短视频生产工作台 — 开发手册（AI Handoff）

> 本文档面向**接手本项目的人工智能 / 开发者**。读完即可在 30 分钟内理解系统全貌、所用模型与云服务、本地部署方式，并知道二次开发该改哪个文件、踩过哪些坑。
> 配套概览见 [`README.md`](./README.md)。

---

## 0. 一句话定位

**本地优先**的财税行业短视频**全自动生产线 Web 控制台**：从选题 → 二次改写 → 配音 → 本地数字人出片 → 字幕 → QC → 发布文案，一条龙跑完。数据不出本机（数字人引擎跑在本地 Docker），仅文本/配音调用云 API。

品牌名：**慧根堂·财税智造**（SaaS 形态，面向 B 端财税老板获客）。

---

## 1. 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│  浏览器 SPA  (rewrite_studio.html, 三栏/九节点流水线)         │
│  端口 http://localhost:8385  ←──HTTP JSON──→                  │
└─────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│  后端  rewrite_studio.py  (纯标准库 http.server, 端口 8385)   │
│  路由见 §6；调用下游：                                        │
│   ├─ 文本写稿   → DeepSeek / 通义千问  (model_providers.py)   │
│   ├─ 配音       → 阿里云百炼 CosyVoice3  (qwen_tts.py)        │
│   ├─ 联网检索   → DeepSeek enable_search / Tavily (可选)      │
│   └─ 出片       → 本地 Docker HEYGEM (make_avatar_video.py)   │
└─────────────────────────────────────────────────────────────┘
                               │
              ┌────────────────┴───────────────┐
              ▼                                 ▼
┌──────────────────────┐          ┌──────────────────────────────┐
│ 云 API（SaaS 调用）   │          │ 本地 Docker（算力/数据不出域） │
│ - api.deepseek.com   │          │ - heygem-gen-video :8383       │
│ - dashscope.aliyuncs │          │ - heygem-tts-old   :18180      │
│ - api.tavily.com     │          │ 静音模特: face2face/*.mp4      │
└──────────────────────┘          └──────────────────────────────┘

产物落盘： output/audio、output/video、output/pkg（字幕/文案/封面位）
```

**关键认知**：本项目**没有独立云服务器**。所有"云"仅指上述 SaaS API 调用（文本/配音/检索）。算力、存储、数字人引擎全在用户本机（Windows + Docker Desktop + 本地 Python）。"数据不出域"是核心卖点，二次开发时不要引入会把视频/音频上传第三方的逻辑（除非用户明确要接第三方数字人，见 §8）。

---

## 2. 模型与云服务清单（重点）

| 用途 | 服务商 | 模型 / 接口 | Key 配置 | 状态 | 说明 |
|---|---|---|---|---|---|
| 文本写稿（主） | **DeepSeek** | `deepseek-v4-flash` @ `api.deepseek.com` (OpenAI 兼容) | `DEEPSEEK_API_KEY` | ✅ 接好 | `model_providers.get_text_config()` 自动优先选它 |
| 文本写稿（备） | **阿里通义千问** | `qwen-turbo` @ `dashscope.aliyuncs.com` | `DASHSCOPE_API_KEY` | ✅ 接好 | 无 DeepSeek key 时自动回退 |
| 声音克隆 / 配音 | **阿里云百炼** | CosyVoice3 `cosyvoice-v3-plus` | `DASHSCOPE_API_KEY` | ✅ 接好 | 锁定"老张"克隆音色（`qwen_tts.DEFAULT_VOICE_ID`）；DeepSeek 无 TTS，配音必须用阿里 |
| 数字人驱动 / 出片 | **本地 HEYGEM**（Docker） | 容器内 `/easy/submit` + `/easy/query` | 无 | ✅ 接好 | 非云；用配音音频驱动嘴型，去双声 |
| 联网检索（可选） | **Tavily** | `api.tavily.com/search` | `TAVILY_API_KEY` | ⚠️ 可选 | 替代 DeepSeek 联网；`topic=finance` 限定财税 |
| 联网检索（内置） | **DeepSeek** | `enable_search=true` | `DEEPSEEK_API_KEY` | ✅ 可用 | 在 `deepseek_chat()` 里开；返回 `search_results` 证据 |
| 候选数字人（未接） | 硅基 / HeyGen / 火山 | — | — | ❌ 框架预留 | `thirdparty_avatar.py` 并联模块已搭，未填 key |

**Key 管理铁律**：所有 key 放本地 `model_keys.env`（格式 `KEY=VALUE`，中文注释），**绝不入库**（`.gitignore` 已排除 `*.env *key* *secret*`）。代码用 `model_providers.ensure_env()` 把文件 key 灌进环境变量，下游 SDK 自动识别。**改完 .env 必须重启服务**（缓存在 `_FILE_CACHE`）。

---

## 3. 环境铁律（部署 / 二次开发必读）

1. **先开 Docker Desktop** → `heygem-gen-video:8383` 与 `heygem-tts-old:18180` 自动 up（约 12 秒就绪）。出片前置，否则 HEYGEM 调用必失败。
2. **Python 版本分工**：Web 控制台 / 二创 / 配音用 **3.13**；HEYGEM 网关线（旧 ffmpeg）用 **3.10**。混用会导致修改不生效。
3. **中文 API 提交**：用 python `urllib` / `requests`，**绝不用 PowerShell `Invoke-RestMethod`**（汉字变 `?`）。
4. **静音模特视频须有音频流**（HEYGEM 旧 ffmpeg 要求），用 ffmpeg 生成静音音轨杜绝原声污染。
5. **finalize_v2_pil 必须 `--replace-audio` 千问音频**：PIL 抽帧重编码会丢原音轨，否则成品静音 → concat 报 `Stream specifier :a matches no streams` → 0 字节。
6. **双声根治**：HEYGEM `-r.mp4` 自带 TTS 音轨，`ffmpeg -an` 剥离再 mux 用户音（音频驱动 / TTS 克隆两模式都生效）。
7. **绿嘴**：`docker restart heygem-gen-video`；轮询"任务不存在"= 完成去取 `face2face/temp/{code}-r.mp4`。
8. **带硬字幕剪辑视频不能当模特**（字进成片）；可用静音模特：`BGZSP20260721_t18_silent.mp4`（主力）、`szrsp`（备选）。
9. **本机 Read 图片被过滤**：视觉质量（嘴型 / 字幕残留 / 旧字幕）只能用户肉眼验收，AI 仅保音频层与流程正确。

---

## 4. 快速启动

```bash
# 0. 前置：Docker Desktop 已启动，确认容器在跑
docker ps   # 应见 heygem-gen-video、heygem-tts-old

# 1. 密钥（仓库未提供示例，自行创建 model_keys.env）
#    格式：KEY=VALUE，每行一条，可加 # 注释
#    DEEPSEEK_API_KEY=sk-xxx
#    DASHSCOPE_API_KEY=sk-xxx
#    TAVILY_API_KEY=xxx        # 可选

# 2. 依赖（受管 Python 3.13）
python -m venv venv && source venv/bin/activate
pip install dashscope requests

# 3. 启动 Web 控制台（端口 8385）
python rewrite_studio.py
#    浏览器打开 http://localhost:8385
```

> 出片长任务由后端起线程跑 `make_avatar_video.py`（用 Python 3.10）。

---

## 5. 目录结构与核心模块

| 文件 | 行数 | 作用 |
|---|---|---|
| `rewrite_studio.py` | ~1305 | 后端 HTTP 服务（纯标准库 `http.server`，端口 8385），九节点流水线 API |
| `rewrite_studio.html` | ~2294 | 前端 SPA：顶部横向 node-nav（9 节点）+ 两栏（左主区 / 右产物区），内联 `<style>`+`<script>` |
| `content_pipeline.py` | ~218 | 选题 → 逐字稿 → 二次改写（违禁词红线 + 三段式），可 `--no-audio` 先审稿 |
| `qwen_tts.py` | ~135 | 阿里 CosyVoice 老张音色配音（`DEFAULT_VOICE_ID` / `DEFAULT_MODEL`） |
| `model_providers.py` | ~191 | 多模型供应商统一入口（DeepSeek / 千问 文本；阿里 配音）；key 读取优先级：环境变量 > `.env` |
| `make_avatar_video.py` | ~161 | 端到端：千问音频 → HEYGEM `/easy/submit` → 轮询 → 去双声 → mux → finalize 出片 |
| `finalize_v2_pil.py` | ~267 | PIL 逐帧烧字幕（多字体 fallback）+ 拼品牌片头 |
| `forbidden_words.py` | ~16k | 违禁词词库（抖音/视频号/快手/小红书/B站）+ 筛查 `scan()` / `build_guidance()` / `format_report()` |
| `check_forbidden.py` | ~423 | 命令行筛查入口 |
| `build_package.py` | ~223 | 生成素材包（字幕 / 文案 / 模特建议 / 封面上传位） |
| `daily_pipeline.py` | ~223 | 每日批量出片 SOP 编排器 |
| `clone_cjps.py` / `clone_cjps_v2.py` | — | 声音克隆脚本（参考样本 → CosyVoice 音色） |
| `thirdparty_avatar.py` | — | 第三方数字人并联框架（预留，未填 key） |
| `SOP_每日出片.md` | — | 每日出片标准作业流程 |

> HEYGEM 原始运行文件（`api.py` `api_v2.py` `Dockerfile` `docker-compose.yaml` `config.py` `download_*.py` `install*.sh` `fonts/` 等）是数字人引擎所需，保留以便复现环境。

---

## 6. 后端 API 路由清单（`rewrite_studio.py`）

**GET**
| 路由 | 说明 |
|---|---|
| `/api/projects` | 项目列表 |
| `/api/guidance` | 各步骤操作指引文案 |
| `/api/models` | 模特列表（含缩略图 URL） |
| `/api/thirdparty/info` | 第三方数字人供应商信息 |
| `/api/model_thumb/(name)` | 模特缩略图 |
| `/api/audio/(name)` | 音频文件 |
| `/api/video/(name)` | 视频文件 |
| `/api/job/(id)` | 出片任务进度 |
| `/api/queue` | 批量队列 |
| `/api/project/(name)` | 项目详情 |
| `/api/project/(name)/(qc\|subtitle\|publish)` | 项目 QC / 字幕 / 发布预览 |

**POST**
| 路由 | 说明 |
|---|---|
| `/api/models/upload` | 上传模特（multipart `file`） |
| `/api/check` | 违禁词检查 |
| `/api/generate` | 直接生成二创稿（不联网） |
| `/api/topic_search` | 联网找爆款（`mode=list` 列候选 / `mode=create` 生成） |
| `/api/new` | 新建项目 |
| `/api/queue` (POST/DELETE) | 加入 / 清空队列 |
| `/api/queue/remove` `/api/queue/move` | 队列项删除 / 移动 |
| `/api/project/(name)/(save\|tts\|publish-check\|render\|publish\|account)` | 保存三段稿 / 出音频 / 发布复核 / 出片 / 发布 / 账号信息 |

---

## 7. 二次开发上手（改哪、踩过哪些坑）

**改前端样式**
- 文件：`rewrite_studio.html` 内联 `<style>`。
- ⚠️ **CSS 优先级坑（曾耗半天才找到）**：`.studio-view.active{display:block}` 优先级 `(0,0,2,0)` 压过 `main{display:grid}` `(0,0,0,1)`，导致 `main` 实际是 `block`，**两栏布局从建站起从未真正生效**。修法：用 `main.studio-view.active{display:grid}`（优先级 `(0,0,2,1)`）。改任何涉及 `.studio-view` / `main` 的 display 都要先想优先级。
- 响应式断点：`@1023px` 以下退化为单列（加 📦 产物预览分割头）；1024+ 保持两栏。验证布局务必用 `getComputedStyle(main).display` 实测，别只信视觉（headless 截图可能误报）。

**改写稿提示词 / 风格**
- 文件：`content_pipeline.py` 顶部 `STYLE_GUIDE`（老张讲财税口播风：朋友聊天叙事、不啰嗦、留资钩子、财税术语准确护栏）。

**换文本模型**
- 文件：`model_providers.py` `get_text_config()`。有 `DEEPSEEK_API_KEY` 用 DeepSeek，否则回退千问；`TEXT_PROVIDER` 环境变量可强制。

**换配音音色**
- 文件：`qwen_tts.py` `DEFAULT_VOICE_ID`（锁定"老张"克隆音色）。想加火山(豆包)重克隆 → 新增 `VOLC_*` 环境变量 + `get_tts_config` 分支，并用 `tts_ab_compare.py` 做 AB 试听。

**加违禁词**
- 文件：`forbidden_words.py`。`build_guidance()` 注入改写提示词，`scan()` 做门禁检查。当前"加我微信"等导流词未覆盖，待补。

**换出片模特**
- 文件：`make_avatar_video.py --model`（容器内路径 `/code/data/xxx.mp4`，静音模特在宿主机 `face2face/`）。确保模特视频带静音音轨。

**改字幕 / 防乱码**
- 文件：`finalize_v2_pil.py`。`FONTS=[SimHei, 微软雅黑, Segoe UI Emoji]` 多字体 fallback；`parse_ass` 清洗 ASS 标签 + 零宽/变体符；逐字符选能渲染字体，根除 emoji 烧成方块。

**服务端口 / 启动**
- `rewrite_studio.py` 端口 8385（纯标准库，无框架）。

---

## 8. 已知问题 / 待办（接手后可能要推进）

| 项 | 状态 | 说明 |
|---|---|---|
| 多租户声音 / 形象克隆 | ⏸ 待拍板 | 架构 A+B 方案（声音用阿里/火山复刻、形象用第三方厂商），等方向 |
| 第三方数字人供应商 | ❌ 未接 | `thirdparty_avatar.py` 框架已搭，待填硅基/HeyGen/阿里云/火山 key |
| 界面说明 Word 文档 | ⏸ 未跑 | `gen_doc.py` 写好未执行 |
| 桌面快捷方式 | ⏸ 沙箱拦 | COM 被拦，需本机跑或换方案 |
| 上传模特端到端验证 | ⏸ 未跑通 | `/api/models/upload` 路由+转码写了，curl 测试未闭环 |
| 违禁词库补全 | ⏸ 待补 | "加我微信"等导流词查不到 |
| saas_plan 差异化章节 | ⏸ 待补 | 纠正"偏重数字人引擎"叙述 |
| 本地 GPT-SoVITS s1 修复 | ⏸ 待激活 | 千问已跑通，本地线 s1 权重未训（需数小时 GPU） |

---

## 9. 安全 / 合规

- 本仓库**不含任何 API Key、音频、视频、模型权重**（见 `.gitignore`）。
- 财税内容经违禁词红线把关，但**仍需人工终审**后再发布（平台封号高风险）。
- 数字人模特与声音须获得本人授权。
- 接手二次开发时：**不要把 key 写进代码或文档、不要 commit `.env`、不要把成片传到未授权第三方**。

---

## 10. 端到端跑通一个视频（最小验证）

```bash
# 1. Docker 起 + 服务起（见 §4）
# 2. 命令行最小链路（绕过 Web 也能验证）
python content_pipeline.py run \
  --topic "个人卡流水为什么会被税务盯上" \
  --out-dir qwen_out/demo1 --no-audio      # 先只出三段式文稿审稿
# 人工审阅 qwen_out/demo1/03_逐字稿定稿.md
python content_pipeline.py tts --from qwen_out/demo1/03_逐字稿定稿.md \
  --out qwen_out/demo1/04_音频.wav          # 出老张音色音频
python build_package.py ...                                 # 生成素材包(字幕ass等)
python make_avatar_video.py --audio qwen_out/demo1/04_音频.wav \
  --ass qwen_out/demo1/subtitle.ass \
  --model /code/data/BGZSP20260721_t18_silent.mp4 \
  --out output/avatar_demo.mp4 --name demo   # HEYGEM 出片
```

Web 控制台则把上述步骤拆成 9 个节点（选题/改写/出音频/选模特/出片/字幕/QC/发布/队列），一个界面跑完。
