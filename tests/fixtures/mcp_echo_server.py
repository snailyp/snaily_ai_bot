"""A harmless local MCP server used only by transport integration tests."""

import sys

from mcp.server.fastmcp import FastMCP

mode = sys.argv[1] if len(sys.argv) > 1 else "stdio"
port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
server = FastMCP("snaily-local-test", host="127.0.0.1", port=port, log_level="ERROR")


@server.tool()
def echo_text(text: str) -> str:
    """Return the supplied test text without reading or writing anything."""
    return "echo: " + text


if __name__ == "__main__":
    server.run(transport=mode)
