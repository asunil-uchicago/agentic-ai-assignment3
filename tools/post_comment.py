"""issues_comment: write a comment on an issue. Public: whoever filed the issue can read it.

Its definition is post_comment.json. `idempotency_key` is not in the definition:
the model never sees or sets it. The registry derives it and passes it (Assignment
3, Part C, task 2), and the tracker accepts each key once.

`result_for` builds the result from a tracker row, so that a comment the registry
found by reading it back after a timeout comes back to the model in exactly the
shape a comment it wrote would: the model cannot tell a confirmed write from a
first write, and nothing in the result pretends otherwise.
"""

from __future__ import annotations

import json


def result_for(row: dict, confirmed_by: str = "write") -> str:
    return json.dumps({
        "comment_id": f"comment-{row['id']}",
        "issue": int(row["issue"]),
        "state": "posted",
        "duplicate": bool(row.get("replayed")),
        "confirmed_by": confirmed_by,
        "body_chars": len(str(row.get("body") or "")),
    }, ensure_ascii=False)


def run(ctx, number, body, idempotency_key=None):
    from harness.tracker import IssueNotFound
    from tools import ToolError

    if not str(body).strip():
        raise ToolError("the comment body is empty, and an empty comment is never posted. A body's first two "
                        "lines must be 'label: <bug | feature | question | docs>' and 'file: <path, or none>'. "
                        "This will not succeed until body carries them.")
    try:
        comment = ctx.tracker.post_comment(ctx.run_id, int(number), str(body), key=idempotency_key)
    except IssueNotFound:
        raise ToolError(f"no issue numbered {number} in the tracker, so there is nothing to comment on. Pass "
                        "the number of the issue being triaged. This will not succeed with any other number.")
    return result_for(comment)
