"""Commit, push and open the PR without an agent in the git loop.

An agent writes `pr-description.md`; everything mechanical happens here, in one process with real
timeouts: stage the change (never workflow files, secrets or build output), commit, push, and open
the PR/MR on the remote's host. It never force-pushes, never skips git hooks, and never works on
the base branch.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from android_workflow.paths import run_dir

EXCLUDED_PREFIXES = (".ai/",)
EXCLUDED_NAMES = frozenset({"local.properties", ".DS_Store"})
EXCLUDED_SUFFIXES = (".jks", ".keystore", ".log", ".hprof", ".apk", ".aab", ".mp4", ".orig", ".rej")
UNTRACKED_SOURCE_SUFFIXES = frozenset({
    ".kt", ".java", ".xml", ".kts", ".gradle", ".toml", ".pro", ".json", ".png", ".webp", ".svg", ".jpg",
})
BASELINE_NAME_PATTERN = re.compile(r"(?i)baseline[^/]*\.xml$")


def git(target: Path, *args: str, timeout: int = 300, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true"}
    try:
        return subprocess.run(
            ["git", "-C", str(target), *args], text=True, capture_output=True, input=stdin,
            timeout=timeout, check=False, env=env,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"`git {' '.join(args[:2])}` timed out after {timeout}s") from error


def tail(text: str, lines: int = 25) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def base_branch(target: Path, override: str | None) -> str:
    if override:
        return override
    head = git(target, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").stdout.strip()
    if head.startswith("origin/"):
        return head[len("origin/"):]
    for name in ("main", "master", "develop"):
        if git(target, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{name}").returncode == 0:
            return name
    raise ValueError("cannot detect the base branch; pass --base")


def stage_plan(target: Path) -> tuple[list[str], list[str]]:
    """`(paths to stage, untracked paths left out)` from `git status`, minus what never ships."""
    from android_workflow.cli import git_status_paths

    include: list[str] = []
    left_out: list[str] = []
    tracked_changes = set(git(target, "diff", "--name-only", "HEAD").stdout.splitlines())
    for name in git_status_paths(target):
        path = Path(name)
        if (
            name.startswith(EXCLUDED_PREFIXES)
            or path.name in EXCLUDED_NAMES
            or name.endswith(EXCLUDED_SUFFIXES)
            or BASELINE_NAME_PATTERN.search(name)
            or ("build" in path.parts[:-1] and "src" not in path.parts)
        ):
            continue
        if name in tracked_changes or (target / name).exists() is False:
            include.append(name)
        elif path.suffix in UNTRACKED_SOURCE_SUFFIXES or "src" in path.parts:
            include.append(name)
        else:
            left_out.append(name)
    return sorted(dict.fromkeys(include)), sorted(dict.fromkeys(left_out))


def pull_request_disclosures(gate: dict[str, Any], state: dict[str, Any], body: str) -> list[str]:
    """Lines the PR must carry that its author may not have written: draft issues, waived lint, skipped device."""
    lower = body.lower()
    lines = []
    draft = state.get("draft") or {}
    if draft.get("issues") and "not ready" not in lower:
        lines.append(f"**Not ready for review:** {draft.get('reason') or 'the workflow stopped before every check passed'}.")
        lines.extend(f"- {issue}" for issue in draft["issues"])
    waivers = gate.get("waivers") or []
    if waivers and "lint" not in lower:
        findings = sum(int(item.get("count") or 0) for item in waivers)
        files = sum(int(item.get("file_count") or 0) for item in waivers)
        lines.append(
            f"**Lint:** {findings} finding(s) in {files} file(s) this PR does not touch predate it and were left as is."
        )
    if state.get("device_partial") and "not verified" not in lower:
        lines.append(f"**Device:** partly verified; not verified on a device: {state['device_partial']}")
    device = (state.get("stages") or {}).get("T7") or {}
    if device.get("status") == "skipped" and device.get("reason") not in (None, "not_required"):
        if "device" not in lower:
            lines.append(f"**Device:** not verified. {device['reason']}")
    return lines


def remote_host(target: Path) -> tuple[str, str]:
    url = git(target, "remote", "get-url", "origin").stdout.strip()
    if "github" in url:
        return "github", url
    if "gitlab" in url:
        return "gitlab", url
    return "other", url


def compare_url(remote: str, branch: str, base: str) -> str | None:
    match = re.match(r"(?:git@|https?://)([^:/]+)[:/](.+?)(?:\.git)?$", remote)
    if not match:
        return None
    host, repo = match.groups()
    return f"https://{host}/{repo}/compare/{base}...{branch}?expand=1"


def open_pull_request(
    target: Path, host: str, remote: str, branch: str, base: str, subject: str, body_file: Path,
    timeout: int, draft: bool = False, keep_body: bool = False,
) -> dict[str, Any]:
    cli = {"github": "gh", "gitlab": "glab"}.get(host)
    if not cli or not shutil.which(cli):
        return {"status": "manual", "compare_url": compare_url(remote, branch, base),
                "reason": f"no {cli or 'CLI'} for this remote; open it from the compare URL"}
    env = dict(os.environ)
    if host == "github":
        from android_workflow.watch import github_env

        try:
            env.update(github_env(target))  # the logged-in account that can see this repository
        except ValueError:
            pass  # `gh` reports the real problem below

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run([cli, *args], cwd=target, text=True, capture_output=True, timeout=timeout, check=False,
                                  env=env)
        except subprocess.TimeoutExpired as error:
            raise ValueError(f"`{cli}` timed out after {timeout}s") from error

    if host == "github":
        existing = run("pr", "view", branch, "--json", "url,state", "-q", '.state + " " + .url')
        if existing.returncode == 0 and existing.stdout.startswith("OPEN "):
            url = existing.stdout.split(" ", 1)[1].strip()
            if keep_body:
                return {"status": "updated", "url": url, "body": "kept"}
            edited = run("pr", "edit", branch, "--body-file", str(body_file))
            return {"status": "updated", "url": url, **({} if edited.returncode == 0 else {"warning": tail(edited.stderr, 5)})}
        created = run(
            "pr", "create", "--base", base, "--head", branch, "--title", subject, "--body-file", str(body_file),
            *(["--draft"] if draft else []),
        )
    else:
        created = run(
            "mr", "create", "--target-branch", base, "--source-branch", branch, "--title", subject,
            "--description", body_file.read_text(encoding="utf-8"), "--yes", *(["--draft"] if draft else []),
        )
    if created.returncode != 0:
        raise ValueError(f"`{cli}` could not open the PR: {tail(created.stderr or created.stdout, 8)}")
    urls = re.findall(r"https?://\S+", created.stdout)
    return {"status": "created", "url": urls[-1] if urls else None, "draft": draft}


CHECKBOX = re.compile(r"^(\s*[-*]\s+\[)([ xX])(\]\s+)(.*)$")
COMMENT = re.compile(r"<!--(.*?)-->")
STRINGS_FILE = re.compile(r"(^|/)res/values[^/]*/strings[^/]*\.xml$")


def _item_key(text: str) -> str:
    return re.sub(r"\s+", " ", COMMENT.sub("", text)).strip().lower()


def _drop_condition(comment: str, visual: bool, changed: list[str]) -> bool | None:
    """A template line's `<!-- Delete if … -->`: True to delete it for this change, None when unknown."""
    text = comment.lower()
    if "delete" not in text:
        return None
    if "string" in text:
        return not any(STRINGS_FILE.search(path) for path in changed)
    if "ui" in re.findall(r"[a-z]+", text) or "visual" in text:
        return not visual
    return None


