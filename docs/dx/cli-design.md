# CLI Design

Tename ships a `platform` CLI (or whatever the final package name is) for common operations that don't require writing Python.

## Design principles

1. **Parallel structure to the SDK.** `tename agents create` matches `client.agents.create()`.
2. **Helpful defaults, explicit overrides.** Most commands work with no flags.
3. **Interactive where it helps, scripted where it doesn't.** Use `--yes` or `--quiet` for scripting.

## Commands

### Dev environment

```bash
# Start the local dev stack (Postgres, etc.)
tename dev

# Stop the dev stack
tename dev-stop

# Reset the dev stack (destroys data, prompts for confirmation)
tename dev-reset

# Check status of the dev stack
tename dev-status
```

### Migrations

```bash
# Apply pending migrations
tename migrate

# Create a new migration (for contributors)
tename migrate create "add_user_preferences"

# Roll back one migration (careful, may be destructive)
tename migrate rollback
```

### Vault

```bash
# Store a credential (prompts for value, doesn't log it)
tename vault set <name>

# List stored credentials (names only, never values)
tename vault list

# Remove a credential
tename vault remove <name>

# Rotate the vault passphrase
tename vault rotate-passphrase
```

### Agents

```bash
# Create an agent from a config file
tename agents create --config agent.yaml

# List agents
tename agents list

# Show an agent's config
tename agents show <agent-id>

# Delete an agent
tename agents delete <agent-id>
```

### Sessions

```bash
# Create and run a session interactively
tename sessions run <agent-id>

# List sessions for an agent
tename sessions list --agent <agent-id>

# Show a session's events
tename sessions show <session-id>

# Show a session's events in a readable format
tename sessions replay <session-id>

# Delete a session
tename sessions delete <session-id>
```

### Profiles

```bash
# List available profiles (built-in + user)
tename profiles list

# Show a profile's config
tename profiles show claude-opus-4-6

# Validate a profile file
tename profiles validate /path/to/my-profile.yaml

# Run benchmarks against a profile
tename profiles benchmark claude-opus-4-6

# Run benchmarks against all built-in profiles
tename profiles benchmark --all
```

### Benchmarks

```bash
# Run a specific benchmark task
tename benchmark run research-001 --profile claude-opus-4-6

# Run all benchmarks
tename benchmark run --all --profile claude-opus-4-6

# Show results for the most recent run
tename benchmark results

# Show results for a specific profile
tename benchmark results --profile claude-opus-4-6
```

### Version / info

```bash
# Show Tename version
tename version

# Show system info (Python version, Docker status, DB status)
tename info

# Run a self-check (validates config, dependencies, connectivity)
tename doctor
```

## Global flags

Available on all commands:

- `--verbose` / `-v` : verbose output
- `--quiet` / `-q` : suppress non-error output
- `--json` : output structured JSON (for scripting)
- `--config <path>` : specify config file location
- `--help` / `-h` : show help

## Implementation

Use `click` for the CLI framework - mature, well-documented, good integration with pytest.

Commands live in `src/<package>/cli/`:
- `main.py` - entry point
- `commands/dev.py`, `commands/vault.py`, etc. - command groups

The CLI is a thin wrapper around the SDK. It doesn't add functionality that the SDK doesn't have.

## Not in v0.1

- `platform serve` - running Tename as a server (v0.2+)
- `tename deploy` - cloud deployment commands (commercial future)
- TUI / interactive dashboard (too much scope)
- Shell completion (nice to have, not urgent)

These come later as demand emerges.
