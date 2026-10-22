"""What the tool surface costs, per turn and per run: Assignment 3, Part D, task 1.

The bill is tools x tokens x turns: every model call is charged for the definitions
it carries. This script measures the tokens by difference, which is the only honest
way to count them, because a vendor's own preamble for tool use is charged once to
any call that carries a tool and is invisible in the definitions themselves.

    python eval/price_surface.py --measure     # 15 tiny calls to your model; writes eval/surface_counts.json
    python eval/price_surface.py               # prices a mean harness-arm run from the cached counts

The difference is taken against one fixed one-message request with no tools:

    the five together   prompt_tokens(request + five definitions) - prompt_tokens(request)
    one alone           prompt_tokens(request + that definition)  - prompt_tokens(request)
    the planner's list  prompt_tokens(request + the list in its message) - prompt_tokens(request)

It counts both surfaces: the starter's five, as its registry sent them (the fields
of tools.SENT each file had, with no examples, kept in eval/starter_definitions.json
from this repository's first commit), and yours, as your registry sends them now
(registry.schemas, examples rendered into each description).

Then it prices a mean run of the harness arm at your model's uncached input rate in
pricing.yaml. Each model call is charged for what it carried (checks/FORMATS.md,
section 11.10): a fill call for its one tool, a plan or revise call for the
planner's tool list, since submit_plan is the same before and after and is left out.
Three prices come out: with the starter's counts, with yours, and as if every call,
the planner's and reviser's included, had carried all five of yours and nothing else.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.plan import _tool_list  # noqa: E402
from harness import LAB  # noqa: E402
from harness import switches as switch_board  # noqa: E402
from harness.backends import make_backend  # noqa: E402
from harness.client import ModelClient  # noqa: E402
from harness.pricing import PriceSheet  # noqa: E402
from harness.ping import current_schemas  # noqa: E402
from harness.roles import roles  # noqa: E402
from harness.runner import is_free, is_trace  # noqa: E402
from harness.settings import load_settings  # noqa: E402
from harness.trace import read_trace  # noqa: E402
from tools import MODULES, SENT  # noqa: E402

COUNTS = ROOT / "eval" / "surface_counts.json"
STARTER = ROOT / "eval" / "starter_definitions.json"
SYSTEM = "You measure a tool surface. Do not call any tool."
MESSAGE = "Answer with the word ok."
HARNESS_ARM = {"flaky_writes": True, "readback_fail": False, "key_mode": "derived", "verify": "on"}


def starter_schemas() -> list[dict]:
    """The starter's five, as its registry sent them: the fields of tools.SENT each file
    had, and no examples. It had no "strict" field, so none goes out."""
    saved = json.loads(STARTER.read_text())
    return [{k: saved[m][k] for k in SENT if k in saved[m]} for m in MODULES]


def prompt_tokens(client, tools, message: str = MESSAGE) -> int:
    response = client.create(system=SYSTEM, messages=[{"role": "user", "content": message}],
                             tools=tools or None)
    return response.usage.prompt_tokens


def measure() -> dict:
    settings = load_settings(ROOT / "settings.yaml")
    client = ModelClient(make_backend(settings.provider, settings), PriceSheet(ROOT / "pricing.yaml"))
    mine, theirs = current_schemas(), starter_schemas()
    base = prompt_tokens(client, None)
    out = {"model": settings.model, "baseline_prompt_tokens": base, "before": {}, "after": {}}
    for label, schemas in (("before", theirs), ("after", mine)):
        out[label]["five"] = prompt_tokens(client, schemas) - base
        out[label]["each"] = {s["name"]: prompt_tokens(client, [s]) - base for s in schemas}
        listing = f"{MESSAGE}\n\nThe tools the plan may use:\n{_tool_list(schemas)}"
        out[label]["list"] = prompt_tokens(client, None, listing) - base
    out["cost_usd_of_measuring"] = round(client.totals()["cost_usd"], 6)
    COUNTS.write_text(json.dumps(out, indent=2) + "\n")
    return out


# -- pricing a mean run of the harness arm ----------------------------------------------

def harness_arm_calls() -> list[list[dict]]:
    """For each finished harness-arm run on the evaluation issues, its model_call lines."""
    answers = set(range(4201, 4207))
    runs = []
    for path in sorted((ROOT / "runs").glob("*.jsonl")):
        if not is_trace(path):
            continue
        try:
            records = read_trace(path)
        except json.JSONDecodeError:
            continue
        start = records[0] if records and records[0].get("type") == "run_start" else None
        if not start or start.get("lab") != LAB or start.get("way") != "planned":
            continue
        if is_free(start.get("backend") or "") or start.get("issue") not in answers:
            continue
        if records[-1].get("type") != "run_end":
            continue
        if {**switch_board.DEFAULTS, **(start.get("switches") or {})} != HARNESS_ARM:
            continue
        runs.append([r for r in records if r.get("type") == "model_call"])
    return runs


def role_pairs(counts: dict) -> dict:
    """{role: {"before": starter's name, "after": yours}}, so a call that carried one of
    your tools can be priced with the starter's definition of the same role."""
    mine = {role: entry["tool"] for role, entry in roles().items()}
    order = list(counts["after"]["each"])
    before_order = list(counts["before"]["each"])
    by_position = dict(zip(order, before_order))
    return {role: {"after": name, "before": by_position.get(name, name)} for role, name in mine.items()}


def price_runs(counts: dict) -> dict:
    runs = harness_arm_calls()
    if not runs:
        return {}
    settings = load_settings(ROOT / "settings.yaml")
    rate = PriceSheet(ROOT / "pricing.yaml").find(settings.model).input / 1_000_000
    pairs = role_pairs(counts)
    per_run = {"before": [], "after": [], "all_five": []}
    for calls in runs:
        totals = {"before": 0, "after": 0, "all_five": 0}
        for call in calls:
            carried = [t for t in (call.get("tools") or []) if t != "submit_plan"]
            is_plan = call.get("purpose") in ("plan", "revise")
            totals["after"] += counts["after"]["list"] if is_plan else sum(
                counts["after"]["each"].get(n, 0) for n in carried)
            totals["before"] += counts["before"]["list"] if is_plan else sum(
                counts["before"]["each"].get(
                    next((p["before"] for p in pairs.values() if p["after"] == n), n), 0) for n in carried)
            totals["all_five"] += counts["after"]["five"]
        for key in per_run:
            per_run[key].append(totals[key])
    mean_calls = statistics.mean(len(c) for c in runs)
    return {
        "runs": len(runs),
        "calls_per_run": round(mean_calls, 2),
        "tokens_per_run": {k: round(statistics.mean(v), 1) for k, v in per_run.items()},
        "cost_per_run": {k: round(statistics.mean(v) * rate, 6) for k, v in per_run.items()},
        "input_rate_per_million": rate * 1_000_000,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--measure", action="store_true", help="make the 15 counting calls and cache them")
    a = p.parse_args(argv)
    counts = measure() if a.measure else (json.loads(COUNTS.read_text()) if COUNTS.exists() else None)
    if counts is None:
        print("no cached counts: run `python eval/price_surface.py --measure` first")
        return 1
    print(f"model {counts['model']}, a one-message request with no tools: "
          f"{counts['baseline_prompt_tokens']} prompt tokens\n")
    print(f"{'':<22}{'starter':>10}{'yours':>10}{'factor':>9}")
    print(f"{'the five together':<22}{counts['before']['five']:>10}{counts['after']['five']:>10}"
          f"{counts['after']['five'] / max(counts['before']['five'], 1):>8.1f}x")
    for (bn, bv), (an, av) in zip(counts["before"]["each"].items(), counts["after"]["each"].items()):
        print(f"{an:<22}{bv:>10}{av:>10}{av / max(bv, 1):>8.1f}x   (was {bn})")
    label = "the planner's list"
    print(f"{label:<22}{counts['before']['list']:>10}{counts['after']['list']:>10}"
          f"{counts['after']['list'] / max(counts['before']['list'], 1):>8.1f}x")
    priced = price_runs(counts)
    if not priced:
        print("\nno finished harness-arm runs yet: price a mean run after Part D, task 3")
        return 0
    print(f"\na mean harness-arm run, {priced['runs']} runs, {priced['calls_per_run']} model calls each, "
          f"at ${priced['input_rate_per_million']:.2f} per million uncached input tokens:")
    for key, label in (("before", "the starter's definitions"), ("after", "yours"),
                       ("all_five", "all five of yours on every call")):
        print(f"  {label:<34}{priced['tokens_per_run'][key]:>10.1f} tok   "
              f"${priced['cost_per_run'][key]:.6f} per run")
    print("\nfor reports/LAB_03.json, surface:")
    print(json.dumps({
        "tokens_before": counts["before"]["five"], "tokens_after": counts["after"]["five"],
        "list_tokens_before": counts["before"]["list"], "list_tokens_after": counts["after"]["list"],
        "calls_per_run": priced["calls_per_run"],
        "cost_per_run_before": priced["cost_per_run"]["before"],
        "cost_per_run_after": priced["cost_per_run"]["after"],
        "cost_per_run_all_five": priced["cost_per_run"]["all_five"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
