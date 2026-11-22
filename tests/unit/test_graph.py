import json

import pytest

from backend.app.agent.graph.runner import AgentRunner
from backend.app.agent.tools.registry import Registry, Tool
from backend.app.domain.schemas import Contract


class EmptyInput(Contract):
    pass


def call(name: str, call_id: str = "one", args: str = "{}") -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}


class ScriptedModel:
    """An explicit offline test double, not evidence of live LLM reasoning."""

    def __init__(self, responses):
        self.responses = iter(responses)

    async def complete(self, messages, tools, timeout):
        response = next(self.responses)
        return response(messages) if callable(response) else response


FINAL = {
    "role": "assistant",
    "content": json.dumps(
        {
            "title": "No news",
            "overview": "Offline fixture",
            "no_news": True,
            "items": [],
        }
    ),
}


@pytest.mark.asyncio
async def test_observation_can_change_next_tool(settings):
    executed = []

    async def first():
        executed.append("first")
        return "read-more"

    async def second():
        executed.append("second")
        return "enough"

    def decide(messages):
        observation = json.loads(messages[-1]["content"])["data"]
        return (
            {"role": "assistant", "content": None, "tool_calls": [call("second", "two")]}
            if observation == "read-more"
            else FINAL
        )

    model = ScriptedModel(
        [
            {"role": "assistant", "content": None, "tool_calls": [call("first")]},
            decide,
            decide,
        ]
    )
    runner = AgentRunner(
        model,
        Registry(
            [
                Tool("first", "first probe", EmptyInput, first),
                Tool("second", "second probe", EmptyInput, second),
            ]
        ),
        settings,
        lambda _: None,
    )
    result = await runner.run({})
    assert executed == ["first", "second"]
    assert result["model_calls"] == 3 and result["tool_calls"] == 2
    assert result["result"]["no_news"]


@pytest.mark.asyncio
async def test_bulk_tools_cannot_bypass_budget(settings):
    executed = []
    audits = []

    async def probe():
        executed.append(1)

    settings.agent_max_tool_calls = 1
    model = ScriptedModel(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [call("probe", "a"), call("probe", "b")],
            }
        ]
    )
    result = await AgentRunner(
        model,
        Registry(
            [
                Tool("probe", "probe", EmptyInput, probe),
            ]
        ),
        settings,
        lambda _: None,
        audits.append,
    ).run({})
    assert result["error"] == "BUDGET_EXHAUSTED"
    assert not executed
    assert len(audits) == 2
    assert len([m for m in result["messages"] if m["role"] == "tool"]) == 2


@pytest.mark.asyncio
async def test_unknown_tool_and_invalid_json_receive_observations(settings):
    model = ScriptedModel(
        [
            {"role": "assistant", "content": None, "tool_calls": [call("missing")]},
            {"role": "assistant", "content": None, "tool_calls": [call("missing", "two", "{bad")]},
            FINAL,
        ]
    )
    result = await AgentRunner(model, Registry([]), settings, lambda _: None).run({})
    observed = [json.loads(m["content"]) for m in result["messages"] if m["role"] == "tool"]
    assert [m["error"]["code"] for m in observed] == ["UNKNOWN_TOOL", "INVALID_ARGUMENTS"]
    assert result["tool_calls"] == 2


@pytest.mark.asyncio
async def test_bad_brief_repairs_consume_original_model_budget(settings):
    settings.agent_max_model_calls = 2
    model = ScriptedModel([{"role": "assistant", "content": "not-json"}] * 3)
    result = await AgentRunner(model, Registry([]), settings, lambda _: None).run({})
    assert result["error"] == "BUDGET_EXHAUSTED"
    assert result["model_calls"] == 2
