# RSS-Notion v2 系统升级设计文档

## 背景

当前系统（v1）实现了 10 个数据源 → LLM 筛选 → Notion 收件箱的基本流程，但存在三个核心问题：

1. **筛选质量不足**：LLM 只看标题和短描述做判断，漏选/误选/千篇一律
2. **阅读体验差**：摘要太浅、没有优先级引导、条目平铺无结构
3. **Web Clipper 孤岛**：用户手动剪藏和 AI 推送完全割裂，没有打通

## 用户真实工作流

```
AI 每日筛选 → 推送到 Notion 收件箱（中转站）
    ↓
用户扫收件箱 → 感兴趣的点链接去浏览器看原文
    ↓
好文章 → Save to Notion 插件 → Web Clipper 库（真正的归档）
```

关键发现：用户不会在收件箱里做收藏/忽略标记（"交互反人类"），Web Clipper 剪藏是唯一的反馈信号。

## 设计目标

- 核心筛选保持单次 LLM 调用（辅助调用如 Deep Reader、Web Clipper 摘要独立运行）
- 提升筛选质量：让 LLM 看到文章正文而非仅标题
- 提升阅读体验：从平铺条目改为分层日报
- 打通 Web Clipper：统一归档 + 反馈闭环
- 偏好动态推断：每次 run 将 Web Clipper 剪藏直接塞进主 LLM 调用，由模型自行推断兴趣方向，无需离线学习步骤

## 架构总览

```
┌─────────────────────────────────────────────────┐
│  Pipeline 主流程（每日 run）                       │
│                                                   │
│  Phase 1: 抓取 10 个数据源（补充正文内容）          │
│      ↓                                            │
│  Phase 2: 读 Web Clipper 最近 14 天剪藏            │
│      ↓                                            │
│  Phase 3: LLM 单次调用                             │
│      输入：剪藏列表 + 200 篇（标题 + 正文）         │
│      LLM 自行推断偏好 → 事件聚类 + 三层筛选 + 摘要  │
│      ↓                                            │
│  Phase 4: 写 Notion                                │
│      - 日报页面（主阅读入口）                       │
│      - 收件箱数据库（备查）                         │
│      ↓                                            │
│  Phase 5: PDF + 邮件（不变）                       │
│      ↓                                            │
│  Phase 6: Deep Reader（不变，YouTube 字幕摘要）     │
│      ↓                                            │
│  Phase 7: Web Clipper 同步（增量摘要新剪藏）        │
│      ↓                                            │
│  Phase 8: 清理收件箱（3 天全删）                    │
└─────────────────────────────────────────────────┘
```

## 模块 1：内容抓取增强

### 目标

让 LLM 在筛选时看到文章正文，而不仅仅是标题和短描述。

### 设计

在 Phase 1 各数据源抓取时，补充正文内容：

| 数据源 | 现状 | v2 改动 |
|--------|------|---------|
| Folo | 有 RSS description（200-500 字）| 保持不变，够用 |
| RSS feeds | 有 description | 保持不变 |
| YouTube | 标题 + 频道名 | 保持不变（Deep Reader 另外处理）|
| Hacker News | 仅标题 | **用 Jina Reader 抓前 800-1000 字** |
| Reddit | 标题 + 少量 selftext | **用 Jina Reader 补充正文** |
| arXiv | 有 abstract | 保持不变，abstract 够用 |
| GitHub Trending | repo 名 + description | **抓 README 前 500 字** |
| 小红书 | 标题 + 摘要 | 保持不变 |
| Tavily | 已返回内容片段 | 保持不变 |
| Product Hunt | tagline + description | 保持不变 |

### Jina Reader 调用策略

- 仅对缺少正文的来源使用（HN、Reddit 部分、GitHub）
- 并发抓取，设超时 10 秒/篇
- 失败时降级为仅标题（不阻塞 pipeline）
- 预估每次 run 额外抓取 20-30 篇，增加 15-30 秒

### 改动文件

- `sources/hackernews.py`：抓取后补充正文
- `sources/reddit.py`：selftext 不足时补充正文
- `sources/github_trending.py`：抓 README 摘要
- 可能新增 `sources/content_fetcher.py`：统一的 Jina Reader 调用封装

## 模块 2：主筛选升级（含动态偏好推断）

### 目标

单次 LLM 调用完成：偏好推断 + 事件聚类 + 筛选分层 + 摘要生成。

