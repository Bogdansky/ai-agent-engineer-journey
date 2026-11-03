"""The investigation agent: a hand-written tool-use loop on the Anthropic Messages API.

THIS FILE IS YOUR EXERCISE. The data classes and constants are given; the three functions marked
TODO are yours. Work in this order, one function at a time, and run the tests after each:

    uv run pytest tests/test_agent.py -q          # everything
    uv run pytest tests/test_agent.py -q -k tool_definitions   # one topic

The tests use a scripted fake client, so no API key and no network are needed. Read the test docstrings:
they are the specification. When all of them pass, the loop is ready for a real model and, later, for evals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from incident_lab.tools import TOOL_NAMES, ToolError, WorkspaceTools  # noqa: F401  (you will need these)

# --------------------------------------------------------------------------- given

CATEGORIES = (
    "connection_leak",
    "bad_config",
    "missing_timeout",
    "memory_leak",
    "n_plus_one",
    "contract_change",
    "expired_credentials",
    "thread_starvation",
    "retry_storm",
    "no_root_cause_found",
)

REPORT_FIELDS = ("category", "root_cause_file", "culprit_commit", "explanation", "evidence")

# Why the loop stopped.
STOP_SUBMITTED = "submitted"  # the model called submit_report with a valid report
STOP_MODEL_STOPPED = "model_stopped"  # the model ended its turn without submitting a report
STOP_MAX_STEPS = "max_steps"  # reached AgentConfig.max_steps API calls without a report
STOP_TOKEN_BUDGET = "token_budget"  # accumulated input+output tokens reached AgentConfig.max_total_tokens

# Version 1 of the prompt. The evals phase is where you will improve it; for now it only has to work.
SYSTEM_PROMPT = """You are an on-call engineer investigating a production incident in the orders-api service.
Use the tools to correlate the alert with the service log and the code history, find the root cause,
and then call submit_report exactly once. Base every claim on evidence you actually retrieved.
If you cannot find a root cause, say so with category no_root_cause_found instead of guessing."""


@dataclass(frozen=True)
class AgentConfig:
    model: str = "claude-sonnet-5-5"
    max_steps: int = 15  # a step is one API call
    max_total_tokens: int = 150_000  # input + output tokens summed over all calls
    max_tokens_per_call: int = 2048
    system_prompt: str = SYSTEM_PROMPT


@dataclass(frozen=True)
class Report:
    category: str
    root_cause_file: str
    culprit_commit: str
    explanation: str
    evidence: list[str]


@dataclass
class AgentResult:
    stop_reason: str  # one of the STOP_* constants
    report: Report | None
    steps: int  # API calls made
    input_tokens: int
    output_tokens: int
    messages: list[dict[str, Any]]  # the full transcript, last assistant message included (tracing needs it)


# ------------------------------------------------------------------------------ TODO 1


def tool_definitions() -> list[dict[str, Any]]:
    """Return the `tools=` argument for messages.create: the 7 workspace tools plus `submit_report`.

    TODO: build one dict per tool: {"name", "description", "input_schema"}.

    Hints
    - input_schema is JSON Schema: {"type": "object", "properties": {...}, "required": [...]}.
      Types matter: line numbers are "integer", `ignore_case` is "boolean", optional params are
      NOT in "required". The signatures and docstrings are in incident_lab/tools.py.
    - The description is the only thing the model knows about a tool when it decides to call it.
      Treat it as prompt text. You may reuse the docstrings or write better ones.
    - You can write the schemas by hand or derive them from inspect.signature; the MCP server
      (incident_lab/mcp_server.py) gets its schemas derived automatically, see what list_tools returns in
      tests/test_mcp_server.py. Decide, and note the trade-off in a comment: control vs. staying in sync.
    - submit_report takes the REPORT_FIELDS. `category` should be restricted to CATEGORIES (JSON Schema "enum"),
      `evidence` is an array of strings, and all five fields are required.
    """
    raise NotImplementedError("TODO 1: tool_definitions")


# ------------------------------------------------------------------------------ TODO 2


def execute_tool(tools: WorkspaceTools, name: str, args: dict[str, Any]) -> tuple[str, bool]:
    """Run one tool call requested by the model. Returns (text, is_error). Must never raise.

    TODO: dispatch `name` to the matching WorkspaceTools method with **args.

    Hints
    - `name` and `args` come from the model, i.e. from outside your trust boundary. Only dispatch names listed
      in TOOL_NAMES: `getattr(tools, name)` on an arbitrary string would let the model call private methods
      such as `_git`. `submit_report` is not a WorkspaceTools method either; the loop handles it.
    - A ToolError is a normal outcome ("that path escapes the repository"): return its message with
      is_error=True so the model can fix its request.
    - The model can also send wrong argument names or types, which raise TypeError when you call the method.
      That is the model's mistake too, so report it as an error result instead of crashing the run.
    """
    raise NotImplementedError("TODO 2: execute_tool")


# ------------------------------------------------------------------------------ TODO 3


def run_agent(client: Any, tools: WorkspaceTools, config: AgentConfig = AgentConfig()) -> AgentResult:
    """The agent loop. `client` is an anthropic.Anthropic (or the test fake): client.messages.create(...).

    TODO: implement the loop. Suggested shape:

      1. Start `messages` with one user message that contains the alert (tools.get_alert()) and the
         instruction to investigate and submit a report.
      2. Repeat: call client.messages.create(model=, max_tokens=, system=, tools=tool_definitions(),
         messages=messages). Add response.usage.input_tokens / output_tokens to your totals.
      3. Append the assistant turn to `messages` ({"role": "assistant", "content": response.content}).
      4. If the model asked for tools (stop_reason == "tool_use"), run each tool_use block and answer with
         ONE user message holding one tool_result block per tool_use:
         {"type": "tool_result", "tool_use_id": block.id, "content": text, "is_error": is_error}
      5. If a block calls submit_report with all REPORT_FIELDS, build a Report and stop with STOP_SUBMITTED.
         If the report is incomplete, send an error tool_result so the model can try again.
      6. Stop with the right STOP_* reason when the model ends its turn without a report, when the token
         budget is reached, or when max_steps calls have been made. Never make a call after a limit is hit.

    Hints and gotchas
    - Every tool_use block must get a tool_result with the same id, all in the same user message, or the
      API rejects the next call. The model may emit several tool_use blocks in one response.
    - Decide the order of the checks after each response and keep it consistent: a valid report wins
      over everything; then a response that is not asking for tools; then the budget; then max_steps.
    - Each call must receive the conversation so far, ending with the tool_results of the previous turn;
      the tests check exactly what every call received.
    - Return the final `messages` in the result: the transcript is what tracing and evals will read.
    - Stretch goals (after the tests pass): nudge the model once if it ends its turn without a report;
      handle stop_reason == "max_tokens" (a tool_use block may be cut off); prompt caching for the tools
      and system prompt; a small CLI (python -m incident_lab.agent --workspace ...) to try it on a real model.
    """
    raise NotImplementedError("TODO 3: run_agent")
