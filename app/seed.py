"""
Creates the login users, since there is no registration endpoint.
Run once after setting up the database (safe to re-run):

    python -m app.seed

Creates `admin` (full access) and, if AGENT_USERNAME / AGENT_PASSWORD are set in .env,
an `agent` user (the voice agent: reads everything, writes only patients + appointments).
"""
from app import migrate
from app.config import settings
from app.database import SessionLocal
from app.models import User
from app.security import generate_api_key, hash_password


def _ensure(db, username: str, password: str, scope: str) -> None:
    existing = db.query(User).filter(User.username == username).first()
    if existing:
        print(f"User '{username}' ({existing.scope}) already exists.")
        print(f"  API key: {existing.api_key}")
        return
    user = User(
        username=username, hashed_password=hash_password(password),
        api_key=generate_api_key(), scope=scope,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    print(f"Created {scope} user: username='{username}'")
    print(f"  API key: {user.api_key}")


def seed() -> None:
    migrate.run()
    db = SessionLocal()
    try:
        _ensure(db, settings.admin_username, settings.admin_password, "admin")
        if settings.agent_username and settings.agent_password:
            _ensure(db, settings.agent_username, settings.agent_password, "agent")
        print("Save these keys — they are also returned by POST /auth/login.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
