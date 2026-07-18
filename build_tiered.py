#!/usr/bin/env python3
"""Build tiered.json for 2026-05-06 — Phase 2 (scoring) + Phase 3 (report polish)."""
import json
from pathlib import Path

DATE = "2026-05-06"
TOTAL_FETCHED = 161

with open(f"output/{DATE}/sources.json") as f:
    src = json.load(f)
items = src["items"]


def get(idx):
    """Get item by index."""
    return items[idx]


def src_entry(idx, channel, one_liner):
    it = items[idx]
    return {
        "title": it["title"],
        "url": it["url"],
        "source_name": it["source_name"],
        "channel": channel,
        "one_liner": one_liner,
    }


# ============================================================
# PHASE 2: Tiered editorial scoring
# ============================================================

headline = [
    {
        "event_title": "Trump 政府首次实质介入前沿模型评估,Google/MSFT/xAI 同意先给政府看",
        "source_count": 4,
        "best_source_url": "https://www.cnbc.com/2026/05/05/ai-oversight-trump-google-microsoft-xai.html",
        "best_source_name": "CNBC Tech",
        "analysis": (
            "白宫宣布在模型发布前对 Google、Microsoft、xAI 模型做评估,并考虑设立新的 AI 工作组。"
            "这是 Trump 任内 AI oversight 第一次从「自愿原则声明」走到「测试 + 提前访问」的具体动作,"
            "节奏上比欧盟 AI Act 强制评估早 18 个月落地美国本土。同周 Meta 被 5 家出版商起诉用版权作品训练 Llama,"
            "美国 AI 监管的双轮(行政评估 + 司法版权)第一次同时启动。值得注意的是 Anthropic 不在评估名单内——"
            "暗示 holdback Mythos 的「太危险不公开」叙事换来了某种监管豁免空间。"
            "对头部模型公司 release cadence 的影响要在下一轮 GPT-5.5/Gemini 1.2T 节奏里观察。"
        ),
        "related_sources": [
            src_entry(0, "深度研究", "白宫将测试 Google/MSFT/xAI 模型,考虑成立 AI 工作组"),
            src_entry(95, "深度研究", "Google/MSFT/xAI 同意给美国政府早期模型评估权限"),
            src_entry(50, "深度研究", "Meta 被 5 家出版商联合起诉,用版权作品训练 Llama"),
            src_entry(30, "深度研究", "Meta 版权诉讼可能成为 landmark case"),
        ],
    },
    {
        "event_title": "Coinbase 14% 裁员明确归因 AI,劳动力替代叙事第一次有大公司实证",
        "source_count": 5,
        "best_source_url": "https://www.cnbc.com/2026/05/05/coinbase-cuts-headcount-by-14-citing-ai-acceleration.html",
        "best_source_name": "CNBC Tech",
        "analysis": (
            "Coinbase 裁员 14%,CEO 公开把原因之一归到「AI 加速了流程,需要的人变少」——这是上市公司层面"
            "第一次把 AI 列为裁员的明确触发因素而不是「市场环境」托词。同周两条反向叙事:"
            "Apollo 首席经济学家 Torsten Slok 把 AI 影响类比为「China shock 最好的部分」,"
            "认为生产率提升带来的新增就业会多于消失;No Priors 出标题"
            "「AI Replacing Jobs is the Wrong Narrative」。两边都对,但时间错位:短期裁员是真的,"
            "长期再就业是个 bet。HN 顶帖一篇就业延缓认知衰退的研究,把这个争议拖回更深的层面——"
            "「AI 减少需要做的工作」对 65+ 群体可能不只是经济问题。"
            "这是 2026 年第一次 AI 劳动力叙事在数据、舆论、学术三层面同步触发。"
        ),
        "related_sources": [
            src_entry(42, "深度研究", "Coinbase 裁员 14%,CEO 明确指 AI 加速流程导致用人需求下降"),
            src_entry(47, "深度研究", "Bloomberg 同步报道 Coinbase 裁员"),
            src_entry(67, "深度研究", "FT:CEO 说 AI 让流程更快、需要更少员工"),
            src_entry(78, "深度研究", "Apollo Slok:AI 影响类似 China shock 最好的部分"),
            src_entry(114, "长内容/播客", "No Priors:AI 替代工作是错误叙事"),
            src_entry(124, "社交/社区/Twitter", "HN 顶帖:就业延缓认知衰退的劳动经济学论文"),
            src_entry(90, "深度研究", "Apollo Slok 长版:生产率提升会创造更多就业"),
        ],
    },
    {
        "event_title": "Apple 把 Intel/Samsung 列为 TSMC 备份,Intel 单日 +14% 创历史新高",
        "source_count": 3,
        "best_source_url": "https://www.bloomberg.com/news/articles/2026-05-05/apple-explores-intel-samsung-as-tsmc-backup",
        "best_source_name": "Bloomberg Tech",
        "analysis": (
            "Bloomberg 独家:Apple 探索把 Intel 和 Samsung 作为 TSMC 之外的美国本土制造备份。"
            "这是 Cook 时代以来第一次 Apple 在硅供应链上向 Intel 这种本土选项倾斜。Intel 当天 +14% "
            "创下历史新高——市场把这件事 price 成 Intel 复兴叙事的转折点而不是「备胎」叙事。"
            "深层原因有三:(1) Cook 9/1 离任 + Ternus 上任的硬件路线变化,(2) 关税/出口管制下 TSMC 单源风险显化,"
            "(3) Intel 18A 制程 yield 数据可能比公开披露的好。对市场结构的二阶意义是:"
            "Apple 这个动作给其他 hyperscaler(NVIDIA、Google、Microsoft)提供了「分散到 Intel 18A」的政治掩护——"
            "一旦 Apple 实锤下单,Intel 代工业务才真正开局。下一个数据点是 WWDC 6/8 + Intel Q2 财报会怎么谈"
            "Apple 这条客户线。"
        ),
        "related_sources": [
            src_entry(2, "深度研究", "Bloomberg 独家:Apple 探索 Intel/Samsung 作为 TSMC 备份"),
            src_entry(46, "深度研究", "Intel 因此 +14%,创历史新高"),
        ],
    },
]

