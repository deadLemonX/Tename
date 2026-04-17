# Harness Philosophy

This document explains WHY the harness is designed the way it is. Understanding the why makes it easier to contribute without accidentally violating the design.

## The harness rot problem

Anthropic's engineering blog coined a term for what happens to agent harnesses over time: "harness rot." As models evolve, workarounds for specific model quirks get added to the harness. Claude Sonnet 4.5 had "context anxiety" — it would wrap up tasks prematurely as context filled. Anthropic added context resets to compensate. Claude Opus 4.5 didn't have the behavior. The resets became dead weight.

This pattern happens with every model and every quirk. Without discipline, the harness becomes a graveyard of obsolete workarounds. Models improve. Workarounds don't get removed. Code complexity grows.

**Tename's answer:** profiles with expiration dates.

Every quirk lives in a profile, not in code. Every quirk has an `added` date and a `review_date`. When the review date passes, we run the benchmark suite with the quirk disabled. If benchmarks still pass, we remove the quirk. If not, we push the review date forward.

This is the discipline that keeps the harness clean forever.

## Why statelessness matters

A stateful harness seems simpler. Hold the event history in memory, append as events happen, write to disk periodically. Fewer database calls. Better performance.

But it's wrong for agents. Here's why:

**Agents run for minutes to hours.** During that time, many things can fail: network blips, provider outages, process crashes, hardware issues, planned deploys. A stateful harness loses work on every failure.

**Recovery is the hard part.** If state lives in memory, recovery requires reconstructing it from somewhere. You end up building a write-ahead log, or crash-safe journaling, or periodic snapshots. You've reinvented the session service, but worse.

**Users don't forgive lost work.** "My agent ran for 45 minutes and then crashed and lost everything" is the kind of experience that kills adoption. Statelessness makes this impossible.

**Debugging is easier.** Every state change is an event. Every event is logged. If something went wrong, the log tells you what happened. No "what was in the harness's memory at the time?" questions.

The cost of statelessness is: more database calls per loop iteration. The benefit is: correctness under failure, recovery for free, full auditability. The trade-off is overwhelmingly in favor of statelessness.

## Why streaming is default

Most agent infrastructure treats streaming as an option. Default is request/response; streaming is extra.

Tename reverses this. Streaming is default. Request/response is a degenerate case of streaming (single chunk).

Why:

**Time to first token matters.** Users forgive slow overall responses if they see something happening. They don't forgive 20 seconds of nothing followed by a wall of text.

**Partial results are valuable.** Even if the full response isn't done, users can start reading, planning next steps, or catching errors early.

**It forces good design.** Streaming-by-default means event emission is incremental. Events are small, atomic, and composable. Batch-first design leads to monolithic events that are hard to reason about.

**It matches reality.** Model providers stream. Tools can stream. Why would we unstream them into batches just to unbatch them again downstream?

The cost is: slightly more complex code to handle chunks. The benefit is: perceived performance, real-time observability, and better architectural hygiene.

## Why we delegate so much

The core loop is ~15 lines of actual logic. Everything interesting is delegated:

- Model calls → Model Router
- Tool execution → Sandbox or Tool Proxy
- Context building → Framework Adapter
- Compaction decisions → Profile
- Tool routing → Profile and agent config

This is deliberate. The loop is supposed to be boring. It's the glue. The interesting decisions happen in the things the loop calls.

**Why this matters for contributors:** If you're about to add logic to the loop, stop. Ask: could this live in a profile? In an adapter? In the model router? Usually yes. Putting it in the loop makes the loop special for some cases, which breaks the "boring glue" property.

## Why profiles are YAML, not code

Profiles could be Python classes with methods. That would be more flexible — you could add arbitrary logic. But we chose YAML.

Why:

**YAML is reviewable.** Non-programmers can read a profile. Model experts who aren't software engineers can contribute improvements. Diffs on YAML are easier to audit.

**YAML is forkable.** A user can copy a profile, modify it for their workload, and use it without needing to write Python. Customization is cheap.

**YAML is declarative.** A profile describes configuration, not behavior. Behavior stays in the harness code. This keeps the harness code the single place where behavior changes require code review.

**YAML is testable.** Profile validation is straightforward. The benchmark suite can iterate through profile variants mechanically.

The cost is: profile expressiveness is limited. Complex logic has to live somewhere. The answer is: if a profile needs complex logic, that's a signal the harness needs a new primitive the profile can use. Add the primitive to the harness, make the profile describe how to use it.

## Why framework adapters instead of one unified API

Every agent framework has opinions. Deep Agents thinks in plans and subagents. Claude Agent SDK thinks in messages and tool uses. Pydantic AI thinks in typed models.

We could:

1. Pick one opinion and force everyone to adopt it.
2. Invent a new opinion and make everyone adapt to it.
3. Support multiple opinions via adapters.

(1) limits adoption. (2) starts a new framework war we don't need to start. (3) lets everyone keep their existing code.

The adapter pattern means Tename doesn't compete with Deep Agents. It runs beneath Deep Agents. Same for every other framework. We're infrastructure; they're frameworks. Clean separation.

## Why open source, not commercial from day one

Commercial-from-day-one would require us to sell something before we've proven anything. Infrastructure trust is earned, not bought. Open source lets trust accumulate over time through:

- Auditability (anyone can read the code)
- Modifiability (anyone can adapt it to their needs)
- Portability (nobody's locked in)
- Community (contributors add value we couldn't add alone)

Open source lets trust accumulate over time through:

## What we reject

- "We should support every model identically" — no, we tune per model via profiles
- "Let's add a quick branch for this model" — no, put it in the profile
- "Let's cache the context in memory for performance" — no, that's stateful
- "Let's batch events before emitting" — no, streaming is default
- "Let's add this framework to the harness loop" — no, add an adapter
- "Profiles should be Python" — no, YAML for reviewability and forkability
- "We need a GUI to configure this" — maybe later, but code and YAML are primary

These aren't arbitrary. Each rejection protects a property that matters (cleanness, reliability, composability, community-friendliness).
