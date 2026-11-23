#!/usr/bin/env python3
"""Evals for the android-workflow agents: a case puts an agent in front of a synthetic app, checks what it
wrote, has a judge of another model score it against a rubric, and keeps the score per kit commit.

  skill_eval.py list
  skill_eval.py prepare --case ID          workspace + the exact prompt to spawn the agent with
  skill_eval.py check   --run RUN          deterministic checks on what the agent wrote
  skill_eval.py judge   --run RUN [--judge codex|claude] [--model M]
  skill_eval.py record  --run RUN [--agent-model M]   score, appended to the history, delta vs the last one
  skill_eval.py report                     latest score per case and its delta
  skill_eval.py clean   [--keep N]         remove old workspaces (default keeps the last 10)

The workspace path and the app in it never say they are an eval: an agent that knows it is being
tested behaves differently. Everything about the eval (case, checks, judge, return) lives in the run
folder outside the workspace.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import os
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

KIT = Path(__file__).resolve().parents[1]
EVALS = KIT / "evals"
CLI = ["python3", str(KIT / "bin" / "android-workflow")]
CLI_TEXT = "python3 ~/.ai/bin/android-workflow"
SECRET = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\b(password|passwd|token|secret|api[_-]?key)\s*[:=]", re.I)
WORDS = ("amber", "birch", "cedar", "delta", "ember", "fjord", "grove", "harbor", "iris", "juniper", "kelp",
         "linden", "maple", "north", "opal", "pine", "quartz", "river", "slate", "tundra", "umber", "willow")
JUDGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["scores", "summary"],
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "score", "reason"],
                "properties": {"id": {"type": "string"}, "score": {"type": "integer", "minimum": 0, "maximum": 10},
                               "reason": {"type": "string"}},
            },
        },
        "summary": {"type": "string"},
    },
}


def state_home() -> Path:
    configured = os.environ.get("ANDROID_WORKFLOW_STATE_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".cache" / "android-workflow"


def runs_dir() -> Path:
    return state_home() / "evals" / "runs"


def history_file() -> Path:
    return state_home() / "evals" / "history.jsonl"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def cases() -> dict[str, dict[str, Any]]:
    found = {}
    for path in sorted((EVALS / "cases").glob("*/case.json")):
        case = read_json(path)
        case["_dir"] = str(path.parent)
        found[case["id"]] = case
    return found


def load_run(run_id: str) -> dict[str, Any]:
    path = runs_dir() / run_id / "run.json"
    if not path.is_file():
        raise ValueError(f"no run {run_id}")
    return read_json(path)


def save_run(run: dict[str, Any]) -> None:
    write_json(runs_dir() / run["id"] / "run.json", run)


def dotted(data: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(data, dict) or part not in data:
            return None
        data = data[part]
    return data


def deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in extra.items():
        merged[key] = deep_merge(merged[key], value) if isinstance(value, dict) and isinstance(merged.get(key), dict) else value
    return merged


def run_cli(args: list[str], cwd: Path) -> dict[str, Any]:
    env = {**os.environ, "ANDROID_WORKFLOW_NO_WATCH": "1"}
    done = subprocess.run(CLI + args, text=True, capture_output=True, cwd=cwd, env=env, check=False)
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError:
        return {"exit": done.returncode, "stdout": done.stdout[-2000:], "stderr": done.stderr[-2000:]}


def git(workdir: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(workdir), *args], check=True, capture_output=True, text=True)


def sections(text: str) -> dict[str, str]:
    parts = re.split(r"^## ", text, flags=re.M)
    return {part.split("\n", 1)[0].strip(): part.split("\n", 1)[1].strip() if "\n" in part else "" for part in parts[1:]}


def kit_commit() -> str:
    done = subprocess.run(["git", "-C", str(KIT), "rev-parse", "--short", "HEAD"], text=True, capture_output=True)
    dirty = subprocess.run(["git", "-C", str(KIT), "status", "--porcelain", "--", "skills", "bin", "tools"],
                           text=True, capture_output=True).stdout.strip()
    return done.stdout.strip() + ("+dirty" if dirty else "")


# ---------------------------------------------------------------- prepare

def prepare(case_id: str) -> dict[str, Any]:
    case = cases().get(case_id)
    if not case:
        raise ValueError(f"unknown case {case_id}; known: {', '.join(cases())}")
    fixture = EVALS / "fixtures" / case["fixture"]
    name = f"{random.choice(WORDS)}-{random.choice(WORDS)}-{os.urandom(2).hex()}"
    workdir = state_home() / "ws" / name / case.get("project_dir", case["fixture"])
    shutil.copytree(fixture, workdir)
    git(workdir, "init", "-q", "-b", "main")
    git(workdir, "-c", "user.name=Dev", "-c", "user.email=dev@localhost", "add", "-A")
    git(workdir, "-c", "user.name=Dev", "-c", "user.email=dev@localhost", "commit", "-q", "-m", "Initial import")
    values: dict[str, str] = {"TARGET": str(workdir), "CLI": CLI_TEXT}
    for step in case.get("prepare", []):
        kind = step["do"]
        if kind == "overrides":
            path = workdir / ".ai/android-workflow.json"
            current = read_json(path) if path.is_file() else {}
            for dropped in step.get("unset", []):
                head, _, last = dropped.rpartition(".")
                parent = dotted(current, head) if head else current
                if isinstance(parent, dict):
                    parent.pop(last, None)
            write_json(path, deep_merge(current, step.get("set", {})))
        elif kind == "copy":
            target = workdir / step["to"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(Path(case["_dir"]) / step["from"], target)
        elif kind == "accept_profile":
            sys.path.insert(0, str(KIT / "bin"))
            import feature_setup  # noqa: PLC0415

            feature_setup.ensure(workdir, no_probe=True)
            feature_setup.ensure(workdir, accept=True)
        elif kind == "cli":
            args = [arg.format(**values) for arg in step["args"]]
            values[step["as"]] = json.dumps(run_cli(args, workdir), indent=2)
        else:
            raise ValueError(f"unknown prepare step {kind}")
    _forget_device_wants(workdir)
    snapshots = {}
    for relative in case.get("snapshot", []):
        path = workdir / relative
        snapshots[relative] = path.read_text(encoding="utf-8") if path.is_file() else None
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + case_id
    run_folder = runs_dir() / run_id
    prompt = Path(case["_dir"], case["prompt_file"]).read_text(encoding="utf-8")
    for key, value in values.items():
        prompt = prompt.replace("{" + key + "}", value)
    run = {"id": run_id, "case": case_id, "agent": case["agent"], "tier": case.get("tier"),
           "workdir": str(workdir), "prompt": prompt, "snapshots": snapshots,
           "return_file": str(run_folder / "return.md"), "kit": kit_commit(),
           "prepared_at": dt.datetime.now().isoformat(timespec="seconds")}
    save_run(run)
    return {key: run[key] for key in ("id", "agent", "tier", "workdir", "return_file", "prompt")}


def _forget_device_wants(workdir: Path) -> None:
    """A run started here never reaches a device: it must not keep the machine's emulators open."""
    sys.path.insert(0, str(KIT / "tools" / "android-workflow"))
    from android_workflow import locks  # noqa: PLC0415

    for run in (workdir / ".ai" / "workflow").glob("*/run-state.json"):
        locks.unwant(run.parent)


