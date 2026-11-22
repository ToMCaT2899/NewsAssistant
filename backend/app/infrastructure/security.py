"""Password hashing and short-lived signed bearer tokens."""

from datetime import timedelta

import jwt
from pwdlib import PasswordHash

from backend.app.conf.settings import Settings
from backend.app.domain.common import AppError, now

password_hash = PasswordHash.recommended()


def token_for(user_id: str, settings: Settings) -> str:
    secret = settings.jwt_secret.get_secret_value()
    if len(secret) < 32:
        raise AppError("AUTH_NOT_CONFIGURED", "请配置至少 32 字符的 JWT_SECRET", 503)
    return jwt.encode(
        {"sub": user_id, "exp": now() + timedelta(minutes=settings.token_minutes), "iat": now()},
        secret,
        algorithm="HS256",
    )


def user_from_token(token: str, settings: Settings) -> str:
    secret = settings.jwt_secret.get_secret_value()
    if len(secret) < 32:
        raise AppError("AUTH_NOT_CONFIGURED", "身份服务尚未配置", 503)
    try:
        claims = jwt.decode(
            token, secret, algorithms=["HS256"], options={"require": ["sub", "exp"]}
        )
        if not isinstance(claims["sub"], str):
            raise ValueError("Invalid subject")
        return claims["sub"]
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise AppError("UNAUTHORIZED", "登录已失效，请重新登录", 401) from exc
