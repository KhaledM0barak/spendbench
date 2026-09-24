"""Run one model across every scenario at high repeats and report variance.

This exists to answer one question before any real matrix is sized: how often
does the same agent produce a different outcome on the same scenario? A pooled
bootstrap cannot answer it, and guessing a repeat count instead of measuring one
is how benchmarks end up reporting noise.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .agents.model import ModelAgent
from .agents.model import estimate_cost as _claude_estimate_cost
from .agents.openai_model import OpenAIModelAgent
from .agents.openai_model import estimate_cost as _openai_estimate_cost
from .agents.openrouter_model import BASE_URL as _OPENROUTER_BASE_URL
from .agents.openrouter_model import OpenRouterModelAgent
from .agents.openrouter_model import estimate_cost as _openrouter_estimate_cost
from .runner import run_episode
from .scenarios.registry import all_scenarios
from .scoring.stats import (
    clean_rate,
    decompose,
    minimum_detectable_effect,
    recommended_repeats,
    violation_rate,
)
from .scoring.violations import Violation


def load_key(env_var: str) -> str | None:
    """Env var first, then a gitignored .env beside the repo root."""
    key = os.environ.get(env_var)
    if key:
        return key
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith(env_var):
                _, _, value = line.partition("=")
                value = value.strip().strip('"').strip("'")
                if value:
                    return value
    return None


#: Which provider a --model string routes to, and everything needed to build
#: that provider's client and agent. Adding a third provider means adding one
#: entry here plus its agent module — nothing else in this file changes.
PROVIDERS = {
    "anthropic": {
        "match": lambda model: model.startswith("claude-"),
        "env_var": "ANTHROPIC_API_KEY",
        "package": "anthropic",
        "estimate_cost": _claude_estimate_cost,
        "make_agent": lambda client, model, effort: ModelAgent(
            client, model=model, effort=effort
        ),
        "make_client": lambda pkg, key: pkg.Anthropic(api_key=key),
    },
    "openai": {
        "match": lambda model: model.startswith("gpt-"),
        "env_var": "OPENAI_API_KEY",
        "package": "openai",
        "estimate_cost": _openai_estimate_cost,
        "make_agent": lambda client, model, effort: OpenAIModelAgent(
            client, model=model, effort=effort
        ),
        "make_client": lambda pkg, key: pkg.OpenAI(api_key=key),
    },
    # Slugs of the form "provider/model" (openai/gpt-6-luna,
    # anthropic/claude-haiku-4.5, ...) route through OpenRouter instead of
    # a provider's own API — one account, one key, works for any model
    # OpenRouter carries. This must be checked after the bare-prefix
    # providers above, since a slug never collides with "claude-"/"gpt-".
    "openrouter": {
        "match": lambda model: "/" in model,
        "env_var": "OPENROUTER_API_KEY",
        "package": "openai",  # OpenRouter is Chat-Completions-compatible
        "estimate_cost": _openrouter_estimate_cost,
        "make_agent": lambda client, model, effort: OpenRouterModelAgent(
            client, model=model, effort=effort
        ),
        "make_client": lambda pkg, key: pkg.OpenAI(
            base_url=_OPENROUTER_BASE_URL, api_key=key
        ),
    },
}


def provider_for(model: str) -> dict:
    for provider in PROVIDERS.values():
        if provider["match"](model):
            return provider
    raise SystemExit(
        f"Don't know which provider '{model}' belongs to. Known prefixes: "
        + ", ".join(f"{p['env_var']}" for p in PROVIDERS.values())
    )


def _jsonable(value):
    """Best-effort conversion of SDK response objects to plain JSON.

    Transcript payloads mostly come from our own dicts/strings, but a
    `refusal` step carries the SDK's `stop_details` object straight through,
    and that's a pydantic model, not a dict.
    """
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spendbench-variance")
    parser.add_argument("--model", default="claude-haiku-4-5")
    parser.add_argument("--effort", default="medium",
                        help="ignored on models without effort support")
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument("--out", default="results/variance.json")
    parser.add_argument("--transcripts-dir", default=None,
                        help="where to write one JSON file per episode with "
                             "every tool call and result (default: a "
                             "'transcripts/<model>' folder beside --out). "
                             "Pass --no-transcripts to skip this.")
    parser.add_argument("--no-transcripts", action="store_true",
                        help="skip writing per-episode transcript files")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the cost estimate and exit")
    args = parser.parse_args(argv)

    scenarios = all_scenarios()
    episodes = len(scenarios) * args.repeats
    provider = provider_for(args.model)

    print(f"model    {args.model}")
    print(f"matrix   {len(scenarios)} scenarios x {args.repeats} repeats = {episodes} episodes")
    print(f"estimate ~{provider['estimate_cost'](episodes, args.model)} at list prices\n")
    if args.dry_run:
        return 0

    key = load_key(provider["env_var"])
    if not key:
        print(f"No {provider['env_var']} in the environment or in spendbench/.env.",
              file=sys.stderr)
        return 2

    import importlib
    try:
        pkg = importlib.import_module(provider["package"])
    except ImportError:
        print(f"pip install {provider['package']}", file=sys.stderr)
        return 2

    client = provider["make_client"](pkg, key)
    agent = provider["make_agent"](client, args.model, args.effort)

    write_transcripts = not args.no_transcripts
    transcripts_dir = None
    if write_transcripts:
        transcripts_dir = (
            Path(args.transcripts_dir) if args.transcripts_dir
            else Path(args.out).parent / "transcripts" / args.model
        )
        transcripts_dir.mkdir(parents=True, exist_ok=True)

    # runner.run_episode() deliberately swallows any exception agent.run()
    # raises and scores the episode anyway (see its docstring) — that's the
    # right call for an agent that genuinely misbehaves mid-task, but it
    # means an API/transport failure (rate limit, exhausted credit, network)
    # looks exactly like a normal completed episode from here, except the
    # store never got touched. ModelAgent marks these via
    # transcript.stopped_because = "api_error:...", which is the only way
    # this loop can tell "the model did nothing" apart from "the request
    # never landed". Three in a row and it's not noise — stop instead of
    # quietly burning the rest of the matrix into unusable episodes.
    API_ERROR_ABORT_THRESHOLD = 3

    results, transcripts, failures = [], [], 0
    consecutive_api_errors = 0
    started = time.time()
    aborted = False
    for i, scenario in enumerate(scenarios, 1):
        for rep in range(args.repeats):
            try:
                result = run_episode(scenario, agent)
            except Exception as exc:  # a dead episode must not kill the run
                failures += 1
                print(f"  ! {scenario.id} rep {rep}: {type(exc).__name__}: {exc}")
                continue
            t = agent.last_transcript
            is_api_error = t is not None and t.stopped_because.startswith("api_error:")

            # The whole point of the marker: an api_error episode never saw a
            # real model turn, so it must not enter the scored population —
            # scoring it would silently reproduce the exact corruption this
            # mechanism exists to prevent (see the block comment above). It's
            # still recorded (transcript, failure count) for visibility.
            if not is_api_error:
                results.append(result)

            if t is not None:
                transcripts.append({
                    "scenario": t.scenario_id,
                    "stopped_because": t.stopped_because,
                    "input_tokens": t.input_tokens,
                    "output_tokens": t.output_tokens,
                    "cache_read_tokens": t.cache_read_tokens,
                })
                if transcripts_dir is not None:
                    path = transcripts_dir / f"{t.scenario_id}-rep{rep:02d}.json"
                    path.write_text(json.dumps({
                        "scenario": t.scenario_id,
                        "model": t.model,
                        "effort": t.effort,
                        "rep": rep,
                        "stopped_because": t.stopped_because,
                        "input_tokens": t.input_tokens,
                        "output_tokens": t.output_tokens,
                        "cache_read_tokens": t.cache_read_tokens,
                        "steps": _jsonable(t.steps),
                    }, indent=2))

                if is_api_error:
                    failures += 1
                    consecutive_api_errors += 1
                    last_step = t.steps[-1]["payload"] if t.steps else ""
                    print(f"  ! {scenario.id} rep {rep}: {t.stopped_because}: "
                          f"{last_step}")
                    if consecutive_api_errors >= API_ERROR_ABORT_THRESHOLD:
                        print(
                            f"\n{consecutive_api_errors} consecutive API "
                            "failures — stopping early rather than scoring "
                            "the rest of the matrix as empty episodes. "
                            "Check your credit balance / rate limit and "
                            "re-run; already-collected episodes below are "
                            "still valid.",
                            file=sys.stderr,
                        )
                        aborted = True
                        break
                else:
                    consecutive_api_errors = 0
        done = len(results)
        print(f"[{i}/{len(scenarios)}] {scenario.id:26s} {done}/{episodes} episodes")
        if aborted:
            break

    elapsed = time.time() - started
    if not results:
        print("no episodes completed", file=sys.stderr)
        return 1

    print(f"\n{len(results)} episodes in {elapsed/60:.1f} min, {failures} failed\n")
    print(f"clean rate: {clean_rate(results)}\n")

    report = {
        "model": args.model,
        "repeats": args.repeats,
        "episodes": len(results),
        "failures": failures,
        "elapsed_seconds": round(elapsed, 1),
        "aborted_early": aborted,
        "transcripts_dir": str(transcripts_dir) if transcripts_dir else None,
        "violations": {},
    }

    print(f"{'violation':<20} {'rate':<28} within-var  flips  rec.repeats")
    print("-" * 82)
    for violation in Violation:
        rate = violation_rate(results, violation)
        d = decompose(results, violation)
        rec = recommended_repeats(d)
        print(f"{violation.value:<20} {str(rate):<28} "
              f"{d.within_var:>9.4f}  {d.flip_fraction:>4.0%}  {rec:>11d}")
        report["violations"][violation.value] = {
            "rate": rate.point,
            "ci": [rate.ci_low, rate.ci_high],
            "between_sd": d.between_sd,
            "within_var": d.within_var,
            "flip_fraction": d.flip_fraction,
            "recommended_repeats": rec,
            "per_scenario": {r.scenario_id: r.rate for r in d.per_scenario},
        }

    overall = max(
        (decompose(results, v) for v in Violation), key=lambda d: d.flip_fraction
    )
    verdict = recommended_repeats(overall)
    print(f"\nMost inconsistent violation: {overall}")
    print(f"Repeats justified by the measured inconsistency: {verdict}")
    print("MDE at that setting, baseline 0.2: "
          f"{minimum_detectable_effect(0.2, len(scenarios), verdict):.3f}")
    report["recommended_repeats"] = verdict
    report["transcript_summary"] = transcripts

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {out}")
    if transcripts_dir is not None:
        print(f"wrote {len(results)} episode transcripts to {transcripts_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