# ---------------------------------------------------------------- checks

def map_rows(workdir: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    sys.path.insert(0, str(KIT / "tools" / "android-workflow"))
    from android_workflow import feature_map  # noqa: PLC0415

    path = feature_map.map_path(workdir)
    return feature_map.parse(path.read_text(encoding="utf-8")) if path.is_file() else ([], [])


def run_check(check: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    workdir = Path(run["workdir"])
    kind = check["kind"]
    path = workdir / check["file"] if "file" in check else None
    if kind == "json_equals":
        actual = dotted(read_json(path), check["path"]) if path.is_file() else None
        return actual == check["value"], f"{check['path']} = {actual!r}"
    if kind == "json_contains":
        actual = dotted(read_json(path), check["path"]) if path.is_file() else None
        return isinstance(actual, list) and check["value"] in actual, f"{check['path']} = {actual!r}"
    if kind == "file_exists":
        return path.is_file(), str(path.relative_to(workdir))
    if kind == "file_absent":
        return not path.exists(), str(path.relative_to(workdir))
    if kind in ("regex", "not_regex"):
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        found = re.search(check["pattern"], text, re.M | re.I)
        return (bool(found) if kind == "regex" else not found), (found.group(0)[:80] if found else "no match")
    if kind == "cli":
        result = run_cli([arg.format(TARGET=str(workdir)) for arg in check["args"]], workdir)
        actual = dotted(result, check["field"])
        wanted = check.get("in", [check.get("value")])
        return actual in wanted, f"{check['field']} = {actual!r}"
    if kind == "unchanged":
        before = run["snapshots"].get(check["file"])
        now = path.read_text(encoding="utf-8") if path.is_file() else None
        return before == now, "unchanged" if before == now else "changed"
    if kind == "sections_unchanged":
        before = sections(run["snapshots"].get(check["file"]) or "")
        now = sections(path.read_text(encoding="utf-8") if path.is_file() else "")
        allowed = set(check.get("except", []))
        changed = sorted(name for name in set(before) | set(now) if name not in allowed and before.get(name) != now.get(name))
        return not changed, ", ".join(changed) or "only allowed sections changed"
    if kind == "no_secrets":
        hits = [name for name in check["files"] if (workdir / name).is_file()
                and SECRET.search((workdir / name).read_text(encoding="utf-8"))]
        return not hits, ", ".join(hits) or "clean"
    if kind == "map_row":
        surfaces, _ = map_rows(workdir)
        column, pattern = check["match"]
        rows = [row for row in surfaces if re.search(pattern, " ".join(row.get(c, "") for c in column.split("|")), re.I)]
        if not rows:
            return False, f"no row matching {pattern}"
        for name, expected in check.get("expect", {}).items():
            if not any(re.search(expected, row.get(name, ""), re.I) for row in rows):
                return False, f"{name} !~ {expected}: {[row.get(name) for row in rows]}"
        return True, rows[0]["Surface"]
    if kind == "not_mapped":
        _, skipped = map_rows(workdir)
        covered = any(fnmatch.fnmatch(check["entry"], row["Entry"].strip("` ")) for row in skipped)
        return covered, "listed" if covered else "missing"
    raise ValueError(f"unknown check {kind}")


def check(run_id: str) -> dict[str, Any]:
    run = load_run(run_id)
    case = cases()[run["case"]]
    results = []
    for item in case.get("checks", []):
        try:
            passed, detail = run_check(item, run)
        except Exception as exc:  # noqa: BLE001 - a broken check fails, it does not stop the others
            passed, detail = False, f"error: {exc}"
        results.append({"id": item["id"], "weight": item.get("weight", 1), "passed": passed, "detail": detail})
    total = sum(item["weight"] for item in results) or 1
    run["checks"] = {"results": results, "score": round(10 * sum(i["weight"] for i in results if i["passed"]) / total, 2)}
    save_run(run)
    return run["checks"]


# ---------------------------------------------------------------- judge

def judge_prompt(run: dict[str, Any], case: dict[str, Any]) -> str:
    workdir = Path(run["workdir"])
    files = []
    for relative in case.get("judge_reads", []):
        path = workdir / relative
        text = path.read_text(encoding="utf-8")[:20000] if path.is_file() else "(missing)"
        files.append(f"### {relative}\n\n```\n{text}\n```")
    returned = Path(run["return_file"])
    rubric = "\n".join(f"- `{item['id']}` (weight {item.get('weight', 1)}): {item['criterion']}" for item in case["rubric"])
    checks = json.dumps((run.get("checks") or {}).get("results", []), indent=1)
    return f"""You are grading the work of a coding agent on an Android repository (the current directory).
Be strict and specific. Read the repository's source to verify claims; never assume a claim is true.

## What the agent was asked to do

{run['prompt']}

## Its final reply

{returned.read_text(encoding='utf-8') if returned.is_file() else '(none recorded)'}

## Files it was expected to write or keep

{chr(10).join(files)}

## Automatic checks already run (pass/fail)

{checks}

## Rubric

Score each criterion from 0 (absent or wrong) to 10 (fully right, verified against the code), with one
line of reason that names the evidence (a file, a line, a value).

{rubric}

Answer with JSON only: {{"scores": [{{"id": "...", "score": 0, "reason": "..."}}], "summary": "two sentences"}}.
"""


def judge(run_id: str, engine: str = "codex", model: str | None = None) -> dict[str, Any]:
    run = load_run(run_id)
    case = cases()[run["case"]]
    folder = runs_dir() / run_id
    prompt = judge_prompt(run, case)
    (folder / "judge-prompt.md").write_text(prompt, encoding="utf-8")
    out = folder / "judge.json"
    if engine == "codex":
        schema = folder / "judge-schema.json"
        write_json(schema, JUDGE_SCHEMA)
        command = ["codex", "exec", "-s", "read-only", "-C", run["workdir"], "--skip-git-repo-check", "--ephemeral",
                   "--output-schema", str(schema), "-o", str(out)] + (["-m", model] if model else []) + [prompt]
        done = subprocess.run(command, text=True, capture_output=True, check=False, timeout=1800)
        text = out.read_text(encoding="utf-8") if out.is_file() else done.stdout
    elif engine == "claude":
        command = ["claude", "-p", "--output-format", "text", "--permission-mode", "plan",
                   "--allowedTools", "Read,Grep,Glob"] + (["--model", model] if model else []) + [prompt]
        done = subprocess.run(command, text=True, capture_output=True, check=False, cwd=run["workdir"], timeout=1800)
        text = done.stdout
    else:
        raise ValueError("judge must be codex or claude")
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"the judge returned no JSON (exit {done.returncode}): {(done.stderr or text)[-500:]}")
    verdict = json.loads(match.group(0))
    weights = {item["id"]: item.get("weight", 1) for item in case["rubric"]}
    scored = [item for item in verdict["scores"] if item["id"] in weights]
    total = sum(weights[item["id"]] for item in scored) or 1
    run["judge"] = {"engine": engine, "model": model, "scores": scored, "summary": verdict.get("summary", ""),
                    "score": round(sum(item["score"] * weights[item["id"]] for item in scored) / total, 2),
                    "missing": sorted(set(weights) - {item["id"] for item in scored})}
    save_run(run)
    return run["judge"]


# ---------------------------------------------------------------- record / report

def history() -> list[dict[str, Any]]:
    path = history_file()
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def record(run_id: str, agent_model: str | None = None) -> dict[str, Any]:
    run = load_run(run_id)
    if "checks" not in run:
        check(run_id)
        run = load_run(run_id)
    checks_score = run["checks"]["score"]
    judge_score = (run.get("judge") or {}).get("score")
    score = round((checks_score + judge_score) / 2, 2) if judge_score is not None else checks_score
    previous = next((item for item in reversed(history()) if item["case"] == run["case"]), None)
    entry = {"at": dt.datetime.now().isoformat(timespec="seconds"), "run": run_id, "case": run["case"],
             "agent": run["agent"], "agent_model": agent_model, "kit": run["kit"], "checks": checks_score,
             "judge": judge_score, "judge_engine": (run.get("judge") or {}).get("engine"), "score": score,
             "failed_checks": [item["id"] for item in run["checks"]["results"] if not item["passed"]],
             "low_rubric": [item["id"] for item in (run.get("judge") or {}).get("scores", []) if item["score"] < 7]}
    history_file().parent.mkdir(parents=True, exist_ok=True)
    with history_file().open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry) + "\n")
    entry["previous"] = previous and {"score": previous["score"], "kit": previous["kit"], "at": previous["at"]}
    entry["delta"] = round(score - previous["score"], 2) if previous else None
    return entry


