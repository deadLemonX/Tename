# Manual grading rubric (v0.1)

For v0.1 the benchmark suite ships with manual grading. Automated
LLM-as-judge grading is deferred to v0.2 (see
`docs/harness/benchmark-suite.md`).

## Workflow

1. Run a task (or all tasks) with `python benchmarks/run.py --task
   <id> --profile <profile>`. The runner creates an agent + session,
   sends the task prompts, drives the harness to completion, and
   writes a JSON file to `benchmarks/results/<profile>-<date>.json`
   containing the full session event log plus placeholder fields
   for grading.
2. Open the results file. Read the event log top to bottom — pay
   attention to assistant turns (`is_complete=True`) and tool
   rounds.
3. For each `pass_criteria` entry in the task, mark PASS / FAIL
   with a short note.
4. Fill in `overall.pass` (true iff every criterion passed),
   `overall.notes`, and `graded_by`.
5. Commit the results file. Regressions show up as a FAIL in a
   later run.

## Scoring guidance

- **Binary per criterion.** Don't invent half-credit. If a criterion
  is ambiguous, tighten it in the YAML before grading.
- **"Under N turns" is counted by assistant-message closers**, not
  tool calls. A turn is one `assistant_message(is_complete=True)`
  event.
- **"Gives relevant X"** is a judgment call. Be strict — the bar is
  "a subject-matter reader would find this useful," not "the agent
  strung words together."
- **Tool-selection criteria** can pass even when the tool execution
  fails. E.g., `tool-001` grades "first tool call is web_search"
  independent of whether Tavily was configured. Document the
  unconfigured-credential case in your notes.
- **Errors in the log**: a single `error` event that the agent
  recovered from is fine; a terminal error event at session end
  fails the task unless the criterion explicitly permits it.

## When a task fails

1. Decide whether the failure is in the profile, the prompt, or the
   criteria. If the criteria are too tight, propose a YAML edit in a
   separate PR.
2. If the profile is at fault, file an issue with the results file
   attached.
3. Don't retry until conditions change. A flaky task teaches nothing.
