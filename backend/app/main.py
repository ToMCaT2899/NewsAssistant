"""FastAPI application. Scheduling runs only in the independent worker."""

import json
import threading
import time
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from backend.app.api.schemas import (
    BriefOutput,
    BriefSummary,
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
from backend.app.infrastructure.security import user_from_token
from backend.app.models.schema import Run, Subscription, ToolCall, User
from backend.app.repository.queries import owned_run, subscription_for
from backend.app.service.accounts import login, save_subscription, subscription_view
from backend.app.service.briefs import brief_detail, list_briefs
from backend.app.service.deliveries import retry_delivery
from backend.app.service.runs import create_run, run_view


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
    async def correlation(request: Request, call_next):
        request.state.request_id = identifier()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(AppError)
    async def business_error(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status,
            content={
                "error": {"code": exc.code, "message": exc.message},
                "request_id": request.state.request_id,
            },
            headers={"WWW-Authenticate": "Bearer"} if exc.status == 401 else None,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "error": {"code": "INVALID_INPUT", "message": "输入格式或字段范围不正确"},
                "request_id": request.state.request_id,
            },
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, _exc: SQLAlchemyError):
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
        user_id = user_from_token(credentials.credentials, settings)
        with database.session() as session:
            user = session.get(User, user_id)
            if user is None or not user.enabled:
                raise AppError("UNAUTHORIZED", "登录已失效", 401)
        return user_id

    @app.get("/health")
    @app.get("/api/v1/health")
    def health():
        return {"status": "ok"}

    @app.get("/ready")
    def readiness():
        with database.session() as session:
            version = session.execute(text("SELECT MAX(version) FROM schema_migrations")).scalar()
        if version != 2:
            raise AppError("MIGRATION_REQUIRED", "数据库需要迁移", 503)
        return {"status": "ready", "schema_version": version}

    @app.post("/api/v1/auth/login", response_model=TokenOutput)
    def sign_in(data: Login, request: Request):
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
    def status(_user_id: str = Depends(current_user)):
        return {
            "model_configured": settings.model_configured,
            "mail_configured": settings.mail_configured,
            "news_provider": "configured_rss",
        }

    @app.get("/api/v1/subscription", response_model=SubscriptionOutput)
    def get_subscription(user_id: str = Depends(current_user)):
        with database.session() as session:
            return subscription_view(subscription_for(session, user_id))

    @app.put("/api/v1/subscription", response_model=SubscriptionOutput)
    def put_subscription(data: SubscriptionInput, user_id: str = Depends(current_user)):
        return save_subscription(database, user_id, data, settings)

    @app.post("/api/v1/runs", status_code=202, response_model=RunOutput)
    def start_run(
        key: str = Header(alias="Idempotency-Key", min_length=1, max_length=100),
        user_id: str = Depends(current_user),
    ):
        return create_run(database, user_id, key, require_model=True, settings=settings)

    @app.get("/api/v1/runs", response_model=list[RunOutput])
    def runs(
        page_size: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
        user_id: str = Depends(current_user),
    ):
        with database.session() as session:
            rows = session.scalars(
                select(Run)
                .join(Subscription, Run.subscription_id == Subscription.id)
                .where(Subscription.user_id == user_id)
                .order_by(Run.created_at.desc())
                .limit(page_size)
                .offset(offset)
            )
            return [run_view(row) for row in rows]

    @app.get("/api/v1/runs/{run_id}", response_model=RunOutput)
    def run_detail(run_id: str, user_id: str = Depends(current_user)):
        with database.session() as session:
            run = owned_run(session, user_id, run_id)
            from backend.app.models.schema import Brief

            brief_id = session.scalar(
                select(Brief.id).where(
                    Brief.subscription_id == run.subscription_id,
                    Brief.business_date == run.business_date,
                )
            )
            return run_view(run, brief_id)

    @app.get("/api/v1/runs/{run_id}/tool-calls", response_model=list[ToolOutput])
    def tools(
        run_id: str,
        page_size: int = Query(100, ge=1, le=100),
        offset: int = Query(0, ge=0),
        user_id: str = Depends(current_user),
    ):
        with database.session() as session:
            owned_run(session, user_id, run_id)
            rows = session.scalars(
                select(ToolCall)
                .where(ToolCall.run_id == run_id)
                .order_by(ToolCall.sequence)
                .limit(page_size)
                .offset(offset)
            )
            return [
                {
                    "id": row.id,
                    "sequence": row.sequence,
                    "tool_name": row.tool_name,
                    "arguments": json.loads(row.arguments_json),
                    "status": row.status,
                    "duration_ms": row.duration_ms,
                    "error_code": row.error_code,
                }
                for row in rows
            ]

    @app.get("/api/v1/briefs", response_model=list[BriefSummary])
    def briefs(
        page_size: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
        user_id: str = Depends(current_user),
    ):
        return list_briefs(database, user_id, page_size, offset)

    @app.get("/api/v1/briefs/{brief_id}", response_model=BriefOutput)
    def detail(brief_id: str, user_id: str = Depends(current_user)):
        return brief_detail(database, user_id, brief_id)

    @app.post("/api/v1/deliveries/{delivery_id}/retry", response_model=RetryOutput)
    def retry(delivery_id: str, data: RetryRequest, user_id: str = Depends(current_user)):
        return retry_delivery(database, user_id, delivery_id, data.confirm_unknown, data.reason)

    return app


app = create_app()
