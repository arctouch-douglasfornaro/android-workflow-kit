# Skill · Quality gate

Run a configurable ladder: format, compile and tests for affected modules; global tests only for
a public API change; assemble only when the Device stage is required; secret scan always.

- **Format first.** When Setup found or recorded a formatter that can also fix (`format_apply`), the
  gate runs it before its check, scoped to the touched modules. Files it rewrites that this change
  already modified stay (`auto_format.formatted`); files it rewrites outside the change are
  restored (`auto_format.restored_outside_change`). A project with no formatter, or a check-only
  one, is never forced to have one. `quality_gates.auto_format: false` opts out. Without an
  auto-fix, the format check runs alone first, so a formatting failure returns in seconds.
- **One build for the rest.** Plain Gradle task commands run as one `--continue --console=plain`
  build; each command still gets its own `outcome` (`passed`, `waived`, `failed`, `not_run`,
  `unverified`), exit code and excerpt in `gate-report.json`. Output goes to
  `gate-run<N>-attempt<M>-<unit>.log`; every gate run keeps its own logs, and `units` records how
  long each build took.
- **Lint scope.** Android lint treats a missing baseline as empty and promotes warnings to errors,
  so findings fail the gate instead of being hidden by a generated baseline. A lint or format
  failure whose findings are **all** in files this change does not touch is `waived`: the step
  is recorded under `waivers` (count, files), the stage log says so, and the PR discloses it.
  Anything else fails: a finding in a touched file (whether or not it predates the branch), a
  failure with no readable findings, or fewer findings parsed than the lint report announces.
  `quality_gates.lint_scope: "all"` disables the waiver for a project that wants zero findings.
  The gate never compares against the base branch and never writes a baseline.
- `unverified` lint (an older AGP wrote a baseline and gave no verdict) fails the gate.
- **Consumers.** Downstream modules (by the recorded module graph) whose sources name a public
  top-level declaration of a changed production file also get compile and unit tests, not lint;
  they are listed under `consumer_modules`. Past `quality_gates.max_consumer_modules` (12) the rest
  are named in `warnings`, not gated.
- **Feature docs.** With `feature_docs.glob` recorded, a production change under a path a doc's
  frontmatter `covers:` fails the gate unless that doc changed too. It reads the working tree.
- **Toolkit integrity.** When the toolkit changed after `start`, the gate is `blocked`
  (`toolkit_modified_during_run`) and runs nothing: a human reviews the change and starts again.

A third failure escalates. Never "fix" by removing a test, suppressing lint, or hiding an exception.
