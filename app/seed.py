"""
Creates the login user since there is no registration endpoint.
Run once after setting up the database:

    python -m app.seed
"""

from app.database import Base, engine, SessionLocal
from app.models import User
from app.security import hash_password, generate_api_key
from app.config import settings

Base.metadata.create_all(bind=engine)


def seed_admin_user():
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == settings.admin_username).first()
        if existing:
            print(f"User '{settings.admin_username}' already exists.")
            print(f"API key: {existing.api_key}")
            return

        user = User(
            username=settings.admin_username,
            hashed_password=hash_password(settings.admin_password),
            api_key=generate_api_key(),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        print(f"Created login user: username='{settings.admin_username}'")
        print(f"API key: {user.api_key}")
        print("Save this — it's also returned by POST /auth/login.")
    finally:
        db.close()


if __name__ == "__main__":
    seed_admin_user()
