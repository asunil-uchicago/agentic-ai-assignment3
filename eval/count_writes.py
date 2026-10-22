"""Count Part D's two arms from the tracker, not from the traces: Part D, task 4.

The trace records what the harness believes; the question is what the tracker holds.
So every number below comes from tracker/tracker.db, and the traces are read only to
say which run_id belongs to which arm and how each run ended.

    python eval/count_writes.py              # the table, and the writes object for LAB_03.json
    python eval/count_writes.py --json       # the writes object alone

The arms are told apart by the four switches recorded in each run's run_start line
(checks/FORMATS.md, section 11.9), exactly as checks/check_03.py tells them apart:

    your harness   {flaky_writes: true, readback_fail: false, key_mode: derived, verify: on}
    the control    {flaky_writes: true, readback_fail: false, key_mode: uuid,    verify: off}

It also reads the tracker's own `attempts` log for `timeout_after_commit`, the
writes that happened and were reported as failures, and checks each of them against
a line of that run's call record with the same key. That log is the tracker's, for
counting here; nothing in tools/, ways/ or agent/ reads it to decide anything.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from eval.check_triage import check  # noqa: E402
from harness import LAB  # noqa: E402
from harness import switches as switch_board  # noqa: E402
from harness.calls import read_calls  # noqa: E402
from harness.formats import UNFINISHED, parse_triage_comment  # noqa: E402
from harness.runner import is_free, is_trace  # noqa: E402
from harness.trace import read_trace  # noqa: E402
from harness.tracker import Tracker  # noqa: E402

ARMS = {"harness": {"flaky_writes": True, "readback_fail": False, "key_mode": "derived", "verify": "on"},
        "control": {"flaky_writes": True, "readback_fail": False, "key_mode": "uuid", "verify": "off"}}
FIELDS = ("runs", "comments_per_run_max", "duplicate_comment_runs", "prs_correct_runs",
          "committed_timeouts", "escalated", "triage_passes")


def answers() -> dict:
    data = yaml.safe_load((ROOT / "fixtures" / "answers.yaml").read_text())["eval"]
    return {int(k): v for k, v in data.items()}


def arms() -> dict[str, list[dict]]:
    """Every finished planned run of this assignment on the evaluation issues, by arm."""
    known = answers()
    out = {arm: [] for arm in ARMS}
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
        if is_free(start.get("backend") or "") or start.get("issue") not in known:
            continue
        if records[-1].get("type") != "run_end":
            continue
        ends = [r.get("terminal_reason") for r in records if r.get("type") == "end"]
        if set(ends) & set(UNFINISHED):
            continue
        recorded = {**switch_board.DEFAULTS, **(start.get("switches") or {})}
        for arm, wanted in ARMS.items():
            if recorded == wanted:
                out[arm].append({"run_id": start["run_id"], "issue": start["issue"],
                                 "end": ends[-1] if ends else None})
    return out


def tally(db: sqlite3.Connection, run: dict) -> dict:
    """What the tracker holds for one run: its comments on its issue, and all its PRs."""
    comments = [dict(r) for r in db.execute(
        "SELECT * FROM comments WHERE run_id = ? AND issue = ? ORDER BY id", (run["run_id"], run["issue"]))]
    prs = [dict(r) for r in db.execute("SELECT * FROM prs WHERE run_id = ? ORDER BY id", (run["run_id"],))]
    file = parse_triage_comment(comments[0]["body"])[1] if comments else None
    branch = f"repomind/issue-{run['issue']}"
    right = [p for p in prs if p["branch"] == branch and p["state"] == "open"]
    want = 1 if file not in (None, "none") else 0
    return {"comments": comments, "prs": prs, "file": file,
            "prs_ok": len(right) == want and len(prs) == len(right)}


def count(db: sqlite3.Connection, tracker: Tracker, runs: list[dict], known: dict) -> dict:
    per, prs_ok, passes, escalated, committed = [], 0, 0, 0, 0
    for run in runs:
        t = tally(db, run)
        per.append(len(t["comments"]))
        prs_ok += t["prs_ok"]
        passes += check(tracker, run["run_id"], run["issue"], known[run["issue"]])
        escalated += run["end"] == "escalated"
        committed += db.execute(
            "SELECT COUNT(*) FROM attempts WHERE run_id = ? AND outcome = 'timeout_after_commit'",
            (run["run_id"],)).fetchone()[0]
    return {"runs": len(runs), "comments_per_run_max": max(per, default=0),
            "duplicate_comment_runs": sum(1 for n in per if n > 1), "prs_correct_runs": prs_ok,
            "committed_timeouts": committed, "escalated": escalated, "triage_passes": passes}


def unmatched_keys(db: sqlite3.Connection, runs: list[dict]) -> list[str]:
    """Every tracker row whose key no line of that run's call record carries. The call
    record is written at the tool boundary, so a row with no line is a write the harness
    does not know it made."""
    missing = []
    for run in runs:
        lines = read_calls(ROOT / "runs" / f"{run['run_id']}.calls.jsonl")
        keys = {(c.get("role"), c.get("key")) for c in lines
                if c.get("key") and c.get("outcome") in ("ok", "timeout")}
        for table, role in (("comments", "comment"), ("prs", "open_pr")):
            for row in db.execute(f"SELECT key FROM {table} WHERE run_id = ?", (run["run_id"],)):
                if (role, row[0]) not in keys:
                    missing.append(f"{run['run_id']}: {table} key {row[0]!r}")
    return missing


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", help="print the writes object alone")
    a = p.parse_args(argv)
    path = ROOT / "tracker" / "tracker.db"
    if not path.exists():
        print("no tracker database at tracker/tracker.db")
        return 1
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    tracker = Tracker(ROOT, flaky=False, readback_fail=False)
    known = answers()
    by_arm = arms()
    writes = {arm: count(db, tracker, runs, known) for arm, runs in by_arm.items()}
    if a.json:
        print(json.dumps({"writes": writes}, indent=2))
        db.close()
        return 0

    rows = [("Triage comments per run (max; should be 1)", "comments_per_run_max"),
            ("Runs with a duplicate comment", "duplicate_comment_runs"),
            ("Runs whose PRs are exactly right", "prs_correct_runs"),
            ("Writes that timed out and had committed", "committed_timeouts"),
            ("Runs that ended escalated", "escalated"),
            ("Triage success (the grounded check)", "triage_passes")]
    print(f"{'Measure':<44}{'your harness':>14}{'control':>10}")
    print(f"{'Finished runs':<44}{writes['harness']['runs']:>14}{writes['control']['runs']:>10}")
    for label, key in rows:
        print(f"{label:<44}{writes['harness'][key]:>14}{writes['control'][key]:>10}")

    for arm, runs in by_arm.items():
        missing = unmatched_keys(db, runs)
        print(f"\n{arm}: {len(runs)} runs; tracker rows with no call-record line of the same key: "
              f"{len(missing)}" + ("" if not missing else " -> " + "; ".join(missing[:5])))
        for run in runs:
            t = tally(db, run)
            flag = "" if len(t["comments"]) == 1 and t["prs_ok"] else "   <-- not one comment and the right PRs"
            print(f"  {run['run_id']:<34} issue {run['issue']} end {str(run['end']):<10} "
                  f"comments {len(t['comments'])} prs {len(t['prs'])} file {t['file']}{flag}")
    print("\nfor reports/LAB_03.json, writes:")
    print(json.dumps(writes, indent=2))
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
