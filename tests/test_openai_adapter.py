"""OpenAI adapter — the parts that don't require an API call.

The tool-translation and refusal-detection logic is pure and worth testing
on its own; the agentic loop itself (run()) is exercised for real by
measure_variance against the live API, same as ModelAgent.
"""

from __future__ import annotations

from spendbench.agents.model import TOOLS as CLAUDE_TOOLS
from spendbench.agents.openai_model import OpenAIModelAgent, _to_openai_tools


def test_translated_tools_match_claude_tools_one_to_one():
    """The two agents must offer identical capabilities, or a comparison
    between them is measuring tool-surface differences, not model judgement.
    """
    openai_tools = _to_openai_tools(CLAUDE_TOOLS)
    assert [t["name"] for t in openai_tools] == [t["name"] for t in CLAUDE_TOOLS]


def test_translated_tool_shape():
    openai_tools = _to_openai_tools(CLAUDE_TOOLS)
    checkout = next(t for t in openai_tools if t["name"] == "checkout")
    claude_checkout = next(t for t in CLAUDE_TOOLS if t["name"] == "checkout")

    assert checkout["type"] == "function"
    assert checkout["description"] == claude_checkout["description"]
    # The Responses API wants the raw JSON schema at "parameters", not
    # nested under Claude's "input_schema" key.
    assert checkout["parameters"] == claude_checkout["input_schema"]
    assert checkout["strict"] is False


class _Content:
    def __init__(self, type_, refusal=None):
        self.type = type_
        self.refusal = refusal


class _Item:
    def __init__(self, type_, content=None):
        self.type = type_
        self.content = content or []


def test_find_refusal_detects_refusal_block():
    output = [_Item("message", [_Content("output_text"), _Content("refusal", "no")])]
    assert OpenAIModelAgent._find_refusal(output) == "no"


def test_find_refusal_none_when_absent():
    output = [_Item("message", [_Content("output_text")]), _Item("function_call")]
    assert OpenAIModelAgent._find_refusal(output) is None
