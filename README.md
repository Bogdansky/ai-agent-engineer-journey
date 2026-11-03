# ai-agent-engineer-journey

A learning project: an **incident-triage agent** built from scratch in Python, to practice the parts of agent
engineering that using agents every day doesn't teach: MCP servers, a hand-written agent loop, evals, and tracing.

The agent receives an alert, investigates a copy of a production service (logs + git history, read-only tools)
and reports the most likely root cause. A set of synthetic incidents with known ground truth makes it possible
to measure how well it does and how that changes when the prompt or the model changes.

## Status

| Part | State |
| --- | --- |
| Sandbox service (`sandbox/`) | done |
| Scenarios s01-s03 + log simulator + workspace builder (`incident_lab/`) | done |
| MCP server (`search_logs`, `read_file`, `git_log`, `git_blame`) | next |
| Own agent loop on the Messages API | planned |
| Evals (graders, runner, results per prompt version) | planned |
| Tracing (OpenTelemetry → Langfuse) | planned |
| Scenarios s04-s10, including decoys | planned |

## How it works

`sandbox/` is a small FastAPI order service (SQLite behind a tiny connection pool, a payment gateway client).
Each scenario is **one commit on top of the healthy service** that introduces a realistic fault, plus a
deterministic log generator that reproduces what production would have logged. Every log line the simulator
writes maps to a log call that exists in the code (a test enforces this), so the agent can always trace a
symptom back to the code.

A built workspace is exactly what the agent gets to see:

```
build/workspaces/<id>/
  alert.txt        the firing alert, with no hint about the cause
  logs/app.log     chronological service logs around the incident
  repo/            git repository of the service at the culprit deploy
build/truth/<id>.json   ground truth (culprit commit, file, category) - for graders only
```

| Id | Fault | Where |
| --- | --- | --- |
| s01 | Connection leaked on a 404 lookup, pool exhausted, 503s | `app/orders.py` |
| s02 | Env var renamed in code but not in the deploy env file, gateway URL falls back to localhost | `app/config.py` |
| s03 | Payment client timeout disabled, slow gateway blocks all worker threads | `app/payments.py` |

## Quick start

```bash
uv sync
uv run pytest                           # sandbox behaviour + scenario integrity
uv run python -m incident_lab.workspace all     # builds ./build/
```

The scenario tests build every workspace and check that the fault is real in the workspace code, that git
history points at the culprit, that builds are deterministic, and that no ground truth leaks into the workspace.
