from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.database import engine
from app.companies import router as companies_router


app = FastAPI(
    title="Career Agent API",
    version="0.1.0",
    debug=False,
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["localhost", "127.0.0.1"],
)


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: Literal["career-agent-api"]


class ReadyResponse(BaseModel):
    status: Literal["ok"]
    database: Literal["connected"]


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="career-agent-api",
    )


@app.get("/ready", response_model=ReadyResponse)
def ready() -> ReadyResponse:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Veritabanı şu anda kullanılamıyor.",
        ) from None

    return ReadyResponse(status="ok", database="connected")


app.include_router(companies_router)
