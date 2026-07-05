# Lead Architect: PipeForge
# Shared Utility: MCP Tool Client (v4)
# Lets Collector and Worker call any MCP-compatible tool server.
# MCP is the 2026 standard for connecting agents to external tools.
#
# Supported transports:
#   - HTTP/SSE  (most hosted MCP servers)
#   - stdio     (local MCP servers)
#
# How to add a tool server:
#   Set MCP_SERVERS in your .env as a JSON array:
#   MCP_SERVERS=[{"name":"brave-search","url":"http://brave-mcp:3001/sse"},
#                {"name":"filesystem","url":"http://fs-mcp:3002/sse"}]

import os, json, asyncio, httpx
from typing import Any
from shared.telemetry import span

# -- Parse configured MCP servers from env -------------------------------
def _load_servers() -> list[dict]:
    raw = os.getenv("MCP_SERVERS", "[]")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print("[MCP] Warning: MCP_SERVERS env var is not valid JSON")
        return []

MCP_SERVERS: list[dict] = _load_servers()


# -- SSE / HTTP MCP client ------------------------------------------------
class MCPClient:
    """
    Lightweight MCP client -- calls tool servers over HTTP/SSE.
    Conforms to MCP spec: sends JSON-RPC 2.0 requests, parses tool results.
    """

    def __init__(self, server_url: str, server_name: str = "mcp"):
        self.url  = server_url.rstrip("/")
        self.name = server_name

    async def list_tools(self) -> list[dict]:
        """Fetch available tools from an MCP server."""
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{self.url}/tools/list",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("result", {}).get("tools", [])

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """
        Call a specific tool on the MCP server.
        Returns the text content of the tool result.
        """
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        }
        with span("mcp.tool_call", server=self.name, tool=tool_name):
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{self.url}/tools/call", json=payload)
                resp.raise_for_status()
                data = resp.json()

            result = data.get("result", {})
            content = result.get("content", [])

            # MCP returns a list of content blocks; extract text
            texts = [
                block.get("text", "")
                for block in content
                if block.get("type") == "text"
            ]
            return "\n".join(texts) if texts else json.dumps(result)


# -- Tool registry: discovers tools across all configured servers ----------
class MCPToolRegistry:
    """
    Aggregates tools from all configured MCP servers.
    Agents can call discover() then use() without knowing which server each tool lives on.
    """

    def __init__(self):
        self._registry: dict[str, tuple[MCPClient, dict]] = {}  # tool_name -> (client, schema)

    async def discover(self):
        """Connect to all configured MCP servers and index their tools."""
        self._registry.clear()
        for server_cfg in MCP_SERVERS:
            name = server_cfg.get("name", "unknown")
            url  = server_cfg.get("url", "")
            if not url:
                continue
            client = MCPClient(url, name)
            try:
                tools = await client.list_tools()
                for tool in tools:
                    tool_name = tool.get("name", "")
                    if tool_name:
                        self._registry[tool_name] = (client, tool)
                print(f"[MCP] Registered {len(tools)} tools from '{name}': "
                      f"{[t['name'] for t in tools]}")
            except Exception as e:
                print(f"[MCP] Could not connect to '{name}' at {url}: {e}")

    @property
    def available_tools(self) -> list[str]:
        return list(self._registry.keys())

    def tool_schemas_for_llm(self) -> list[dict]:
        """
        Return OpenAI-compatible tool schemas for all discovered tools.
        Pass this to the LLM so it can decide which tools to call.
        """
        schemas = []
        for tool_name, (_, tool_def) in self._registry.items():
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": tool_def.get("description", ""),
                    "parameters": tool_def.get("inputSchema", {"type": "object", "properties": {}})
                }
            })
        return schemas

    async def use(self, tool_name: str, arguments: dict) -> str:
        """Call a tool by name. Raises KeyError if not discovered."""
        if tool_name not in self._registry:
            raise KeyError(f"Tool '{tool_name}' not found. Available: {self.available_tools}")
        client, _ = self._registry[tool_name]
        return await client.call_tool(tool_name, arguments)


# -- Singleton registry (shared across the process) ------------------------
registry = MCPToolRegistry()


# -- Sync wrappers for use in non-async nodes ------------------------------
def discover_tools() -> list[str]:
    """Sync wrapper -- discovers MCP tools at node startup."""
    asyncio.run(registry.discover())
    return registry.available_tools

def call_tool_sync(tool_name: str, arguments: dict) -> str:
    """Sync wrapper -- calls an MCP tool from a synchronous context."""
    return asyncio.run(registry.use(tool_name, arguments))


# -- LLM tool-call loop helper ---------------------------------------------
async def run_tool_calls(tool_calls: list, registry: MCPToolRegistry) -> list[dict]:
    """
    Given a list of tool_calls from an OpenAI response,
    execute each one against the MCP registry and return results.
    """
    results = []
    for tc in tool_calls:
        tool_name = tc.function.name
        try:
            args   = json.loads(tc.function.arguments)
            output = await registry.use(tool_name, args)
        except Exception as e:
            output = f"Tool error: {e}"
        results.append({
            "tool_call_id": tc.id,
            "role":         "tool",
            "name":         tool_name,
            "content":      output,
        })
    return results