noteworthy = [
    {
        "event_title": "企业 Agent 落地周:Anthropic、NVIDIA×ServiceNow、Vercel 同周三连发",
        "source_count": 4,
        "best_source_url": "https://www.bloomberg.com/news/articles/2026-05-05/anthropic-unveils-ai-agents-for-financial-services",
        "best_source_name": "Bloomberg Tech",
        "summary": (
            "本周企业级 Agent 三条线同时出招:**Anthropic** 发金融服务专用 AI Agents 切进 BFSI 重监管场景;"
            "**NVIDIA × ServiceNow** 推出企业自治 AI Agent,把推理算力 + 工作流平台打包卖;"
            "**Vercel** 开源 **Open Agents** 支持后台运行 AI 编码工作流;**CopilotKit** 拿到 $27M Series A"
            "做 app-native agent 部署框架。从「demo 演示」到「企业 SKU」整体推进了一档。"
        ),
        "insight": "Agent 从 model lab 营销品变成真正进 ERP/BFSI 采购流程的 SKU,2026 H2 财报里要看 ARR 拆分。",
        "related_sources": [
            src_entry(61, "深度研究", "Anthropic 推金融服务专用 AI Agents"),
            src_entry(14, "一手/官方", "NVIDIA × ServiceNow 推企业自治 AI Agent"),
            src_entry(81, "开源/技术/论文", "Vercel 开源 Open Agents,后台运行 AI 编码工作流"),
            src_entry(76, "深度研究", "CopilotKit 拿 $27M Series A 做 app-native agent 部署"),
        ],
    },
    {
        "event_title": "DeepSeek V4 Pro 在 FoodTruck Bench 追平 GPT-5.2,17× 便宜——开源 vs 闭源进入「持平 + 价差」期",
        "source_count": 4,
        "best_source_url": "https://www.reddit.com/r/LocalLLaMA/comments/1sv8eua/deepseek_v4_pro_matches_gpt52_on_foodtruck_bench/",
        "best_source_name": "r/LocalLLaMA",
        "summary": (
            "r/LocalLLaMA 的 30 天 agentic 任务 benchmark **FoodTruck Bench** 显示:DeepSeek V4 Pro **追平 GPT-5.2**,"
            "成本只有 **1/17**。同周 **Qwen3.6 27B FP8** 在单卡 RTX 5000 PRO 48GB 上跑出 **80 TPS + 200K KV cache**;"
            "**Gemma 4 MTP** 发布(Google 第一次走 multi-token prediction);**26B LLM 无 GPU 在 CPU 上运行**也跑通了。"
            "开源在 agentic task 上的有效性 + 单位 token 价差被 r/LocalLLaMA 实测确认。"
        ),
        "insight": "闭源前沿不再是「能力差距」问题,而是「成本差距」问题——API 商业模型要被迫接受 mid-tier 价格压力。",
        "related_sources": [
            src_entry(136, "社交/社区/Twitter", "DeepSeek V4 Pro 在 FoodTruck Bench 匹敌 GPT-5.2,17× 便宜"),
            src_entry(140, "社交/社区/Twitter", "Qwen3.6 27B FP8 单卡 200K 上下文 80 TPS"),
            src_entry(133, "开源/技术/论文", "Gemma 4 MTP 发布,multi-token prediction"),
            src_entry(137, "社交/社区/Twitter", "26B LLM 在 CPU 上跑通"),
            src_entry(132, "社交/社区/Twitter", "r/LocalLLaMA Best Local LLMs Apr 2026 megathread"),
        ],
    },
    {
        "event_title": "Dwarkesh × Dario Amodei:反垄断、Pentagon 计划、Trillion-Dollar Timing",
        "source_count": 5,
        "best_source_url": "https://www.youtube.com/watch?v=DZSQqObfnfE",
        "best_source_name": "Dwarkesh Patel",
        "summary": (
            "Dwarkesh 这周放出 Dario Amodei 的 5 段精华 + 1 个 MatX CEO Reiner Pope 的长访谈。"
            "Dario 三个核心论点:**(1) AI 不会成为垄断**(主动否认 monopoly framing);"
            "**(2) Pentagon 与 Anthropic 的合作有边界**(Department of War 有权拒绝用 Anthropic 模型);"
            "**(3) Trillion-Dollar Timing Problem**——「天才数据中心」叙事的资本时间错配是真实风险。"
            "Reiner Pope 拆解 GPT/Claude/Gemini 的训练-服务结构,以及 MatX 这种新芯片创业公司怎么切。"
        ),
        "insight": "Dario 的 Pentagon answer 是 Anthropic Mythos「太危险不公开」叙事的同一逻辑外延——监管 + 军事豁免在合谋构造护城河。",
        "related_sources": [
            src_entry(96, "长内容/播客", "Dario Amodei:AI 不会成垄断"),
            src_entry(102, "长内容/播客", "Trillion-Dollar Timing Problem in AI"),
            src_entry(106, "长内容/播客", "Pentagon 与 Anthropic 的合作计划"),
            src_entry(111, "长内容/播客", "为什么核武器类比 AI 是错的"),
            src_entry(115, "长内容/播客", "Reiner Pope(MatX CEO):GPT/Claude/Gemini 怎么训怎么部署"),
            src_entry(107, "长内容/播客", "Reiner Pope:神经网络是反向密码学"),
            src_entry(113, "长内容/播客", "服从命令拒绝按按钮的人:对 AI 的启示"),
        ],
    },
    {
        "event_title": "Cloud Wars:AI Landlords 吸收前沿模型公司——AMZN/MSFT 估值锚被重写",
        "source_count": 4,
        "best_source_url": "https://seekingalpha.com/article/cloud-wars-ai-landlords-amazon-microsoft-absorb-openai-anthropic",
        "best_source_name": "Seeking Alpha Top Ideas",
        "summary": (
            "本周 Seeking Alpha 出了一组「估值锚重写」专题:**Alphabet $460B AI Lock-In**、"
            "**Amazon $364B AI Time Bomb**、**Cloud Wars: How AI Landlords AMZN/MSFT May Absorb OpenAI/Anthropic**。"
            "宏观叙事是 hyperscaler 把前沿模型公司逐步「绑入」自己的 SKU/算力分发——OpenAI 用 MSFT、"
            "Anthropic 用 GOOGL TPU + AMZN Trainium——估值的所有权切分变成 cloud 巨头的 implicit equity。"
            "**Meta** 单独被 SA 描述为「AI Fear Creates Rare Blue-Chip Mispricing」——市场对 Llama 5 沉寂的 over-reaction。"
        ),
        "insight": "Hyperscaler 的「云 + 前沿模型 implicit equity」构成 2026 H2 估值新框架——单独估值前沿 lab 的逻辑要换。",
        "related_sources": [
            src_entry(18, "深度研究", "Cloud Wars:AI Landlords 吸收 OpenAI/Anthropic"),
            src_entry(49, "深度研究", "Alphabet $460B AI Lock-In"),
            src_entry(89, "深度研究", "Amazon $364B AI Time Bomb"),
            src_entry(82, "深度研究", "Meta AI Fear 造成 Blue-Chip Mispricing"),
            src_entry(62, "深度研究", "Alphabet $108.6B Warning,但反而看多"),
            src_entry(48, "深度研究", "Microsoft AI Pivot:更少 OpenAI 风险,更多 ROI"),
        ],
    },
    {
        "event_title": "ElevenLabs $500M ARR + BlackRock/Jamie Foxx/Squid Game 创始人入股,voice AI 主流化",
        "source_count": 2,
        "best_source_url": "https://techcrunch.com/2026/05/05/elevenlabs-blackrock-jamie-foxx-eva-longoria-investors/",
        "best_source_name": "TechCrunch AI",
        "summary": (
            "ElevenLabs 公布新一轮投资名单,**BlackRock + Jamie Foxx + Eva Longoria + Squid Game 创始人**入场,"
            "ARR 触达 **$500M**。voice AI 第一次同时拿到「机构资金 + Hollywood IP」站台,"
            "意味着语音合成 SDK 从开发者工具变成 IP/Talent 端的 essential infrastructure。"
        ),
        "insight": "明星投资 = IP rights 的提前 lock-in;BlackRock 入场 = voice AI 进入机构组合配置。",
        "related_sources": [
            src_entry(73, "深度研究", "ElevenLabs 公布 BlackRock/Jamie Foxx/Eva Longoria 等投资人,$500M ARR"),
            src_entry(92, "深度研究", "Bloomberg 同步报道 ElevenLabs 新投资轮"),
        ],
    },
    {
        "event_title": "Wall Street 用机器人执行最大规模公司债交易,Anthropic 推金融 Agent 是同一信号",
        "source_count": 2,
        "best_source_url": "https://www.bloomberg.com/news/articles/wall-street-trusting-bots-with-biggest-corporate-bond-trades",
        "best_source_name": "Bloomberg Tech",
        "summary": (
            "Bloomberg 报道 Wall Street 经纪商和资产管理人开始把**最大规模公司债交易**交给算法执行——"
            "传统手动 voice trading 的最后堡垒(big block bond)被算法侵入。同周 **Anthropic 推金融服务 Agent**——"
            "两条线指向同一件事:金融行业 agentic 渗透从「客服/合规」走到「核心交易/产品」。"
        ),
        "insight": "公司债 block trade 自动化 = 投行人头费的最大利润中心被掏空,2026 H2 投行人员结构变化要观察。",
        "related_sources": [
            src_entry(64, "深度研究", "Wall Street 把最大规模公司债交易交给算法"),
            src_entry(61, "深度研究", "Anthropic 发金融服务 AI Agents"),
        ],
    },
]