### Phase 2：读 Web Clipper 剪藏

在主 LLM 调用前，从 Notion 读取 Web Clipper 数据库最近 14 天的剪藏：
- 查询 Web Clipper 数据库（ID 从 config.json 读取）
- 提取：标题、URL、标签、摘取时间
- 拼成文本列表，作为 prompt 的一部分传给 LLM
- 若 Web Clipper 为空，降级使用 Notion 配置页面的兴趣描述

数据量很小（通常几十条标题，几千 tokens），对 400K 窗口无影响。

### Phase 3：LLM 单次调用

重构 `generator/interest_scorer.py` 的 prompt 和输出格式。

**输入**：
```
System Prompt:
- 角色定义（信息编辑部主编）
- 筛选规则（信息增量原则、聚类规则、分层标准）
- 指令：先根据用户剪藏推断兴趣方向，再据此筛选

User Prompt:
- 用户近期主动收藏（Web Clipper 剪藏列表，标题+标签+日期）
- 200 篇文章，每篇包含：
  - 标题
  - 来源
  - URL
  - 正文内容（500-1000 字）
```

**输出 JSON 结构**：
```json
{
  "headline": [
    {
      "event_title": "GPT-5 正式发布：原生多模态 + 100 万上下文",
      "source_count": 5,
      "best_source_url": "https://...",
      "best_source_name": "OpenAI Blog",
      "analysis": "200-300 字深度分析：发生了什么、为什么重要、对你意味着什么",
      "related_urls": ["https://...", "https://..."]
    }
  ],
  "noteworthy": [
    {
      "event_title": "LangChain v0.3 重构消息系统",
      "source_count": 2,
      "best_source_url": "https://...",
      "best_source_name": "LangChain Blog",
      "summary": "80-100 字摘要",
      "insight": "一句话洞察"
    }
  ],
  "glance": [
    {
      "title": "Mistral 开源新 MoE 模型",
      "url": "https://...",
      "one_liner": "一句话概括"
    }
  ],
  "daily_summary": "今天 AI 圈最值得关注的是...",
  "events_total": 45,
  "selected_total": 12
}
```

**分层标准**（写在 prompt 里）：
- **头条**（1-2 条）：改变行业格局的事件，或多源热点（3+ 来源报道同一事件）
- **值得关注**（3-5 条）：有信息增量，值得花 2 分钟了解
- **速览**（5-8 条）：知道有这事就行，不需要深入

### Token 预估与安全边界

- 条目上限：200 篇（沿用现有 pre_filter 逻辑，从当前 150 提升到 200）
- 每篇内容预算：截断到 800 字（中文约 1.5-2 token/字 → ~1200-1600 tokens/篇）
- 输入总量：200 × 1400 tokens + prompt ~5K ≈ **~28.5 万 tokens**
- 输出：~3-5K tokens
- GPT-5.4-mini 窗口 400K，安全余量 ~11 万 tokens（28%）
- 成本：~$0.20/次（GPT-5.4-mini $0.75/1M input）
- **安全阀**：若实际 token 数超过 35 万，自动降级截断每篇到 500 字

### 改动文件

- **重构** `generator/interest_scorer.py`：新增 Web Clipper 读取 + 新 prompt + 新输出解析
- **改动** `main.py`：Phase 2 改为读 Web Clipper 数据库
- **改动** `config.json`：新增 `clipper_database_id`

## 模块 4：Notion 输出重构

### 目标

从平铺数据库表格 → 结构化日报页面，提升阅读体验。

### 日报页面设计

每次 run 在 Notion 创建一个新页面，结构如下：

```
页面标题：📰 AI Daily — 2026-03-27

---
{daily_summary — 今天 AI 圈一句话总结}

## 📰 头条
[对每个 headline 事件]
### {event_title}
**来源**：{best_source_name} | 被 {source_count} 个来源报道
{analysis — 200-300 字}
[相关链接列表]

---

## 🔍 值得关注
[对每个 noteworthy 事件]
### {event_title}
{summary — 80-100 字}
💡 {insight}
🔗 {best_source_url}

---

## ⚡ 速览
- {title} — {one_liner} [链接]
- {title} — {one_liner} [链接]
- ...

---
📊 来源统计：抓取 {N} 篇 → 聚合 {M} 事件 → 精选 {K} 条
```

