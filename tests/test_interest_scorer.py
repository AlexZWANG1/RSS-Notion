import pytest
import json
from unittest.mock import MagicMock, patch


def test_parse_clipper_items():
    from generator.interest_scorer import _parse_clipper_results

    mock_results = [
        {
            "properties": {
                "标题": {"title": [{"plain_text": "MCP Protocol Deep Dive"}]},
                "userDefined:URL": {"url": "https://example.com/mcp"},
                "标签": {"multi_select": [{"name": "研究"}]},
                "摘取时间": {"created_time": "2026-03-25T10:00:00.000Z"},
            }
        },
        {
            "properties": {
                "标题": {"title": [{"plain_text": "LangChain vs CrewAI"}]},
                "userDefined:URL": {"url": "https://example.com/compare"},
                "标签": {"multi_select": [{"name": "工具"}, {"name": "研究"}]},
                "摘取时间": {"created_time": "2026-03-24T08:00:00.000Z"},
            }
        },
    ]

    text = _parse_clipper_results(mock_results)
    assert "MCP Protocol Deep Dive" in text
    assert "研究" in text
    assert "LangChain vs CrewAI" in text
    assert "工具" in text


def test_parse_clipper_results_empty():
    from generator.interest_scorer import _parse_clipper_results
    text = _parse_clipper_results([])
    assert text == ""


def test_parse_tiered_response():
    from generator.interest_scorer import _parse_tiered_response

    raw = json.dumps({
        "headline": [{
            "event_title": "GPT-5 Launch",
            "source_count": 5,
            "best_source_url": "https://openai.com",
            "best_source_name": "OpenAI Blog",
            "analysis": "Big release with new capabilities.",
            "related_urls": ["https://hn.com/1"]
        }],
        "noteworthy": [{
            "event_title": "LangChain v0.3",
            "source_count": 2,
            "best_source_url": "https://langchain.com",
            "best_source_name": "LangChain Blog",
            "summary": "Refactored message system.",
            "insight": "Makes agent development easier."
        }],
        "glance": [{
            "title": "Mistral MoE",
            "url": "https://mistral.ai",
            "one_liner": "New open-source model released."
        }],
        "daily_summary": "Today was about GPT-5.",
        "events_total": 45,
        "selected_total": 8
    })

    result = _parse_tiered_response(raw)
    assert result is not None
    assert len(result["headline"]) == 1
    assert result["headline"][0]["event_title"] == "GPT-5 Launch"
    assert len(result["noteworthy"]) == 1
    assert len(result["glance"]) == 1
    assert result["daily_summary"] == "Today was about GPT-5."


def test_parse_tiered_response_with_code_fences():
    from generator.interest_scorer import _parse_tiered_response

    raw = '```json\n{"headline":[],"noteworthy":[],"glance":[],"daily_summary":"test","events_total":0,"selected_total":0}\n```'
    result = _parse_tiered_response(raw)
    assert result is not None
    assert result["daily_summary"] == "test"


def test_parse_tiered_response_invalid_json():
    from generator.interest_scorer import _parse_tiered_response
    result = _parse_tiered_response("not json at all")
    assert result is None


def test_parse_tiered_response_missing_key():
    from generator.interest_scorer import _parse_tiered_response
    raw = json.dumps({"headline": [], "noteworthy": []})  # missing glance, daily_summary
    result = _parse_tiered_response(raw)
    assert result is None


def test_parse_tiered_response_missing_run_report_ok():
    from generator.interest_scorer import _parse_tiered_response
    raw = json.dumps({
        "headline": [], "noteworthy": [], "glance": [],
        "daily_summary": "test", "events_total": 0, "selected_total": 0
    })  # missing run_report — should still parse
    result = _parse_tiered_response(raw)
    assert result is not None
    assert result["run_report"] == ""


def test_build_scoring_prompt_with_clipper():
    from generator.interest_scorer import _build_scoring_prompt
    from sources.models import SourceItem

    items = [
        SourceItem(title="Test Article", url="https://test.com", source_name="Test", description="Body text here"),
    ]
    clipper_text = "- MCP Deep Dive [研究] (2026-03-25)"

    system_prompt, user_prompt = _build_scoring_prompt(items, clipper_text, "")
    assert "信息编辑部主编" in system_prompt
    assert "MCP Deep Dive" in user_prompt
    assert "Test Article" in user_prompt
    assert "Body text here" in user_prompt


def test_build_scoring_prompt_fallback_to_interests():
    from generator.interest_scorer import _build_scoring_prompt
    from sources.models import SourceItem

    items = [
        SourceItem(title="Test", url="https://test.com", source_name="Test", description="Body"),
    ]

    system_prompt, user_prompt = _build_scoring_prompt(items, "", "关注: AI Agent")
    assert "AI Agent" in user_prompt
    assert "主动收藏" not in user_prompt
