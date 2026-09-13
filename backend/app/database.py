import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import URL, create_engine


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)

password = os.environ.get("APP_DB_PASSWORD")
if not password:
    raise RuntimeError("Uygulama veritabanı parolası yapılandırılmamış.")

database_url = URL.create(
    drivername="postgresql+psycopg",
    username="career_app",
    password=password,
    host="127.0.0.1",
    port=55432,
    database="career_agent",
)

engine = create_engine(
    database_url,
    pool_pre_ping=True,
    hide_parameters=True,
    echo=False,
    connect_args={"connect_timeout": 5},
)
