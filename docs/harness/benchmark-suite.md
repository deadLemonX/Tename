# Benchmark Suite

## Purpose

Validates that profiles actually work well, not just that they parse correctly. Every profile change should be validated against the benchmark suite before merging.

## Structure

```
benchmarks/
├── tasks/
│   ├── research-001.yaml
│   ├── coding-001.yaml
│   ├── data-001.yaml
│   ├── tool-001.yaml
│   └── integration-001.yaml
├── graders/
│   └── manual.md  # Manual grading rubric for v0.1
├── results/
│   ├── m0-baseline.json
│   └── {profile-name}-{date}.json
└── run.py  # Benchmark runner CLI
```

## v0.1: Five starter tasks

Start small. Ship benchmarks that prove the profile works for real scenarios. Expand over time.

### research-001: Market sizing

**Prompt:** "Estimate the global market size for EV charging infrastructure in 2028. Provide a range with key assumptions."

**What we test:**
- Can the agent gather evidence?
- Does it reason under uncertainty?
- Does it structure a clear answer?

**Pass criteria (manual):**
- Arrives at a quantified estimate with a range (not just "big")
- Cites at least 3 distinct data points
- Acknowledges key assumptions
- Completes in under 10 turns

### coding-001: Bug fix

**Setup:** Provide a Python script with a specific bug (off-by-one error in a loop).

**Prompt:** "This script isn't producing the correct output. Fix the bug and verify your fix works."

**What we test:**
- Can the agent read code?
- Does it use the sandbox to test?
- Does it verify the fix?

**Pass criteria (manual):**
- Correctly identifies the bug
- Produces a working fix
- Runs the fixed code to verify
- Doesn't introduce new bugs
- Completes in under 15 turns

### data-001: Basic CSV analysis

**Setup:** Provide a CSV file with sample data (e.g., sales by region, 1000 rows).

**Prompt:** "Analyze this sales data. What are the top three trends worth highlighting?"

**What we test:**
- Can the agent read files in the sandbox?
- Does it write and execute analysis code?
- Does it interpret results, not just report numbers?

**Pass criteria (manual):**
- Uses pandas or similar to analyze
- Identifies at least 3 trends supported by the data
- Explains each trend in business terms
- Completes in under 12 turns

### tool-001: Tool selection

**Setup:** Agent has access to: web_search, python, file_read, bash. Provide ambiguous task.

**Prompt:** "What's the current price of Apple stock?"

**What we test:**
- Does the agent select the right tool (web_search, not python)?
- Does it avoid over-engineering?

**Pass criteria (manual):**
- Uses web_search first
- Doesn't attempt to scrape or compute the answer
- Returns a current price with date
- Completes in 1-3 turns

### integration-001: Multi-turn context

**Setup:** Multi-turn conversation where context from turn 1 matters for turn 4.

**Turn 1:** "My name is Alex and I'm researching renewable energy."
**Turn 2:** "What are the main types?"
**Turn 3:** "Focus on solar."
**Turn 4:** "Given what I originally said I was researching, what solar companies should I look at?"

**What we test:**
- Does the agent remember Alex's original context?
- Does it connect "renewable energy" → "solar" → "solar companies"?

**Pass criteria (manual):**
- Correctly recalls the research context
- Gives relevant solar company recommendations
- Doesn't need the user to remind it of earlier context
- Completes in 4 turns (one per user turn)

## Manual grading for v0.1

Automated grading is deferred to v0.2. For v0.1, grading is manual:

1. Run the task with `python benchmarks/run.py --task research-001`
2. Review the session log output
3. Score against the pass criteria
4. Record result in `benchmarks/results/{profile}-{date}.json`

```json
{
  "profile": "claude-opus-4-6",
  "date": "2026-02-15",
  "tasks": {
    "research-001": {"pass": true, "turns": 8, "notes": "Good sources"},
    "coding-001": {"pass": true, "turns": 11, "notes": "Clean fix"},
    "data-001": {"pass": true, "turns": 9, "notes": "Clear trends"},
    "tool-001": {"pass": true, "turns": 2, "notes": "Correct tool"},
    "integration-001": {"pass": true, "turns": 4, "notes": "Good recall"}
  },
  "overall": {"pass_rate": 1.0, "avg_turns": 6.8}
}
```

## Running a benchmark

```bash
# Run a single task
python benchmarks/run.py --task research-001 --profile claude-opus-4-6

# Run all tasks
python benchmarks/run.py --all --profile claude-opus-4-6

# Run with custom profile
python benchmarks/run.py --all --profile ./my-custom-profile.yaml
```

The runner:
1. Creates a session using the specified profile
2. Sends the task prompt
3. Lets the session run to completion (with max turn limit)
4. Dumps the full session log
5. Outputs results in a format ready for manual grading

## The future (v0.2+)

**Automated grading:** LLM-as-judge grading where a separate model reviews the session and scores it against criteria. Requires careful prompt engineering to avoid gaming.

**More tasks:** Expand to 50+ tasks covering research, coding, data analysis, tool selection, multi-step planning, subagent coordination, error recovery.

**Regression testing:** On every profile change, run the full suite and block merges if any benchmarks regress.

**Benchmark leaderboard:** Public comparison of profiles across models. Helps users pick the right profile for their workload.

**Community benchmarks:** Users contribute tasks from their real workloads. We curate the best into the main suite.

## Why manual grading in v0.1

Automated grading is hard to do well. A bad automated grader makes the benchmark suite actively misleading. Manual grading is slower but trustworthy.

With 5 tasks and a few profiles, manual grading is tractable. When we have 50 tasks and 10 profiles, automation becomes necessary. That's v0.2 work.

## Contribution guidelines

To contribute a benchmark task:

1. Write the task YAML in `benchmarks/tasks/`
2. Include pass criteria explicit enough that anyone can grade it
3. Keep tasks realistic (actual agent use cases, not synthetic tests)
4. Run it against at least one profile and document results
5. Submit a PR with the task and results

We reject tasks that:
- Are trivially easy (every model passes)
- Are impossibly hard (every model fails)
- Rely on specific external services that may disappear
- Don't measure something agents actually need to do
