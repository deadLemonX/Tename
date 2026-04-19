"""One-off: fill in pass_criteria and overall grades for today's results.

Run once after a live benchmark run. The grading is my (Claude's)
manual grade based on reading the full session logs; the human
reviewing this PR should cross-check.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

RESULTS_DIR = Path(__file__).parent
GRADER = "Claude Code (automated grading pass during S11)"

GRADES = {
    "research-001": {
        "pass_criteria": [
            {
                "criterion": 'Arrives at a quantified estimate with a range (not just "big")',
                "pass": True,
                "notes": "Estimate: $105B-$190B. Explicit range with BLUF structure.",
            },
            {
                "criterion": "Cites at least 3 distinct data points",
                "pass": True,
                "notes": (
                    "Fortune Business Insights ($25.5B), Allied Market Research "
                    "($30.7B), BloombergNEF (~$36B), plus IEA and McKinsey."
                ),
            },
            {
                "criterion": "Acknowledges key assumptions",
                "pass": True,
                "notes": "Step-by-step reasoning section lays out each assumption.",
            },
            {"criterion": "Completes in under 10 turns", "pass": True, "notes": "1 turn."},
        ],
        "overall": {
            "pass": True,
            "notes": "Clean single-turn research answer with quantified range and citations.",
        },
    },
    "coding-001": {
        "pass_criteria": [
            {
                "criterion": (
                    "Correctly identifies the off-by-one (range(1, n) should be range(1, n + 1))"
                ),
                "pass": True,
                "notes": (
                    '"range(1, n) goes from 1 to n-1, so it excludes n. It should be '
                    'range(1, n + 1)"'
                ),
            },
            {
                "criterion": "Produces a working fix via file_edit or file_write",
                "pass": True,
                "notes": "Used file_edit with an exact old_str/new_str swap.",
            },
            {
                "criterion": 'Runs the fixed code (python or bash) and observes "OK"',
                "pass": True,
                "notes": "python tool ran the fixed script; tool_result shows 'sum_to_n(10) = 55\\nOK'.",
            },
            {
                "criterion": "Doesn't introduce new bugs",
                "pass": True,
                "notes": "Single-line fix, verified by runtime check.",
            },
            {"criterion": "Completes in under 15 turns", "pass": True, "notes": "3 turns."},
        ],
        "overall": {
            "pass": True,
            "notes": (
                "Exercises read → edit → run loop; all criteria met. "
                "Only possible after the VanillaAdapter tool-round fix landed."
            ),
        },
    },
    "data-001": {
        "pass_criteria": [
            {
                "criterion": "Uses pandas (or similar) to load and inspect the CSV",
                "pass": True,
                "notes": "Multiple pandas read_csv + groupby steps in the tool calls.",
            },
            {
                "criterion": "Identifies at least 3 trends supported by the data",
                "pass": True,
                "notes": (
                    "(1) revenue growth acceleration Jan→Mar (+34%), "
                    "(2) East region concentration (52% of revenue, +65% growth), "
                    "(3) Gadget revenue share rising (+51% vs Widget +23%)."
                ),
            },
            {
                "criterion": "Explains each trend in business terms, not just numbers",
                "pass": True,
                "notes": (
                    "Uses phrases like 'momentum curve', 'concentration risk', "
                    "'ASP uplift' and ties each trend to a recommended action."
                ),
            },
            {"criterion": "Completes in under 12 turns", "pass": True, "notes": "3 turns."},
        ],
        "overall": {
            "pass": True,
            "notes": (
                "Handled a couple of transient sandbox errors (one missing import, "
                "one dt accessor without parse_dates) and recovered cleanly — shows "
                "the error-surface path also works end-to-end."
            ),
        },
    },
    "tool-001": {
        "pass_criteria": [
            {
                "criterion": "First tool call is web_search (NOT python or bash)",
                "pass": True,
                "notes": ("Single tool call was web_search({'query': 'Apple stock price today'})."),
            },
            {
                "criterion": "Does not attempt to scrape or compute the answer locally",
                "pass": True,
                "notes": "No python or bash tool calls.",
            },
            {
                "criterion": (
                    "Returns a price with a date / timestamp (if web_search credential "
                    "is configured; otherwise grades the tool choice only)"
                ),
                "pass": True,
                "notes": (
                    "Conditional: no Tavily key in vault. web_search returned a "
                    "credential-missing error, and the model correctly told the user "
                    "it couldn't fetch real-time data rather than hallucinating. "
                    "Tool selection criterion is the binding one here (per task YAML)."
                ),
            },
            {"criterion": "Completes in 1 to 3 turns", "pass": True, "notes": "1 turn."},
        ],
        "overall": {
            "pass": True,
            "notes": (
                "Tool selection correct; price retrieval requires Tavily credential "
                "in vault for a full end-to-end pass."
            ),
        },
    },
    "integration-001": {
        "pass_criteria": [
            {
                "criterion": (
                    'Turn 4 answer recalls the "renewable energy" research context '
                    "Alex set in turn 1"
                ),
                "pass": True,
                "notes": (
                    "\"Since you mentioned you're researching renewable energy, here "
                    'are some key solar companies..."'
                ),
            },
            {
                "criterion": "Gives relevant solar company recommendations",
                "pass": True,
                "notes": (
                    "First Solar, LONGi, JinkoSolar, Canadian Solar, REC Group, "
                    "NextEra Energy, Enphase. Mix of manufacturers + developers."
                ),
            },
            {
                "criterion": "Agent does not ask the user to remind it of earlier context",
                "pass": True,
                "notes": "Directly answered using turn 1 context.",
            },
            {
                "criterion": "Completes in 4 user turns (one per prompt)",
                "pass": True,
                "notes": "4 user turns, 4 assistant turns.",
            },
        ],
        "overall": {
            "pass": True,
            "notes": (
                "Context retention across 4 user turns confirmed. Validated the "
                "benchmark runner's reactivate-between-turns escape hatch."
            ),
        },
    },
}


def main() -> int:
    updated = 0
    for task_id, grade in GRADES.items():
        matches = list(RESULTS_DIR.glob(f"*-{task_id}.json"))
        if not matches:
            print(f"no result file found for {task_id}", file=sys.stderr)
            continue
        path = matches[0]
        data = json.loads(path.read_text())
        data["pass_criteria"] = grade["pass_criteria"]
        data["overall"] = grade["overall"]
        data["graded_by"] = GRADER
        data["graded_at"] = datetime.now(UTC).isoformat()
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        updated += 1
        print(f"graded {task_id}: overall={grade['overall']['pass']}")
    return 0 if updated == len(GRADES) else 1


if __name__ == "__main__":
    sys.exit(main())
