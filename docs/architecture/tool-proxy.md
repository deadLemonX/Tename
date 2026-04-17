# Tool Proxy and Vault

## Purpose

Structurally prevents credentials from ever entering the sandbox. Tool calls that require credentials go through a proxy that injects them at call time.

This is the architectural answer to prompt injection. Even if an attacker convinces the model to do anything in the sandbox, there are no credentials to steal.

## Design

Two components that work together:

**Vault:** Stores credentials securely.
**Tool Proxy:** Makes external tool calls using credentials from the vault, returning results to the harness without exposing the credentials.

In v0.1, both are Python modules in the same process. In commercial deployments, they could be separate services with stronger isolation.

## Vault

### v0.1 implementation: Encrypted file on disk

Credentials are stored in an encrypted JSON file (`~/.tename/vault.json.enc` by default). Encryption uses the Python `cryptography` library with a key derived from a user-supplied passphrase or environment variable.

```python
class Vault:
    def store(credential_name: str, value: str) -> None:
        """Encrypt and store a credential."""
    
    def retrieve(credential_name: str) -> str:
        """Decrypt and return a credential. Throws if not found."""
    
    def revoke(credential_name: str) -> None:
        """Delete a credential."""
    
    def list() -> List[str]:
        """Return credential names (not values)."""
```

**Key derivation:**
- User provides a passphrase via env var `Tename_VAULT_PASSPHRASE` or interactive prompt
- We use PBKDF2 to derive a key
- Never store the passphrase on disk

**File format:**
```json
{
  "version": 1,
  "salt": "base64_salt",
  "credentials": {
    "web_search_api_key": "encrypted_value",
    "mcp_server_notion_token": "encrypted_value"
  }
}
```

### What v0.1 does NOT include

- Cloud vault integrations (AWS Secrets Manager, HashiCorp Vault) — added in v0.2 if users ask
- Credential rotation
- Audit log of credential access
- Per-user or per-tenant isolation (single-user mode)

### CLI for managing credentials

```bash
# Add a credential
tename vault set web_search_api_key

# List credentials (names only)
tename vault list

# Remove a credential
tename vault remove web_search_api_key
```

## Tool Proxy

### What it does

When the harness wants to execute an external tool (web search, MCP server call, custom HTTP tool), it calls the Tool Proxy instead of making the call directly. The proxy:

1. Looks up the credential for that tool
2. Makes the outbound call with real credentials
3. Returns the result to the harness without exposing the credential
4. Logs the call (without credential values) for debugging

### Interface

```python
class ToolProxy:
    async def execute(
        tool_name: str,
        input: dict,
        session_id: UUID
    ) -> ToolResult:
        """Execute an external tool, handling credentials."""
```

### Tool definitions

Tools that need proxying are defined with credential requirements:

```python
@proxy_tool(
    name="web_search",
    credential_names=["web_search_api_key"],
    endpoint="https://api.search-provider.com/v1/search"
)
async def web_search_tool(input: dict, credentials: dict) -> dict:
    response = await httpx.post(
        "https://api.search-provider.com/v1/search",
        headers={"Authorization": f"Bearer {credentials['web_search_api_key']}"},
        json={"query": input["query"]}
    )
    return response.json()
```

The harness never sees `credentials`. It passes `input` to the proxy and receives a result.

### MCP server support

MCP (Model Context Protocol) servers are first-class supported tools. Users register an MCP server and its credentials once; the proxy handles authentication transparently on every call.

```yaml
# MCP server registration
mcp_servers:
  - name: notion
    url: https://mcp.notion.com/
    credential_name: mcp_server_notion_token
    auth_method: bearer_token
  
  - name: github
    url: https://mcp.github.com/
    credential_name: github_oauth_token
    auth_method: oauth_bearer
```

### Git integration pattern

For sandbox tools that need Git access (e.g., agents that need to read a repo):

1. When provisioning a sandbox, if the recipe specifies a Git repo:
2. Tool proxy fetches the Git access token from vault
3. Proxy embeds token in the local Git remote URL INSIDE the sandbox (e.g., `https://x-access-token:<token>@github.com/user/repo.git`)
4. Sandbox can now `git pull` and `git push` without the agent handling the token
5. The token is baked into the remote config, never exposed via environment variables
6. When the sandbox is destroyed, the token is destroyed with it

This lets agents work with real repositories without giving the model access to the underlying access token.

### Circuit breakers (v0.2, not v0.1)

In v0.1, if a tool fails, it fails. The model sees the error and decides what to do.

In v0.2, we may add circuit breakers: after N consecutive failures of a tool, the proxy short-circuits and returns an error immediately for some cooldown period. This protects against cascading failures when external services degrade.

Not in v0.1 because it adds complexity and individual users aren't likely to hit cascading failures.

## Security properties we get

**Prompt injection resistance:** A malicious prompt convinces the model to exfiltrate secrets. But the model only has access to tools, not credentials. Tool responses come through the proxy without credentials. There's nothing to exfiltrate.

**Credential audit:** Every credential access is logged (without the value). If something bad happens, we can see which tools accessed which credentials when.

**Credential rotation:** Since credentials live in the vault (not in code or environment variables), rotating them doesn't require code changes. Update the vault, and subsequent tool calls use the new value.

**Separation of concerns:** The model reasons about what tool to call. The harness routes the call. The proxy handles authentication. The sandbox executes. Each layer has one job and limited blast radius if compromised.

## What users should NOT do

- Put credentials in environment variables that the sandbox can read
- Put credentials in code (even in "private" repos)
- Use the vault passphrase in multiple places
- Share credentials between tools when they could be scoped to one

The whole point is architectural separation. Don't defeat it with convenience.

## Testing requirements

- Unit tests for vault (encrypt, decrypt, rotate passphrase)
- Integration test: set credential, call tool, verify credential reached the external service
- Security test: credential never appears in logs at any level
- Security test: credential never appears in event payloads stored in session log
- Error test: credential missing, verify error message is clear but doesn't leak partial info
