"""Research agent example.

A Deep Agents-style agent with planning (`write_todos`) and web search
(`web_search`, Tavily-backed). The agent plans its research, calls the
proxy-backed web_search tool, and synthesizes a brief answer.

Prerequisites:
  - Postgres running (`make dev` + `make migrate`)
  - `ANTHROPIC_API_KEY` in the environment
  - A Tavily API key stored in the vault under the name `web_search_api_key`:

      tename vault set web_search_api_key
      # paste your Tavily key when prompted

    (Tavily offers a free tier at https://tavily.com)

Run it:
  python examples/02-research-agent/main.py
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

    with Tename(enable_sandbox=False) as client:
        if "web_search_api_key" not in client.vault.list():
            print(
                "warning: no `web_search_api_key` in the vault — web_search calls "
                "will return credential errors. See this example's README.",
                file=sys.stderr,
            )

        agent = client.agents.create(
            name="research-agent",
            model="claude-opus-4-6",
            framework="deep_agents",
            system_prompt=(
                "You are a concise research assistant. Use write_todos to sketch "
                "a short plan, then use web_search to gather evidence, then answer "
                "in two or three sentences with citations."
            ),
            tools=["write_todos", "web_search"],
        )

        session = client.sessions.create(agent_id=agent.id)
        prompt = "Who won the 2024 Nobel Prize in Physics, and for what work?"
        print(f"\nuser: {prompt}\n")

        for event in session.send(prompt):
            if event.type == EventType.TOOL_CALL:
                tool = event.payload.get("tool_name")
                print(f"[tool_call] {tool}({event.payload.get('input')})")
            elif event.type == EventType.TOOL_RESULT:
                if event.payload.get("is_error"):
                    print(f"[tool_result ERROR] {event.payload.get('error')}")
                else:
                    content = event.payload.get("content", "")
                    snippet = content[:200].replace("\n", " ")
                    print(f"[tool_result] {snippet}...")
            elif (
                event.type == EventType.ASSISTANT_MESSAGE
                and event.payload.get("is_complete")
                and event.payload.get("content")
            ):
                print(f"\nassistant: {event.payload['content']}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
