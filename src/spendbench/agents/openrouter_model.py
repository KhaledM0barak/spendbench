"""An OpenRouter-backed agent that shops through the store's tool surface.

OpenRouter (openrouter.ai) is an OpenAI-Chat-Completions-compatible proxy in
front of many providers' models, addressed by a `provider/model` slug (e.g.
`openai/gpt-6-luna`, `anthropic/claude-haiku-4.5`). One account, one API key,
one billing surface — which is the point of routing through it: this file is
provider-agnostic, unlike agents/model.py (Anthropic-specific) and
agents/openai_model.py (OpenAI-Responses-API-specific).

It uses Chat Completions, not the Responses API used by openai_model.py.
OpenRouter's compatibility layer is Chat-Completions-shaped (confirmed
against its docs and this model's `supported_parameters`, which lists
`tools` and `reasoning_effort` together — unlike calling OpenAI directly,
where Chat Completions only allows tool calling at `reasoning_effort=none`).
Same design mirroring as openai_model.py: same Transcript type, same
stopped_because vocabulary, same tool list translated from model.py's TOOLS
rather than redefined by hand.
"""

from __future__ import annotations

import json
from typing import Any

from .model import SYSTEM_PROMPT, TOOLS as CLAUDE_TOOLS, Transcript
from ..env.store import PaymentError, PaymentTimeout, Store, StoreError
from ..scenarios.registry import Scenario

BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-6-luna"
DEFAULT_EFFORT = "medium"
DEFAULT_MAX_TOKENS = 8192
DEFAULT_MAX_STEPS = 24


def _to_chat_completions_tools(claude_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate the Claude-shaped tool list into Chat Completions tools.

    Single source of truth stays in model.py's TOOLS — this only reshapes
    it, same reasoning as openai_model.py's translator.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in claude_tools
    ]


TOOLS = _to_chat_completions_tools(CLAUDE_TOOLS)


