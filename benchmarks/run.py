"""Benchmark runner for Tename v0.1.

Runs a YAML-defined task against a named profile, captures the full
session event log, and writes a results JSON into
`benchmarks/results/<profile>-<date>.json` with placeholder fields for
manual grading.

CLI::

    python benchmarks/run.py --task research-001 --profile claude-opus-4-6
    python benchmarks/run.py --all --profile claude-opus-4-6
    python benchmarks/run.py --all --skip-sandbox  # no docker-backed tasks

The runner drives the Session Service and Harness Runtime directly
rather than going through the SDK. That gives it three things the
SDK doesn't expose:

- **Multi-prompt tasks.** Integration-001 runs four user turns in one
  session; the SDK marks a session terminal after each `send()` call.
  The runner reactivates the session between turns via a raw UPDATE
  (see `_reactivate_session`). This is a benchmark-only escape hatch —
  production code should create a new session instead.

- **Sandbox recipe pre-seeding.** Coding-001 and data-001 seed files
  via `agent.sandbox_recipe.files`; the runner writes those straight
  into the agents row.

- **Deterministic output.** One JSON file per run, ready for manual
  grading per `benchmarks/graders/manual.md`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

try:  # dev convenience — not required in production
    from dotenv import dotenv_values

    # Promote .env values only when the shell value is missing or empty.
    # Matches how `tests/conftest.py` treats an empty shell export as "unset".
    for _k, _v in dotenv_values().items():
        if _v and not os.environ.get(_k):
            os.environ[_k] = _v
except ImportError:  # pragma: no cover
    pass

from tename.harness import HarnessRuntime, ProfileLoader
from tename.proxy import ToolProxy
from tename.router.service import ModelRouter
from tename.sandbox import DockerBackend, Sandbox
from tename.sessions import EventType, SessionService
from tename.vault import Vault

DEFAULT_DATABASE_URL = "postgresql+psycopg://tename:tename@localhost:5433/tename_dev"
DEFAULT_PROFILE = "claude-opus-4-6"
REPO_ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = REPO_ROOT / "benchmarks" / "tasks"
RESULTS_DIR = REPO_ROOT / "benchmarks" / "results"


@dataclass
class Task:
    """Parsed YAML benchmark task."""

    id: str
    name: str
    description: str
    system_prompt: str
    framework: str
    tools: list[str]
    sandbox_recipe: dict[str, Any] | None
    prompts: list[str]
    max_turns: int
    pass_criteria: list[str]
    notes: str | None = None


@dataclass
class TaskResult:
    task_id: str
    task_name: str
    profile: str
    started_at: str
    duration_seconds: float
    session_id: str
    agent_id: str
    num_user_turns: int
    num_assistant_turns: int
    num_tool_calls: int
    stop_reasons: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    pass_criteria: list[dict[str, Any]] = field(default_factory=list)
    overall: dict[str, Any] = field(default_factory=lambda: {"pass": None, "notes": ""})
    graded_by: str = ""
    graded_at: str = ""


def load_task(task_id: str) -> Task:
    path = TASKS_DIR / f"{task_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"task file not found: {path}")
    raw = cast(dict[str, Any], yaml.safe_load(path.read_text()))

    agent = raw.get("agent", {})
    prompts_raw = raw.get("prompts", [])
    prompts = [prompts_raw] if isinstance(prompts_raw, str) else list(prompts_raw)
    if not prompts:
        raise ValueError(f"{task_id}: prompts must be a non-empty list")

    sandbox_recipe = raw.get("sandbox")
    if sandbox_recipe is not None and not isinstance(sandbox_recipe, dict):
        raise ValueError(f"{task_id}: sandbox must be a mapping")

    return Task(
        id=str(raw["id"]),
        name=str(raw.get("name", raw["id"])),
        description=str(raw.get("description", "")),
        system_prompt=str(agent.get("system_prompt", "")),
        framework=str(agent.get("framework", "vanilla")),
        tools=list(agent.get("tools", [])),
        sandbox_recipe=sandbox_recipe,
        prompts=prompts,
        max_turns=int(raw.get("max_turns", 10)),
        pass_criteria=list(raw.get("pass_criteria", [])),
        notes=raw.get("notes"),
    )


def list_tasks() -> list[str]:
    return sorted(p.stem for p in TASKS_DIR.glob("*.yaml"))


async def _insert_agent(
    engine: AsyncEngine,
    *,
    task: Task,
    profile: str,
) -> uuid.UUID:
    agent_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO agents "
                "(id, name, model, framework, system_prompt, tools, sandbox_recipe) "
                "VALUES (:id, :name, :model, :framework, :system_prompt, "
                "(:tools)::jsonb, "
                + ("(:recipe)::jsonb" if task.sandbox_recipe is not None else "NULL")
                + ")"
            ),
            {
                "id": str(agent_id),
                "name": f"bench-{task.id}",
                "model": profile,
                "framework": task.framework,
                "system_prompt": task.system_prompt or None,
                "tools": json.dumps(task.tools),
                **(
                    {"recipe": json.dumps(task.sandbox_recipe)}
                    if task.sandbox_recipe is not None
                    else {}
                ),
            },
        )
    return agent_id


async def _reactivate_session(engine: AsyncEngine, session_id: uuid.UUID) -> None:
    """Flip a terminal session back to ACTIVE so another turn can run.

    Benchmark-only. Production code should create a new session instead.
    Multi-prompt tasks need this because the harness marks every session
    COMPLETED on exit.
    """
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE sessions SET status = 'active' WHERE id = :id"),
            {"id": str(session_id)},
        )


def _summarize_event(event: Any) -> dict[str, Any]:
    """Convert an Event model to a JSON-serializable summary dict.

    Preserves the full payload; the JSON file IS the grading artifact.
    """
    return {
        "sequence": event.sequence,
        "id": str(event.id),
        "type": event.type.value,
        "payload": event.payload,
        "created_at": event.created_at.isoformat(),
    }


def _count_turns(events: list[Any]) -> tuple[int, int, int]:
    """Return (user_turns, assistant_turns, tool_calls) over an event list."""
    user = sum(1 for e in events if e.type == EventType.USER_MESSAGE)
    assistant = sum(
        1
        for e in events
        if e.type == EventType.ASSISTANT_MESSAGE and bool(e.payload.get("is_complete"))
    )
    tools = sum(1 for e in events if e.type == EventType.TOOL_CALL)
    return user, assistant, tools


async def run_task(
    task: Task,
    *,
    profile_name: str,
    database_url: str,
    enable_sandbox: bool,
    output_dir: Path,
) -> TaskResult:
    engine = create_async_engine(database_url, future=True)
    service = SessionService(database_url)
    model_router = ModelRouter()
    sandbox: Sandbox | None = Sandbox(DockerBackend()) if enable_sandbox else None
    vault = Vault()
    tool_proxy = ToolProxy(vault=vault)
    harness = HarnessRuntime(
        session_service=service,
        model_router=model_router,
        sandbox=sandbox,
        tool_proxy=tool_proxy,
        profile_loader=ProfileLoader(),
    )

    started_at = datetime.now(UTC)
    t0 = time.monotonic()
    session_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None

    try:
        agent_id = await _insert_agent(engine, task=task, profile=profile_name)
        session = await service.create_session(agent_id)
        session_id = session.id

        for i, prompt in enumerate(task.prompts):
            await service.emit_event(
                session_id,
                event_id=uuid.uuid4(),
                event_type=EventType.USER_MESSAGE,
                payload={"content": prompt},
            )
            await harness.run_session(session_id)
            if i < len(task.prompts) - 1:
                await _reactivate_session(engine, session_id)

        events = await service.get_events(session_id, limit=10000)

    finally:
        await service.close()
        await engine.dispose()

    duration = time.monotonic() - t0
    user_turns, assistant_turns, tool_calls = _count_turns(events)

    result = TaskResult(
        task_id=task.id,
        task_name=task.name,
        profile=profile_name,
        started_at=started_at.isoformat(),
        duration_seconds=round(duration, 3),
        session_id=str(session_id),
        agent_id=str(agent_id),
        num_user_turns=user_turns,
        num_assistant_turns=assistant_turns,
        num_tool_calls=tool_calls,
        events=[_summarize_event(e) for e in events],
        pass_criteria=[{"criterion": c, "pass": None, "notes": ""} for c in task.pass_criteria],
    )

    _write_result(result, output_dir, profile_name, started_at)
    return result


def _write_result(
    result: TaskResult,
    output_dir: Path,
    profile_name: str,
    started_at: datetime,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = started_at.strftime("%Y-%m-%d")
    path = output_dir / f"{profile_name}-{date_str}-{result.task_id}.json"
    payload = {
        "task_id": result.task_id,
        "task_name": result.task_name,
        "profile": result.profile,
        "started_at": result.started_at,
        "duration_seconds": result.duration_seconds,
        "session_id": result.session_id,
        "agent_id": result.agent_id,
        "counts": {
            "user_turns": result.num_user_turns,
            "assistant_turns": result.num_assistant_turns,
            "tool_calls": result.num_tool_calls,
        },
        "pass_criteria": result.pass_criteria,
        "overall": result.overall,
        "graded_by": result.graded_by,
        "graded_at": result.graded_at,
        "events": result.events,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return path


def _print_result_summary(result: TaskResult) -> None:
    print(f"\n=== {result.task_id}: {result.task_name} ===")
    print(f"  profile:           {result.profile}")
    print(f"  duration:          {result.duration_seconds:.1f}s")
    print(f"  user turns:        {result.num_user_turns}")
    print(f"  assistant turns:   {result.num_assistant_turns}")
    print(f"  tool calls:        {result.num_tool_calls}")
    print("  pass criteria:")
    for c in result.pass_criteria:
        print(f"    - [ ] {c['criterion']}")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tename benchmark runner")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--task", help="Task id to run (e.g. research-001).")
    group.add_argument("--all", action="store_true", help="Run every task in benchmarks/tasks/.")
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument(
        "--database-url",
        default=os.getenv("TENAME_DATABASE_URL", DEFAULT_DATABASE_URL),
    )
    parser.add_argument(
        "--output-dir",
        default=str(RESULTS_DIR),
        help="Directory for results JSON files.",
    )
    parser.add_argument(
        "--skip-sandbox",
        action="store_true",
        help="Disable the docker-backed sandbox. Skips tasks that require it.",
    )
    return parser.parse_args(argv)


def _task_needs_sandbox(task: Task) -> bool:
    if task.sandbox_recipe:
        return True
    sandbox_tools = {"bash", "python", "file_read", "file_write", "file_edit", "file_list"}
    return any(t in sandbox_tools for t in task.tools)


async def _main_async(args: argparse.Namespace) -> int:
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("error: ANTHROPIC_API_KEY must be set", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir)
    enable_sandbox = not args.skip_sandbox

    task_ids = list_tasks() if args.all else [args.task]
    results: list[TaskResult] = []

    for task_id in task_ids:
        task = load_task(task_id)

        if not enable_sandbox and _task_needs_sandbox(task):
            print(f"skipping {task_id}: requires sandbox (rerun without --skip-sandbox)")
            continue

        print(f"\n--- Running {task_id} ({task.name}) ---")
        try:
            result = await run_task(
                task,
                profile_name=args.profile,
                database_url=args.database_url,
                enable_sandbox=enable_sandbox,
                output_dir=output_dir,
            )
        except Exception as exc:
            print(f"error running {task_id}: {exc!r}", file=sys.stderr)
            continue

        _print_result_summary(result)
        results.append(result)

    print(f"\nWrote {len(results)} result file(s) to {output_dir}/")
    return 0 if results else 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