def normalize_checklist(body: str, template: str, ticket_id: str | None, visual: bool, changed: list[str]) -> tuple[str, list[str]]:
    """The template's checklist, decided by code instead of the author's guess.

    Each checklist item from the template goes back to the template's own state (a human ticks what a
    human must check), except the ticket link, ticked when the body names the ticket. An item whose
    `<!-- Delete if … -->` condition holds for this change (no strings changed, not a UI change) is
    removed. Returns the body and one note per change made."""
    template_items = {}
    for line in template.splitlines():
        match = CHECKBOX.match(line)
        if match:
            template_items[_item_key(match.group(4))] = (match.group(2), " ".join(COMMENT.findall(match.group(4))))
    if not template_items:
        return body, []
    notes: list[str] = []
    out: list[str] = []
    others = "\n".join(line for line in body.splitlines() if not CHECKBOX.match(line))
    names_ticket = bool(ticket_id) and str(ticket_id) in others
    for line in body.splitlines():
        match = CHECKBOX.match(line)
        key = _item_key(match.group(4)) if match else None
        if not match or key not in template_items:
            out.append(line)
            continue
        state, comment = template_items[key]  # the template's comment, even when the author dropped it
        if _drop_condition(comment, visual, changed):
            notes.append(f"removed: {key}")
            continue
        if re.search(r"\b(jira|issue|ticket)\b", key) and re.search(r"\blink", key) and names_ticket:
            state = "x"
        if state.lower() != match.group(2).lower():
            notes.append(f"{'ticked' if state == 'x' else 'unticked'}: {key}")
        out.append(f"{match.group(1)}{state}{match.group(3)}{match.group(4)}")
    return "\n".join(out) + ("\n" if body.endswith("\n") else ""), notes


