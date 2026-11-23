"""FastAPI 应用；调度仅在独立工作进程中运行。"""

import threading
import time
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError

from backend.app.api.schemas import (
    BriefOutput,
    BriefSummary,
    ChatSessionOutput,
    ChatTurnOutput,
    ChatTurnTicket,
    RetryOutput,
    RunOutput,
    ServiceStatus,
    SubscriptionOutput,
    TokenOutput,
    ToolOutput,
)
from backend.app.conf.settings import Settings, get_settings
from backend.app.domain.common import AppError, identifier
from backend.app.domain.schemas import Login, RetryRequest, SubscriptionInput
from backend.app.infrastructure.database import Database
from backend.app.service import chat
from backend.app.service.accounts import (
    authenticate_token,
    login,
    read_subscription,
    save_subscription,
)
from backend.app.service.briefs import brief_detail, list_briefs
from backend.app.service.deliveries import retry_delivery
from backend.app.service.runs import create_run, get_run, list_runs, list_tool_calls


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    database = Database(settings.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        database.close()

    app = FastAPI(title="每日 AI 新闻助手", version="0.2.0", lifespan=lifespan)
    app.state.database = database
    app.state.settings = settings
    bearer = HTTPBearer(auto_error=False)
    attempts: dict[str, list[float]] = {}
    attempts_lock = threading.Lock()

    @app.middleware("http")
    async def correlation(request: Request, call_next) -> Response:
        request.state.request_id = identifier()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(AppError)
    async def business_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={
                "error": {"code": exc.code, "message": exc.message},
                "request_id": request.state.request_id,
            },
            headers={"WWW-Authenticate": "Bearer"} if exc.status == 401 else None,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {"code": "INVALID_INPUT", "message": "输入格式或字段范围不正确"},
                "request_id": request.state.request_id,
            },
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, _exc: SQLAlchemyError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "error": {"code": "DATABASE_UNAVAILABLE", "message": "数据库不可用，请先完成迁移"},
                "request_id": request.state.request_id,
            },
        )

    def current_user(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> str:
        if credentials is None:
            raise AppError("UNAUTHORIZED", "请先登录", 401)
        return authenticate_token(database, settings, credentials.credentials)

    @app.get("/health")
    @app.get("/api/v1/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/ready")
    def readiness() -> dict:
        version = database.schema_version()
        if version != 3:
            raise AppError("MIGRATION_REQUIRED", "数据库需要迁移", 503)
        return {"status": "ready", "schema_version": version}

    @app.post("/api/v1/auth/login", response_model=TokenOutput)
    def sign_in(data: Login, request: Request) -> dict:
        address = request.client.host if request.client else "unknown"
        current = time.monotonic()
        with attempts_lock:
            for key in list(attempts):
                attempts[key] = [item for item in attempts[key] if current - item < 60]
                if not attempts[key]:
                    del attempts[key]
            recent = attempts.setdefault(address, [])
            if len(recent) >= 20:
                raise AppError("RATE_LIMITED", "登录尝试过多，请稍后重试", 429)
            recent.append(current)
        return {
            "access_token": login(database, settings, str(data.email), data.password),
            "token_type": "bearer",
        }

    @app.get("/api/v1/status", response_model=ServiceStatus)
    def status(_user_id: str = Depends(current_user)) -> dict:
        return {
            "model_configured": settings.model_configured,
            "mail_configured": settings.mail_configured,
            "news_provider": "configured_rss",
        }

    @app.get("/api/v1/subscription", response_model=SubscriptionOutput)
    def get_subscription(user_id: str = Depends(current_user)) -> dict:
        return read_subscription(database, user_id)

    @app.put("/api/v1/subscription", response_model=SubscriptionOutput)
    def put_subscription(data: SubscriptionInput, user_id: str = Depends(current_user)) -> dict:
        return save_subscription(database, user_id, data, settings)

    @app.post("/api/v1/runs", status_code=202, response_model=RunOutput)
    def start_run(
        key: str = Header(alias="Idempotency-Key", min_length=1, max_length=100),
        user_id: str = Depends(current_user),
    ) -> dict:
        return create_run(database, user_id, key, require_model=True, settings=settings)

    @app.get("/api/v1/runs", response_model=list[RunOutput])
    def runs(
        page_size: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
        user_id: str = Depends(current_user),
    ) -> list[dict]:
        return list_runs(database, user_id, page_size, offset)

    @app.get("/api/v1/runs/{run_id}", response_model=RunOutput)
    def run_detail(run_id: str, user_id: str = Depends(current_user)) -> dict:
        return get_run(database, user_id, run_id)

    @app.get("/api/v1/runs/{run_id}/tool-calls", response_model=list[ToolOutput])
    def tools(
        run_id: str,
        page_size: int = Query(100, ge=1, le=100),
        offset: int = Query(0, ge=0),
        user_id: str = Depends(current_user),
    ) -> list[dict]:
        return list_tool_calls(database, user_id, run_id, page_size, offset)

    @app.get("/api/v1/briefs", response_model=list[BriefSummary])
    def briefs(
        page_size: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
        user_id: str = Depends(current_user),
    ) -> list[dict]:
        return list_briefs(database, user_id, page_size, offset)

    @app.get("/api/v1/briefs/{brief_id}", response_model=BriefOutput)
    def detail(brief_id: str, user_id: str = Depends(current_user)) -> dict:
        return brief_detail(database, user_id, brief_id)

    @app.post("/api/v1/deliveries/{delivery_id}/retry", response_model=RetryOutput)
    def retry(delivery_id: str, data: RetryRequest, user_id: str = Depends(current_user)) -> dict:
        return retry_delivery(database, user_id, delivery_id, data.confirm_unknown, data.reason)

    @app.get("/api/v1/chat/sessions", response_model=list[ChatSessionOutput])
    def chat_sessions(user_id: str = Depends(current_user)) -> list[dict]:
        return chat.sessions(database, user_id)

    @app.post("/api/v1/chat/sessions", status_code=201, response_model=ChatSessionOutput)
    def open_chat(user_id: str = Depends(current_user)) -> dict:
        return chat.new_session(database, user_id)

    @app.get("/api/v1/chat/sessions/{session_id}/turns", response_model=list[ChatTurnOutput])
    def chat_turns(session_id: str, user_id: str = Depends(current_user)) -> list[dict]:
        return chat.turns(database, user_id, session_id)

    @app.post(
        "/api/v1/chat/sessions/{session_id}/turns", status_code=202, response_model=ChatTurnTicket
    )
    def send_chat(
        session_id: str,
        data: chat.TurnInput,
        key: str = Header(alias="Idempotency-Key", min_length=1, max_length=100),
        user_id: str = Depends(current_user),
    ) -> dict:
        return chat.start_turn(database, settings, user_id, session_id, key, data)

    return app


app = create_app()
