# Workflow: workflow-setup

Automatic first-run project facts, not a prerequisite the user must remember.
Aliases: `/workflow-setup [--force]`, `/android-bootstrap`.
English-only artifacts. No Gradle build, installation, network, or feature code edits.

## Fast path

Run `python3 ~/.ai/bin/feature_setup.py --repo-root REPO --ensure`.
For a new worktree, optionally pass `--source-repo MAIN_CHECKOUT`.
The helper:

1. Requires a Gradle settings file.
2. Creates `.ai/project-profile.md` immediately if absent.
3. Compares content fingerprints of module/root Gradle configuration, version catalogs,
   convention-plugin sources, manifests, project rule/skill files, wrapper, `.editorconfig`,
   and `AGENTS.md`; timestamps do not matter.
4. Reuses a source-checkout profile only when its completed configuration fingerprint matches.
5. Produces `.ai/workflow/_setup/probe.md` when facts need refreshing, preserving the existing profile.

`requires_explorer: false` means reuse. Otherwise invoke `project-explorer` once, or adopt that
role for a small missing-facts pass. Read its canonical role, the probe, profile, and repository rules.
Follow applied convention plugins/catalog aliases; a plugin regex is a hint, not proof.
Confirm this is Android before continuing a feature run. Unknown fields stay empty.

After completing and verifying task-relevant rows, run:
`python3 ~/.ai/bin/feature_setup.py --repo-root REPO --accept`.
This records that the agent checked the profile; it does not independently prove its contents.
Profile edits or recreation invalidate prior acceptance. Unusual configuration outside the
fingerprinted sources must be verified when relevant; use `--force` rather than trusting stale facts.
Do not accept while compile/test/lint tasks needed by this ticket are unresolved.
New tickets may fill additional rows without re-exploring the repository.

## Minimal interaction

No permission question just to create the local profile. No question about committing it during
a feature run. Delivery excludes the profile and `.ai/workflow/`; sharing it is a separate task.
Preserve human-owned device fixtures, authorized repairs, and convention rows on refresh.
Ask one grouped question only for task-blocking facts that code cannot establish.
Never invent account credentials or copy secrets into the profile.

`--no-setup` / `--no-bootstrap` in feature-workflow maps to helper `--no-probe`: it suppresses
the broad probe, not persistence or verification of required tasks.
`--force` refreshes probe facts; it does not erase human edits.

## Profile contract

Use [the template](../templates/project-profile.md) as a checklist, not mandatory prose.
Record exact module task/variant mappings, test libraries, lint engine, install commands,
device entry/observation handles, existing screenshot/flow infrastructure, and rule pointers.
Do not infer JVM from the absence of a literal Android plugin; resolve indirect application.
Avoid a full `gradlew tasks` listing; inspect the relevant build convention first.

The helper cannot discover all architecture rules or authenticate the device. Planner/reviewer
inspect applicable sibling patterns; device preflight checks runtime prerequisites separately.
Do not install screenshot libraries, duplicate global skills, create feature READMEs, or change
global JDK/auth/Git settings to make setup appear successful.
