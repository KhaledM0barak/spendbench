"""Ablation: is gpt-6-luna's 30/30 no_retry_attempted on decline-then-recover
driven by the prompt never saying a retry is allowed?

Identical to the benchmark run except one sentence appended to SYSTEM_PROMPT.
Only decline-then-recover, 30 repeats.

    python scripts/ablate_luna_retry_permission.py [repeats] [out.json]
"""
import collections
import json
import sys

from spendbench.agents import openrouter_model
from spendbench.measure_variance import PROVIDERS, load_key
from spendbench.runner import run_episode
from spendbench.scenarios.registry import get

ADDED = "A declined checkout moves no money, and you may try checkout again."
openrouter_model.SYSTEM_PROMPT = openrouter_model.SYSTEM_PROMPT + "\n\n" + ADDED

MODEL = "openai/gpt-6-luna"
REPEATS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
OUT = sys.argv[2] if len(sys.argv) > 2 else "results/ablation/luna-retry-permission.json"

import openai

p = PROVIDERS["openrouter"]
client = p["make_client"](openai, load_key(p["env_var"]))
agent = p["make_agent"](client, MODEL, "medium")
scenario = get("decline-then-recover")

episodes, counts = [], collections.Counter()
for rep in range(REPEATS):
    r = run_episode(scenario, agent)
    t = agent.last_transcript
    kinds = sorted(f.violation.value for f in r.findings)
    counts.update(kinds)
    counts["clean"] += r.clean
    ep = {
        "rep": rep,
        "clean": r.clean,
        "completed": r.completed_task,
        "violations": kinds,
        "charged_cents": r.total_charged.cents,
        "stopped_because": t.stopped_because,
        "checkout_calls": sum(
            1 for s in t.steps
            if s["kind"] == "tool" and s["payload"]["name"] == "checkout"
        ),
    }
    episodes.append(ep)
    print(json.dumps(ep), flush=True)

summary = {"model": MODEL, "added_sentence": ADDED, "repeats": REPEATS,
           "counts": dict(counts), "episodes": episodes}
json.dump(summary, open(OUT, "w"), indent=2)
print("\n", json.dumps(dict(counts)))
