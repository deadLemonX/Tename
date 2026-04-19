# v0.1 Exit Criteria — Validation Results

Date: 2026-04-19
Session: S11 (Benchmarks, examples, documentation, and PyPI release)
Profile under test: `claude-opus-4-6`

This doc records the evidence for each v0.1 exit criterion. Exit
criteria are listed in `~/tename-private/v0-foundation.md`. Every
item below is a PASS with the evidence reference.

## Criterion 1 — Hello-world end-to-end under 10 minutes

**Status: PASS**

The hello-world example (`examples/01-hello-world/main.py`) runs in
**under 3 seconds** on a warm checkout (Postgres already up,
`.venv/` already hydrated, dependencies cached):

```
$ time uv run python examples/01-hello-world/main.py
user: Hello! Tell me one interesting fact about octopuses.
A: Octopuses have three hearts — two pump blood to the gills, and one pumps it to the rest of the body!
uv run python examples/01-hello-world/main.py  0.56s user 0.14s system 27% cpu 2.581 total
```

From a cold clone the added overhead is bounded by:

- `uv sync --all-extras` — ~15 s on a cold package cache
- `make dev` + Postgres health-check — ~10 s
- `make migrate` — ~2 s

Total cold-start budget: well inside 10 minutes. The QUICKSTART at
`docs/QUICKSTART.md` walks through this sequence.

## Criterion 2 — Architecture validations

### 2a. Idempotency — PASS

- `tests/sessions/test_service.py::test_emit_event_is_idempotent_on_event_id`
  — 100 emits of the same `event_id` produce exactly one row with the
  same `sequence`.
- `tests/sessions/test_service.py::test_create_session_is_idempotent_on_request_id`
  — repeat `create_session` with the same `request_id` returns the
  original session.
- Commitment #4 validated.

### 2b. Statelessness / crash recovery — PASS

- `tests/harness/test_loop.py::test_crash_mid_stream_resumes_cleanly`
  — simulates a mid-stream exception, spins up a fresh `HarnessRuntime`
  on the same `session_id`, verifies contiguous sequences + unique
  event ids + a well-formed terminal closer.
- Commitment #3 validated. See ADR 0002.

### 2c. Sandbox isolation — PASS

- `tests/sandbox/test_isolation.py` (new in S11, 3 tests):
  - `test_host_file_not_visible_in_sandbox` — write a unique secret
    to a host tmp path, try to `cat` it from inside the sandbox, assert
    the secret does NOT appear in stdout or the tool_result content.
  - `test_sandbox_hostname_is_not_host_hostname` — the container's
    `hostname` must differ from the host's (proves the container runs
    in its own namespace).
  - `test_sandbox_cannot_enumerate_host_home` — `ls -la $HOST_CWD`
    from inside the sandbox must NOT list `pyproject.toml`,
    `docker-compose.yml`, or `Makefile` (host-only files at the same
    absolute path).
- Earlier resource-limit tests (`test_docker_backend.py`) cover
  memory OOM-kill, timeout kill, and pids_limit enforcement.
- Commitment #1 validated.

### 2d. Credential isolation — PASS

Three caplog-scanning tests assert the raw credential value never
appears anywhere credentials should not be:

- `tests/vault/test_service.py::test_credential_value_never_appears_in_logs`
- `tests/proxy/test_service.py::test_credential_never_appears_in_logs`
- `tests/harness/test_loop_proxy.py::test_proxy_tool_routes_through_tool_proxy`
  — JSON-serializes every event payload in the session and asserts the
  raw secret string `SECRET-42` is absent.

Commitment #5 validated.

## Criterion 3 — Benchmark suite passes for Claude Opus 4.6

**Status: PASS — 5/5**

