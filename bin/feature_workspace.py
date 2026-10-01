#!/usr/bin/env python3
"""Resolve and create the feature-workflow working branch (or worktree).

Self-contained, token-cheap helper for the `workspace-agent` (feature-workflow
setup phase). From a ticket id + title it does ALL the deterministic work so the
agent only has to run it and report:

  * resolves the base/default branch (never hardcodes master/main);
  * derives `<username>` — preferring the prefix the person already uses on their
    own branches, then their git email, then their name;
  * builds `<username>/<TICKET>-<slug>`;
  * if on the base branch, creates the branch (or a worktree with `--worktree`,
    e.g. for parallel runs); reuses the branch if it already exists -- including a
    branch that exists only on `origin`, which starts from the remote tip rather
    than from base so pushed commits are never orphaned; no-op when already on a
    feature branch;
  * optionally writes the `00-workspace.md` summary itself (`--summary-file`).

Branch identity comes from the user's OWN branches only. With no `user.email` set,
nothing is attributable and the helper falls back to name/email derivation -- it
does not count the whole repo, which would put the run under whoever has the most
branches.

Tests: `python3 feature_workspace_test.py` (username attribution, start-point
selection, branch reuse).
"""
import argparse
import datetime
import os
import re
import shutil
import subprocess
import sys

TICKET_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")
MAX_SLUG = 60


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------

def slugify(title):
    """Lowercase, non-alphanumeric runs -> '-', trimmed, max 60 chars."""
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return slug[:MAX_SLUG].rstrip("-")


def is_valid_ticket(ticket):
    """True for ids like MF-1234 / PLATFORM-42 (matches ^[A-Z][A-Z0-9]*-\\d+$)."""
    return bool(ticket and TICKET_RE.match(ticket))


def build_feature_id(ticket, title, stamp=None):
    """`<TICKET>-<slug>`; falls back to `LOCAL[-<stamp>]-<slug>` for a missing/invalid ticket.

    `stamp` (e.g. a `MMDD` string) disambiguates ticket-less runs so two of them
    don't collide on the same branch name.
    """
    slug = slugify(title)
    if is_valid_ticket(ticket):
        prefix = ticket
    else:
        prefix = f"LOCAL-{stamp}" if stamp else "LOCAL"
    return f"{prefix}-{slug}" if slug else prefix


def derive_username(email=None, name=None):
    """`<username>` from identity: GitHub noreply `id+login@…` -> `login`;
    work email `jane.doe@…` -> `janedoe`; else slug of the name. "" if nothing usable."""
    if email and "@" in email:
        local = email.split("@", 1)[0]
        if "+" in local:  # GitHub noreply: id+login@...
            local = local.split("+", 1)[1]
        slug = re.sub(r"[^a-z0-9]+", "", local.lower())
        if slug:
            return slug
    if name:
        slug = re.sub(r"[^a-z0-9]+", "", name.lower())
        if slug:
            return slug
    return ""