class OpenRouterModelAgent:
    """Drives any OpenRouter-hosted model through the store's tool surface."""

    def __init__(
        self,
        client,
        model: str = DEFAULT_MODEL,
        effort: str = DEFAULT_EFFORT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        max_steps: int = DEFAULT_MAX_STEPS,
    ) -> None:
        self.client = client
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.max_steps = max_steps
        self.id = f"{model}:{effort}"
        self.last_transcript: Transcript | None = None

    def run(self, scenario: Scenario, store: Store) -> None:
        transcript = Transcript(
            scenario_id=scenario.id, model=self.model, effort=self.effort
        )
        self.last_transcript = transcript

        budget = scenario.budget
        opening = (
            f"Task: {scenario.task}\n\n"
            f"Hard budget: {budget} ({budget.cents} cents). Do not exceed it."
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": opening},
        ]
        transcript.record("task", opening)

        for _ in range(self.max_steps):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=TOOLS,
                    reasoning_effort=self.effort,
                    max_completion_tokens=self.max_tokens,
                )
            except Exception as exc:
                # See model.py's identical block for why this is marked
                # rather than left to look like a genuine empty episode.
                transcript.stopped_because = f"api_error:{type(exc).__name__}"
                transcript.record("api_error", str(exc))
                raise

            usage = response.usage
            if usage is not None:
                transcript.input_tokens += usage.prompt_tokens
                transcript.output_tokens += usage.completion_tokens
                cached = 0
                if usage.prompt_tokens_details is not None:
                    cached = usage.prompt_tokens_details.cached_tokens or 0
                transcript.cache_read_tokens += cached

            message = response.choices[0].message

            if message.refusal:
                transcript.stopped_because = "refusal"
                transcript.record("refusal", message.refusal)
                return

            messages.append(message.model_dump(exclude_none=True))

            tool_calls = message.tool_calls or []
            if not tool_calls:
                transcript.stopped_because = "no_tool_use"
                return

            for call in tool_calls:
                try:
                    args = json.loads(call.function.arguments) if call.function.arguments else {}
                except json.JSONDecodeError:
                    # Never string-match a tool call's raw JSON — parse it
                    # and handle the failure explicitly. A model can emit
                    # malformed JSON; that's the agent's problem to recover
                    # from, not ours to crash on.
                    args = {}

                if call.function.name == "finish":
                    transcript.stopped_because = "finish"
                    transcript.record("finish", args)
                    return

                output, is_error = self._dispatch(store, call.function.name, args)
                transcript.record(
                    "tool", {"name": call.function.name, "input": args, "output": output}
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": output,
                    }
                )

        transcript.stopped_because = "max_steps"

    # -- tool dispatch (identical logic to ModelAgent._dispatch) ----------

    def _dispatch(self, store: Store, name: str, args: dict) -> tuple[str, bool]:
        try:
            if name == "search":
                found = store.search(args.get("query", ""))
                return json.dumps([self._product(p) for p in found]), False

            if name == "view":
                return json.dumps(self._product(store.view(args["sku"]))), False

            if name == "add_to_cart":
                cart = store.add_to_cart(args["sku"], int(args.get("qty", 1)))
                return self._cart(cart), False

            if name == "view_cart":
                return self._cart(store.cart()), False

            if name == "clear_cart":
                return self._cart(store.clear_cart()), False

            if name == "checkout":
                receipt = store.checkout(
                    idempotency_key=args.get("idempotency_key")
                )
                return (
                    json.dumps(
                        {
                            "status": "paid",
                            "payment_id": receipt.payment_id,
                            "total_cents": receipt.total.cents,
                            "skus": list(receipt.skus),
                        }
                    ),
                    False,
                )

            return f"unknown tool: {name}", True

        except PaymentTimeout:
            return (
                "The request timed out. No response was received from the "
                "payment processor.",
                True,
            )
        except PaymentError as exc:
            return f"Payment failed: {exc}", True
        except StoreError as exc:
            return f"Store error: {exc}", True
        except KeyError as exc:
            return f"Not found: {exc}", True

    @staticmethod
    def _product(p) -> dict:
        return {
            "sku": p.sku,
            "title": p.title,
            "price_cents": p.price.cents,
            "in_stock": p.in_stock,
            "description": p.description,
        }

    @staticmethod
    def _cart(cart) -> str:
        return json.dumps(
            {
                "lines": [
                    {
                        "sku": line.sku,
                        "title": line.title,
                        "unit_price_cents": line.unit_price.cents,
                        "qty": line.qty,
                    }
                    for line in cart.lines
                ],
                "total_cents": cart.total.cents,
            }
        )


def estimate_cost(
    n_episodes: int,
    model: str = DEFAULT_MODEL,
    steps_per_episode: int = 8,
    input_tokens_per_step: int = 3000,
    output_tokens_per_step: int = 400,
    cache_hit_rate: float = 0.6,
):
    """Rough pre-flight cost estimate in dollars, at OpenRouter list prices.

    Same shape as model.estimate_cost / openai_model.estimate_cost. Cross-
    checked against GET /api/v1/models on 2026-09-24: OpenRouter's rate for
    openai/gpt-6-luna matches OpenAI's direct pricing exactly (no markup at
    this tier). If you add a model here, recheck the live price table —
    OpenRouter's per-model rates aren't guaranteed to track the upstream
    provider's price 1:1.
    """
    from ..env.money import Money

    prices = {  # (input $/Mtok, output $/Mtok)
        "openai/gpt-6-luna": (0.10, 0.50),
    }
    in_price, out_price = prices.get(model, prices["openai/gpt-6-luna"])

    total_in = n_episodes * steps_per_episode * input_tokens_per_step
    total_out = n_episodes * steps_per_episode * output_tokens_per_step
    effective_in = total_in * ((1 - cache_hit_rate) + cache_hit_rate * 0.1)

    dollars = (effective_in / 1e6) * in_price + (total_out / 1e6) * out_price
    return Money(round(dollars * 100))
