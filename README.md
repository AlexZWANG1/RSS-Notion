# AI 认知日报 — 多源聚合 × LLM 编辑 × 个性化推送

> 一个**全自动化的 AI 信息聚合 Agent**：从 6 个数据源并发抓取内容，由 LLM 担任"编辑"进行筛选评分，生成专业 PDF 日报，通过邮件推送并同步到 Notion 知识库。

```
抓取 50+ 条/天 → LLM 编辑筛选 → 精选 10-15 条 → PDF + 邮件 + Notion
```

---

## 目录

- [快速开始](#快速开始)
- [核心功能](#核心功能)
- [系统架构](#系统架构)
- [开发思想](#开发思想)
- [技术选型与原理](#技术选型与原理)
- [数据源详解](#数据源详解)
- [LLM 编辑策略](#llm-编辑策略)
- [个性化配置](#个性化配置)
- [输出与交付](#输出与交付)
- [部署与自动化](#部署与自动化)
- [项目结构](#项目结构)
- [实现效果](#实现效果)
- [迭代计划](#迭代计划)

---

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 OPENAI_API_KEY（必需）

# 3. 运行（个性化模式，跳过邮件）
python main.py --interests "AI Agent, LLM 推理, SaaS" --skip-email

# 4. 查看输出
# output/2026-03-19/report.pdf + data.json
```

### CLI 参数

```bash
python main.py                                          # 完整流水线
python main.py --interests "AI Agent, robotics"         # 自定义兴趣
python main.py --skip-email --skip-notion               # 仅本地输出
python main.py --sources hackernews,arxiv,github_trending  # 指定数据源
```

---

## 核心功能

| 功能 | 说明 |
|------|------|
| **多源并发抓取** | 6 个数据源异步并发，单源故障不影响整体 |
| **LLM 编辑筛选** | LLM 作为编辑决定 include/exclude，而非硬编码规则 |
| **个性化评分** | 每条内容基于用户兴趣打分（1-10），非通用分类 |
| **Notion 双向集成** | 读取用户配置 + 写入精选内容，构建知识库 |
| **专业 PDF 报告** | Playwright (Chromium) 渲染，Notion 风格排版 |
| **邮件推送** | SMTP/TLS，支持 PDF/PNG 附件 + 富文本摘要 |
| **定时自动化** | GitHub Actions / Cron / Windows 任务计划 |

---

## 系统架构

```
                         ┌──────────────────────────────────────┐
                         │         python main.py               │
                         │    --interests "AI Agent, ..."       │
                         └──────────────┬───────────────────────┘
                                        │
  ┌─────────────────────────────────────┼─────────────────────────────────────┐
  │  Phase 1: Concurrent Fetch          │  asyncio.gather()                   │
  │                                     │                                     │
  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────────┐ ┌────────┐ ┌────────┐ │
  │  │HackerNews│ │ arXiv   │ │ Reddit  │ │Product   │ │GitHub  │ │Folo RSS│ │
  │  │Firebase  │ │Python   │ │PRAW→RSS │ │Hunt GQL  │ │Trending│ │Notion  │ │
  │  │  API     │ │  pkg    │ │→Jina    │ │→Jina     │ │Jina+BS4│ │  API   │ │
  │  └────┬─────┘ └────┬────┘ └────┬────┘ └────┬─────┘ └───┬────┘ └───┬────┘ │
  └───────┼────────────┼──────────┼────────────┼───────────┼──────────┼──────┘
          └────────────┴──────────┴────────────┴───────────┴──────────┘
                                        │
                                        ▼ all_items[] (40-60 条)
  ┌─────────────────────────────────────────────────────────────────────────┐
  │  Phase 2: Load User Interests                                          │
  │  ┌─ CLI: --interests "AI Agent, SaaS"                                  │
  │  └─ Notion: Config Page → perspective + topics + keywords              │
  └─────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼ UserInterests
  ┌─────────────────────────────────────────────────────────────────────────┐
  │  Phase 3: LLM Editorial Curation (batch scoring)                       │
  │                                                                         │
  │  ┌─ Input: items + user interests                                      │
  │  ├─ LLM 决策: include/exclude + score + topic + importance             │
  │  └─ Output: ScoredItem[] (include=True 的进入精选)                      │
  └─────────────────────────────────────────────────────────────────────────┘
                                        │
                        ┌───────────────┼───────────────┐
                        ▼               ▼               ▼
                 ┌─────────────┐ ┌────────────┐ ┌─────────────┐
                 │  Phase 4    │ │  Phase 5   │ │  Phase 6    │
                 │  Notion     │ │  PDF/PNG   │ │  Email      │
                 │  Write-back │ │  Generation│ │  Delivery   │
                 └─────────────┘ └────────────┘ └─────────────┘
```

### 数据流模型

```
SourceItem → (LLM Scoring) → ScoredItem → (Filter) → Selected Items
                                                           │
                                    ┌──────────────────────┼──────────────┐
                                    ▼                      ▼              ▼
                              Notion Inbox           PDF Report       Email
                            (structured DB)      (HTML→Chromium)   (SMTP+附件)
```

**关键设计**：数据在管线中逐步增值。`SourceItem` 是原始数据，`ScoredItem` 附带了 LLM 的编辑判断（评分、话题、重要性、一句话摘要），最终输出时每个端（Notion/PDF/Email）各取所需。

---

## 开发思想

### 1. LLM 作为编辑，而非关键词匹配器

传统 RSS 聚合器用关键词过滤或简单分类。本系统将 **LLM 定位为"编辑角色"**：

- LLM 直接做 **include/exclude 决策**，不依赖硬编码的 score 阈值
- 话题分类和内容类型由 **LLM 自由生成**，无预定义枚举
- 重要性（高/中/低）由 **LLM 直接判断**，不从分数机械映射
- Prompt 设计强调编辑直觉："Would a smart colleague forward this to the reader?"

**为什么这样做？** 硬编码的话题列表（如 `VALID_TOPICS = [...]`）会在信息领域快速过时。LLM 能理解新出现的话题并做出合理分类，无需频繁更新代码。

### 2. 容错优先 (Fault-Tolerant by Design)

每个数据源都可能失败（API 限流、Cloudflare 拦截、网络超时）。系统设计为 **任何单点故障都不阻塞整体流水线**：

- 每个 Source 的 `fetch()` 独立 try/catch，失败返回空结果 + 错误信息
- LLM 调用有 **指数退避重试**（1s → 4s → 16s）
- LLM 完全失败时，使用 **fallback item**（保留原始数据，跳过 LLM 增值）
- 邮件/Notion 配置缺失时 **优雅跳过**，不 crash

### 3. Notion 作为双向知识库

Notion 不仅是输出目标，也是 **配置源和知识积累**：

| 方向 | 用途 |
|------|------|
| **读** | 用户兴趣配置（视角、话题、关键词、指定课题） |
| **读** | 已有研究数据库标题（避免重复推荐已知内容） |
| **写** | 精选内容写入 Inbox 数据库（带结构化属性） |
| **写** | 每次运行的 Run Report（运行统计、话题分布） |

这意味着系统使用越久，Notion 中的知识库越丰富，去重越精准。

### 4. 两种配置模式，渐进式上手

- **CLI 模式**（零配置）：`--interests "AI Agent"` 即可运行，适合快速体验和演示
- **Notion 模式**（持久化）：配置一次，每次运行自动同步，适合日常使用

### 5. 异步并发，最小化延迟

所有数据源通过 `asyncio.gather()` **并发抓取**。6 个源的总延迟 ≈ 最慢的单源延迟（通常 3-5 秒），而非串行累加。

---

## 技术选型与原理

### LLM 处理层

| 组件 | 选型 | 原理 |
|------|------|------|
| **API 接口** | OpenAI SDK (AsyncOpenAI) | 兼容任何 OpenAI-compatible 端点（包括 EasyCIL 反代、OneAPI、vLLM） |
| **评分模型** | 可配置（默认 gpt-5.4） | `config.json` 中的 `processing_model` 字段 |
| **批处理** | 15 条/batch（评分）、10 条/batch（摘要） | 平衡 token 效率和单次调用质量 |
| **响应格式** | JSON Mode (`response_format: json_object`) | 强制结构化输出，避免解析错误 |
| **重试策略** | 指数退避（1→4→16s），最多 3 次 | 处理 429/500 暂时性错误 |

**Prompt 工程核心**：评分 prompt 包含完整的用户画像（视角、话题、关键词、已有研究），让 LLM 作为知道读者背景的编辑来做决策。

### 数据抓取层

| 技术 | 用途 | 为什么选它 |
|------|------|-----------|
| **aiohttp** | HTTP 客户端 | 原生 asyncio 支持，不阻塞事件循环 |
| **Firebase REST API** | Hacker News | 官方公开 API，无需认证 |
| **arxiv Python 包** | arXiv 论文 | 官方 Python SDK，支持日期和分类过滤 |
| **PRAW** | Reddit | 官方 OAuth SDK，速率限制友好 |
| **Jina Reader** | 反爬降级 | `r.jina.ai/{url}` 服务端渲染 JS，绕过 Cloudflare |
| **BeautifulSoup4** | HTML 解析 | GitHub Trending 页面的 DOM 解析 |

**Jina Reader 策略**：对于有反爬保护的站点（Product Hunt、GitHub Trending），使用 Jina Reader 作为服务端渲染代理。这避免了在 CI 环境维护 headless browser 的成本。

### 报告生成层

| 技术 | 用途 | 为什么选它 |
|------|------|-----------|
| **Playwright (Chromium)** | 主 PDF + PNG 渲染 | 完整 CSS3 支持、中文字体渲染、同时生成 PDF 和截图 |
| **xhtml2pdf** | PDF 降级方案 | 纯 Python，无需浏览器依赖 |
| **Jinja2** | HTML 模板 | 轻量、逻辑清晰、Python 原生 |
| **Notion 风格 CSS** | 排版设计 | 卡片布局、色彩编码标签、专业但友好 |

**渲染策略**：优先用 Playwright Chromium 同时生成 PDF 和全页 PNG。如果 Playwright 不可用（如精简环境），自动降级到 xhtml2pdf（需手动注册中文字体）。

### 交付层

| 技术 | 用途 | 为什么选它 |
|------|------|-----------|
| **smtplib + TLS** | 邮件发送 | Python 标准库，零额外依赖 |
| **notion-client** | Notion API | 官方 Python SDK |
| **FastAPI** | REST API（可选） | 异步、自动文档、触发远程运行 |

### 可选扩展层

| 技术 | 用途 |
|------|------|
| **FastAPI + Uvicorn** | REST API 后端（查看报告、远程触发流水线） |
| **Next.js 15 + Tailwind** | Web 前端仪表盘 |
| **GitHub Actions** | 定时调度、CI/CD |

---

## 数据源详解

### 抓取策略与降级

```
每个数据源的降级链:

Product Hunt:  GraphQL API (token) → Jina Reader → 优雅返回 0 条
Reddit:        PRAW OAuth → RSS Feed (/.rss) → Jina Reader
GitHub:        Jina Reader (JS渲染) → 直接 BS4 解析
Hacker News:   Firebase REST API (无需认证)
arXiv:         arxiv Python 包 (官方)
Folo RSS:      Notion API (可选, 需 NOTION_TOKEN)
```

### AI 关键词过滤（Hacker News）

Hacker News 每天有 500+ 条热帖，大部分与 AI 无关。系统使用**客户端关键词预过滤**（`AI`, `LLM`, `GPT`, `neural`, `model` 等 30+ 关键词），将候选缩小到 10-15 条再交给 LLM 评分。这**不替代** LLM 评分，只是减少 LLM 处理量。

### 源结果数据结构

```python
@dataclass
class SourceItem:
    title: str                    # 标题
    url: str                      # 原文链接
    source_name: str              # 来源名称
    description: str = ""         # 摘要/描述
    author: str = ""              # 作者
    score: Optional[int] = None   # 平台热度（upvotes/stars）
    published: Optional[datetime] = None
    extra: dict = {}              # 源特定数据
```

---

## LLM 编辑策略

### Prompt 设计哲学

系统 prompt 将 LLM 定位为 **"编辑策展人"（editorial curator）**，而非关键词匹配工具：

```
You are an editorial curator for a personalized AI daily digest.
Your job is to decide which items are worth the reader's time today.

Think like a thoughtful human editor, not a keyword matcher:
- Include items that would make the reader stop scrolling
- Exclude routine announcements, tangential topics
- Prefer quality over quantity — it's OK to include only 2-3 from a batch of 15
- Consider: Would a smart colleague forward this to the reader?
```

### LLM 输出结构

对每条内容，LLM 返回：

| 字段 | 类型 | 说明 |
|------|------|------|
| `include` | bool | **核心决策**：是否收入今日日报 |
| `score` | 1-10 | 推荐置信度 |
| `topic` | string | **自由分类**，LLM 自行决定话题标签 |
| `content_type` | string | **自由分类**（新闻/深度分析/技术报告/开源项目/...） |
| `importance` | 高/中/低 | 对读者的重要程度 |
| `one_line_summary` | string | 中文一句话摘要（20-40 字） |
| `key_insight` | string | 英文核心洞察（1 句） |
| `tags` | string[] | 2-4 个标签 |
| `score_reason` | string | 编辑决策理由 |

### 筛选逻辑

```python
# 主筛选：LLM 的编辑决策 (include=True)
included = [s for s in scored if s.include]

# 兜底：如果 LLM 过于保守（<3 条），追加高分项
if len(included) < 3:
    included += [s for s in scored if s.score >= threshold]

# 安全上限：防止输出过多
return included[:max_items]  # 默认 15
```

---

## 个性化配置

### 方式一：CLI 快速模式

```bash
python main.py --interests "AI Agent 基础设施, 垂直 SaaS, LLM 推理成本"
```

适合快速演示，无需任何外部依赖。

### 方式二：Notion 配置页面（持久化）

在 Notion 中创建一个配置页面，包含以下章节：

| 章节标题 | 内容 | 示例 |
|----------|------|------|
| **筛选视角** | 用户角色定位 | "产品人和投资者" |
| **长期关注课题** | 长期研究方向（列表） | AI Agent 生态、SaaS 转型、推理经济学 |
| **关键词表** | 追踪的技术关键词 | Agent, MCP, PMF, RAG, fine-tuning |
| **指定课题** | 今日特别关注（one-shot） | "今天重点看 MCP 生态进展" |

每次运行时，系统自动从 Notion 读取最新配置，无需重启。

### 评分示例

```
输入: "unslothai/unsloth" (GitHub Trending, ★975)
用户兴趣: "LLM 推理成本优化"
→ LLM 评分: 8/10 (直接相关)
→ Topic: 大模型推理 | Importance: 中
→ include: true ✅

输入: "best-keyboard-for-coding" (Reddit)
用户兴趣: "AI Agent, SaaS"
→ LLM 评分: 2/10 (无关)
→ include: false ❌
```

---

## 输出与交付

每次运行在 `output/{YYYY-MM-DD}/` 下生成：

### PDF 报告 (`report.pdf`)

- Notion 风格卡片布局
- 今日概览（LLM 生成的趋势摘要）
- 按数据源分组的内容卡片（评分徽章、话题标签、一句话摘要）
- A4 尺寸，中文字体完整支持

### 全页截图 (`report.png`)

- 用于邮件附件（部分邮件客户端对 PDF 预览不友好）
- Playwright 全页截图，与 PDF 内容一致

### 结构化数据 (`data.json`)

```json
{
  "date": "2026-03-19",
  "executive_summary": "今日 AI 领域三大趋势...",
  "interests": {
    "topics": ["AI Agent 基础设施", "..."],
    "keywords": ["Agent", "MCP", "..."],
    "designated_topic": null
  },
  "sources": [
    {"name": "Hacker News", "item_count": 8, "error": null, "duration_ms": 1200}
  ],
  "items": [
    {
      "title": "...",
      "url": "...",
      "source": "Hacker News",
      "summary": "...",
      "interest_score": 9,
      "topic": "Agent Infra",
      "content_type": "开源项目",
      "tags": ["agent", "MCP", "open-source"]
    }
  ]
}
```

### Notion Inbox

精选内容自动写入 Notion 数据库，带结构化属性：

| 属性 | 类型 | 说明 |
|------|------|------|
| 名称 | Title | 内容标题 |
| 来源 | Select | 数据源名称 |
| 话题 | Multi-select | LLM 分配的话题 |
| 重要性 | Select | 高/中/低 |
| 原文链接 | URL | 原始 URL |
| 收录时间 | Date | 收录日期 |
| 状态 | Select | 默认"待阅读" |

**去重机制**：写入前检查 **标题匹配 + URL 匹配**，避免重复收录。

### 邮件推送

```
Subject: [AI日报] 2026-03-19 每日认知日报
Body:
  AI 认知日报 — 2026-03-19
  今日扫描了 44 条内容（来源: HN(8), arXiv(12), Reddit(7)...）
  经过 LLM 编辑筛选，推荐 14 条值得关注。

  ⭐ 重点阅读（高重要性）
    [Agent Infra] Mistral Forge — 企业级AI应用定制平台发布
    → 一句话摘要...
    💡 核心洞察...

  📌 值得一看
    ...

  📈 核心趋势与发现
    ...

Attachment: report.png (或 report.pdf)
```

---

## 部署与自动化

### GitHub Actions（推荐）

已配置 `.github/workflows/daily-digest.yml`：

- **定时运行**：每日 08:00 和 18:00（UTC+8）
- **手动触发**：支持 `workflow_dispatch`
- **CJK 字体**：自动安装 `fonts-noto-cjk`
- **产物归档**：output/ 目录上传为 Artifact，保留 30 天

需要配置的 GitHub Secrets：
```
OPENAI_API_KEY, OPENAI_BASE_URL
NOTION_TOKEN
SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD
EMAIL_FROM, EMAIL_RECIPIENTS
```

### Cron（Linux/Mac）

```bash
# 每日早 8 点运行
0 8 * * * cd /path/to/RSS-Notion && python main.py >> /var/log/digest.log 2>&1
```

### Windows 任务计划

双击 `run.bat` 或配置 Task Scheduler 执行 `run.bat`。

### FastAPI 后端（可选）

```bash
python -m api.server
# → http://localhost:8000/docs (Swagger UI)
```

API 端点：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/reports` | 列出所有可用报告 |
| GET | `/api/reports/{date}` | 获取 data.json |
| GET | `/api/reports/{date}/pdf` | 下载 PDF |
| POST | `/api/trigger` | 异步触发流水线 |
| GET | `/api/trigger/{job_id}` | 查询任务状态 |

---

## 项目结构

```
RSS-Notion/
├── main.py                        # 流水线编排 + CLI 入口
├── config.json                    # 数据源和 LLM 配置
├── requirements.txt               # Python 依赖
├── .env.example                   # 环境变量模板
│
├── sources/                       # 数据源抓取层
│   ├── base.py                    # BaseSource 抽象基类（fetch + 计时 + 错误处理）
│   ├── models.py                  # 数据模型（SourceItem, ProcessedItem, PipelineResult）
│   ├── hackernews.py              # Hacker News — Firebase REST API
│   ├── arxiv_source.py            # arXiv — arxiv Python 包
│   ├── reddit.py                  # Reddit — PRAW OAuth → RSS → Jina 三级降级
│   ├── producthunt.py             # Product Hunt — GraphQL → Jina 降级
│   ├── github_trending.py         # GitHub Trending — Jina Reader + BS4
│   └── folo.py                    # Folo RSS — 通过 Notion API 读取（可选）
│
├── generator/                     # LLM 处理层
│   ├── interest_scorer.py         # 个性化兴趣评分（LLM 编辑筛选）
│   ├── summarizer.py              # 批量处理 + 每日概览生成
│   └── pdf_builder.py             # PDF/PNG 生成（Playwright → xhtml2pdf 降级）
│
├── delivery/                      # 交付层
│   ├── emailer.py                 # SMTP 邮件发送（支持 PDF/PNG 附件）
│   └── notion_writer.py           # Notion 写入（去重 + 结构化属性）
│
├── templates/                     # 报告模板
│   ├── daily_report.html          # Jinja2 HTML 模板（Notion 风格）
│   └── styles.css                 # 打印优化 CSS（卡片布局、色彩编码）
│
├── api/                           # REST API（可选）
│   └── server.py                  # FastAPI 后端（报告查看 + 远程触发）
│
├── web/                           # Web 前端（可选）
│   └── src/                       # Next.js 15 + Tailwind CSS
│
├── docs/                          # 文档
│   └── design.md                  # 架构设计文档
│
├── .github/workflows/
│   └── daily-digest.yml           # GitHub Actions 定时任务
│
└── output/                        # 生成的报告（按日期组织）
    └── {YYYY-MM-DD}/
        ├── report.pdf
        ├── report.png
        └── data.json
```

---

## 实现效果

### 典型运行数据

```
[08:00:01] Starting AI Daily Digest for 2026-03-19
[08:00:01] Phase 1: Fetching data sources...
[08:00:04] [Hacker News] Fetched 8 items in 1200ms
[08:00:04] [arXiv] Fetched 15 items in 2100ms
[08:00:05] [Reddit] Fetched 7 items in 3200ms
[08:00:05] [GitHub Trending] Fetched 10 items in 2800ms
[08:00:05] [Product Hunt] Fetched 4 items in 1500ms
[08:00:05] Fetched 44 items from 5 sources
[08:00:06] Phase 2: Loading user interests from Notion...
[08:00:07] Phase 3: Scoring items against user interests...
[08:00:15]   LLM included 14 items, final selected 14
[08:00:16] Phase 4: Writing to Notion...
[08:00:18]   Wrote 14 items to Notion inbox
[08:00:19] Phase 5: Generating PDF...
[08:00:22] Phase 6: Sending email...
[08:00:23] Pipeline complete! Items: 44 fetched → 14 selected
```

### 关键指标

| 指标 | 典型值 |
|------|--------|
| 总抓取量 | 40-60 条/次 |
| LLM 精选 | 10-15 条/次 |
| 筛选率 | 25-35% |
| 数据源 | 5-6 个（含 Folo RSS） |
| 端到端耗时 | 20-30 秒 |
| LLM 调用次数 | 5-8 次（评分 3 batch + 摘要 3 batch + 概览 1） |
| PDF 大小 | 200-500 KB |

### 错误容忍

系统已在以下场景验证：

- Product Hunt Cloudflare 封禁 → 自动降级 Jina Reader → 再失败返回 0 条，流水线继续
- Reddit OAuth token 过期 → 自动降级 RSS feed
- OpenAI API 429 限流 → 指数退避重试，最终成功
- Notion token 未配置 → 跳过 Notion 读写，使用默认兴趣
- SMTP 未配置 → 跳过邮件，仅生成本地 PDF

---

## 迭代计划

基于生产运行反馈，后续迭代方向：

### 近期

- [ ] **LLM 调用预算治理**：统一计量所有 LLM 调用（评分 + 摘要 + 概览），添加 token/调用次数上限
- [ ] **消除 summarizer.py 与 interest_scorer.py 的重复处理**：当前 Phase 3 同时运行两套 LLM 处理，应合并为单一路径
- [ ] **结构化运行日志**：持久化每次运行的统计数据（抓取量、筛选量、耗时、错误），支持历史趋势分析
- [ ] **信息图表生成**：集成 Gemini Image API 生成可视化信息图（Dashboard 风格），替代 HTML 截图

### 中期

- [ ] **源质量评分**：基于历史数据分析每个数据源的"入选率"，动态调整抓取优先级
- [ ] **增量式知识图谱**：从 Notion inbox 中提取实体关系，构建 AI 领域知识图谱
- [ ] **Web 仪表盘**：完善 Next.js 前端，支持历史报告浏览、兴趣配置、实时触发

---

## 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `OPENAI_API_KEY` | **是** | OpenAI API 密钥 |
| `OPENAI_BASE_URL` | 否 | 自定义端点（支持 EasyCIL、OneAPI 等反代） |
| `NOTION_TOKEN` | 否 | 启用 Notion 配置同步 + 写入 + Folo RSS |
| `REDDIT_CLIENT_ID` | 否 | Reddit OAuth（无则降级 RSS） |
| `REDDIT_CLIENT_SECRET` | 否 | Reddit OAuth |
| `PRODUCTHUNT_TOKEN` | 否 | Product Hunt API（无则降级 Jina） |
| `SMTP_HOST` | 否 | 邮件服务器 |
| `SMTP_PORT` | 否 | 邮件端口（默认 587） |
| `SMTP_USER` | 否 | SMTP 用户名 |
| `SMTP_PASSWORD` | 否 | SMTP 密码 |
| `EMAIL_FROM` | 否 | 发件人地址 |
| `EMAIL_RECIPIENTS` | 否 | 收件人（逗号分隔） |

---

## License

MIT
