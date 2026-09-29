from fastapi import FastAPI

from app.database import Base, engine
from app.routers import auth, patients, doctors, appointments

# Create tables on startup (fine for SQLite / simple deployments;
# use Alembic migrations instead for production Postgres).
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Hospital Management API",
    description="Simple hospital management system: login, patients, doctors, appointments.",
    version="1.0.0",
)

app.include_router(auth.router)
app.include_router(patients.router)
app.include_router(doctors.router)
app.include_router(appointments.router)


@app.get("/", tags=["Health"])
def health_check():
    return {"status": "ok", "service": "Hospital Management API"}
