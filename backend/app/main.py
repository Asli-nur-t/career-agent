from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.database import engine
from app.companies import router as companies_router
from app.operator_api import router as operator_router


app = FastAPI(
    title="Career Agent API",
    version="0.1.0",
    debug=False,
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["localhost", "127.0.0.1"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/operator"):
        response.headers["Cache-Control"] = "no-store"
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Operator-Token"],
    max_age=600,
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
app.include_router(operator_router)