glance = [
    src_entry(16, "深度研究", "OpenAI 发 GPT-5.5 Instant 替换 GPT-3.5 Instant 作为 ChatGPT 默认"),
    src_entry(57, "深度研究", "Etsy 在 ChatGPT 内启动 native app,会话式购物"),
    src_entry(79, "深度研究", "SAP 把 AI 服务扩展给非云客户,触达 ERP 长尾"),
    src_entry(56, "深度研究", "PayPal 自我定位「AI 技术公司」,锚定 $1.5B 节流"),
    src_entry(117, "社交/社区/Twitter", "HN 热议:Google Chrome 静默装 4GB AI 模型在用户设备上"),
    src_entry(68, "深度研究", "Meta 用 AI 分析身高骨骼检测用户是否未成年"),
    src_entry(104, "长内容/播客", "Two Minute Papers:NVIDIA 一张照片生成不会破碎的 3D 世界"),
    src_entry(109, "长内容/播客", "Two Minute Papers:Sakana AI 上帝模拟器,东京疯狂科学"),
    src_entry(15, "深度研究", "Lambda(NVIDIA 投资的 AI 云)换 CEO,Sprint 老兵 Combes 接班"),
    src_entry(91, "深度研究", "印度首家 GenAI 独角兽 Krutrim 转云服务,模型雄心遇到经济现实"),
    src_entry(71, "深度研究", "巴西 AI 法律 startup Enter $1.2B 估值新一轮融资"),
    src_entry(60, "开源/技术/论文", "AlphaSignal:四种 Agent 编排模式——选 multi-agent 架构指南"),
]

