"""Hello-world example.

The simplest possible Tename script: create an agent, open a session,
send one message, stream the response to stdout. No tools, no sandbox.

Prerequisites:
  - Postgres running (`make dev` + `make migrate`)
  - `ANTHROPIC_API_KEY` in the environment

Run it:
  python examples/01-hello-world/main.py
"""

from __future__ import annotations

import os
import sys

from tename.sdk import EventType, Tename


def main() -> int:
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("error: ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 1

    # The SDK reads TENAME_DATABASE_URL from the environment; fall back to
    # the default dev compose URL so the example works after `make dev`.
    os.environ.setdefault(
        "TENAME_DATABASE_URL",
        "postgresql+psycopg://tename:tename@localhost:5433/tename_dev",
    )

    with Tename(enable_sandbox=False) as client:
        agent = client.agents.create(
            name="hello-world",
            model="claude-opus-4-6",
            system_prompt="You are a friendly assistant. Reply in one short sentence.",
        )
        session = client.sessions.create(agent_id=agent.id)

        print("\nuser: Hello! Tell me one interesting fact about octopuses.\n")
        print("assistant: ", end="", flush=True)

        for event in session.send("Hello! Tell me one interesting fact about octopuses."):
            if event.type == EventType.ASSISTANT_MESSAGE and event.payload.get("is_complete"):
                print(event.payload.get("content", ""))

    return 0


if __name__ == "__main__":
    sys.exit(main())
