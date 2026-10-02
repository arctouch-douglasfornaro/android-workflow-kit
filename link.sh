#!/usr/bin/env bash
# One-time setup after cloning this kit to ~/.ai: point every coding tool at it.
#
# Each tool reads its own folder in your home (~/.claude, ~/.codex, ~/.cursor, ~/.gemini, ~/.agents).
# This creates links from those folders to the matching folder in this kit, so every tool reads the
# same source of truth, and allows the workflow's own commands in Claude Code. Edits to the kit apply at once; re-run only after the kit adds or removes an
# agent or skill. Safe to run again.
set -euo pipefail

KIT="$(cd "$(dirname "$0")" && pwd)"
if [ "$KIT" != "$HOME/.ai" ]; then
  echo "error: the kit must live at ~/.ai (found at $KIT); clone it with: git clone <repo> ~/.ai" >&2
  exit 1
fi
BACKUP="$HOME/.ai-backup/$(date +%Y%m%d-%H%M%S)"

for host in .claude .codex .cursor .gemini .agents; do
  for kind in skills agents; do
    src="$KIT/$host/$kind"
    [ -d "$src" ] || continue
    dest_dir="$HOME/$host/$kind"
    mkdir -p "$dest_dir"
    # Drop links to agents or skills the kit no longer has.
    for existing in "$dest_dir"/*; do
      if [ -L "$existing" ] && [[ "$(readlink "$existing")" == "$src/"* ]] && [ ! -e "$existing" ]; then
        rm "$existing"
        echo "removed retired $existing"
      fi
    done
    for item in "$src"/*; do
      dest="$dest_dir/$(basename "$item")"
      if [ -L "$dest" ]; then
        rm "$dest"
      elif [ -e "$dest" ]; then
        # An older copy (e.g. from install-host): keep it out of the tool's folder, never delete it.
        mkdir -p "$BACKUP/$host/$kind"
        mv "$dest" "$BACKUP/$host/$kind/"
        echo "moved old $dest to $BACKUP/$host/$kind/"
      fi
      ln -s "$item" "$dest"
      echo "linked $dest"
    done
  done
done

# Codex only spawns the named agents with multi_agent_v2 enabled.
python3 - "$BACKUP" <<'PY'
import re
import shutil
import sys
from pathlib import Path

config = Path.home() / ".codex" / "config.toml"
text = config.read_text(encoding="utf-8") if config.exists() else ""
section = re.search(r"^\[features\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
if section and re.search(r"^\s*multi_agent_v2\s*=\s*true\b", section.group(1), re.M):
    print("codex: multi_agent_v2 already enabled")
    raise SystemExit(0)
if config.exists():
    backup = Path(sys.argv[1]) / ".codex"
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config, backup / "config.toml")
if section and re.search(r"^\s*multi_agent_v2\s*=", section.group(1), re.M):
    body = re.sub(r"^(\s*multi_agent_v2\s*=\s*).*$", r"\1true", section.group(1), count=1, flags=re.M)
    text = text[: section.start(1)] + body + text[section.end(1):]
elif section:
    text = text[: section.start(1)] + "\nmulti_agent_v2 = true" + text[section.start(1):]
else:
    text = (text.rstrip("\n") + "\n\n" if text.strip() else "") + "[features]\nmulti_agent_v2 = true\n"
config.parent.mkdir(parents=True, exist_ok=True)
config.write_text(text, encoding="utf-8")
print(f"codex: enabled multi_agent_v2 in {config}")
PY

# Claude Code: allow the workflow's own commands, so auto mode never stops its steps (the CLI does
# the git work for commit, push and PR itself). Only adds the rules; the rest of the file is kept.
python3 - "$BACKUP" <<'PY'
import json
import shutil
import sys
from pathlib import Path

settings = Path.home() / ".claude" / "settings.json"
rules = ["Bash(python3 ~/.ai/bin/android-workflow:*)", "Bash(python3 ~/.ai/bin/feature_workspace.py:*)"]
try:
    data = json.loads(settings.read_text(encoding="utf-8")) if settings.exists() else {}
except ValueError:
    print(f"claude: {settings} is not valid JSON; add these permissions by hand: {', '.join(rules)}")
    raise SystemExit(0)
if not isinstance(data, dict) or not isinstance(data.get("permissions", {}), dict):
    print(f"claude: unexpected shape in {settings}; add these permissions by hand: {', '.join(rules)}")
    raise SystemExit(0)
allow = data.setdefault("permissions", {}).setdefault("allow", [])
if allow is None:
    allow = data["permissions"]["allow"] = []
if not isinstance(allow, list):
    print(f"claude: permissions.allow in {settings} is not a list; add these permissions by hand: {', '.join(rules)}")
    raise SystemExit(0)
missing = [rule for rule in rules if rule not in allow]
if not missing:
    print("claude: workflow commands already allowed")
    raise SystemExit(0)
if settings.exists():
    backup = Path(sys.argv[1]) / ".claude"
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(settings, backup / "settings.json")
allow.extend(missing)
settings.parent.mkdir(parents=True, exist_ok=True)
settings.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"claude: allowed the workflow commands in {settings}")
PY

echo "done: Claude Code, Codex, Cursor and Gemini now read the android-workflow kit from $KIT"