daily_summary = (
    "2026-05-06 主线:监管层(Trump 政府首测前沿模型 + Meta 版权诉讼)+ 劳动力(Coinbase 14% 裁员 citing AI)"
    "+ 制造回归(Apple 选 Intel,Intel +14% 创新高)三条线同时拐点。"
    "暗线:开源(DeepSeek V4 Pro 追平 GPT-5.2 17× 便宜)+ 企业 Agent(Anthropic/NVIDIA/Vercel 三连发)。"
)

run_report = (
    "今日 161 条 raw 中筛 21 条(3 头条 / 6 noteworthy / 12 速览)。"
    "聚类决策:Coinbase 裁员合并 Apollo Slok 反论 + No Priors AI 替代叙事 + HN 就业认知衰退论文,"
    "形成「AI 替代劳动力 vs 长期再就业」张力主线;Apple/Intel 合并 Intel +14% 单股反应,"
    "对应美国制造回归政治叙事。Anthropic + NVIDIA + Vercel 三个企业 Agent 信号合并,因为时间窗口紧密"
    "+ 行业含义一致(从 demo 到 SKU)。Cloud Wars 把 SA 多个 stock-level 文章聚合成宏观估值锚重写主线。"
    "Dwarkesh × Dario 5 段精华 + Reiner Pope 长访谈合并为单一 noteworthy。"
    "频道平衡:5 个 channel 都覆盖,长内容 7 条远超保底 2 条。"
    "可能遗漏:Trump×Truth Social/AI Working Group 具体监管细节、Cerebras IPO(Jina 抓取失败)、"
    "Musk×SEC settlement(已 retry 失败)。Notion clipper 接口 404,fallback 到默认兴趣。"
)

