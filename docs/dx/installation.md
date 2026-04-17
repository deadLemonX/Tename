# Installation

## Prerequisites

- Python 3.12 or later
- Docker (for sandboxes)
- Postgres 16+ or SQLite (Postgres recommended for production)
- An API key from at least one model provider (Anthropic, OpenAI, or OpenAI-compatible endpoint)

## Install Tename

```bash
pip install tename-sdk  # placeholder name
```

Or with uv (faster):

```bash
uv pip install tename-sdk
```

## Quick setup

### 1. Start the local database

The fastest way is using the built-in docker-compose:

```bash
tename dev
```

This starts Postgres on localhost:5432 with a default dev database.

Alternatively, point Tename at an existing Postgres:

```bash
export Tename_DATABASE_URL="postgresql://user:pass@host:5432/dbname"
tename migrate
```

### 2. Set API key

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

### 3. Verify the installation

```bash
tename doctor
```

You should see all green checks for: Python version, Docker available, database reachable, API key configured.

### 4. Run the hello-world

```python
from tename_sdk import Tename

client = Tename()

agent = client.agents.create(
    name="assistant",
    model="claude-opus-4-6",
    system_prompt="You are a helpful assistant."
)

session = client.sessions.create(agent_id=agent.id)

for event in session.send("What's 2 + 2?"):
    if event.type == "assistant_message":
        print(event.payload["content"], end="", flush=True)
```

If you see a response streamed to your terminal, you're set.

## Configuration

Tename can be configured via:

1. Environment variables (easiest)
2. Config file at `~/.tename/config.yaml`
3. Arguments to `Tename(...)` in code

### Environment variables

```bash
# Database connection
Tename_DATABASE_URL=postgresql://...

# Model providers (at least one required)
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...

# Vault (for proxy tools that need credentials)
Tename_VAULT_PASSPHRASE=your-strong-passphrase

# Profiles directory (optional, default uses built-in)
Tename_PROFILES_DIR=/path/to/custom/profiles

# Data directory (optional, default is ~/.tename)
Tename_DATA_DIR=/path/to/platform/data
```

### Config file

```yaml
# ~/.tename/config.yaml
database:
  url: postgresql://user:pass@localhost:5432/tename

providers:
  anthropic:
    api_key_env: ANTHROPIC_API_KEY
  openai:
    api_key_env: OPENAI_API_KEY

vault:
  path: ~/.tename/vault.json.enc
  passphrase_env: Tename_VAULT_PASSPHRASE

profiles:
  dir: /path/to/custom/profiles
```

## Troubleshooting

### "Docker is not running"

Tename needs Docker for sandboxes. Start Docker Desktop (or the Docker daemon) and try again.

### "Could not connect to database"

Make sure Postgres is running and `Tename_DATABASE_URL` is correct. If using the built-in dev database, run `tename dev` first.

### "ANTHROPIC_API_KEY not set"

Export the environment variable. Or set it in your config file. Or pass it to `Tename(anthropic_api_key="...")` in code.

### "Profile not found: claude-opus-4-6"

Tename ships with this profile built in. If you see this error, there's likely a bug in how Tename is finding its profiles directory. Check `tename profiles list` to see what it sees, and report it as an issue.

### "ImportError: No module named tename_sdk"

Make sure you installed it: `pip install tename-sdk`. Check your Python environment is the one you installed into.

### Something else

Open an issue on GitHub with the error message and what you were doing when it happened. Include the output of `tename doctor` and `tename info`.

## Upgrading

```bash
pip install --upgrade tename-sdk
tename migrate  # apply any new schema migrations
```

Breaking changes are documented in CHANGELOG.md. Tename uses semantic versioning - major version bumps indicate breaking changes, minor versions are backward-compatible additions, patches are bug fixes.

## Uninstalling

```bash
pip uninstall tename-sdk
```

This removes the code. Data in your database and the vault file at `~/.tename/` is NOT automatically deleted. Remove those manually if desired:

```bash
# Drop the database
dropdb tename  # or however your Postgres is set up

# Remove vault and config
rm -rf ~/.tename/
```
