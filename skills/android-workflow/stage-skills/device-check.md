# Skill · Device check

Runs only for surfaces configured as visual or runtime. Use an already-open device; if needed,
wait for the boot the Implementer stage started asynchronously.

Run configured instrumented/scenario tests only when they exist. Visual proof is
`evidence capture` / `ingest` into `.ai/workflow/<ticket>/media/{before,after}/`, then
`evidence compare` (optionally `--previous` another ticket). Record device, scenarios,
and those media paths in `device-report.md`. A failure that does not reproduce on the
second run is flaky; a second real failure escalates.
