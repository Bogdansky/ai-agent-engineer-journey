"""Specification of incident_lab/agent.py, run against a scripted fake client (no API key, no network).

Work through the sections in order: tool_definitions (TODO 1), execute_tool (TODO 2), run_agent (TODO 3).
"""

import copy
import json
from types import SimpleNamespace as NS

import pytest

from incident_lab.agent import (
    CATEGORIES,
    REPORT_FIELDS,
    STOP_MAX_STEPS,
    STOP_MODEL_STOPPED,
    STOP_SUBMITTED,
    STOP_TOKEN_BUDGET,
    AgentConfig,
    Report,
    execute_tool,
    run_agent,
    tool_definitions,
)
from incident_lab.tools import TOOL_NAMES, WorkspaceTools

REPORT = {
    "category": "connection_leak",
    "root_cause_file": "app/orders.py",
    "culprit_commit": "c926211",
    "explanation": "get_order no longer releases its connection when the order does not exist.",
    "evidence": ["L1777: PoolExhausted ... in_use=10", "git show c926211 removed the try/finally"],
}

# ------------------------------------------------------------------------------ test doubles


def text(t):
    return NS(type="text", text=t)


def tool_use(block_id, name, **tool_input):
    return NS(type="tool_use", id=block_id, name=name, input=tool_input)


def reply(*blocks, stop="tool_use", tokens_in=100, tokens_out=50):
    return NS(content=list(blocks), stop_reason=stop, usage=NS(input_tokens=tokens_in, output_tokens=tokens_out))


class FakeClient:
    """Plays back scripted replies and records exactly what each call received."""

    def __init__(self, replies, *, repeat_last=False):
        self._replies = list(replies)
        self._repeat_last = repeat_last
        self.calls = []
        self.messages = NS(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))  # snapshot: later mutation of `messages` must not leak in
        index = len(self.calls) - 1
        if index < len(self._replies):
            return self._replies[index]
        if self._repeat_last:
            return self._replies[-1]
        raise AssertionError("the agent called the API again after the scripted conversation had ended")


def submit(block_id="s", **overrides):
    return tool_use(block_id, "submit_report", **{**REPORT, **overrides})


def tool_results(call):
    last = call["messages"][-1]
    assert last["role"] == "user" and isinstance(last["content"], list), "tool results go in one user message"
    return last["content"]


def roles(call):
    return [m["role"] for m in call["messages"]]


@pytest.fixture
def tools(built):
    return WorkspaceTools(built["s01"].root)


# ------------------------------------------------------------------- TODO 1: tool_definitions


def test_tool_definitions_list_every_tool_exactly_once():
    names = [t["name"] for t in tool_definitions()]
    assert sorted(names) == sorted([*TOOL_NAMES, "submit_report"])


def test_tool_definitions_are_serialisable_and_documented():
    definitions = tool_definitions()
    json.dumps(definitions)
    for tool in definitions:
        assert len(tool["description"]) >= 20, f"{tool['name']}: the model decides from this text"
        assert tool["input_schema"]["type"] == "object"
        assert isinstance(tool["input_schema"]["properties"], dict)


def test_parameter_types_are_right():
    props = {t["name"]: t["input_schema"]["properties"] for t in tool_definitions()}
    assert props["read_file"]["path"]["type"] == "string"
    assert props["read_file"]["start_line"]["type"] == "integer"
    assert props["search_logs"]["limit"]["type"] == "integer"
    assert props["search_logs"]["ignore_case"]["type"] == "boolean"
    assert props["git_show"]["commit"]["type"] == "string"


def test_only_mandatory_parameters_are_required():
    required = {t["name"]: t["input_schema"].get("required", []) for t in tool_definitions()}
    assert required["read_file"] == ["path"]
    assert required["search_logs"] == ["pattern"]
    assert required["git_show"] == ["commit"]
    assert required["git_blame"] == ["path"]
    for no_args in ("get_alert", "list_files", "git_log"):
        assert required[no_args] == []


def test_submit_report_schema_matches_the_report():
    schema = next(t for t in tool_definitions() if t["name"] == "submit_report")["input_schema"]
    assert set(schema["required"]) == set(REPORT_FIELDS)
    assert set(schema["properties"]) == set(REPORT_FIELDS)
    assert schema["properties"]["category"]["enum"] == list(CATEGORIES)
    assert schema["properties"]["evidence"]["type"] == "array"
    assert schema["properties"]["evidence"]["items"]["type"] == "string"


# --------------------------------------------------------------------- TODO 2: execute_tool


def test_execute_tool_runs_a_tool(tools):
    output, is_error = execute_tool(tools, "search_logs", {"pattern": "startup complete"})
    assert not is_error
    assert output.startswith("matched 2 of ")


def test_execute_tool_returns_tool_errors_instead_of_raising(tools):
    output, is_error = execute_tool(tools, "read_file", {"path": "../../etc/passwd"})
    assert is_error
    assert "escapes the repository" in output


