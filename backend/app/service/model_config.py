"""按用户管理模型配置；密钥加密保存，不进入接口输出、任务快照或提示词。"""

import json

from cryptography.fernet import Fernet, InvalidToken
from pydantic import Field, SecretStr, field_validator
from sqlalchemy import select, update

from backend.app.conf.settings import Settings
from backend.app.domain.common import AppError, stamp
from backend.app.domain.schemas import Contract
from backend.app.infrastructure.database import Database
from backend.app.infrastructure.llm import ChatCompletionsClient
from backend.app.infrastructure.model_endpoint import validate_endpoint
from backend.app.models.schema import (
    ChatSession,
    ChatTurn,
    Run,
    Subscription,
    User,
    UserModelConfig,
)


class ModelConfigInput(Contract):
    version: int = Field(ge=0)
    base_url: str = Field(min_length=1, max_length=500)
    model_name: str = Field(min_length=1, max_length=120)
    api_key: SecretStr = Field(default=SecretStr(""), max_length=4096)

    @field_validator("model_name")
    @classmethod
    def model_name_valid(cls, value: str) -> str:
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("模型名称无效")
        return value.strip()


def cipher(settings: Settings) -> Fernet:
    try:
        return Fernet(settings.model_encryption_key.get_secret_value().encode())
    except (ValueError, TypeError) as exc:
        raise AppError("MODEL_STORAGE_NOT_READY", "管理员尚未配置模型密钥加密服务", 503) from exc


def view(database: Database, settings: Settings, user_id: str) -> dict:
    try:
        cipher(settings)
        ready = True
    except AppError:
        ready = False
    with database.session() as session:
        row = session.get(UserModelConfig, user_id)
        has_key = bool(row and row.encrypted_api_key)
        return {
            "base_url": row.base_url if row else "",
            "model_name": row.model_name if row else "",
            "has_api_key": has_key,
            "configured": bool(ready and row and has_key and row.base_url and row.model_name),
            "storage_ready": ready,
            "version": row.version if row else 0,
            "tested_at": row.tested_at if row else None,
        }


def require_config(database: Database, settings: Settings, user_id: str) -> None:
    if not view(database, settings, user_id)["configured"]:
        raise AppError(
            "MODEL_NOT_CONFIGURED", "请先在模型设置中保存你的模型地址、名称和 API Key", 503
        )


def resolve(database: Database, settings: Settings, user_id: str) -> Settings:
    require_config(database, settings, user_id)
    with database.session() as session:
        row = session.get(UserModelConfig, user_id)
        if row is None:
            raise AppError("MODEL_NOT_CONFIGURED", "请先完成模型设置", 503)
        try:
            key = cipher(settings).decrypt(row.encrypted_api_key.encode()).decode()
        except (InvalidToken, UnicodeError) as exc:
            raise AppError(
                "MODEL_KEY_UNAVAILABLE", "模型密钥无法解密，请重新填写 API Key 并保存", 503
            ) from exc
        return settings.model_copy(
            update={
                "llm_base_url": row.base_url,
                "llm_model": row.model_name,
                "llm_api_key": SecretStr(key),
                "llm_provider": "chat_completions",
                "llm_public_endpoint": True,
            }
        )


def ensure_idle(session, user_id: str) -> None:
    # 先取得写锁，再检查活动任务，避免配置在任务使用期间被替换。
    session.execute(update(User).where(User.id == user_id).values(updated_at=stamp()))
    active_run = session.scalar(
        select(Run.id)
        .join(Subscription)
        .where(Subscription.user_id == user_id, Run.status.in_(["queued", "running"]))
        .limit(1)
    )
    active_chat = session.scalar(
        select(ChatTurn.id)
        .join(ChatSession)
        .where(ChatSession.user_id == user_id, ChatTurn.status.in_(["queued", "running"]))
        .limit(1)
    )
    if active_run or active_chat:
        raise AppError("MODEL_CONFIG_BUSY", "任务正在处理，请完成后再修改或清除模型配置", 409)


def save(database: Database, settings: Settings, user_id: str, data: ModelConfigInput) -> dict:
    base = validate_endpoint(data.base_url)
    encryptor = cipher(settings)
    key = data.api_key.get_secret_value().strip()
    if any(ord(c) < 32 for c in key):
        raise AppError("INVALID_MODEL_KEY", "API Key 不能包含换行或控制字符", 422)
    with database.session.begin() as session:
        ensure_idle(session, user_id)
        row = session.get(UserModelConfig, user_id)
        if data.version != (row.version if row else 0):
            raise AppError("VERSION_CONFLICT", "模型设置已更新，请重新加载后编辑", 409)
        if not key and (row is None or not row.encrypted_api_key or row.base_url != base):
            raise AppError("MODEL_KEY_REQUIRED", "首次保存或更换 API 地址时必须填写 API Key", 422)
        if row is None:
            row = UserModelConfig(user_id=user_id, version=0)
            session.add(row)
        row.base_url, row.model_name = base, data.model_name
        if key:
            row.encrypted_api_key = encryptor.encrypt(key.encode()).decode()
        row.version, row.tested_at, row.updated_at = row.version + 1, None, stamp()
    return view(database, settings, user_id)


def clear(database: Database, settings: Settings, user_id: str, version: int) -> dict:
    with database.session.begin() as session:
        ensure_idle(session, user_id)
        row = session.get(UserModelConfig, user_id)
        if version != (row.version if row else 0):
            raise AppError("VERSION_CONFLICT", "模型设置已更新，请重新加载后操作", 409)
        if row:
            row.base_url = row.model_name = row.encrypted_api_key = ""
            row.tested_at, row.updated_at, row.version = None, stamp(), row.version + 1
    return view(database, settings, user_id)


async def test_connection(database: Database, settings: Settings, user_id: str) -> dict:
    current = view(database, settings, user_id)
    configured = resolve(database, settings, user_id)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "connection_probe",
                "description": "验证工具调用能力，不执行外部操作",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        }
    ]
    response = await ChatCompletionsClient(configured).complete(
        [{"role": "user", "content": "请调用 connection_probe 工具验证连接，不输出其他内容。"}],
        tools,
        15,
    )
    calls = response.get("tool_calls", [])
    try:
        valid = bool(calls) and all(
            c["function"]["name"] == "connection_probe"
            and json.loads(c["function"]["arguments"]) == {}
            for c in calls
        )
    except (ValueError, KeyError, TypeError):
        valid = False
    if not valid:
        raise AppError(
            "MODEL_TOOLS_UNSUPPORTED",
            "模型未按要求返回工具调用，请选择支持 function calling 的模型",
            422,
        )
    with database.session.begin() as session:
        row = session.get(UserModelConfig, user_id)
        if row is None or row.version != current["version"]:
            raise AppError("VERSION_CONFLICT", "测试期间配置已改变，请重新测试", 409)
        row.tested_at = stamp()
    return {"status": "ok", "message": "连接及工具调用验证通过"}
