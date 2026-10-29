from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    is_sqlite = url.startswith("sqlite")
    engine = create_engine(
        url, connect_args={"check_same_thread": False} if is_sqlite else {}
    )
    if is_sqlite:
        # Runs on EVERY new connection, not once at startup.
        @event.listens_for(engine, "connect")
        def _set_pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")    # SQLite ignores FKs unless asked
            cur.execute("PRAGMA journal_mode=WAL")   # readers don't block the writer
            cur.execute("PRAGMA busy_timeout=5000")  # wait 5s instead of "database is locked"
            cur.close()
    return engine


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()