def report() -> dict[str, Any]:
    latest: dict[str, dict[str, Any]] = {}
    before: dict[str, dict[str, Any]] = {}
    for item in history():
        if item["case"] in latest:
            before[item["case"]] = latest[item["case"]]
        latest[item["case"]] = item
    rows = []
    for case_id in cases():
        item = latest.get(case_id)
        prior = before.get(case_id)
        rows.append({"case": case_id, "score": item and item["score"], "checks": item and item["checks"],
                     "judge": item and item["judge"], "kit": item and item["kit"],
                     "delta": round(item["score"] - prior["score"], 2) if item and prior else None,
                     "failed_checks": item and item["failed_checks"], "low_rubric": item and item["low_rubric"]})
    return {"cases": rows}


def clean(keep: int = 10) -> dict[str, Any]:
    spaces = sorted((state_home() / "ws").glob("*"), key=lambda path: path.stat().st_mtime, reverse=True)
    removed = [str(path) for path in spaces[keep:]]
    for path in spaces[keep:]:
        shutil.rmtree(path, ignore_errors=True)
    return {"removed": removed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    sub.add_parser("report")
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--case", required=True)
    for name in ("check", "judge", "record"):
        command = sub.add_parser(name)
        command.add_argument("--run", required=True)
        if name == "judge":
            command.add_argument("--judge", default="codex", choices=("codex", "claude"))
            command.add_argument("--model")
        if name == "record":
            command.add_argument("--agent-model")
    clean_parser = sub.add_parser("clean")
    clean_parser.add_argument("--keep", type=int, default=10)
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            result: Any = [{"id": case["id"], "agent": case["agent"], "about": case.get("about", "")} for case in cases().values()]
        elif args.command == "prepare":
            result = prepare(args.case)
        elif args.command == "check":
            result = check(args.run)
        elif args.command == "judge":
            result = judge(args.run, args.judge, args.model)
        elif args.command == "record":
            result = record(args.run, args.agent_model)
        elif args.command == "report":
            result = report()
        else:
            result = clean(args.keep)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
