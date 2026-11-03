"""End-to-end: talk to the server over stdio with a real MCP client."""

import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from incident_lab.tools import TOOL_NAMES

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def run_session(workspace, work):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "incident_lab.mcp_server", "--workspace", str(workspace)],
        cwd=PROJECT_ROOT,
    )

    async def main():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await work(session)

    return asyncio.run(asyncio.wait_for(main(), timeout=60))


def text_of(result) -> str:
    return "\n".join(block.text for block in result.content if getattr(block, "text", None))


def test_server_lists_only_read_only_tools_with_schemas(built):
    async def work(session):
        return (await session.list_tools()).tools

    tools = run_session(built["s01"].root, work)
    assert {t.name for t in tools} == set(TOOL_NAMES)
    for tool in tools:
        assert tool.annotations is not None and tool.annotations.read_only_hint is True, tool.name
        assert tool.description, f"{tool.name} needs a description the model can read"
        assert tool.input_schema["type"] == "object"
    by_name = {t.name: t for t in tools}
    assert by_name["read_file"].input_schema["required"] == ["path"]
    assert set(by_name["search_logs"].input_schema["properties"]) >= {"pattern", "start", "end", "level", "limit"}


def test_investigation_flow_over_mcp(built):
    ws = built["s01"]

    async def work(session):
        alert = await session.call_tool("get_alert", {})
        errors = await session.call_tool("search_logs", {"pattern": "PoolExhausted", "level": "ERROR", "limit": 1})
        history = await session.call_tool("git_log", {"path": "app/orders.py"})
        diff = await session.call_tool("git_show", {"commit": ws.culprit_commit[:7], "path": "app/orders.py"})
        return alert, errors, history, diff

    alert, errors, history, diff = run_session(ws.root, work)
    assert "[FIRING]" in text_of(alert)
    assert "PoolExhausted" in text_of(errors)
    assert text_of(history).splitlines()[0].startswith(ws.culprit_commit[:7])
    assert "+    pool.release(conn)" in text_of(diff)


def test_tool_errors_are_reported_as_errors_not_crashes(built):
    async def work(session):
        escape = await session.call_tool("read_file", {"path": "../../etc/passwd"})
        still_alive = await session.call_tool("get_alert", {})
        return escape, still_alive

    escape, still_alive = run_session(built["s01"].root, work)
    assert escape.is_error is True
    assert "escapes the repository" in text_of(escape)
    assert still_alive.is_error in (False, None)
