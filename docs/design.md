# AI Daily Digest Agent — 架构设计文档

## 1. 系统架构

```
                         ┌──────────────────────────────────────┐
                         │         python main.py               │
                         │    --interests "AI Agent, ..."       │
                         └──────────────┬───────────────────────┘
                                        │
  Phase 1: Fetch         ┌──────────────┼──────────────┐
  (asyncio.gather)       │              │              │
                  ┌──────▼──┐  ┌───────▼───┐  ┌──────▼─────┐
                  │ HN/Reddit│  │arXiv/GitHub│  │PH/Folo RSS │  (6 sources)
                  └──────┬───┘  └───────┬───┘  └──────┬─────┘
                         └──────────────┼──────────────┘
                                        │ all_items[]
  Phase 2: Interests                    ▼
                  ┌──────────────────────────────────────────┐
                  │  Load User Interests                     │
                  │  CLI: --interests "..." / Notion config   │
                  └──────────────┬───────────────────────────┘
                                 │
  Phase 3: LLM Curation         ▼
                  ┌──────────────────────────────────────────┐
                  │  LLM Editorial Curation (batch)          │
                  │  include/exclude + score + topic + ...   │
                  └──────────────┬───────────────────────────┘
                                 │ scored_items[] → selected[]
                  ┌──────────────┼──────────────┐
  Phase 4-6       ▼              ▼              ▼
           ┌────────────┐ ┌───────────┐ ┌─────────────┐
           │   Notion    │ │ PDF/PNG   │ │   Email     │
           │  write-back │ │ Playwright│ │  SMTP+TLS   │
           └────────────┘ └───────────┘ └─────────────┘
```

## 2. 核心设计决策

### 2.1 LLM 作为编辑，而非规则引擎

**问题**：早期版本使用硬编码的话题列表（`VALID_TOPICS`）和内容类型枚举（`VALID_CONTENT_TYPES`），以及基于分数的机械映射（score >= 8 → 高重要性）。这导致：
- 新话题无法自动分类
- 重要性判断与内容实际价值脱节
- 每次需求变化都要改代码

**决策**：将 LLM 定位为"编辑策展人"，所有分类和决策由 LLM 自由生成：
- `include: bool` — LLM 直接决定是否收入
- `topic: string` — LLM 自由分配话题
- `importance: 高/中/低` — LLM 直接判断，不从分数映射
- `content_type: string` — LLM 自由分类

**效果**：零代码维护成本，新领域自动适应。

### 2.2 容错与降级策略

每个数据源有独立的降级链：

| 源 | 主方案 | 降级 1 | 降级 2 | 最终兜底 |
|-----|--------|--------|--------|----------|
| Product Hunt | GraphQL API | Jina Reader | — | 返回 0 条 |
| Reddit | PRAW OAuth | RSS Feed | Jina Reader | 返回 0 条 |
| GitHub | Jina Reader | 直接 BS4 | — | 返回 0 条 |
| HN | Firebase API | — | — | 返回 0 条 |
| arXiv | arxiv 包 | — | — | 返回 0 条 |

LLM 降级：评分/摘要失败 → 使用 fallback item（保留原始数据，跳过 LLM 增值）。

**原则**：任何单点故障都不阻塞流水线。系统宁可输出一份不完整的报告，也不因一个源的错误而完全失败。

### 2.3 Notion 双向集成

Notion 在系统中扮演三个角色：

1. **配置源**：用户兴趣（视角、话题、关键词、指定课题）从 Notion config page 读取
2. **知识库**：精选内容写入 inbox 数据库，带结构化属性（话题、重要性、来源）
3. **去重参考**：已有研究数据库的标题用于避免重复推荐

**去重机制**：写入前检查标题匹配 + URL 匹配，同一条内容不会重复收录。

### 2.4 PDF 渲染策略

**主方案：Playwright Chromium**
- 完整 CSS3 支持（Flexbox、Grid、自定义属性）
- 中文字体原生渲染
- 同时生成 PDF 和全页 PNG
- 适合本地开发和 CI 环境（GitHub Actions 上预装 Chromium）

**降级方案：xhtml2pdf**
- 纯 Python，无浏览器依赖
- 需要手动注册中文字体（通过 reportlab TTFont API）
- CSS 支持有限（不支持 Flexbox/Grid）

**模板设计**：Notion 风格卡片布局，Jinja2 模板引擎。每个内容项展示：评分徽章、标题链接、内容类型标签、来源、一句话摘要、标签。

### 2.5 异步并发

所有数据源通过 `asyncio.gather()` 并发抓取。总延迟 ≈ 最慢源的延迟（通常 3-5 秒），而非 6 个源串行累加。

LLM 处理使用批量 prompt（15 条/batch 评分，10 条/batch 摘要），减少 API 调用次数。

### 2.6 两种配置模式

- **CLI 模式**：`--interests "AI Agent"` 参数直接传入，零配置即可运行
- **Notion 模式**：从 Notion 配置页面读取，每次运行自动同步最新配置

设计为渐进式上手：先用 CLI 体验，再迁移到 Notion 长期使用。

## 3. Jina Reader 反爬策略

对于有 Cloudflare 等反爬保护的站点，使用 `r.jina.ai/{url}` 作为服务端渲染代理：

- Jina 在服务端使用 headless browser 渲染页面
- 返回纯文本/Markdown 格式
- 避免在本地维护 headless browser
- 无需 API key，免费使用

适用场景：Product Hunt（Cloudflare）、GitHub Trending（JS 渲染）。

## 4. 邮件推送设计

邮件内容包含：

1. **统计概要**：今日扫描量、来源分布、精选数
2. **重点阅读**（高重要性）：标题 + 话题 + 摘要 + 核心洞察 + 链接
3. **值得一看**（中重要性）：标题 + 话题 + 摘要 + 链接
4. **核心趋势**：LLM 生成的每日概览

附件：优先发送 PNG 截图（邮件客户端预览友好），PNG 不存在时发送 PDF。

## 5. 已知限制与后续方向

| 限制 | 说明 | 改进方向 |
|------|------|----------|
| LLM 调用无预算控制 | 当前不限制总 token/调用次数 | 添加 budget 治理层 |
| 双路 LLM 处理 | summarizer + interest_scorer 分别处理 | 合并为单一评分路径 |
| 无运行历史持久化 | 每次运行的统计数据未持久化 | 添加 run_log 表或 JSON 日志 |
| 信息图表为 HTML 截图 | 不够精美 | 集成 AI 生图（Gemini Image） |
| Folo 源不稳定 | Notion API 的 query 接口偶尔报错 | 需排查 notion-client 版本兼容性 |
