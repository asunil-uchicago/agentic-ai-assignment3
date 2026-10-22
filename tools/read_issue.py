"""issues_read: return one issue from the tracker. Untrusted content: anyone can file an issue.

Its definition, the only thing the model sees of it, is read_issue.json. The result
is a JSON object, so that a body cut at the cap says so in a field rather than in a
sentence the model has to notice (Part A, task 1).
"""

from __future__ import annotations

import json

MAX_BODY_CHARS = 12_000


def run(ctx, number):
    from harness.tracker import IssueNotFound
    from tools import ToolError

    try:
        issue = ctx.tracker.issue(int(number))
    except IssueNotFound:
        known = sorted(ctx.tracker.issue_numbers("eval") + ctx.tracker.issue_numbers("dev"))
        raise ToolError(f"no issue numbered {number} in the tracker. The numbers it holds are "
                        f"{', '.join(str(n) for n in known)}. Pass the number of the issue you were asked to "
                        "triage, as a bare integer. This will not succeed with any other number.")
    body = str(issue["body"])
    cut = body[:MAX_BODY_CHARS]
    return json.dumps({
        "number": int(issue["number"]),
        "title": str(issue["title"]),
        "author": str(issue["author"]),
        "opened": str(issue["opened"]),
        "body": cut,
        "body_chars": len(cut),
        "truncated": len(body) > len(cut),
    }, ensure_ascii=False)
