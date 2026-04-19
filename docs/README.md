# Tename Documentation

## Directory map

```
docs/
├── QUICKSTART.md                ← clone → running agent in 10 minutes
│
├── vision/                      ← what and why
│   ├── product-vision.md        ← positioning, audience, goals
│   └── principles.md            ← non-negotiable architectural commitments
│
├── architecture/                ← how the system fits together
│   ├── overview.md              ← system diagram and component map
│   ├── session-service.md       ← durable event log
│   ├── harness-runtime.md       ← stateless brain loop
│   ├── sandbox.md               ← code execution in isolation
│   ├── model-router.md          ← provider abstraction
│   ├── tool-proxy.md            ← credential isolation
│   └── data-model.md            ← event schemas and types
│
├── harness/                     ← the "brain" layer
│   ├── philosophy.md            ← why it's designed this way
│   ├── profile-format.md        ← YAML schema for model profiles
│   ├── profile-claude-opus-4-6.md ← reference profile
│   ├── adapter-deep-agents.md   ← Deep Agents adapter concept mapping
│   └── benchmark-suite.md       ← how we validate profiles
│
├── dx/                          ← developer experience
│   ├── sdk-design.md            ← Python SDK API
│   ├── cli-design.md            ← CLI commands (v0.1 = vault only)
│   └── installation.md          ← setup and configuration
│
├── operations/                  ← running Tename
│   ├── deployment.md            ← deployment modes and considerations
│   └── observability.md         ← logging and debugging
│
├── reference/                   ← external framework details
│   └── deep-agents-overview.md  ← Deep Agents adapter reference
│
└── memory/                      ← release / validation records
    └── v0-validation-results.md ← v0.1 exit criteria evidence
```

## Where to start

If you're a **new user**, start with [QUICKSTART.md](QUICKSTART.md) —
clone to running agent in under 10 minutes. The
[installation.md](dx/installation.md) doc is the reference version
of the same material.

If you're a **contributor**, start with [product-vision.md](vision/product-vision.md) to understand the project, then [principles.md](vision/principles.md) for the design constraints, then [overview.md](architecture/overview.md) for the system architecture.

If you want to **add a model profile**, read [profile-format.md](harness/profile-format.md) and use [profile-claude-opus-4-6.md](harness/profile-claude-opus-4-6.md) as a reference.

If you want to **add a framework adapter**, read [harness-runtime.md](architecture/harness-runtime.md) (framework adapter section) and [deep-agents-overview.md](reference/deep-agents-overview.md) for an example of how adapters work.