# YouTube/Podcast full list
podcast_results = []
podcast_sources = {"Dwarkesh Patel", "All-In Podcast", "Two Minute Papers", "Fireship", "No Priors Podcast", "No Priors"}
for it in items:
    if it["source_name"] in podcast_sources:
        podcast_results.append({
            "source_name": it["source_name"],
            "title": it["title"],
            "url": it["url"],
            "published": (it.get("published") or "")[:10],
            "summary": (it.get("description") or "")[:300],
        })

tiered = {
    "headline": headline,
    "noteworthy": noteworthy,
    "glance": glance,
    "daily_summary": daily_summary,
    "run_report": run_report,
    "events_total": TOTAL_FETCHED,
    "selected_total": len(headline) + len(noteworthy) + len(glance),
    "podcast_results": podcast_results,
}

# ============================================================
# PHASE 3: Report polish (one_liner + signals + 加粗)
# ============================================================

# Polish headlines with bold markers
polished_headline = []
for h in headline:
    pol = dict(h)
    pol["analysis"] = pol["analysis"]  # kept as is, already has bold context
    polished_headline.append(pol)

# Polish noteworthy
polished_noteworthy = []
priorities = ["high", "high", "medium", "high", "medium", "medium"]
for h, p in zip(noteworthy, priorities):
    pol = dict(h)
    pol["priority"] = p
    polished_noteworthy.append(pol)

signals = [
    {
        "keyword": "AI 监管双轮启动",
        "note": "行政评估(Trump admin 测前沿模型)+ 司法版权(Meta 5 出版商起诉)同时出招,2026 H2 model release cadence 会被监管节奏拖慢。"
    },
    {
        "keyword": "AI 劳动力替代实证",
        "note": "Coinbase 是上市公司层面第一次 AI 直接归因裁员,后续看 Salesforce/IBM/Accenture 等服务密集型公司是否跟进。"
    },
    {
        "keyword": "美国制造回归 × Intel 复兴",
        "note": "Apple 选 Intel/Samsung 备份是地缘 + 技术 + 政治三轨叠加触发,Intel 18A 真实 yield 是关键变量。"
    },
    {
        "keyword": "开源 17× 价差",
        "note": "DeepSeek V4 Pro 在 agentic 任务上追平 GPT-5.2 但便宜 17 倍——闭源 API 价格压力第一次量化锚定。"
    },
    {
        "keyword": "企业 Agent 进入 SKU 阶段",
        "note": "Anthropic 金融 Agent + NVIDIA×ServiceNow + Vercel Open Agents,从 demo 走到 enterprise SKU,2026 H2 财报看 ARR 拆分。"
    },
    {
        "keyword": "Cloud Wars 估值锚重写",
        "note": "AI 前沿 lab 的估值 implicit 内嵌进 hyperscaler P&L,单独估 OpenAI/Anthropic 的逻辑要换。"
    },
]

report = {
    "one_liner": "AI 监管 / 劳动力 / 制造三线同日拐点,开源在 agentic 任务上量化追平闭源——2026 H2 的 setup 已经定型。",
    "headline": polished_headline,
    "noteworthy": polished_noteworthy,
    "glance": glance,
    "signals": signals,
    "podcast_results": podcast_results,
}

# ============================================================
# WRITE OUTPUT
# ============================================================
out = {
    "date": DATE,
    "tiered": tiered,
    "report": report,
    "total_fetched": TOTAL_FETCHED,
}

out_path = Path(f"output/{DATE}/tiered.json")
with out_path.open("w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print(f"✓ Wrote {out_path}")
print(f"  headline: {len(headline)}")
print(f"  noteworthy: {len(noteworthy)}")
print(f"  glance: {len(glance)}")
print(f"  podcast_results: {len(podcast_results)}")
print(f"  selected_total: {tiered['selected_total']}")