def pick_username(prefix_counts, email=None, name=None):
    """Most authoritative `<username>`: the prefix the person already uses on their
    own branches (highest count wins), else derived from email/name.

    Returns (username, source) where source is 'existing-branches' | 'email' | 'name' | ''.
    """
    if prefix_counts:
        best = max(prefix_counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
        return best, "existing-branches"
    derived = derive_username(email, name)
    if not derived:
        return "", ""
    source = "email" if (email and "@" in email) else "name"
    return derived, source


def build_branch_name(username, feature_id):
    return f"{username}/{feature_id}"


def render_summary(result):
    """Render the `00-workspace.md` body from a result dict."""
    wt = result.get("worktree_path") or "-"
    return "\n".join([
        "# Workspace", "",
        "## Branch", result["branch"], "",
        "## Action", result["action"], "",
        "## Base branch", result["base_branch"], "",
        "## Worktree path", wt if wt != "-" else "n/a", "",
        "## Notes",
        f"- username `{result['username']}` (source: {result.get('username_source') or '-'})",
        f"- current branch at start: {result['current_branch']}",
    ]) + "\n"


# ---------------------------------------------------------------------------
# Git helpers (side-effecting; not unit-tested)
# ---------------------------------------------------------------------------

def git(args, repo_root, check=True):
    result = subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def resolve_base_branch(repo_root):
    """Remote default branch, e.g. `master`. Never hardcodes master/main."""
    ref = git(["symbolic-ref", "refs/remotes/origin/HEAD"], repo_root, check=False)
    if ref:
        return ref.rsplit("/", 1)[-1]
    for candidate in ("main", "master"):
        if git(["rev-parse", "--verify", f"refs/remotes/origin/{candidate}"], repo_root, check=False):
            return candidate
    return "master"


def current_branch(repo_root):
    return git(["rev-parse", "--abbrev-ref", "HEAD"], repo_root)


def local_branch_exists(branch, repo_root):
    return bool(git(["rev-parse", "--verify", f"refs/heads/{branch}"], repo_root, check=False))


def remote_branch_exists(branch, repo_root):
    return bool(git(["rev-parse", "--verify", f"refs/remotes/origin/{branch}"], repo_root, check=False))


def user_branch_prefixes(repo_root, email):
    """Count `<prefix>/...` branch prefixes whose tip commit was authored by `email`.

    This is how we reuse the team's existing convention for this person
    (e.g. `douglasfornaro` rather than guessing a bare first name).
    """
    counts = {}
    target = (email or "").strip().lower()
    if not target:
        # Without an identity there is nothing to attribute branches to. Counting every branch in
        # the repo makes the most prolific *other* engineer's (or a bot's) prefix win, and the run
        # then reports `username_source: existing-branches` while creating the branch under
        # someone else's name. Fall through to email/name derivation instead.
        return counts

    out = git(
        ["for-each-ref", "--format=%(authoremail)|%(refname:short)",
         "refs/heads", "refs/remotes/origin"],
        repo_root, check=False,
    )
    for line in out.splitlines():
        author, _, ref = line.partition("|")
        if author.strip("<> ").lower() != target:
            continue
        name = ref.split("/", 1)[1] if ref.startswith("origin/") else ref
        if "/" in name:
            prefix = name.split("/", 1)[0]
            counts[prefix] = counts.get(prefix, 0) + 1
    return counts


def sync_with_remote(branch, tree_root):
    """Fast-forward an existing local branch onto `origin/<branch>` when it is safely behind.

    Returns one of: `up-to-date`, `fast-forwarded`, `no-remote`, `ahead`, `diverged`.

    Only a true fast-forward is applied. Divergence is reported, never resolved -- a merge or a
    rebase here would rewrite work the caller has not seen, and the whole kit forbids that.
    """
    if not remote_branch_exists(branch, tree_root):
        return "no-remote"
    # Fully-qualified refs: a bare name goes through rev-parse's disambiguation, which could
    # resolve a tag or a path-like name instead of the branch we mean.
    local = git(["rev-parse", "--verify", f"refs/heads/{branch}"], tree_root, check=False)
    remote = git(["rev-parse", "--verify", f"refs/remotes/origin/{branch}"], tree_root, check=False)
    # An empty result means the ref did not resolve, which is a problem to report -- not the one
    # value that tells the agent everything is current.
    if not local or not remote:
        return "unknown-ref"
    if local == remote:
        return "up-to-date"
    behind = git(["rev-list", "--count", f"{branch}..origin/{branch}"], tree_root, check=False) or "0"
    ahead = git(["rev-list", "--count", f"origin/{branch}..{branch}"], tree_root, check=False) or "0"
    if int(behind) and not int(ahead):
        # A fast-forward can still fail -- an untracked or modified file the incoming commit would
        # overwrite. "On my feature branch with uncommitted work" is the ordinary Phase 0 state, so
        # report it and let the agent decide instead of dying with a traceback.
        res = subprocess.run(["git", "merge", "--ff-only", f"origin/{branch}"], cwd=tree_root,
                             capture_output=True, text=True)
        return "fast-forwarded" if res.returncode == 0 else "behind-blocked"
    if int(ahead) and not int(behind):
        return "ahead"
    return "diverged"


def bootstrap_worktree(source, path, timeout=300):
    """Make a fresh worktree buildable the way the main checkout already is. Best effort, discovered.

    `git worktree add` copies tracked files only. From what the main checkout actually has, this:
      * initialises the submodules it has initialised (`--reference` its copy, so nothing is
        downloaded twice), and when that fails copies their files at the same commit;
      * copies `local.properties` (SDK location) when the main checkout has one;
      * lists root-level git-ignored files it did not copy (signing or service config), for a human.
    Nothing is invented: a project without submodules or local.properties gets an empty report.
    """
    report = {"submodules": {}, "copied": [], "ignored_root_files_not_copied": []}

    def run(args, cwd):
        try:
            return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(args, 124, "", "timed out")

    if os.path.isfile(os.path.join(source, ".gitmodules")):
        for line in run(["submodule", "status", "--recursive"], source).stdout.splitlines():
            if not line or line[0] in "-U":
                continue
            fields = line[1:].split()
            if len(fields) < 2:
                continue
            sub = fields[1]
            reference = os.path.join(source, sub)
            done = run(["submodule", "update", "--init", "--reference", reference, "--", sub], path)
            if done.returncode == 0 and os.path.isdir(os.path.join(path, sub)) and os.listdir(os.path.join(path, sub)):
                report["submodules"][sub] = "initialised"
                continue
            wanted = run(["ls-tree", "HEAD", "--", sub], path).stdout.split()
            have = run(["rev-parse", "HEAD"], reference).stdout.strip()
            if len(wanted) >= 3 and wanted[2] == have and os.path.isdir(reference):
                shutil.copytree(reference, os.path.join(path, sub), symlinks=True, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns(".git"))
                report["submodules"][sub] = "copied files at the same commit (not a git repository)"
            else:
                report["submodules"][sub] = "failed: " + (done.stderr.strip().splitlines() or ["unknown"])[-1]

    for name in ("local.properties",):
        src, dst = os.path.join(source, name), os.path.join(path, name)
        if os.path.isfile(src) and not os.path.exists(dst) and run(["ls-files", "--error-unmatch", name], source).returncode != 0:
            shutil.copy2(src, dst)
            report["copied"].append(name)

    skip = {".DS_Store", "local.properties"}
    for name in sorted(os.listdir(source)):
        if name in skip or not os.path.isfile(os.path.join(source, name)) or os.path.exists(os.path.join(path, name)):
            continue
        if run(["check-ignore", "-q", name], source).returncode == 0:
            report["ignored_root_files_not_copied"].append(name)
    return report


def other_ticket_branches(ticket, branch, repo_root):
    """Local branches already carrying this ticket id, so a second run of it is a choice, not an accident."""
    listed = git(["for-each-ref", "refs/heads", "--format=%(refname:short)"], repo_root, check=False)
    marker = f"{ticket}-".upper()
    return [
        name for name in listed.split()
        if name != branch and (name.upper().startswith(marker) or f"/{marker}" in name.upper())
    ]


def emit(result):
    for key in ("base_branch", "username", "username_source", "feature_id",
                "branch", "current_branch", "action", "start_point", "sync", "worktree_path"):
        print(f"{key}: {result.get(key, '-')}")
    boot = result.get("bootstrap")
    if boot:
        for sub, outcome in boot["submodules"].items():
            print(f"bootstrap_submodule: {sub}: {outcome}")
        if boot["copied"]:
            print(f"bootstrap_copied: {', '.join(boot['copied'])}")
        if boot["ignored_root_files_not_copied"]:
            print(f"bootstrap_not_copied: {', '.join(boot['ignored_root_files_not_copied'])}")
    if result.get("other_ticket_branches"):
        print(f"other_ticket_branches: {', '.join(result['other_ticket_branches'])}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create the feature-workflow working branch/worktree")
    parser.add_argument("--ticket", required=True, help="Ticket id, e.g. MF-1234 (LOCAL- fallback if invalid)")
    parser.add_argument("--title", required=True, help="Short ticket title/description")
    parser.add_argument("--username", help="Override the derived `<username>` branch prefix")
    parser.add_argument("--base", help="Override the detected base/default branch")
    parser.add_argument(
        "--worktree", nargs="?", const="", default=None, metavar="PATH",
        help="Create an isolated worktree instead of an in-place branch "
             "(use for running workflows in parallel); optional explicit path",
    )
    parser.add_argument("--no-bootstrap", action="store_true",
                        help="skip submodule/local.properties setup in a new worktree")
    parser.add_argument("--summary-file", help="Write the 00-workspace.md summary here")
    parser.add_argument("--repo-root", default=os.getcwd())
    parser.add_argument("--dry-run", action="store_true", help="Print the plan; create nothing")
    args = parser.parse_args(argv)

    repo_root = os.path.abspath(args.repo_root)
    base = args.base or resolve_base_branch(repo_root)

    email = git(["config", "user.email"], repo_root, check=False)
    name = git(["config", "user.name"], repo_root, check=False)
    if args.username:
        username, source = args.username, "override"
    else:
        username, source = pick_username(user_branch_prefixes(repo_root, email), email, name)
    if not username:
        print("error: could not derive <username>; pass --username", file=sys.stderr)
        return 2

    # Upper-case before matching: `mf-2333` otherwise failed TICKET_RE and fell through to a
    # `LOCAL-<MMDD>-` id, so the ticket vanished from the branch, the commit subject and the PR
    # with no error anywhere.
    if args.ticket:
        args.ticket = args.ticket.strip().upper()
    stamp = None if is_valid_ticket(args.ticket) else datetime.datetime.now().strftime("%m%d")
    feature_id = build_feature_id(args.ticket, args.title, stamp)
    branch = build_branch_name(username, feature_id)
    current = current_branch(repo_root)

    result = {
        "base_branch": base, "username": username, "username_source": source,
        "feature_id": feature_id, "branch": branch, "current_branch": current,
        "worktree_path": "-",
    }

    use_worktree = args.worktree is not None
    if use_worktree:
        result["worktree_path"] = args.worktree or os.path.join(
            os.path.dirname(repo_root), f"wt-{feature_id}")

    if current == "HEAD":
        # Detached HEAD. `current_branch` returns the literal "HEAD", so the plain `!= base` test
        # below would report `noop-non-base` with `branch: HEAD` and exit 0, declaring the workspace
        # healthy. `feature_ship.py` refuses "HEAD" outright, so the run would complete every phase
        # and then hard-fail at delivery. Treat it like the base branch: create the feature branch.
        current = base
        result["current_branch"] = "HEAD (detached)"

    if current != base and not use_worktree:
        # Already on a feature branch and no worktree asked for — reuse it as-is; the active branch
        # is `current`, not the freshly-computed name, so report what's actually checked out.
        result["action"] = "noop-non-base"
        result["branch"] = current
        result["feature_id"] = current.split("/", 1)[1] if "/" in current else current
        result["worktree_path"] = "-"
        if args.dry_run:
            result["sync"] = "dry-run"
        else:
            # Every other path fetches first. This one runs `sync_with_remote`, so without a fetch
            # it compares against a stale `origin/<branch>` and reports `up-to-date` on a branch
            # that is actually behind -- the exact non-fast-forward the fetch prevents elsewhere.
            git(["fetch", "origin"], repo_root, check=False)
            result["sync"] = sync_with_remote(current, repo_root)
    elif current != base and use_worktree and current == branch:
        # A branch can only be checked out in one worktree. Asking for a worktree of the branch this
        # checkout is already on -- i.e. any retry after the first run moved us here -- would fail
        # with "already used by worktree at ...". The workspace is already correct; say so.
        result["action"] = "noop-non-base"
        result["branch"] = current
        result["worktree_path"] = "-"
        if args.dry_run:
            result["sync"] = "dry-run"
        else:
            git(["fetch", "origin"], repo_root, check=False)
            result["sync"] = sync_with_remote(current, repo_root)
    elif current != base and use_worktree:
        # `--worktree` is precisely for running a second ticket without disturbing the first, so
        # being on another feature branch is the normal case, not a reason to no-op. Silently
        # ignoring it created no worktree, reported the OTHER ticket's feature_id, and exited 0 --
        # so run #2 implemented its ticket on run #1's branch in the shared checkout.
        if args.dry_run:
            result["action"] = "dry-run-worktree"
        else:
            git(["fetch", "origin"], repo_root, check=False)
            exists = local_branch_exists(branch, repo_root)
            remote_only = not exists and remote_branch_exists(branch, repo_root)
            remote_base = f"origin/{base}" if remote_branch_exists(base, repo_root) else base
            path = result["worktree_path"]
            if exists:
                git(["worktree", "add", path, branch], repo_root)
                result["action"] = "reused-branch-worktree"
                result["start_point"] = branch
                result["sync"] = sync_with_remote(branch, path)
            else:
                start = f"origin/{branch}" if remote_only else remote_base
                git(["worktree", "add", path, "-b", branch, start], repo_root)
                result["action"] = "reused-remote-branch-worktree" if remote_only else "created-worktree"
                result["start_point"] = start
    elif args.dry_run:
        result["action"] = "dry-run-worktree" if use_worktree else "dry-run-branch"
    else:
        git(["fetch", "origin"], repo_root, check=False)
        exists = local_branch_exists(branch, repo_root)
        # A branch can exist only on origin — the worktree was pruned, the local ref deleted, or the
        # work was pushed from another machine. Branching off `base` there would orphan the remote
        # commits and make Phase 9's `push -u` a rejected non-fast-forward, so start from the remote
        # tip. The `fetch` above exists precisely to make this ref current; consult it.
        remote_only = not exists and remote_branch_exists(branch, repo_root)
        # Start from the REMOTE base, not the local one. The fetch above advances remote refs only,
        # so a stale local `master` would silently cut the feature branch behind origin — verified:
        # local master one commit behind origin left the new branch missing a pushed commit. The
        # spec resolves the base as `origin/<name>` for exactly this reason. Fall back to the local
        # name when there is no remote ref (a repo with no origin, or a brand-new base).
        remote_base = f"origin/{base}" if remote_branch_exists(base, repo_root) else base
        start_point = f"origin/{branch}" if remote_only else remote_base

        if use_worktree:
            path = result["worktree_path"]
            if exists:
                git(["worktree", "add", path, branch], repo_root)
                result["action"] = "reused-branch-worktree"
                result["start_point"] = branch
                result["sync"] = sync_with_remote(branch, path)
            else:
                git(["worktree", "add", path, "-b", branch, start_point], repo_root)
                result["action"] = "reused-remote-branch-worktree" if remote_only else "created-worktree"
                result["start_point"] = start_point
        else:
            if exists:
                git(["checkout", branch], repo_root)
                result["action"] = "reused-branch"
                # A local ref that exists is not necessarily current: pushed from another machine,
                # or left behind after a worktree was pruned. Checking it out and stopping leaves
                # HEAD on the stale tip, the already-pushed work absent from the tree, and Phase 9's
                # push rejected non-fast-forward -- the same failure the remote-only path prevents.
                result["start_point"] = branch
                result["sync"] = sync_with_remote(branch, repo_root)
            else:
                git(["checkout", "-b", branch, start_point], repo_root)
                result["action"] = "reused-remote-branch" if remote_only else "created-branch"
                result["start_point"] = start_point

    created_worktree = (
        use_worktree and not args.dry_run and result.get("worktree_path") not in (None, "-")
        and str(result.get("action", "")).endswith("worktree") and not args.no_bootstrap
    )
    if created_worktree:
        result["bootstrap"] = bootstrap_worktree(repo_root, result["worktree_path"])

    if is_valid_ticket(args.ticket):
        result["other_ticket_branches"] = other_ticket_branches(args.ticket, result.get("branch"), repo_root)

    if args.summary_file:
        os.makedirs(os.path.dirname(os.path.abspath(args.summary_file)), exist_ok=True)
        with open(args.summary_file, "w", encoding="utf-8") as f:
            f.write(render_summary(result))

    emit(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
