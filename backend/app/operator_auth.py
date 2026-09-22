"""Fail-closed authentication for the local operator API."""

import os
import secrets
import unicodedata
from typing import Annotated

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader


OPERATOR_TOKEN_HEADER = "X-Operator-Token"
operator_token_header = APIKeyHeader(
    name=OPERATOR_TOKEN_HEADER,
    auto_error=False,
)


def _configured_operator_token() -> str:
    token = os.environ.get("OPERATOR_API_TOKEN", "")
    if (
        len(token) < 32
        or len(token) > 256
        or token != token.strip()
        or any(
            unicodedata.category(character).startswith("C")
            for character in token
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "operator_auth_not_configured"},
        )
    return token


def require_operator_token(
    supplied_token: Annotated[
        str | None,
        Security(operator_token_header),
    ] = None,
) -> None:
    configured_token = _configured_operator_token()
    if supplied_token is None or not secrets.compare_digest(
        supplied_token,
        configured_token,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": "operator_auth_failed"},
            headers={"WWW-Authenticate": "ApiKey"},
        )
