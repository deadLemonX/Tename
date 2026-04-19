"""Coding agent example.

An agent that writes Python, runs it in an isolated Docker sandbox, and
reports the result. Uses the built-in sandbox tools (`python`, `bash`,
`file_read`, `file_write`).

Prerequisites:
  - Docker running
  - Postgres running (`make dev` + `make migrate`)
  - `ANTHROPIC_API_KEY` in the environment

Run it:
  python examples/03-coding-agent/main.py
"""

from __future__ import annotations

import os
import sys

from tename.sdk import EventType, Tename


def main() -> int:
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("error: ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 1

    os.environ.setdefault(
        "TENAME_DATABASE_URL",
        "postgresql+psycopg://tename:tename@localhost:5433/tename_dev",
    )

    with Tename() as client:
        agent = client.agents.create(
            name="coding-agent",
            model="claude-opus-4-6",
            system_prompt=(
                "You are a careful software engineer. You have a Python sandbox "
                "(tools: python, bash, file_read, file_write, file_edit, file_list). "
                "Write code to solve problems, run it, observe the output, and "
                "iterate until you're confident the answer is correct. Report the "
                "final answer clearly."
            ),
            tools=["python", "bash", "file_read", "file_write", "file_edit", "file_list"],
        )

        session = client.sessions.create(agent_id=agent.id)
        prompt = (
            "Use the Python sandbox to compute the 50th Fibonacci number "
            "(where fib(0)=0, fib(1)=1). Report the number and the code you "
            "used."
        )
        print(f"\nuser: {prompt}\n")

        for event in session.send(prompt):
            if event.type == EventType.TOOL_CALL:
                tool = event.payload.get("tool_name")
                inp = event.payload.get("input", {})
                preview = str(inp)[:120].replace("\n", " ")
                print(f"[tool_call] {tool}({preview}...)")
            elif event.type == EventType.TOOL_RESULT:
                if event.payload.get("is_error"):
                    print(f"[tool_result ERROR] {event.payload.get('error')}")
                else:
                    content = event.payload.get("content", "")
                    snippet = content[:200].replace("\n", " ")
                    print(f"[tool_result] {snippet}")
            elif (
                event.type == EventType.ASSISTANT_MESSAGE
                and event.payload.get("is_complete")
                and event.payload.get("content")
            ):
                print(f"\nassistant: {event.payload['content']}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
