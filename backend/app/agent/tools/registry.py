"""带类型的工具注册、结果大小限制和安全错误处理。"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from backend.app.domain.common import AppError


@dataclass
class Tool:
    name: str
    description: str
    parameters: type[BaseModel]
    execute: Callable[..., Awaitable[object]]

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters.model_json_schema(),
            },
        }


class Registry:
    def __init__(self, tools: list[Tool]) -> None:
        self.tools = {tool.name: tool for tool in tools}
        if len(self.tools) != len(tools):
            raise ValueError("Duplicate tool registration")

    def schemas(self) -> list[dict]:
        return [tool.schema() for tool in self.tools.values()]

    async def invoke(self, name: str, arguments: dict, timeout: float) -> dict:
        try:
            if name not in self.tools:
                raise AppError("UNKNOWN_TOOL", "工具不存在")
            tool = self.tools[name]
            values = tool.parameters.model_validate(arguments).model_dump()
            async with asyncio.timeout(timeout):
                data = await tool.execute(**values)
            truncated = False

            def bound(value: object) -> object:
                nonlocal truncated
                if isinstance(value, str) and len(value) > 12000:
                    truncated = True
                    return value[:12000]
                if isinstance(value, list):
                    truncated = truncated or len(value) > 100
                    return [bound(item) for item in value[:100]]
                if isinstance(value, dict):
                    return {key: bound(item) for key, item in value.items()}
                return value

            data = bound(data)
            if len(json.dumps(data, ensure_ascii=False).encode()) > 100000:
                raise AppError("TOOL_RESULT_TOO_LARGE", "工具输出超过限制")
            return {"ok": True, "data": data, "truncated": truncated}
        except ValidationError:
            code, message = "INVALID_ARGUMENTS", "工具参数不符合 schema"
        except AppError as exc:
            code, message = exc.code, exc.message
        except (TimeoutError, OSError):
            code, message = "TOOL_TIMEOUT_OR_IO", "工具超时或资源不可用"
        except Exception:
            # 不可信的外部服务异常可能包含凭证或文章正文。
            code, message = "TOOL_FAILED", "外部工具执行失败"
        return {
            "ok": False,
            "data": None,
            "truncated": False,
            "error": {"code": code, "message": message, "retryable": code == "TOOL_TIMEOUT_OR_IO"},
        }