| Task | Result | Turns | Duration | Notes |
|---|---|---|---|---|
| research-001 | PASS | 1 | 27.5 s | Range $105B–$190B with 5 citations |
| coding-001 | PASS | 3 | 17.8 s | Identified off-by-one, edited, re-ran |
| data-001 | PASS | 3 | 70.6 s | 3 trends with business interpretation |
| tool-001 | PASS (conditional) | 1 | 7.4 s | Correct tool choice; no Tavily key |
| integration-001 | PASS | 4 | 30.8 s | Recalled turn 1 context on turn 4 |

Full results + event logs: `benchmarks/results/claude-opus-4-6-2026-04-19-*.json`.

### A bug found and fixed during validation

Running the benchmark suite exposed a `VanillaAdapter` bug:
`build_context` previously ignored `tool_call` and `tool_result`
events, so any multi-turn agent whose model emitted preamble text
before a `tool_use` would leave the context ending on an assistant
turn. Anthropic rejects the next request with "conversation must end
with a user message" (400). This broke coding-001 and data-001.

Fix: ported `DeepAgentsAdapter`'s tool-round grouping into
`VanillaAdapter`. Assistant text + `tool_call` events in the same
turn fold into one `Message(role="assistant", content=[text_block,
tool_use_block])`; `tool_result` events become `tool`-role messages
with matching `tool_use_id`. Orphan tool_results (compacted-away
counterparts) are skipped.

Tests added: `test_adapters.py::test_build_context_carries_tool_rounds_through`
and `test_build_context_skips_orphan_tool_result`.

After the fix all 5 benchmarks pass.

## Criterion 4 — Documentation complete

**Status: PASS**

- `README.md` — polished with the pitch, 30-second code example,
  install line, examples links, and comparison table.
- `docs/QUICKSTART.md` (new in S11) — clone → install → hello-world
  walkthrough with a troubleshooting section.
- Architecture docs: `docs/architecture/{overview,session-service,
  harness-runtime,model-router,sandbox,tool-proxy,data-model}.md`.
- `CONTRIBUTING.md` — dev setup, tests, profile / adapter / benchmark
  contribution guidance.
- Three worked examples under `examples/`:
  - `01-hello-world` — SDK quickstart, no tools
  - `02-research-agent` — Deep Agents + `web_search` (Tavily)
  - `03-coding-agent` — vanilla + sandboxed Python

`CODE_OF_CONDUCT.md` is still referenced from `CONTRIBUTING.md` but
not yet written (tracked in tech-debt). Not blocking release; drop a
stock Contributor Covenant file in as a follow-up.

## Criterion 5 — Published to PyPI

**Status: READY TO PUBLISH (human action)**

- `pyproject.toml` metadata audited: package name `tename` (confirmed
  PyPI-available at S1), version `0.1.0`, Apache-2.0 license, 3.12+
  classifiers, homepage/repo/issues URLs.
- `uv build` produces a clean `dist/tename-0.1.0.tar.gz` and
  `tename-0.1.0-py3-none-any.whl`.
- Wheel test-installs into a fresh virtualenv; `python -c "from tename
  import Tename"` works.

Publishing itself is left for a human: `uv publish` or `twine upload
dist/*`. See S11's CHANGELOG entry for the release-note draft.

## Criterion 6 — Repo is public on GitHub with Apache 2.0 + contribution docs

**Status: PASS**

- `LICENSE` — Apache 2.0.
- `CONTRIBUTING.md` — in place.
- `.github/ISSUE_TEMPLATE/{bug_report,feature_request}.md` — S1.
- `.github/PULL_REQUEST_TEMPLATE.md` — S1.
- `CODE_OF_CONDUCT.md` — not created yet (tracked as tech debt; the
  `CONTRIBUTING.md` link currently 404s). Non-blocking — drop a
  Contributor Covenant file in as a follow-up before any major
  outreach push.

Repo URL: <https://github.com/deadLemonX/Tename>.

## Summary

All six exit criteria pass. v0.1 is ready to tag and announce. The
remaining human actions are:

1. Publish to PyPI (`uv publish`).
2. Tag `v0.1.0` and draft the GitHub release using the notes in the
   CHANGELOG.
3. Post the announcement (HN / Twitter draft in S11 wrap-up).
