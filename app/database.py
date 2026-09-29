from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import settings

connect_args = {}
if settings.database_url.startswith("sqlite"):
    # Needed only for SQLite when used with FastAPI's threaded requests.
    # timeout: wait for a competing writer instead of failing immediately.
    connect_args = {"check_same_thread": False, "timeout": 15}

engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
