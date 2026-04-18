# Adapter: Deep Agents

This document describes how Tename's Deep Agents adapter translates
between the [Deep Agents](https://github.com/langchain-ai/deepagents)
framework conventions and Tename's session event log.

Deep Agents is Tename's primary target framework for v0.1. Users who
write `framework: deep_agents` on an agent get:

- A message format that preserves full tool-use rounds, so multi-turn
  tool-calling agents work correctly against Anthropic's Messages API.
- The Deep Agents built-in tool schemas (`write_todos`, filesystem tools,
  `task`) surfaced to the model router.

## Scope in v0.1

The adapter is a **translation layer**. Tename's own stateless harness
loop drives the turns; we do not execute Deep Agents' LangGraph graph.
Users writing Deep Agents agents get:

- Correct message shape and tool schemas, so the model behaves as if it
  were running inside Deep Agents proper.
- Durability and crash recovery from Tename's session log.

What the adapter does NOT do in v0.1:

- **No `deepagents` Python package dependency.** The adapter hardcodes
  the tool schemas (see below). If a future version of `deepagents`
  changes a tool signature, we'll update the adapter — same cadence as
  any other model/framework profile change. We revisit the decision to
  import `deepagents` directly once the package's API stabilizes and the
  value of importing real types exceeds the dependency weight.
- **No real tool execution.** `write_todos`, filesystem tools, and `task`
  are schema-only surface in v0.1. Tename's harness stubs every tool call
  with `tool_result(is_error=True)`. Real execution lands in S9 (sandbox)
  and S10 (tool proxy).
- **No subagent graph.** The `task` tool schema is surfaced, but
  spawning a child session is deferred. For now, calling `task` produces
  the same stubbed `tool_result` as any other tool.

## Concept mapping

| Deep Agents concept             | Tename event                                             | v0.1 status |
| :------------------------------ | :------------------------------------------------------- | :---------- |
| `HumanMessage`                  | `user_message`                                           | implemented |
| `AIMessage` (text)              | `assistant_message(is_complete=True)`                    | implemented |
| `AIMessage.tool_calls`          | `tool_call` event(s) following the assistant message     | implemented |
| `ToolMessage`                   | `tool_result` event                                      | implemented |
| System prompt                   | `system_event(type=system_prompt)` at session head       | implemented |
| `write_todos` → plan record     | `harness_event(type=plan)` (sidecar observability event) | **deferred** — see below |
| `task` → subagent spawn         | `harness_event(type=subagent_spawn)` + child session     | **deferred** — see below |

### Why plan / subagent_spawn harness_events are deferred

The `FrameworkAdapter.chunk_to_event` contract returns a single
`PendingEvent | None` per chunk. Emitting both a `tool_call` event (which
S7's harness loop needs to execute or stub) AND a sidecar
`harness_event(type=plan)` from the same `tool_call_end` chunk would
require extending the ABC to return multiple events. That's the right
long-term shape — but it's a coordinated change across the ABC,
VanillaAdapter, the S7 loop, and ADR 0002, and we don't yet have real
production observability tooling that needs the split.

For v0.1 the `tool_call` event with `tool_name="write_todos"` is the plan
record: its input payload contains the full todo list. Observability
callers filter by `type=tool_call AND tool_name=write_todos`. Same for
`task`. We'll promote these to first-class harness events when users
report real friction from the current shape, informed by how plans
actually flow in practice (v0.2 or later).

### Message grouping

Tool-calling turns arrive in the log as multiple events that represent
a single Anthropic "message":

```
[1] user_message            "analyze the CSV"
[2] assistant_message       "I'll read it first."  (is_complete=True)
[3] tool_call               tool_id=toolu_A   name=read_file
[4] tool_call               tool_id=toolu_B   name=ls
[5] tool_result             tool_call_id=<event 3's id>
[6] tool_result             tool_call_id=<event 4's id>
[7] assistant_message       "Here's what I found..."  (is_complete=True)
```

The adapter's `build_context` folds this into:

- `Message(role="user", content="analyze the CSV")`
- `Message(role="assistant", content=[text_block, tool_use_A, tool_use_B])`
- `Message(role="tool", content=[tool_result_A, tool_result_B])`
- `Message(role="assistant", content=[text_block])`

The Anthropic provider (`tename.router.providers.anthropic`) then folds
tool-role messages into user messages with `tool_result` content blocks,
matching Anthropic's wire format.

### tool_use_id resolution

`tool_result` events store `tool_call_id` = the UUID of the corresponding
`tool_call` event (internal pointer). Anthropic's API needs
`tool_use_id` = the model's opaque `tool_use.id` from the tool_use block.
The adapter builds an `event_id → model_tool_id` map on entry to
`build_context` by scanning `tool_call` events for their
`payload.tool_id`, then uses that map to populate each tool_result
block's `tool_use_id`.

Orphan tool_results (whose tool_call event was dropped by compaction or
never recorded) are **skipped** — Anthropic would reject the request if
a tool_result referenced a tool_use_id that didn't appear in the
preceding assistant message.

## Tool schemas

The adapter hardcodes JSON schemas for Deep Agents' built-in tools. They
match the conventions published in the `deepagents` README and
`langchain.agents.middleware.TodoListMiddleware` / Deep Agents'
`FilesystemMiddleware` as of v0.5:

| Tool            | Summary                                            |
| :-------------- | :------------------------------------------------- |
| `write_todos`   | Replace the full todo list (planning primitive)    |
| `ls`            | List virtual-filesystem paths (no arguments)       |
| `read_file`     | Read a file, with optional offset/limit            |
| `write_file`    | Create or overwrite a file                         |
| `edit_file`     | String-replacement edit with optional `replace_all`|
| `task`          | Spawn a subagent (description + subagent_type)     |

An agent configured with `framework: deep_agents` lists the built-ins it
wants in `agent.tools`. The adapter surfaces only those names as
`ToolDef`s to the model router. Unknown names are skipped with a log
warning — user-defined tools get surfaced via the sandbox (S9) and tool
proxy (S10) integrations, which attach to the harness layer, not the
adapter.

## Contributor guidance

Extending this adapter later:

1. **Adding / updating a Deep Agents built-in:** update
   `BUILTIN_TOOLS` in
   `src/tename/harness/adapters/deep_agents.py`. Keep the schema aligned
   with the Deep Agents tool's actual Pydantic args model. Add a test in
   `tests/harness/test_deep_agents_adapter.py` asserting the `required`
   and any enum fields match.

2. **Promoting `plan` / `subagent_spawn` to sidecar harness events:**
   this requires extending `FrameworkAdapter.chunk_to_event` to return
   `Iterable[PendingEvent]` (or adding a second method
   `extra_events_for_chunk`). Update VanillaAdapter and the S7 loop's
   `_run_turn`. Write an ADR documenting the multi-emit convention and
   how replay idempotency interacts with it (the `tool_call` event
   should keep its current uuid4; the `harness_event` sidecar needs
   either a deterministic id keyed on the tool_call's event id or a
   uuid4, and `build_context` must be idempotent on replay).

3. **Real tool execution for Deep Agents built-ins:** filesystem tools
   (`ls`, `read_file`, `write_file`, `edit_file`) naturally map onto
   the sandbox filesystem in S9. `write_todos` is a state mutation
   with no side effect in the sandbox — the harness can record the
   todo list as a `harness_event(type=plan)` (after the sidecar
   change above) and emit a synthetic `tool_result` with
   `content="ok"`. `task` needs sub-session support in SessionService
   (deferred beyond v0.1).

## References

- Deep Agents repository: <https://github.com/langchain-ai/deepagents>
- Tename harness runtime: [`harness-runtime.md`](../architecture/harness-runtime.md)
- Tename adapter interface:
  [`src/tename/harness/adapters/base.py`](../../src/tename/harness/adapters/base.py)
- ADR 0002 (stateless harness design):
  `~/tename-private/memory/decisions/0002-stateless-harness-design.md`
