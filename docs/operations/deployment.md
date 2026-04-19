# Deployment

v0.1 is local-first and in-process. This doc covers how users
deploy Tename for real usage.

## Deployment modes in v0.1

### 1. Local development (primary mode for v0.1)

User's laptop, running everything locally:
- Postgres in Docker (from the bundled `docker-compose.yml`)
- Tename as a Python library imported into the developer's code
- Sandboxes as Docker containers on the same machine
- Everything on one machine

This is how all v0.1 usage happens.

### 2. Single-server deployment (advanced)

For users running a dedicated Tename-backed service:
- A VM or dedicated server
- Postgres running on it (or managed Postgres nearby)
- The user's Python service imports `tename` and holds a `Tename`
  client instance for the process lifetime
- Docker installed for sandboxes

v0.1 runs in-process only — there is no separate `tename serve`
daemon. The SDK's layering leaves room for a client-server split in a
later release.

### 3. Containerized deployment (experimental)

Running the user's Tename-using service inside a container on
Kubernetes, Nomad, ECS, etc:
- Container image built from the user's code plus `pip install tename`
- Postgres connection configured via `TENAME_DATABASE_URL`
- Docker-in-Docker or a Docker-socket mount for sandboxes (see below)
- Not officially supported in v0.1, but possible for advanced users

## Sandboxes in production

The Docker-in-Docker question is real. Running sandboxes requires access to a container runtime. Options:

**Simple: Sandboxes on the same host**
- Mount the Docker socket into the Tename container (`/var/run/docker.sock`)
- Tename uses the host's Docker to create sibling containers (not nested)
- Simple, works, but gives Tename full host Docker access

**Better: Dedicated sandbox host**
- Tename service on one machine
- Sandbox execution on a separate machine pool
- Requires backend development (remote Docker client or custom backend)
- Not in v0.1

**Future: Firecracker microVMs**
- Better isolation than Docker
- Needs a sandbox backend implementation
- Commercial opportunity

For v0.1, the "simple" option is what most users will do. Users with stricter isolation needs can develop their own backend that conforms to the sandbox interface.

## Database considerations

### Connection pooling

Tename uses SQLAlchemy's async connection pool with default settings
(`pool_size=5`, `max_overflow=10`). v0.1 does not expose an env var
knob for pool size; if you need to override, pass a
pre-configured `AsyncEngine` by constructing `SessionService` directly
(the SDK currently owns engine creation internally — finer-grained
pool configuration is a v0.2 scope item).

### Migrations

From a repo checkout, run `make migrate` (equivalent to
`uv run alembic upgrade head`) on first deployment and on every
upgrade. Migrations are forward-only; we don't support rollbacks
automatically. Test upgrades in a non-production environment first.

### Backups

Tename doesn't manage database backups. Use standard Postgres tooling (`pg_dump`, continuous archiving, managed service backups). The event log is the source of truth, so backups are critical.

### Scaling

For v0.1:
- Postgres vertical scaling is enough for most use cases
- Session Service is the primary hot path - index usage is critical
- Read replicas can be added for read-heavy workloads (Tename doesn't know about them natively yet, but can be pointed at a replica for read-only operations)

For larger scale, the architecture supports splitting services. Not in v0.1 scope.

## Observability

v0.1 uses standard Python `logging` with structured JSON output. Aggregate logs however you normally would (Loki, ELK, CloudWatch, etc.).

Metrics to watch:
- Session creation rate
- Events per second
- Model call latency p50/p95/p99
- Sandbox provision latency
- Error rate by type

Not built in for v0.1:
- Prometheus metrics endpoint
- OpenTelemetry tracing
- Dedicated dashboards

These come in v0.2+ if operators ask for them. For now, structured logs + your existing logging pipeline is enough.

## Secrets management

The vault stores tool credentials, but Tename's own configuration (API keys, DB password) lives in env vars. For production:

- Use your existing secrets management (Kubernetes Secrets, AWS Secrets Manager, HashiCorp Vault, etc.)
- Inject as env vars at container startup
- Do NOT commit secrets to code or config files

The vault itself has a passphrase. Store that passphrase the same way you'd store any other master secret.

## Upgrading

1. Read CHANGELOG.md for the target version
2. Test in a non-production environment first
3. Back up the database
4. Stop the process that holds the `Tename` client
5. Upgrade the package: `pip install --upgrade tename`
6. Run migrations: `tename migrate` (uses `TENAME_DATABASE_URL`).
   From a repo checkout, `make migrate` does the same thing.
7. Restart the process
8. Verify by running your own smoke test
   (`examples/01-hello-world/main.py` is a good starting point)

## Not in v0.1

- Helm chart for Kubernetes deployment (coming in v0.2+ if demand)
- Official Docker images (coming in v0.2)
- Multi-region deployment patterns (not for a while)
- HA configurations (single-instance is fine for v0.1 adoption)

## Security considerations

**The sandbox is the primary attack surface.** LLM-generated code runs there. Network egress is open by default (for local dev convenience). In production:

- Consider running Tename in a VPC with restricted network policies
- Restrict sandbox network egress via Docker network policies or VPC security groups
- Monitor sandbox resource usage for anomalies
- Rotate any credentials in the vault periodically

**The vault passphrase is the master key.** If it's compromised, all stored credentials are compromised. Protect it like you'd protect any master secret.

**Database contains conversation history.** Sessions may contain sensitive information from users. Encrypt the database at rest. Restrict access to production databases to minimum-necessary personnel.