### 收件箱数据库

保留写入（作为备查和结构化数据存储），但：
- **砍掉字段**：选择（收藏/不收藏）
- **简化清理逻辑**：3 天后全部删除，不区分

### Web Clipper 同步（Phase 7）

独立 Phase，在主筛选和 Deep Reader 之后运行。

**触发条件**：每次 pipeline run 自动执行

**流程**：
1. 查询 Web Clipper 数据库（ID 从 config.json 读取），过滤 `已处理 = false` 的条目
2. 若无新条目，跳过（日志记录 "No new clippings"）
3. 对每个新条目：
   a. 用 Jina Reader 抓取原文（超时 10 秒，失败则用标题 + URL 降级）
   b. 单独 LLM 调用生成摘要 + 洞察 + 重要性评级（轻量调用，每条 ~500 tokens output）
   c. 通过 Notion API 更新该条目的字段
   d. 设置 `已处理 = true`
4. 批量处理，每条独立——一条失败不影响其他条目

**错误处理**：
- Jina Reader 失败 → 降级为仅标题生成摘要
- LLM 调用失败 → 跳过该条目，下次 run 重试（已处理仍为 false）
- Notion API 写入失败 → 日志告警，不阻塞 pipeline

**LLM 调用**：每个新剪藏一次轻量调用（GPT-5.4-mini），非批量。通常每次 run 0-3 条新剪藏，成本可忽略。

需要在 Web Clipper 数据库新增字段：
- `摘要`（text）— AI 生成的摘要
- `洞察`（text）— AI 生成的洞察
- `来源类型`（select）— 手动剪藏 / AI精选
- `重要性`（select）— 高/中/低
- `已处理`（checkbox）— 防止重复处理

### 改动文件

- **重构** `delivery/notion_writer.py`：新增日报页面写入 + Web Clipper 同步
- **改动** `main.py`：调整 Phase 4 和 Phase 6 逻辑
- **改动** `config.json`：新增 Web Clipper 数据库 ID

## 砍掉的东西

| 现有功能 | 处理 |
|----------|------|
| 收件箱 收藏/不收藏 机制 | 删除，用户不用 |
| 收件箱 → 归档库 自动迁移 | 删除 |
| 归档数据库 | 废弃（Web Clipper 库接管） |
| Phase 2b 加载收件箱反馈 | 删除（改为 Phase 2 读 Web Clipper 剪藏） |
| interest_scorer 中的反馈拼接逻辑 | 删除 |
| `generator/summarizer.py` 的 `generate_executive_summary` | 删除，日报摘要由 LLM 的 `daily_summary` 字段替代 |
| `generator/preference_learner.py` | 不再需要（偏好推断合并进主 LLM 调用） |
| `data/preference_profile.md` | 不再需要 |
| `--learn-preferences` CLI 参数 | 不再需要 |

## 迁移计划

1. 原归档库中已有数据不做自动迁移（量不大，用户可手动处理或忽略）
2. `ARCHIVE_DATABASE_ID` 常量和 `_archive_to_database` 函数直接删除
3. Web Clipper 数据库字段新增可通过 Notion API 或手动在 UI 中添加
4. 部署顺序：先加 Web Clipper 新字段 → 再部署新代码 → 最后清理旧逻辑

## LLM 调用汇总

| 调用 | 模型 | 频率 | 用途 |
|------|------|------|------|
| 主筛选 | GPT-5.4-mini | 每次 run | 偏好推断 + 聚类 + 筛选 + 分层 + 摘要（单次调用） |
| Deep Reader | GPT-5.4-mini | 按需 | YouTube 字幕摘要（不变） |
| Web Clipper 摘要 | GPT-5.4-mini | 每次 run（增量） | 新剪藏的 AI 摘要 |

## 风险和降级策略

| 风险 | 降级 |
|------|------|
| Jina Reader 抓取超时 | 降级为仅标题，不阻塞 pipeline |
| 正文太长超出 token | 截断到 1000 字/篇 |
| LLM 输出格式异常 | JSON schema 校验 + 重试一次 |
| Web Clipper 库为空 | 主 LLM 调用不含剪藏数据，降级使用 Notion 配置页面的兴趣描述 |

## 不在本次范围

- 更换 LLM 提供商（保持 OpenAI）
- 新增数据源
- 移动端推送
- 实时流式处理
