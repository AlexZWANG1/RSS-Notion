"""Rebuild today's Notion page with improvements. One-time script."""
import os, sys, json, dotenv
sys.stdout.reconfigure(encoding='utf-8')
dotenv.load_dotenv()
from notion_client import Client
notion = Client(auth=os.environ.get('NOTION_TOKEN'))
page_id = '3391683183e681ff9893d7ca0f5c478b'

with open("output/2026-04-05/data.json", "r", encoding="utf-8") as f:
    data = json.load(f)

def rt(t, bold=False, italic=False):
    return {"type":"text","text":{"content":t},"annotations":{"bold":bold,"italic":italic}}
def lk(t, url):
    return {"type":"text","text":{"content":t,"link":{"url":url}},"annotations":{"bold":True}}
def h2(t): return {"type":"heading_2","heading_2":{"rich_text":t if isinstance(t,list) else [rt(t)]}}
def h3(t): return {"type":"heading_3","heading_3":{"rich_text":t if isinstance(t,list) else [rt(t)]}}
def p(t): return {"type":"paragraph","paragraph":{"rich_text":t if isinstance(t,list) else [rt(t)]}}
def bl(t): return {"type":"bulleted_list_item","bulleted_list_item":{"rich_text":t if isinstance(t,list) else [rt(t)]}}
def co(t, icon="💡", bg="default"):
    return {"type":"callout","callout":{"rich_text":t if isinstance(t,list) else [rt(t)],"icon":{"type":"emoji","emoji":icon},"color":bg}}
def div(): return {"type":"divider","divider":{}}
def tr(cells): return {"type":"table_row","table_row":{"cells":cells}}
def tbl(w, rows):
    return {"type":"table","table":{"table_width":w,"has_column_header":True,"has_row_header":False,"children":rows}}

blocks = []

# ===== MARKET SECTION =====
blocks.append(h2([rt("📈 市场日报 · 数据截至 4/2 收盘（4/4 Good Friday 休市）")]))

blocks.append(co([
    rt("$19B ",bold=True),rt("Anthropic ARR · "),
    rt("$650B ",bold=True),rt("AI Capex · "),
    rt("$111 ",bold=True),rt("WTI原油 · "),
    rt("$78B ",bold=True),rt("英伟达Q1指引 · "),
    rt("70% ",bold=True),rt("AI占DRAM"),
], icon="📊", bg="blue_background"))

blocks.append(tbl(4,[
    tr([[rt("指标",bold=True)],[rt("数据",bold=True)],[rt("指标",bold=True)],[rt("数据",bold=True)]]),
    tr([[rt("标普500")],[rt("🟢 6,582 +0.11%")],[rt("纳指")],[rt("🟢 21,879 +0.18%")]]),
    tr([[rt("道指")],[rt("🔴 46,505 -0.13%")],[rt("本周")],[rt("🟢 标普+3.4% 纳指+4.4%",bold=True)]]),
    tr([[rt("原油WTI")],[rt("🔴 $111.54 +11.4%",bold=True)],[rt("布伦特")],[rt("🔴 $109.03 +7.8%")]]),
    tr([[rt("恐慌指数")],[rt("23.87")],[rt("十年美债")],[rt("4.31%")]]),
    tr([[rt("黄金")],[rt("$4,703/oz")],[rt("汽油均价")],[rt(">$5/gal")]]),
]))

blocks.append(h3("M7"))
blocks.append(tbl(3,[
    tr([[rt("标的",bold=True)],[rt("收盘价",bold=True)],[rt("涨跌",bold=True)]]),
    tr([[rt("MSFT")],[rt("$373.46")],[rt("🟢 +1.2%")]]),
    tr([[rt("AAPL")],[rt("~$246")],[rt("🟢 +0.3%")]]),
    tr([[rt("GOOGL")],[rt("~$273")],[rt("🟢 +0.5%")]]),
    tr([[rt("META")],[rt("-")],[rt("-")]]),
    tr([[rt("AMZN")],[rt("~$200")],[rt("🟢 +0.4%")]]),
    tr([[rt("TSLA")],[rt("$360")],[rt("🔴 -5.42%")]]),
    tr([[rt("NVDA")],[rt("$177")],[rt("🟢 +0.93%")]]),
]))

blocks.append(h3("Semis + AI"))
blocks.append(tbl(3,[
    tr([[rt("标的",bold=True)],[rt("收盘价",bold=True)],[rt("涨跌",bold=True)]]),
    tr([[rt("AMD")],[rt("-")],[rt("🟢 +3.47%")]]),
    tr([[rt("SMCI")],[rt("-")],[rt("🟢 +3.15%")]]),
    tr([[rt("AVGO")],[rt("~$311")],[rt("-")]]),
    tr([[rt("TSM")],[rt("~$341")],[rt("-")]]),
    tr([[rt("PLTR")],[rt("~$146")],[rt("-")]]),
    tr([[rt("ARM")],[rt("-")],[rt("🔴 -2.5%")]]),
]))

blocks.append(h3("要点"))

# Paragraph 1: Iran/oil
blocks.append(p([
    rt("伊朗战争第 34 天，油价创 2022 年来新高。",bold=True),
    rt(" Trump 对伊朗持续施压，WTI 突破 "),
    rt("$111",bold=True),
    rt("，布伦特 "),
    rt("$109",bold=True),
    rt("。但本周标普 "),
    rt("+3.4%",bold=True),
    rt("、纳指 "),
    rt("+4.4%",bold=True),
    rt("，走出 V 形反转，是战争爆发以来第一个收涨周。"),
]))
blocks.append(bl([rt("📰 "),lk("CNBC: Trump Iran escalation","https://www.cnbc.com/2026/04/02/trump-iran-escalation-asian-stocks-oil-prices-markets.html")]))
blocks.append(bl([rt("📰 "),lk("CNN: US stocks Iran","https://www.cnn.com/2026/04/02/investing/us-stocks-iran")]))

