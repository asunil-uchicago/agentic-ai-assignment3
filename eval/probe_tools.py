"""Call the registry from Python and read what comes back: Assignment 3, Part B, task 5.

It spends no tokens. It makes the calls the lecture's error contract is about, on a
tracker of its own in a scratch directory, and prints the result the model would
have been given and the line the call record kept:

    python eval/probe_tools.py

  an unknown tool          a protocol error: generic for the model, in full in the log
  arguments that are wrong a boundary rejection naming every field at fault
  a near miss              read_file on a path that does not exist, case included
  an empty and a long query, and a path filter that is not a glob
  a search that matches nothing, which is a success with count 0
  a hook that raises, and one that returns a reason
"""

from __future__ import annotations

import json
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness.calls import CallRecord, read_calls  # noqa: E402
from harness.scripted import role_call  # noqa: E402
from harness.switches import DEFAULTS  # noqa: E402
from harness.tracker import Tracker  # noqa: E402
from tools import RunContext, default_tools  # noqa: E402

DEV_ISSUE = 4192


def fresh(tmp: Path, label: str):
    run_id = f"probe-{label}-{uuid.uuid4().hex[:6]}"
    ctx = RunContext(run_id=run_id, tracker=Tracker(ROOT, state_dir=tmp / label, flaky=False),
                     repo_root=ROOT / "fixtures" / "repo",
                     calls=CallRecord(tmp / f"{run_id}.calls.jsonl", run_id, ROOT),
                     harness_log=tmp / f"{run_id}.harness.log", switches=dict(DEFAULTS))
    return default_tools(ctx), ctx


def show(title: str, tools, ctx, name: str, args: dict) -> None:
    result = tools.call(name, args)
    lines = read_calls(ctx.calls.path)
    log = Path(ctx.harness_log)
    print(f"\n=== {title}")
    print(f"  called      {name} {json.dumps(args, default=str)[:160]}")
    print(f"  is_error    {result.is_error}   escalate {result.escalate}")
    print(f"  content     {result.content[:400]}")
    print(f"  call record {[(c['attempt'], c['outcome'], c['role'], c['key']) for c in lines]}")
    if log.exists():
        print(f"  harness log {log.read_text().strip()[:300]}")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        tools, ctx = fresh(tmp, "unknown")
        show("an unknown tool name (a protocol error)", tools, ctx, "repo_delete_branch", {"branch": "main"})

        tools, ctx = fresh(tmp, "malformed")
        name, args = role_call("read_file", path="src/util/money.ts")
        show("a malformed argument (the boundary)", tools, ctx, name, {**args, "zz_unexpected": 1, "path": 12345})

        tools, ctx = fresh(tmp, "nearmiss")
        name, args = role_call("read_file", path="src/report.ts")
        show("read_file on src/report.ts (the near miss)", tools, ctx, name, args)

        tools, ctx = fresh(tmp, "searches")
        name, args = role_call("search", query="zqxwvkjh")
        show("a search that matches nothing (a success)", tools, ctx, name, args)
        for label, change in (("an empty query", {"query": ""}), ("a whitespace-only query", {"query": "   "}),
                              ("a 5,000-character query", {"query": "x" * 5000})):
            tools, ctx = fresh(tmp, f"search-{len(label)}-{label[3:9]}")
            name, args = role_call("search", query=change["query"])
            show(f"a malformed search: {label}", tools, ctx, name, args)
        tools, ctx = fresh(tmp, "glob")
        name, args = role_call("search", query="export", path="src/[unclosed")
        show("a malformed search: a path filter that is not a glob", tools, ctx, name, args)

        for label, hook in (("a hook that raises", lambda n, a: 1 / 0),
                            ("a hook that returns a reason", lambda n, a: "no writes from the probe")):
            tools, ctx = fresh(tmp, f"hook-{label[8:14]}")
            tools.hooks.append(hook)
            name, args = role_call("comment", number=DEV_ISSUE, body="label: bug\nfile: src/util/money.ts")
            show(label, tools, ctx, name, args)
            print(f"  comments    {len(ctx.tracker.comments(ctx.run_id))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
