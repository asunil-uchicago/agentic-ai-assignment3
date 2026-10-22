"""pulls_open_draft: open a draft pull request on the mock remote. New in Assignment 3.

Its definition is open_pr.json. The mock remote needs no commits: a PR is a branch,
a title and a body, stored in the tracker's database. `idempotency_key` is not in
the definition: the registry derives it and passes it, as for issues_comment.

`result_for` builds the result from a tracker row, so a PR found by its branch
before an attempt, or read back after a timeout, comes back in the shape a PR this
call had just opened would.
"""

from __future__ import annotations

import json


def result_for(row: dict, confirmed_by: str = "write", already_open: bool | None = None) -> str:
    return json.dumps({
        "pr_id": f"pr-{row['id']}",
        "issue": int(row["issue"]),
        "branch": str(row["branch"]),
        "state": str(row.get("state") or "open"),
        "draft": bool(row.get("draft", 1)),
        "already_open": bool(row.get("replayed")) if already_open is None else bool(already_open),
        "confirmed_by": confirmed_by,
        "body_chars": len(str(row.get("body") or "")),
    }, ensure_ascii=False)


def run(ctx, number, branch, title, body, idempotency_key=None):
    from harness.tracker import IssueNotFound
    from tools import ToolError

    if not str(branch).strip() or not str(title).strip():
        raise ToolError("a pull request needs a branch and a title, and one of them is blank. The branch is "
                        f"repomind/issue-{number} and the title is one line naming the file and the fix. "
                        "This will not succeed until both carry text.")
    try:
        pr = ctx.tracker.open_pr(ctx.run_id, int(number), str(branch), str(title), str(body),
                                 key=idempotency_key)
    except IssueNotFound:
        raise ToolError(f"no issue numbered {number} in the tracker, so there is nothing to open a pull request "
                        "for. Pass the number of the issue being triaged. This will not succeed with any other "
                        "number.")
    return result_for(pr)
