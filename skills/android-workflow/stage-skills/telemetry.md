# Skill · Telemetry

Record wall time, outcome, attempts, and token use in `stage-metrics.json` when the adapter
reports that value. Do not estimate missing data: unknown stays `null`, never `0`.

CLI stages are timed by the CLI. A host stage logged with `log` is timed from the previous
recorded event (or `--seconds`) and takes tokens from `--tokens`; repeated rounds accumulate.
`totals` holds the run's wall time and the sum of reported tokens.

Metrics calibrate skip rules later; they do not change the route of the current run.