@pytest.mark.parametrize(
    "name, args",
    [
        ("does_not_exist", {}),
        ("read_file", {"nope": 1}),  # wrong argument name
        ("read_file", {}),  # missing required argument
        ("_git", {"args": ["status"]}),  # private method: must not be dispatchable
        ("__class__", {}),
        ("submit_report", dict(REPORT)),  # not a workspace tool; the loop owns it
    ],
)
def test_execute_tool_survives_bad_calls(tools, name, args):
    output, is_error = execute_tool(tools, name, args)
    assert is_error is True
    assert output.strip()


# ------------------------------------------------------------------------ TODO 3: run_agent


def test_first_call_carries_config_tools_and_alert(tools):
    config = AgentConfig(model="test-model", max_tokens_per_call=777, system_prompt="SYS")
    client = FakeClient([reply(submit())])
    run_agent(client, tools, config)

    call = client.calls[0]
    assert call["model"] == "test-model"
    assert call["max_tokens"] == 777
    assert call["system"] == "SYS"
    assert call["tools"] == tool_definitions()
    assert call["messages"][0]["role"] == "user"
    assert "[FIRING]" in str(call["messages"][0]["content"]), "the alert must be in the first message"


def test_tool_results_are_fed_back_and_the_report_is_returned(tools):
    client = FakeClient([reply(tool_use("t1", "get_alert")), reply(submit())])
    result = run_agent(client, tools)

    assert result.stop_reason == STOP_SUBMITTED
    assert result.report == Report(**REPORT)
    assert result.steps == 2
    assert (result.input_tokens, result.output_tokens) == (200, 100)

    second = client.calls[1]
    assert roles(second) == ["user", "assistant", "user"]
    (block,) = tool_results(second)
    assert block["type"] == "tool_result" and block["tool_use_id"] == "t1"
    assert "[FIRING]" in str(block["content"])
    assert not block.get("is_error")
    assert [m["role"] for m in result.messages] == ["user", "assistant", "user", "assistant"]


def test_parallel_tool_calls_are_answered_in_one_message(tools):
    client = FakeClient([reply(tool_use("a", "get_alert"), tool_use("b", "list_files")), reply(submit())])
    run_agent(client, tools)

    second = client.calls[1]
    assert roles(second) == ["user", "assistant", "user"]
    blocks = tool_results(second)
    assert [b["tool_use_id"] for b in blocks] == ["a", "b"]
    assert all(str(b["content"]).strip() for b in blocks)


def test_tool_errors_go_back_to_the_model_and_the_run_continues(tools):
    client = FakeClient([reply(tool_use("t1", "read_file", path="../../etc/passwd")), reply(submit())])
    result = run_agent(client, tools)

    (block,) = tool_results(client.calls[1])
    assert block["is_error"] is True
    assert "escapes the repository" in str(block["content"])
    assert result.stop_reason == STOP_SUBMITTED


@pytest.mark.parametrize(
    "broken",
    [{"explanation": None}, {"evidence": None}, {"category": "banana"}],
    ids=["missing-field", "missing-evidence", "unknown-category"],
)
def test_a_bad_report_is_rejected_and_the_model_may_retry(tools, broken):
    bad = {k: v for k, v in {**REPORT, **broken}.items() if v is not None}
    client = FakeClient([reply(tool_use("s1", "submit_report", **bad)), reply(submit("s2"))])
    result = run_agent(client, tools)

    (block,) = tool_results(client.calls[1])
    assert block["tool_use_id"] == "s1" and block["is_error"] is True
    assert result.stop_reason == STOP_SUBMITTED
    assert result.steps == 2


def test_model_ending_its_turn_without_a_report(tools):
    client = FakeClient([reply(text("I think it is the database."), stop="end_turn")])
    result = run_agent(client, tools)
    assert result.stop_reason == STOP_MODEL_STOPPED
    assert result.report is None
    assert result.steps == 1


def test_max_steps_stops_a_model_that_never_finishes(tools):
    client = FakeClient([reply(tool_use("t", "get_alert"))], repeat_last=True)
    result = run_agent(client, tools, AgentConfig(max_steps=3))
    assert result.stop_reason == STOP_MAX_STEPS
    assert result.steps == 3
    assert len(client.calls) == 3, "no API call after the limit is reached"


def test_token_budget_stops_the_run_without_another_call(tools):
    client = FakeClient([reply(tool_use("t", "get_alert"), tokens_in=1000, tokens_out=1000)], repeat_last=True)
    result = run_agent(client, tools, AgentConfig(max_steps=10, max_total_tokens=3000))
    assert result.stop_reason == STOP_TOKEN_BUDGET
    assert len(client.calls) == 2, "budget is checked after each response (2000 < 3000 <= 4000)"
    assert (result.input_tokens, result.output_tokens) == (2000, 2000)


def test_a_valid_report_wins_over_the_limits(tools):
    heavy = reply(submit(), tokens_in=10**6, tokens_out=10**6)
    result = run_agent(FakeClient([heavy]), tools, AgentConfig(max_steps=1, max_total_tokens=1000))
    assert result.stop_reason == STOP_SUBMITTED
    assert result.report == Report(**REPORT)
