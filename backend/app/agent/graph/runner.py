"""A real conditional LangGraph loop; tools are selected by model responses."""

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypedDict, cast

from langgraph.graph import END, START, StateGraph

from backend.app.agent.tools.registry import Registry
from backend.app.conf.settings import Settings
from backend.app.domain.common import AppError
from backend.app.domain.schemas import BriefDraft
from backend.app.infrastructure.llm import ModelClient


class AgentState(TypedDict, total=False):
    messages: list[dict]
    model_calls: int
    tool_calls: int
    repairs: int
    response: dict
    result: dict
    error: str
    usage: dict


class AgentRunner:
    def __init__(
        self,
        model: ModelClient,
        registry: Registry,
        settings: Settings,
        validate_sources: Callable[[BriefDraft], None],
        audit: Callable[[dict], None] | None = None,
    ) -> None:
        self.model, self.registry, self.settings = model, registry, settings
        self.validate_sources = validate_sources
        self.audit = audit or (lambda _: None)

    async def run(self, context: dict) -> AgentState:
        settings = self.settings
        deadline = time.monotonic() + settings.agent_max_seconds
        progress: AgentState = {"model_calls": 0, "tool_calls": 0}
        system = (Path(__file__).parents[1] / "prompts/editor.txt").read_text(encoding="utf-8")
        system = system.replace(
            "{schema}", json.dumps(BriefDraft.model_json_schema(), ensure_ascii=False)
        )

        def guard(state: AgentState) -> dict:
            if (
                time.monotonic() >= deadline
                or state["model_calls"] >= settings.agent_max_model_calls
            ):
                return {"error": "BUDGET_EXHAUSTED"}
            return {}

        async def agent(state: AgentState) -> dict:
            count = state["model_calls"] + 1
            progress["model_calls"] = count
            try:
                response = await self.model.complete(
                    state["messages"],
                    self.registry.schemas(),
                    max(0.01, deadline - time.monotonic()),
                )
                raw_usage = response.pop("_usage", {})
                metrics = (
                    {
                        key: value
                        for key, value in raw_usage.items()
                        if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
                        and type(value) is int
                        and value >= 0
                    }
                    if isinstance(raw_usage, dict)
                    else {}
                )
                calls = [*state.get("usage", {}).get("calls", []), {"attempt": count, **metrics}]
                totals = {
                    key: sum(call.get(key, 0) for call in calls)
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                    if any(key in call for call in calls)
                }
                usage = {
                    "calls": calls,
                    "reported_totals": totals,
                    "all_calls_reported": len(calls) == count
                    and all("total_tokens" in call for call in calls),
                }
                progress["usage"] = usage
                return {
                    "model_calls": count,
                    "response": response,
                    "usage": usage,
                    "messages": [*state["messages"], response],
                }
            except AppError as exc:
                retryable = exc.code == "MODEL_TEMPORARY" and count <= 2
                return {
                    "model_calls": count,
                    "response": {},
                    "error": "" if retryable else exc.code,
                }
            except Exception:
                return {"model_calls": count, "error": "MODEL_PROTOCOL_ERROR", "response": {}}

        async def tools(state: AgentState) -> dict:
            calls = state["response"].get("tool_calls", [])
            messages = list(state["messages"])
            remaining = settings.agent_max_tool_calls - state["tool_calls"]
            count = state["tool_calls"]
            exceeded = len(calls) > remaining
            result: dict
            for call in calls:
                count_before = count
                if not exceeded:
                    count += 1
                    progress["tool_calls"] = count
                started = time.monotonic()
                name = str(call.get("function", {}).get("name", ""))
                try:
                    args = json.loads(call.get("function", {}).get("arguments", "{}"))
                    if not isinstance(args, dict):
                        raise ValueError("Arguments must be an object")
                except (ValueError, TypeError):
                    args = {}
                    result = {"ok": False, "error": {"code": "INVALID_ARGUMENTS"}}
                else:
                    if exceeded:
                        result = {"ok": False, "error": {"code": "BUDGET_EXHAUSTED"}}
                    else:
                        result = await self.registry.invoke(
                            name,
                            args,
                            min(
                                settings.tool_timeout_seconds,
                                max(0.01, deadline - time.monotonic()),
                            ),
                        )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
                self.audit(
                    {
                        "sequence": count_before + 1,
                        "call_id": str(call.get("id", "")),
                        "name": name,
                        "arguments": {
                            key: ("<text omitted>" if key == "content" else value)
                            for key, value in args.items()
                        },
                        "ok": bool(result.get("ok")),
                        "error": result.get("error", {}).get("code"),
                        "duration_ms": int((time.monotonic() - started) * 1000),
                        "summary": "ok" if result.get("ok") else "error",
                    }
                )
            return {
                "messages": messages,
                "tool_calls": count,
                "error": "BUDGET_EXHAUSTED" if exceeded else "",
            }

        def validate(state: AgentState) -> dict:
            try:
                draft = BriefDraft.model_validate_json(state["response"].get("content") or "")
                self.validate_sources(draft)
                return {"result": draft.model_dump()}
            except (ValueError, TypeError):
                repairs = state["repairs"] + 1
                if repairs > 2:
                    return {"error": "INVALID_BRIEF"}
                return {
                    "repairs": repairs,
                    "messages": [
                        *state["messages"],
                        {
                            "role": "user",
                            "content": "简报结构或来源校验失败。核对schema、条数和本次来源后修正。",
                        },
                    ],
                }

        graph = StateGraph(AgentState)
        graph.add_node("budget_guard", guard)
        graph.add_node("agent", agent)
        graph.add_node("tools", tools)
        graph.add_node("validate", validate)
        graph.add_edge(START, "budget_guard")
        graph.add_conditional_edges("budget_guard", lambda s: END if s.get("error") else "agent")
        graph.add_conditional_edges(
            "agent",
            lambda s: (
                END
                if s.get("error")
                else "tools"
                if s["response"].get("tool_calls")
                else "validate"
                if s["response"]
                else "budget_guard"
            ),
        )
        graph.add_conditional_edges("tools", lambda s: END if s.get("error") else "budget_guard")
        graph.add_conditional_edges(
            "validate", lambda s: END if s.get("error") or s.get("result") else "budget_guard"
        )
        initial: AgentState = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            "model_calls": 0,
            "tool_calls": 0,
            "repairs": 0,
            "error": "",
        }
        try:
            async with asyncio.timeout(settings.agent_max_seconds):
                return cast(
                    AgentState, await graph.compile().ainvoke(initial, {"recursion_limit": 500})
                )
        except TimeoutError:
            return {**initial, **progress, "error": "BUDGET_EXHAUSTED"}
