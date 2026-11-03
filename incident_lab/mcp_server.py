"""MCP server exposing the read-only investigation tools of one incident workspace.

    python -m incident_lab.mcp_server --workspace build/workspaces/s01

Stdio transport: stdout belongs to the protocol, so nothing in here may print to it.
"""

from __future__ import annotations

import argparse
import functools
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as McpToolError
from mcp_types import ToolAnnotations

from incident_lab.tools import TOOL_NAMES, ToolError, WorkspaceTools

INSTRUCTIONS = (
    "Read-only tools for investigating a production incident in the orders-api service. "
    "Start with get_alert, then correlate the service log (search_logs) with the code and its history "
    "(list_files, read_file, git_log, git_show, git_blame). Nothing here can modify anything."
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


def _expose(fn):
    """Show our ToolError messages to the model.

    The SDK treats any exception other than its own ToolError as a crash and hides the text
    behind a generic "Error executing tool ...", which would leave the model unable to correct
    a bad request. tools.py stays free of the SDK, so the translation happens here.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ToolError as exc:
            raise McpToolError(str(exc)) from exc

    return wrapper


def build_server(workspace: Path) -> MCPServer:
    tools = WorkspaceTools(workspace)
    server = MCPServer("incident-workspace", instructions=INSTRUCTIONS)
    for name in TOOL_NAMES:
        server.add_tool(_expose(getattr(tools, name)), name=name, annotations=READ_ONLY)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Incident workspace MCP server (stdio)")
    parser.add_argument("--workspace", required=True, type=Path, help="a directory built by incident_lab.workspace")
    args = parser.parse_args()
    build_server(args.workspace).run("stdio")


if __name__ == "__main__":
    main()
