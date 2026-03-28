import pytest
from delivery.notion_writer import _build_daily_report_blocks, _build_clipper_summary_prompt


def test_build_daily_report_blocks():
    tiered = {
        "headline": [{
            "event_title": "GPT-5 Launch",
            "source_count": 5,
            "best_source_url": "https://openai.com",
            "best_source_name": "OpenAI Blog",
            "analysis": "Big release.",
            "related_urls": ["https://hn.com/1"]
        }],
        "noteworthy": [{
            "event_title": "LangChain v0.3",
            "source_count": 2,
            "best_source_url": "https://langchain.com",
            "best_source_name": "LangChain Blog",
            "summary": "Refactored messages.",
            "insight": "Easier agent dev."
        }],
        "glance": [{
            "title": "Mistral MoE",
            "url": "https://mistral.ai",
            "one_liner": "New model."
        }],
        "daily_summary": "Big day for AI.",
        "events_total": 45,
        "selected_total": 8
    }

    blocks = _build_daily_report_blocks(tiered, total_fetched=200)
    assert len(blocks) > 0

    heading_texts = [b["heading_2"]["rich_text"][0]["text"]["content"]
                     for b in blocks if b.get("type") == "heading_2"]
    assert any("头条" in t for t in heading_texts)
    assert any("值得关注" in t for t in heading_texts)
    assert any("速览" in t for t in heading_texts)


def test_build_daily_report_blocks_empty_tiers():
    tiered = {
        "headline": [],
        "noteworthy": [],
        "glance": [],
        "daily_summary": "Quiet day.",
        "events_total": 0,
        "selected_total": 0
    }
    blocks = _build_daily_report_blocks(tiered, total_fetched=50)
    assert len(blocks) > 0
    # Should still have section headings
    heading_texts = [b["heading_2"]["rich_text"][0]["text"]["content"]
                     for b in blocks if b.get("type") == "heading_2"]
    assert len(heading_texts) == 3


def test_build_daily_report_blocks_capped_at_98():
    tiered = {
        "headline": [{"event_title": f"Event {i}", "source_count": 1,
                       "best_source_url": f"https://example.com/{i}",
                       "best_source_name": "Test", "analysis": "A" * 200,
                       "related_urls": [f"https://r.com/{j}" for j in range(5)]}
                      for i in range(20)],
        "noteworthy": [],
        "glance": [],
        "daily_summary": "Lots of news.",
        "events_total": 20,
        "selected_total": 20
    }
    blocks = _build_daily_report_blocks(tiered, total_fetched=200)
    assert len(blocks) <= 98


def test_build_clipper_summary_prompt():
    prompt = _build_clipper_summary_prompt("Test Article", "https://example.com", "Body text here")
    assert "Test Article" in prompt
    assert "https://example.com" in prompt
    assert "summary" in prompt