# Paragraph 2: Semis
blocks.append(p([
    rt("半导体在油价暴涨日逆势走强。",bold=True),
    rt(" "),
    rt("AMD",bold=True),
    rt(" +3.47%，"),
    rt("SMCI",bold=True),
    rt(" +3.15%，"),
    rt("NVDA",bold=True),
    rt(" +0.93%。AI capex "),
    rt("$650B",bold=True),
    rt(" 共识，NVDA Q1 指引 "),
    rt("$78B",bold=True),
    rt("，GPU ASP 翻倍，"),
    rt("70%",bold=True),
    rt(" DRAM 被 AI 吃掉，memory shortage 预计延续到 2027。"),
]))
blocks.append(bl([rt("📰 "),lk("Bloomberg: AI chip memory shortage","https://www.bloomberg.com/graphics/2026-ai-boom-memory-chip-shortage/")]))
blocks.append(bl([rt("📰 "),lk("CNBC: AI memory shortage","https://www.cnbc.com/2026/01/10/micron-ai-memory-shortage-hbm-nvidia-samsung.html")]))

# Paragraph 3: Anthropic
blocks.append(p([
    rt("Anthropic $19B ARR，14 个月前只有 $1B。",bold=True),
    rt(" "),
    rt("Claude Code",bold=True),
    rt(" "),
    rt("$2.5B",bold=True),
    rt(" ARR（9 个月），企业占比 >50%。估值 "),
    rt("$380B",bold=True),
    rt("。"),
]))
blocks.append(bl([rt("📰 "),lk("CNBC: Anthropic $30B funding at $380B","https://www.cnbc.com/2026/02/12/anthropic-closes-30-billion-funding-round-at-380-billion-valuation.html")]))
blocks.append(bl([rt("📰 "),lk("Bloomberg: Anthropic nears $20B run rate","https://www.bloomberg.com/news/articles/2026-03-03/anthropic-nears-20-billion-revenue-run-rate-amid-pentagon-feud")]))
blocks.append(bl([rt("📰 "),lk("Yahoo Finance: Anthropic ARR surges to $19B","https://finance.yahoo.com/news/anthropic-arr-surges-19-billion-151028403.html")]))

# Paragraph 4: MSFT
blocks.append(p([
    rt("微软 $373，22x PE 是 3 年低点。",bold=True),
    rt(" Azure "),
    rt("+39%",bold=True),
    rt("，AI Foundry "),
    rt("80K",bold=True),
    rt(" 客户，Copilot "),
    rt("3.3%",bold=True),
    rt(" 渗透率，agent-framework 开源，计划 2027 年前自研 frontier models。"),
]))
blocks.append(bl([rt("📰 "),lk("CNBC: Anthropic model in Copilot","https://www.cnbc.com/2025/09/24/microsoft-adds-anthropic-model-to-microsoft-365-copilot.html")]))
blocks.append(bl([rt("📰 "),lk("CNBC: Microsoft Agent 365","https://www.cnbc.com/2025/11/18/microsoft-unveils-agent-365-to-help-companies-control-track-ai-agents.html")]))

# Paragraph 5: TSLA
blocks.append(p([
    rt("特斯拉 -5.42%，Q1 交付不及预期。",bold=True),
    rt(" 交付 "),
    rt("358K",bold=True),
    rt(" vs 预期 365K，库存积压 "),
    rt("50K",bold=True),
    rt("。高油价 + 通胀 + Musk 政治风险三重压力。"),
]))
blocks.append(bl([rt("📰 "),lk("CNBC: Tesla Q1 2026 deliveries","https://www.cnbc.com/2026/04/02/tesla-tsla-q1-2026-vehicle-delivery-production.html")]))

# F) 下周关注
blocks.append(co([rt("下周关注")],icon="🔮"))

# G) Divider
blocks.append(div())

# ===== DAILY REPORT (from tiered data, using _build_v2_blocks with priority injection) =====
sys.path.insert(0, ".")
from delivery.notion_writer import _build_v2_blocks

tiered = data.get("tiered", data)

# Inject priority field into noteworthy items for _build_v2_blocks
pmap = [
    ("Copilot", "low"), ("品牌", "low"),
    ("算力", "high"), ("供给", "high"),
    ("Agent 基础设施", "medium"), ("agent-framework", "medium"),
    ("Anthropic", "medium"), ("增长", "medium"),
    ("Hermes", "medium"), ("记忆", "medium"), ("自我改进", "medium"),
    ("微软", "medium"),
]
for item in tiered.get("noteworthy", []):
    t = item.get("event_title", "")
    item["priority"] = next((v for k, v in pmap if k in t), "medium")

daily = _build_v2_blocks(tiered, total_fetched=198, today="2026-04-05")
blocks.extend(daily)

print(f"Total: {len(blocks)} blocks")

# Delete existing blocks
existing = []
cursor = None
while True:
    kw = {'block_id': page_id, 'page_size': 100}
    if cursor:
        kw['start_cursor'] = cursor
    resp = notion.blocks.children.list(**kw)
    existing.extend(resp['results'])
    if not resp.get('has_more'):
        break
    cursor = resp['next_cursor']
print(f"Deleting {len(existing)} blocks...")
for b in existing:
    try:
        notion.blocks.delete(block_id=b['id'])
    except Exception:
        pass

# Write in batches of 100
for i in range(0, len(blocks), 100):
    batch = blocks[i:i+100]
    notion.blocks.children.append(block_id=page_id, children=batch)
    print(f"  Batch {i//100+1}: {len(batch)} blocks")
print("Done!")