def deliver(
    target: Path,
    subject: str | None = None,
    body: str | None = None,
    base: str | None = None,
    no_commit: bool = False,
    no_push: bool = False,
    no_pr: bool = False,
    keep_body: bool = False,
) -> dict[str, Any]:
    from android_workflow.cli import (
        AGENT_ATTRIBUTION_PATTERN,
        is_stub_pr_body,
        load_state,
        read_json,
        source_fingerprint,
        strip_agent_attribution,
        write_json,
    )

    started = time.monotonic()
    agent_dir = run_dir(target)
    state = load_state(target)
    spec = read_json(agent_dir / "ticket-spec.json")
    gate = read_json(agent_dir / "gate-report.json") if (agent_dir / "gate-report.json").is_file() else {}
    if state.get("status") != "completed":
        raise ValueError("run `finish` first (or `finish --draft` when the run stopped after the code was written)")
    finished = state.get("finished_fingerprint")
    if finished and finished != source_fingerprint(target):
        raise ValueError("source changed after `finish`; re-run the gate, review and `finish`")
    body_file = agent_dir / "pr-description.md"
    text = body_file.read_text(encoding="utf-8") if body_file.is_file() else ""
    if is_stub_pr_body(text):
        raise ValueError("pr-description.md is missing or still the stub; write the PR body first")

    ticket = spec.get("ticket") or {}
    subject = strip_agent_attribution(subject or f"{ticket.get('id')}: {ticket.get('title')}").strip()
    if not subject:
        raise ValueError("no commit subject; pass --subject")
    branch = git(target, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    base_name = base_branch(target, base)
    if branch in {base_name, "HEAD", ""}:
        raise ValueError(
            f"HEAD is `{branch or 'detached'}`, the base branch or no branch; create the ticket branch first "
            "(feature_workspace.py) so delivery never touches the base"
        )
    gitdir = Path(git(target, "rev-parse", "--absolute-git-dir").stdout.strip() or ".")
    operation = next(
        (name for name in ("MERGE_HEAD", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD") if (gitdir / name).exists()),
        None,
    )
    if operation:
        raise ValueError(f"a git operation is in progress ({operation}); finish or abort it first")

    result: dict[str, Any] = {"branch": branch, "base": base_name, "subject": subject}
    warnings: list[str] = []

    from android_workflow import pr_media

    text, media = pr_media.apply(agent_dir, strip_agent_attribution(text), spec)
    if media:
        result["media"] = media
        if media["unpaired"]:
            warnings.append("captures without a pair (use the same --name in before and after): "
                            + ", ".join(media["unpaired"]))
    template_path = spec.get("pr_template")
    if template_path and (target / template_path).is_file():
        from android_workflow.cli import source_changes
        from android_workflow.decide import visual

        text, checklist = normalize_checklist(
            text, (target / template_path).read_text(encoding="utf-8"), (spec.get("ticket") or {}).get("id"),
            visual(spec), source_changes(target))
        if checklist:
            result["checklist"] = checklist
    disclosures = pull_request_disclosures(gate, state, text)
    if disclosures:
        text = text.rstrip() + "\n\n" + "\n".join(disclosures) + "\n"
    body_file.write_text(text, encoding="utf-8")
    result["disclosures_added"] = disclosures

    paths, left_out = stage_plan(target)
    if left_out:
        warnings.append(f"left out {len(left_out)} untracked non-source file(s): {', '.join(left_out[:8])}")
    if no_commit:
        result.update({"stage": paths, "committed": False, "warnings": warnings})
        return result

    if paths:
        added = git(target, "add", "-A", "--pathspec-from-file=-", "--pathspec-file-nul", stdin="\0".join(paths))
        if added.returncode != 0:
            raise ValueError(f"git add failed: {tail(added.stderr, 6)}")
        staged = set(git(target, "diff", "--cached", "--name-only").stdout.splitlines())
        stray = sorted(staged - set(paths))
        if stray:
            git(target, "reset", "-q", "--", *stray)
            raise ValueError(f"unrelated files were already staged and were unstaged: {', '.join(stray[:8])}")
        message = agent_dir / "commit-message.txt"
        body_lines = [line for line in (body or "").strip().splitlines()[:3]]
        message.write_text(strip_agent_attribution(subject + ("\n\n" + "\n".join(body_lines) if body_lines else "")),
                           encoding="utf-8")
        committed = git(target, "commit", "-F", str(message), timeout=900)
        if committed.returncode != 0:
            raise ValueError(f"git commit failed (hooks run here): {tail(committed.stdout + committed.stderr, 15)}")
        result["committed"] = True
    else:
        ahead = git(target, "rev-list", "--count", f"origin/{base_name}..HEAD").stdout.strip() or "0"
        if int(ahead) == 0:
            raise ValueError("nothing to commit and the branch has no commits beyond the base")
        result["committed"] = False
        warnings.append("nothing left to stage; delivering the commits already on the branch")
    message_text = git(target, "log", "-1", "--format=%B").stdout
    if AGENT_ATTRIBUTION_PATTERN.search(message_text):
        cleaned = agent_dir / "commit-message.txt"
        cleaned.write_text(strip_agent_attribution(message_text), encoding="utf-8")
        git(target, "commit", "--amend", "-F", str(cleaned), "--no-edit")
        warnings.append("removed tool attribution from the commit message before pushing")
    result["sha"] = git(target, "rev-parse", "HEAD").stdout.strip()
    result["files"] = [line for line in git(target, "show", "--name-only", "--format=", "HEAD").stdout.splitlines() if line]

    if no_push:
        result.update({"pushed": False, "warnings": warnings, "duration_seconds": round(time.monotonic() - started, 1)})
        write_json(agent_dir / "delivery.json", result)
        return result
    pushed = git(target, "push", "-u", "origin", branch, timeout=900)
    if pushed.returncode != 0:
        raise ValueError(
            "push failed (a pre-push hook or a rejected update; never force-pushed): "
            f"{tail(pushed.stdout + pushed.stderr, 20)}"
        )
    result["pushed"] = True

    if no_pr:
        result.update({"pr": None, "warnings": warnings, "duration_seconds": round(time.monotonic() - started, 1)})
        write_json(agent_dir / "delivery.json", result)
        return result
    host, remote = remote_host(target)
    draft = bool((state.get("draft") or {}).get("issues"))
    pr = open_pull_request(
        target, host, remote, branch, base_name, subject, body_file, timeout=300, draft=draft, keep_body=keep_body,
    )
    result["pr"] = pr
    if media and pr.get("status") in {"created", "updated"} and pr.get("body") != "kept":
        media["opened"] = pr_media.reveal(media["folder"])  # one drag away for the Before/After cells
    result["warnings"] = warnings
    result["duration_seconds"] = round(time.monotonic() - started, 1)
    write_json(agent_dir / "delivery.json", result)
    return result
