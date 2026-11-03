"""An OpenAI-backed agent that shops through the store's tool surface.

Mirrors `agents/model.py` (the Claude adapter) as closely as the two APIs
allow, so the two are a fair comparison and not an artifact of one harness
being more carefully built than the other:

- Same `Transcript` type, same `stopped_because` vocabulary (including the
  `api_error:<type>` marker — see model.py's docstring for why that exists).
- Same tool surface, translated from the Claude-shaped `TOOLS` list in
  model.py rather than redefined by hand, so the two agents are provably
  offered the same capabilities.
- Same design commitment: idempotency_key is a visible tool parameter, so
  this measures whether the agent *reaches for* the safety mechanism, not
  whether it can invent one from nothing.

This uses the Responses API (not Chat Completions), because tool calling
alongside a nonzero reasoning effort is only supported there as of the
model this was written against (gpt-6-luna, 2026-09).
"""

from __future__ import annotations

import json
from typing import Any

from .model import SYSTEM_PROMPT, TOOLS as CLAUDE_TOOLS, Transcript
from ..env.store import PaymentError, PaymentTimeout, Store, StoreError
from ..scenarios.registry import Scenario

DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_EFFORT = "medium"
DEFAULT_MAX_OUTPUT_TOKENS = 8192
DEFAULT_MAX_STEPS = 24


def _to_openai_tools(claude_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate the Claude-shaped tool list into Responses API function tools.

    Single source of truth stays in model.py's TOOLS — this only reshapes
    it, so the two agents can never drift into offering different tools.
    """
    return [
        {
            "type": "function",
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
            "strict": False,
        }
        for t in claude_tools
    ]


TOOLS = _to_openai_tools(CLAUDE_TOOLS)


class OpenAIModelAgent:
    """Drives an OpenAI model through the store's tool surface."""

    def __init__(
        self,
        client,
        model: str = DEFAULT_MODEL,
        effort: str = DEFAULT_EFFORT,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        max_steps: int = DEFAULT_MAX_STEPS,
    ) -> None:
        self.client = client
        self.model = model
        self.effort = effort
        self.max_output_tokens = max_output_tokens
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
        # The Responses API's `input` plays the role Claude's `messages` does:
        # the full conversation is resent each turn. We append the model's own
        # output items back in verbatim (not just their text), same reason
        # Claude's adapter appends response.content rather than a summary —
        # tool-call items carry ids the next turn's function_call_output must
        # reference.
        input_list: list[dict[str, Any]] = [{"role": "user", "content": opening}]
        transcript.record("task", opening)

        for _ in range(self.max_steps):
            try:
                response = self.client.responses.create(
                    model=self.model,
                    instructions=SYSTEM_PROMPT,
                    input=input_list,
                    tools=TOOLS,
                    reasoning={"effort": self.effort},
                    max_output_tokens=self.max_output_tokens,
                )
            except Exception as exc:
                # See model.py's identical block: this is an API/transport
                # failure, not the agent's judgement, and needs to be
                # distinguishable from a genuine empty episode.
                transcript.stopped_because = f"api_error:{type(exc).__name__}"
                transcript.record("api_error", str(exc))
                raise

            usage = response.usage
            if usage is not None:
                transcript.input_tokens += usage.input_tokens
                transcript.output_tokens += usage.output_tokens
                cached = 0
                if usage.input_tokens_details is not None:
                    cached = usage.input_tokens_details.cached_tokens or 0
                transcript.cache_read_tokens += cached

            refusal = self._find_refusal(response.output)
            if refusal is not None:
                transcript.stopped_because = "refusal"
                transcript.record("refusal", refusal)
                return

            input_list.extend(
                item.model_dump(exclude_none=True, mode="json")
                for item in response.output
            )

            tool_calls = [
                item for item in response.output if item.type == "function_call"
            ]
            if not tool_calls:
                transcript.stopped_because = "no_tool_use"
                transcript.record("text", response.output_text)  # see model.py
                return

            for call in tool_calls:
                try:
                    args = json.loads(call.arguments) if call.arguments else {}
                except json.JSONDecodeError:
                    # Never string-match a tool call's raw JSON — parse it and
                    # handle the failure explicitly, same rule that applies to
                    # Claude's tool inputs and for the same reason: a model can
                    # emit malformed JSON, and that's the agent's problem to
                    # recover from, not ours to crash on.
                    args = {}

                if call.name == "finish":
                    transcript.stopped_because = "finish"
                    transcript.record("finish", args)
                    return

                output, is_error = self._dispatch(store, call.name, args)
                transcript.record(
                    "tool", {"name": call.name, "input": args, "output": output}
                )
                input_list.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": output,
                    }
                )

        transcript.stopped_because = "max_steps"

    @staticmethod
    def _find_refusal(output) -> str | None:
        for item in output:
            if item.type != "message":
                continue
            for block in item.content:
                if block.type == "refusal":
                    return block.refusal
        return None

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
    """Rough pre-flight cost estimate in dollars, at list prices.

    Same shape as model.estimate_cost, different price table. gpt-6-luna:
    $0.10 / $0.50 per 1M tokens in/out (cached input ~$0.01/1M).
    """
    from ..env.money import Money

    prices = {  # (input $/Mtok, output $/Mtok)
        "gpt-6-luna": (0.10, 0.50),
    }
    in_price, out_price = prices.get(model, prices["gpt-6-luna"])

    total_in = n_episodes * steps_per_episode * input_tokens_per_step
    total_out = n_episodes * steps_per_episode * output_tokens_per_step
    effective_in = total_in * ((1 - cache_hit_rate) + cache_hit_rate * 0.1)

    dollars = (effective_in / 1e6) * in_price + (total_out / 1e6) * out_price
    return Money(round(dollars * 100))
