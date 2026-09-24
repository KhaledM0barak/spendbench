"""OpenRouter adapter — the parts that don't require an API call."""

from __future__ import annotations

from spendbench.agents.model import TOOLS as CLAUDE_TOOLS
from spendbench.agents.openrouter_model import _to_chat_completions_tools


def test_translated_tools_match_claude_tools_one_to_one():
    tools = _to_chat_completions_tools(CLAUDE_TOOLS)
    assert [t["function"]["name"] for t in tools] == [t["name"] for t in CLAUDE_TOOLS]


def test_translated_tool_shape():
    tools = _to_chat_completions_tools(CLAUDE_TOOLS)
    checkout = next(t for t in tools if t["function"]["name"] == "checkout")
    claude_checkout = next(t for t in CLAUDE_TOOLS if t["name"] == "checkout")

    assert checkout["type"] == "function"
    assert checkout["function"]["description"] == claude_checkout["description"]
    assert checkout["function"]["parameters"] == claude_checkout["input_schema"]
