#!/usr/bin/env python3
"""Content-bound workflow receipts; Python standard library, Git required.

API: snapshot(repo_root, base, *, use_cache=True) -> dict; verify_ready(repo_root,
run_dir, base=None, *, use_cache=True) -> dict (raises ValueError/RuntimeError).
An omitted base means HEAD for a new run, the pinned base for an existing run.
Always start a run with an explicit --base; use its recorded base_commit when
capturing the snapshot BEFORE manual review/device work. Pass that fingerprint
to record, not a newly captured end-of-work fingerprint. Positive manual records
require expected_fingerprint; FAIL/BLOCKED do not. Review requires current gate;
device (including NOT_REQUIRED and run) requires current gate and review.

Examples (global options precede the subcommand):
  python3 feature_state.py --repo-root /repo --base main snapshot
  python3 feature_state.py --repo-root /repo --base main run --run-dir .ai/workflow/x --phase gate --log gate.log -- ./gradlew test
  python3 feature_state.py --repo-root /repo record --run-dir .ai/workflow/x --phase review --status PASS --evidence review.md --expected-fingerprint BEFORE_REVIEW
  python3 feature_state.py --repo-root /repo record --run-dir .ai/workflow/x --phase device --status NOT_REQUIRED --reason "Documentation only" --evidence device.md --expected-fingerprint BEFORE_DEVICE
  python3 feature_state.py --repo-root /repo check --run-dir .ai/workflow/x

Trust boundary: manual review/device receipts attest to supplied evidence, not
its truth. These local JSON files are not tamper-proof signatures. `run` proves
only exit status and equal endpoint snapshots, not command quality or absence of
transient edits. Do not run concurrent writers in one run directory.
The expected fingerprint is a caller attestation of when work began, not a
cryptographic proof of review timing. CLI run/record/check output is a summary;
full run endpoint snapshots remain in feature-state.json and Python run results.

The source universe is tracked files plus nonignored untracked files. Gitignored
untracked build outputs are not source. Only the exact workflow/profile paths
below are additionally excluded. Symlink targets are hashed without following
them; external referents are not source. Initialized gitlinks must be recursively
clean (including index and nonignored untracked files); their actual HEAD is bound.
Ignored outputs inside submodules are not source either.
All submodule HEAD-tracked raw bytes/modes must match HEAD, independent of index
flags or clean filters, including tracked ignored files and .ai/profile/workflow
paths. Their regular-file hashes reuse the superproject cache; no cache is ever
written inside a submodule. Dependency checkouts transformed by smudge/CRLF
filters fail closed if their raw bytes differ from HEAD.

snapshot and verify_ready also return content_fingerprint: SHA-256 over effective
existing source paths, raw SHA-256 file/link hashes, modes and clean gitlink HEADs.
Unlike fingerprint it excludes repository/branch/base/HEAD identity and deleted
tombstones, so committing the same working source preserves it. Ship should call
verify_ready BEFORE committing, then compare its content_fingerprint against
snapshot(repo_root, ready["base_commit"])["content_fingerprint"] AFTER hooks/commit.
The ordinary fingerprint intentionally becomes stale after a commit. Content
equality is not approval or proof that a commit's index matched working bytes.
Newly tracked ignored files enter the source universe and change this digest.

HEAD represents unchanged tracked inputs; raw Git blob hashes identify working
overrides, whose bytes/modes/deletions are fingerprinted with SHA-256. Regular-file
hashes are cached in .ai/workflow/_state-cache.json using fresh filesystem device,
inode, mode, size, mtime_ns AND ctime_ns (never Git index stat assumptions).
Snapshots leave source and Git state untouched but atomically write this excluded
local cache. Missing, malformed, incompatible or inaccessible caches are misses.
Symlinks and submodule checks are never cached. Global --no-cache bypasses all
cache reads/writes, as does use_cache=False in Python. Cache data is local trusted
metadata, not tamper-proof; use no-cache on filesystems without reliable ctime or
when an external actor can forge metadata/cache contents.
No base blobs are read except for changed source DI classification.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import time


VERSION = 1
PHASES = ("gate", "review", "device")
STATUSES = ("PASS", "FAIL", "BLOCKED", "NOT_REQUIRED")
STATE_FILE = "feature-state.json"
CACHE_PATH = ".ai/workflow/_state-cache.json"
CACHE_VERSION = 1
# Exact local metadata names, deliberately not a broad project-profile.* glob.
EXCLUDED = {
    ".ai/project-profile.md",
    ".ai/project-profile.meta.json",
    ".ai/project-profile.local.json",
}
BUILDS = {"build.gradle", "build.gradle.kts"}
DI = re.compile(rb"@(Inject|Provides|Binds|Module|InstallIn|Component|Subcomponent)\b"
                rb"|\b(dagger|hilt|koin)\b", re.I)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _git(root, *args):
    env = os.environ.copy()
    # Do not let an inherited index/worktree redirect a supposedly scoped query.
    for key in tuple(env):
        if key.startswith("GIT_") and key not in {
                "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM"}:
            del env[key]
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1")
    result = subprocess.run(
        ["git", "--no-pager", "-C", str(root), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    if result.returncode:
        raise RuntimeError("git " + args[0] + ": " +
                           result.stderr.decode("utf-8", "replace").strip())
    return result.stdout


def _root(repo_root):
    root = Path(repo_root).resolve(strict=True)
    actual = Path(os.fsdecode(_git(root, "rev-parse", "--show-toplevel")).strip()).resolve()
    if root != actual:
        raise ValueError("--repo-root must be the repository's working-tree root")
    return root


def _commit(root, ref):
    return _git(root, "rev-parse", "--verify", "--end-of-options",
                str(ref) + "^{commit}").decode().strip()


def _excluded(path):
    return path in EXCLUDED or path.startswith(".ai/workflow/")


def _names(data):
    return {os.fsdecode(p) for p in data.split(b"\0") if p}


def _tree(root, commit):
    result = {}
    for row in _git(root, "ls-tree", "-rz", "--full-tree", commit).split(b"\0"):
        if row:
            meta, path = row.split(b"\t", 1)
            mode, _, oid = meta.decode().split()
            result[os.fsdecode(path)] = (mode, oid)
    return result


def _safe_path(root, relative):
    """Reject traversal and symlink parents, never dereference source symlinks."""
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("unsafe relative path: " + relative)
    current = root
    for part in path.parts[:-1]:
        current /= part
        if current.is_symlink():
            raise ValueError("symlink parent: " + relative)
    return root.joinpath(*path.parts)


def _working(root, path, algorithm, cache=None, updated=None):
    target = _safe_path(root, path)
    try:
        before = target.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return {"mode": "deleted"}, None
    if stat.S_ISLNK(before.st_mode):
        data = os.fsencode(os.readlink(target))
        mode = "120000"
    elif stat.S_ISREG(before.st_mode):
        mode = "100755" if before.st_mode & 0o111 else "100644"
        key = str(target)
        identity = list(_stat_identity(before))
        hit = cache.get(key) if cache is not None else None
        if (isinstance(hit, dict) and hit.get("stat") == identity and
                hit.get("blob_algorithm") == algorithm and
                _hex_hash(hit.get("oid"), hashlib.new(algorithm).digest_size * 2) and
                _hex_hash(hit.get("sha256"), 64)):
            if _stat_identity(before) != _stat_identity(target.lstat()):
                raise RuntimeError("file changed during snapshot: " + path)
            if updated is not None:
                updated[key] = hit
            return {"mode": mode, "sha256": hit["sha256"]}, hit["oid"]
        # O_NOFOLLOW protects against a file becoming a symlink during the read.
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode) or
                    (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)):
                raise RuntimeError("file changed during snapshot: " + path)
            blob = hashlib.new(algorithm)
            blob.update(b"blob " + str(before.st_size).encode() + b"\0")
            sha = hashlib.sha256()
            while chunk := stream.read(1024 * 1024):
                blob.update(chunk)
                sha.update(chunk)
        after = target.lstat()
        if _stat_identity(before) != _stat_identity(after):
            raise RuntimeError("file changed during snapshot: " + path)
        if updated is not None:
            updated[key] = {"stat": identity, "sha256": sha.hexdigest(), "blob_algorithm": algorithm,
                            "oid": blob.hexdigest()}
        return {"mode": mode, "sha256": sha.hexdigest()}, blob.hexdigest()
    else:
        raise ValueError("unsupported source type (including gitlink directory): " + path)
    if _stat_identity(before) != _stat_identity(target.lstat()):
        raise RuntimeError("symlink changed during snapshot: " + path)
    blob = hashlib.new(algorithm, b"blob " + str(len(data)).encode() + b"\0" + data)
    return {"mode": mode, "sha256": _digest(data)}, blob.hexdigest()


def _stat_identity(info):
    # Reading may update atime; it is not source identity.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _hex_hash(value, length):
    return isinstance(value, str) and len(value) == length and re.fullmatch("[0-9a-f]+", value)


def _cache_header(root, algorithm):
    return {"version": CACHE_VERSION, "repo_root": str(root),
            "blob_algorithm": algorithm, "content_algorithm": "sha256"}


def _load_hash_cache(root, algorithm):
    try:
        path = _safe_path(root, CACHE_PATH)
        if path.is_symlink() or not path.is_file():
            return {}
        data = json.loads(path.read_bytes())
        if (isinstance(data, dict) and
                all(data.get(k) == v for k, v in _cache_header(root, algorithm).items()) and
                isinstance(data.get("files"), dict)):
            return data["files"]
    except (OSError, ValueError):
        pass
    return {}


def _save_hash_cache(root, algorithm, files):
    try:
        path = _safe_path(root, CACHE_PATH)
        if path.is_symlink():
            return
        _atomic(path, (_json(dict(_cache_header(root, algorithm), files=files)) + "\n").encode())
    except (OSError, ValueError):
        # Optimization only: failed cache persistence cannot alter attestation.
        pass


def _clean_submodule(root, path, cache=None, updated=None):
    """Check porcelain AND raw HEAD-tracked bytes recursively; cache in parent only."""
    target = _safe_path(root, path)
    setup = ("Initialize from the superproject with "
             "`git submodule update --init --recursive` (preserve local work first).")
    if target.is_symlink() or not target.is_dir() or not (target / ".git").exists():
        raise ValueError("uninitialized/unsafe submodule " + str(target) + ". " + setup)
    try:
        _root(target)
    except (ValueError, RuntimeError) as exc:
        raise RuntimeError("cannot verify submodule " + str(target) + ": " +
                           str(exc) + ". " + setup) from exc
    try:
        head = _commit(target, "HEAD")
        tree = _tree(target, head)
        index_bytes = _git(target, "ls-files", "--stage", "-z")
        algorithm = _git(target, "rev-parse", "--show-object-format").decode().strip()
        for nested, (mode, oid) in tree.items():
            if mode == "160000":
                _, actual_oid = _clean_submodule(target, nested, cache, updated)
                actual_mode = "160000"
            else:
                entry, actual_oid = _working(target, nested, algorithm, cache, updated)
                actual_mode = entry["mode"]
            if (actual_mode, actual_oid) != (mode, oid):
                raise ValueError(
                    "dirty submodule " + str(target) + ": tracked bytes/mode differ from HEAD: " +
                    nested + ". Inspect hidden index flags with `git ls-files -v`; "
                    "preserve local changes, then commit or restore the dependency before retrying.")
        dirty = _git(target, "status", "--porcelain=v1", "-z",
                     "--untracked-files=all", "--ignore-submodules=none")
        if dirty:
            raise ValueError(
                "dirty submodule " + str(target) +
                "; inspect `git status --porcelain --untracked-files=all "
                "--ignore-submodules=none` there and commit/stash/remove intended "
                "local changes before retrying. " + setup)
        if (_commit(target, "HEAD") != head or
                _git(target, "ls-files", "--stage", "-z") != index_bytes):
            raise RuntimeError("submodule HEAD/index changed during snapshot: " + str(target))
        return {"mode": "160000", "head": head}, head
    except RuntimeError as exc:
        raise RuntimeError("cannot verify submodule " + str(target) + ": " +
                           str(exc) + ". " + setup) from exc


def _widening(root, paths, base_tree, head_tree):
    reasons = set()
    for path in paths:
        p = PurePosixPath(path)
        parts = {part.lower() for part in p.parts}
        name = p.name.lower()
        if (name.endswith((".gradle", ".gradle.kts")) or
                name in {"gradle.properties", "gradlew", "gradlew.bat", "libs.versions.toml"} or
                parts & {"gradle", "buildsrc", "build-logic"}):
            reasons.add("build/settings change: " + path)
        if parts & {"res", "resources", "assets"} or name == "androidmanifest.xml":
            reasons.add("resources/manifest change: " + path)
        if (any("schema" in part or "migration" in part for part in parts) or
                name.endswith((".proto", ".graphql", ".graphqls", ".sql"))):
            reasons.add("schema/migration change: " + path)
        di = bool(parts & {"di", "dagger", "hilt", "injection"} or
                  re.search(r"(module|component|injector)\.(kt|java)$", name))
        if name.endswith((".kt", ".java", ".kts")):
            target = _safe_path(root, path)
            if target.is_file() and not target.is_symlink():
                di = di or bool(DI.search(target.read_bytes()))
            for tree in (base_tree, head_tree):
                entry = tree.get(path)
                if entry and entry[0] in ("100644", "100755"):
                    di = di or bool(DI.search(_git(root, "cat-file", "blob", entry[1])))
        if di:
            reasons.add("dependency-injection change (conservative heuristic): " + path)
    return sorted(reasons)


def snapshot(repo_root, base, *, use_cache=True):
    """Return deterministic JSON-compatible source identity and changed surface."""
    root = _root(repo_root)
    head = _commit(root, "HEAD")
    base_commit = _commit(root, base if base is not None else "HEAD")
    merge_base = _git(root, "merge-base", head, base_commit).decode().strip()
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").decode().strip()
    base_tree, head_tree = _tree(root, merge_base), _tree(root, head)
    index = {}
    index_bytes = _git(root, "ls-files", "--stage", "-z")
    for row in index_bytes.split(b"\0"):
        if row:
            meta, path = row.split(b"\t", 1)
            mode, oid, stage = meta.decode().split()
            if stage != "0":
                raise ValueError("unresolved Git conflicts")
            index[os.fsdecode(path)] = (mode, oid)
    source_paths = set(head_tree) | set(index)
    source_paths |= _names(_git(root, "ls-files", "--others", "--exclude-standard", "-z"))
    universe = set(base_tree) | source_paths
    universe = {p for p in universe if not _excluded(p)}
    gitlinks = {p for p in universe if any(tree.get(p, ("", ""))[0] == "160000"
                                         for tree in (base_tree, head_tree, index))}
    paths = {
        p for p in universe
        if base_tree.get(p) != head_tree.get(p) or index.get(p) != head_tree.get(p)
    }
    algorithm = _git(root, "rev-parse", "--show-object-format").decode().strip()
    cache = _load_hash_cache(root, algorithm) if use_cache else None
    updated_cache = {} if use_cache else None
    overrides = {}
    effective = {}
    submodules = {}
    for path in sorted(universe):
        target = _safe_path(root, path)
        if path in gitlinks and (
                index.get(path, ("", ""))[0] == "160000" or
                (target.is_dir() and not target.is_symlink())):
            entry, oid = _clean_submodule(root, path, cache, updated_cache)
            submodules[path] = oid
        else:
            entry, oid = _working(root, path, algorithm, cache, updated_cache)
        if path in source_paths and entry["mode"] != "deleted":
            effective[path] = entry
        if head_tree.get(path) != (entry["mode"], oid):
            overrides[path] = entry
            paths.add(path)
    paths = sorted(paths)
    builds = {p for p in universe if PurePosixPath(p).name in BUILDS}
    modules = set()
    for path in paths:
        parent = PurePosixPath(path).parent
        while True:
            if any((parent / name).as_posix() in builds for name in BUILDS):
                modules.add(parent.as_posix())
                break
            if parent == PurePosixPath("."):
                break
            parent = parent.parent
    reasons = _widening(root, paths, base_tree, head_tree)
    reasons += ["submodule dependency change: " + p for p in paths if p in gitlinks]
    reasons.sort()
    result = {
        "version": VERSION, "repo_root": str(root), "branch": branch,
        "base_commit": base_commit, "merge_base": merge_base, "head": head,
        "paths": paths, "modules": sorted(modules),
        "widening_required": bool(reasons), "widening_reasons": reasons,
        "working_overrides": overrides,
        "submodules": submodules,
        "content_fingerprint": _digest(_json(effective).encode()),
    }
    # Detect obvious concurrent checkout/commit/index changes; no atomic filesystem
    # snapshot is possible. `run` additionally compares complete endpoint snapshots.
    if (_commit(root, "HEAD") != head or
            _git(root, "rev-parse", "--abbrev-ref", "HEAD").decode().strip() != branch or
            _git(root, "ls-files", "--stage", "-z") != index_bytes):
        raise RuntimeError("Git state changed during snapshot")
    result["fingerprint"] = _digest(_json(result).encode())
    if use_cache:
        _save_hash_cache(root, algorithm, updated_cache)
    return result


def _run_dir(root, run_dir):
    candidate = Path(run_dir)
    if ".." in candidate.parts:
        raise ValueError("run directory cannot contain '..'")
    if not candidate.is_absolute():
        candidate = root / candidate
    for parent in (candidate, *candidate.parents):
        if parent.is_symlink():
            raise ValueError("run directory cannot use symlinks")
    candidate = candidate.resolve()
    if candidate.is_relative_to(root):
        relative = candidate.relative_to(root).as_posix()
        if not relative.startswith(".ai/workflow/"):
            raise ValueError("in-repo run directory must be inside .ai/workflow/")
    return candidate


def _artifact(directory, name):
    candidate = Path(name)
    if ".." in candidate.parts:
        raise ValueError("artifact path cannot contain '..'")
    if candidate.is_absolute():
        try:
            candidate = candidate.relative_to(directory)
        except ValueError:
            raise ValueError("evidence/log must be inside run directory") from None
    target = _safe_path(directory, candidate.as_posix())
    if target.is_symlink() or target == directory / STATE_FILE:
        raise ValueError("unsafe or reserved artifact path")
    return target


def _atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".feature-state-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _load(root, directory, base):
    path = directory / STATE_FILE
    if path.is_symlink():
        raise ValueError("state file cannot be a symlink")
    if path.exists():
        state = json.loads(path.read_text())
        if (not isinstance(state, dict) or state.get("version") != VERSION or
                state.get("repo_root") != str(root) or
                not isinstance(state.get("phases"), dict) or
                not isinstance(state.get("base_commit"), str)):
            raise ValueError("invalid state or repository binding")
        if base is not None and _commit(root, base) != state["base_commit"]:
            raise ValueError("requested base differs from recorded base")
        return state
    return {"version": VERSION, "repo_root": str(root),
            "base_commit": _commit(root, base if base is not None else "HEAD"), "phases": {}}


def _save(directory, state):
    _atomic(directory / STATE_FILE, (_json(state) + "\n").encode())


def _evidence(directory, name):
    path = _artifact(directory, name)
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("evidence must be a nonempty regular file inside run directory")
    return {"path": path.relative_to(directory).as_posix(),
            "sha256": _digest(path.read_bytes())}


def record(repo_root, run_dir, base, phase, status, evidence, reason=None, *,
           expected_fingerprint=None, use_cache=True):
    """Record manual evidence. Gate PASS is available exclusively via run."""
    if phase not in PHASES or status not in STATUSES:
        raise ValueError("invalid phase/status")
    if phase == "gate" and status == "PASS":
        raise ValueError("gate PASS requires run, not manual record")
    if status == "NOT_REQUIRED" and (phase != "device" or not reason or not reason.strip()):
        raise ValueError("only device may be NOT_REQUIRED, with a nonempty reason")
    positive = status in ("PASS", "NOT_REQUIRED")
    if positive and not expected_fingerprint:
        raise ValueError("positive record requires --expected-fingerprint captured before work")
    root = _root(repo_root)
    directory = _run_dir(root, run_dir)
    state = _load(root, directory, base)
    current = snapshot(root, state["base_commit"], use_cache=use_cache)
    if positive:
        if expected_fingerprint != current["fingerprint"]:
            raise ValueError("expected fingerprint differs from current source; repeat the work")
        _validate_receipts(directory, state, current, PHASES[:PHASES.index(phase)])
    receipt = {
        "status": status, "fingerprint": current["fingerprint"],
        "evidence": _evidence(directory, evidence), "reason": reason,
        "source": "manual",
        "expected_fingerprint": expected_fingerprint,
        "trust_boundary": "Supplied evidence is a human/agent attestation, not independently verified.",
    }
    state["phases"][phase] = receipt
    _save(directory, state)
    return receipt


def _validate_receipts(directory, state, current, phases):
    """Validate against one snapshot, avoiding repeated full source scans."""
    for phase in phases:
        if phase not in PHASES:
            raise ValueError("invalid phase: " + phase)
        receipt = state["phases"].get(phase)
        if not isinstance(receipt, dict):
            raise ValueError("missing phase: " + phase)
        status = receipt.get("status")
        if status != "PASS" and not (
                phase == "device" and status == "NOT_REQUIRED" and
                isinstance(receipt.get("reason"), str) and receipt["reason"].strip()):
            raise ValueError("phase not approved: " + phase)
        if receipt.get("fingerprint") != current["fingerprint"]:
            raise ValueError("stale fingerprint: " + phase)
        if (receipt.get("source") == "manual" and
                receipt.get("expected_fingerprint") != current["fingerprint"]):
            raise ValueError("manual approval lacks matching expected fingerprint: " + phase)
        if phase == "gate" and (
                receipt.get("source") != "run" or receipt.get("exit_code") != 0 or
                receipt.get("before") != current["fingerprint"] or
                receipt.get("after") != current["fingerprint"]):
            raise ValueError("gate requires successful unchanged run")
        evidence = receipt.get("evidence")
        if not isinstance(evidence, dict) or not isinstance(evidence.get("path"), str):
            raise ValueError("missing evidence: " + phase)
        if _evidence(directory, evidence["path"]) != evidence:
            raise ValueError("evidence content changed: " + phase)


def check(repo_root, run_dir, base=None, phases=PHASES, *, use_cache=True):
    """Validate selected receipts and prerequisites using the pinned base."""
    root = _root(repo_root)
    directory = _run_dir(root, run_dir)
    state = _load(root, directory, base)
    current = snapshot(root, state["base_commit"], use_cache=use_cache)
    phases = tuple(phases)
    if not phases or any(phase not in PHASES for phase in phases):
        raise ValueError("check requires valid phases")
    required = PHASES[:max(PHASES.index(phase) for phase in phases) + 1]
    _validate_receipts(directory, state, current, required)
    return {"status": "PASS", "fingerprint": current["fingerprint"],
            "content_fingerprint": current["content_fingerprint"],
            "base_commit": state["base_commit"], "phases": list(required)}


def verify_ready(repo_root, run_dir, base=None, *, use_cache=True):
    """Ship seam: all three phases must approve the exact current source."""
    try:
        return check(repo_root, run_dir, base, use_cache=use_cache)
    except OSError as exc:
        raise RuntimeError(str(exc)) from exc


def run(repo_root, run_dir, base, phase, log, command, *, use_cache=True):
    """Execute argv without a shell; persist non-PASS before starting the command."""
    if phase not in ("gate", "device") or not command:
        raise ValueError("run requires gate/device and a command")
    root = _root(repo_root)
    directory = _run_dir(root, run_dir)
    state = _load(root, directory, base)
    log_path = _artifact(directory, log)
    receipt = {"status": "BLOCKED", "source": "run", "command": list(command),
               "reason": "Command has not completed", "exit_code": None}
    state["phases"][phase] = receipt
    _save(directory, state)  # A failed/interrupted attempt must supersede old PASS.
    started = time.monotonic()
    temporary = None
    try:
        before = snapshot(root, state["base_commit"], use_cache=use_cache)
        receipt.update(fingerprint=before["fingerprint"], before=before["fingerprint"],
                       snapshot_before=before)
        if phase == "device":
            _validate_receipts(directory, state, before, ("gate", "review"))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".feature-run-", dir=log_path.parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(b"feature_state command: " + _json(command).encode() + b"\n")
            stream.flush()
            completed = subprocess.run(command, cwd=root, stdout=stream, stderr=subprocess.STDOUT)
            receipt["exit_code"] = completed.returncode
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, log_path)
        temporary = None
        receipt["evidence"] = _evidence(directory, log_path)
        after = snapshot(root, state["base_commit"], use_cache=use_cache)
        receipt["after"] = after["fingerprint"]
        receipt["snapshot_after"] = after
        if phase == "device":
            # A command must not revoke or replace its prerequisite evidence.
            state = _load(root, directory, state["base_commit"])
            state["phases"][phase] = receipt
            _validate_receipts(directory, state, after, ("gate", "review"))
        unchanged = before["fingerprint"] == after["fingerprint"]
        receipt["status"] = ("FAIL" if completed.returncode else
                             "PASS" if unchanged else "BLOCKED")
        receipt["reason"] = ("command failed" if completed.returncode else
                             "unchanged source" if unchanged else "source changed during command")
    except (OSError, ValueError, RuntimeError, KeyboardInterrupt) as exc:
        receipt["status"] = "FAIL" if receipt["exit_code"] not in (None, 0) else "BLOCKED"
        receipt["reason"] = str(exc) or "interrupted"
        if temporary is not None:
            os.replace(temporary, log_path)
            temporary = None
            receipt["evidence"] = _evidence(directory, log_path)
    finally:
        receipt["duration_seconds"] = round(time.monotonic() - started, 6)
        _save(directory, state)
        if temporary is not None:
            os.unlink(temporary)
    return receipt


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def _summary(result):
    keys = ("status", "fingerprint", "content_fingerprint", "base_commit", "phases",
            "reason", "duration_seconds", "exit_code")
    summary = {key: result[key] for key in keys if key in result}
    if "evidence" in result:
        summary["evidence"] = result["evidence"]["path"]
    return summary


def main(argv=None):
    try:
        parser = _Parser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.add_argument("--repo-root", default=os.getcwd())
        parser.add_argument("--base", default=None)
        parser.add_argument("--no-cache", action="store_true",
                            help="Read all source bytes; do not read or write the local hash cache")
        subs = parser.add_subparsers(dest="action", required=True)
        subs.add_parser("snapshot")
        for action in ("record", "check", "run"):
            sub = subs.add_parser(action)
            sub.add_argument("--run-dir", required=True)
            sub.add_argument("--phase", choices=PHASES if action != "run" else ("gate", "device"),
                             required=action != "check", action="append" if action == "check" else "store")
            if action == "record":
                sub.add_argument("--status", choices=STATUSES, required=True)
                sub.add_argument("--evidence", required=True)
                sub.add_argument("--reason")
                sub.add_argument("--expected-fingerprint",
                                 help="Required for PASS/NOT_REQUIRED; snapshot fingerprint from before work")
            if action == "run":
                sub.add_argument("--log", required=True)
                sub.add_argument("command", nargs=argparse.REMAINDER)
        args = parser.parse_args(argv)
        if args.action == "snapshot":
            result = snapshot(args.repo_root, args.base, use_cache=not args.no_cache)
        elif args.action == "record":
            result = record(args.repo_root, args.run_dir, args.base, args.phase,
                            args.status, args.evidence, args.reason,
                            expected_fingerprint=args.expected_fingerprint, use_cache=not args.no_cache)
        elif args.action == "check":
            result = check(args.repo_root, args.run_dir, args.base, args.phase or PHASES,
                           use_cache=not args.no_cache)
        else:
            command = args.command
            if command[:1] == ["--"]:
                command = command[1:]
            result = run(args.repo_root, args.run_dir, args.base, args.phase, args.log, command,
                         use_cache=not args.no_cache)
        print(_json(result if args.action == "snapshot" else _summary(result)))
        return 0 if result.get("status", "PASS") in ("PASS", "NOT_REQUIRED") else 1
    except (OSError, ValueError, RuntimeError) as exc:
        print(_json({"status": "BLOCKED", "error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
