# Deep Agents Overview

Reference material for implementing the Deep Agents adapter. This is background material - check the actual Deep Agents repo and docs for current details.

## What Deep Agents is

Deep Agents is an open-source Python framework from LangChain for building agents that can handle deep, multi-step tasks. It extends the basic ReAct pattern with:

- **Planning** via a built-in `write_todos` tool that the agent uses to create and track plans
- **Subagents** that can be spawned to handle focused sub-tasks
- **Virtual filesystem** where agents can write and read files during execution
- **Context management** strategies for long-running tasks

It's inspired by Anthropic's Claude Code agent architecture and published by the LangChain team.

## Core concepts

### Planning

Agents use `write_todos(todos: list[str])` to create a plan, then work through items. The todo list is maintained across turns.

### Subagents

Agents can invoke `call_subagent(prompt: str, subagent_type: str)` to delegate work to a specialized sub-agent. The subagent runs to completion and returns a result.

### Virtual filesystem

Agents have access to `write_file(path, content)` and `read_file(path)` for persistent notes and intermediate results.

### Message format

Deep Agents uses LangChain's message types:
- `HumanMessage` - user input
- `AIMessage` - agent responses, includes `tool_calls` field when the agent wants to invoke tools
- `ToolMessage` - tool execution results
- `SystemMessage` - instructions

## How Tename integrates

The Deep Agents adapter in Tename translates between Deep Agents concepts and Tename primitives:

### Event mapping

| Deep Agents | Tename event |
|-------------|----------------|
| HumanMessage | user_message |
| AIMessage (text content) | assistant_message |
| AIMessage.tool_calls | tool_call (one per call) |
| ToolMessage | tool_result |
| write_todos() call | harness_event (type=plan) |
| call_subagent() call | subagent_spawn + creates child session |
| Subagent return | subagent_result |
| write_file() / read_file() | handled via sandbox filesystem |

### Why this mapping

Deep Agents has opinions about how agents are structured. Tename provides runtime infrastructure. The adapter translates Deep Agents' conceptual model (messages, tool calls, todos, subagents) into Tename's conceptual model (events, sessions, sandboxes).

Neither framework "wins" - they compose. Deep Agents orchestrates the agent's logic. Tename runs the infrastructure underneath.

### What the user writes

A user using Deep Agents + Tename writes essentially normal Deep Agents code:

```python
from deepagents import create_deep_agent
from tename_sdk import Tename

# Normal Deep Agents agent setup
agent_impl = create_deep_agent(
    tools=[web_search_tool, ...],
    instructions="You are a research assistant.",
)

# Tename provides the runtime
client = Tename()
agent = client.agents.create(
    name="researcher",
    model="claude-opus-4-6",
    framework="deep_agents",
    system_prompt="You are a research assistant.",
    tools=["web_search"],  # Tename's tool registry
)

session = client.sessions.create(agent_id=agent.id)
for event in session.send("Research the EV charging market"):
    # Events come through in Tename's event format
    ...
```

The user benefits from Deep Agents' orchestration (planning, subagents, etc.) AND Tename's reliability (durable sessions, sandbox isolation, credential vault).

## What to verify when implementing

Before implementing the adapter, check:

1. Current Deep Agents API (it evolves quickly)
2. Exact message format expected by `create_deep_agent`
3. How tool_calls are represented in AIMessage
4. Whether Deep Agents supports streaming callbacks
5. How subagents are currently implemented (direct recursion vs. separate process)

Check the GitHub repo or package documentation to verify current state.

## Known differences from Tename's model

- Deep Agents assumes a single model throughout an agent's run (Tename supports per-session model selection; the adapter needs to handle this)
- Deep Agents manages its own context (Tename's harness also manages context; the adapter chooses which wins - probably Tename's harness, with Deep Agents seeing a pre-built context window)
- Deep Agents' virtual filesystem conflicts with Tename's sandbox filesystem (the adapter maps them to each other)

These are solvable but require careful design. The adapter session (S8 in the roadmap) is where these get worked out.
