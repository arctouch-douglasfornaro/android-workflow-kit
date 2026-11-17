#!/usr/bin/env python3
"""GitHub activity for a standup: the developer's own PRs and the reviews they gave, as JSON.

Standard library plus the `gh` CLI (already logged in), so every agent host runs it the same way.

    python3 github_activity.py [--period day|week] [--org quizlet] [--since YYYY-MM-DD]

`day` starts at the previous working day (Friday on a Monday), `week` at 7 days ago.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys

PR_FIELDS = "number,title,url,state,repository,isDraft,createdAt,updatedAt,closedAt"
DETAIL_FIELDS = "reviewDecision,statusCheckRollup,mergedAt,reviews,additions,deletions"
MAX_DETAILS = 25


def gh(*args: str) -> object:
    result = subprocess.run(["gh", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:2])}: {result.stderr.strip()[:300]}")
    return json.loads(result.stdout or "null")


def window_start(period: str, today: dt.date) -> dt.date:
    if period == "week":
        return today - dt.timedelta(days=7)
    back = 3 if today.weekday() == 0 else 2 if today.weekday() == 6 else 1
    return today - dt.timedelta(days=back)


def checks_state(rollup: list[dict] | None) -> str:
    states = {(item.get("conclusion") or item.get("state") or item.get("status") or "").upper() for item in rollup or []}
    if not states:
        return "none"
    if states & {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED"}:
        return "failing"
    if states & {"PENDING", "IN_PROGRESS", "QUEUED", "EXPECTED", "WAITING"}:
        return "pending"
    return "passing"


def search(org: str, since: str, *filters: str) -> list[dict]:
    return gh("search", "prs", *filters, "--owner", org, "--updated", f">={since}",
              "--limit", "100", "--json", PR_FIELDS) or []


def compact(pr: dict) -> dict:
    return {
        "repo": pr["repository"]["nameWithOwner"],
        "number": pr["number"],
        "title": pr["title"],
        "url": pr["url"],
        "state": "draft" if pr.get("isDraft") and pr["state"] == "open" else pr["state"],
        "created": pr["createdAt"][:10],
        "closed": None if pr.get("closedAt", "").startswith("0001") else (pr.get("closedAt") or "")[:10] or None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--period", choices=["day", "week"], default="day")
    parser.add_argument("--org", default="quizlet")
    parser.add_argument("--since", help="YYYY-MM-DD; overrides --period")
    args = parser.parse_args()

    today = dt.date.today()
    since = args.since or window_start(args.period, today).isoformat()
    try:
        login = gh("api", "user")["login"]
        authored = [compact(pr) for pr in search(args.org, since, "--author", "@me")]
        reviewed = [compact(pr) for pr in search(args.org, since, "--reviewed-by", "@me")
                    if pr["url"] not in {own["url"] for own in authored}]
    except (RuntimeError, ValueError, KeyError) as error:
        print(json.dumps({"error": str(error), "hint": "run `gh auth status`; the active account needs access to the org"}))
        return 1

    for pr in authored[:MAX_DETAILS]:
        if pr["state"] == "closed":
            continue
        try:
            detail = gh("pr", "view", pr["url"], "--json", DETAIL_FIELDS)
        except RuntimeError:
            continue
        pr["review_decision"] = detail.get("reviewDecision") or "NONE"
        pr["checks"] = checks_state(detail.get("statusCheckRollup"))
        pr["size"] = f"+{detail.get('additions', 0)}/-{detail.get('deletions', 0)}"
        pr["reviewers"] = sorted({r["author"]["login"] for r in detail.get("reviews") or []
                                  if r.get("author") and r["author"]["login"] != login})
        if detail.get("mergedAt"):
            pr["merged"] = detail["mergedAt"][:10]

    for pr in reviewed:
        try:
            detail = gh("pr", "view", pr["url"], "--json", "reviews")
        except RuntimeError:
            continue
        mine = [r for r in detail.get("reviews") or [] if (r.get("author") or {}).get("login") == login
                and (r.get("submittedAt") or "")[:10] >= since]
        pr["my_reviews"] = [{"state": r["state"], "date": r["submittedAt"][:10]} for r in mine]
    reviewed = [pr for pr in reviewed if pr.get("my_reviews")]

    print(json.dumps({
        "login": login, "org": args.org, "since": since, "today": today.isoformat(),
        "authored": authored, "reviewed": reviewed,
    }, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
