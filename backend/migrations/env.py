import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import dotenv_values
from sqlalchemy import URL, create_engine, pool


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.models import Base


config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

settings = dotenv_values(PROJECT_ROOT / ".env")
password = os.environ.get("POSTGRES_PASSWORD") or settings.get(
    "POSTGRES_PASSWORD"
)
if not password:
    raise RuntimeError("Migration veritabanı parolası yapılandırılmamış.")

database_url = URL.create(
    "postgresql+psycopg",
    username="postgres",
    password=password,
    host="127.0.0.1",
    port=55432,
    database="career_agent",
)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(
        database_url,
        poolclass=pool.NullPool,
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
