"""Additive, idempotent upgrade of an existing database to the current models
(1.0.0 -> 1.2.0). Safe to run repeatedly; also runs automatically at startup.

    python -m app.migrate

It only ever ADDS: new tables, new columns, new indexes. It never drops or rewrites data,
apart from back-filling the new NOT NULL-ish columns it just added (appointment serials,
normalised phone numbers)."""
import logging

from sqlalchemy import inspect, text

from app import models  # noqa: F401  (registers tables)
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.utils import normalize_phone

log = logging.getLogger("app.migrate")

_TRUE = "1"  # SQLite; PostgreSQL gets 'true' below

# Columns that need a DEFAULT so existing rows stay valid after ALTER TABLE.
_DEFAULTS = {
    ("users", "scope"): "'admin'",
    ("doctors", "is_active"): "TRUE",
    ("doctors", "accepting_appointments"): "TRUE",
    ("appointments", "visit_type"): "'new'",
    ("appointments", "queue"): "'regular'",
}


def _default_sql(table: str, col: str, dialect: str):
    d = _DEFAULTS.get((table, col))
    if d is None:
        return None
    if d == "TRUE":
        return "true" if dialect == "postgresql" else "1"
    return d


def add_missing_columns() -> list:
    added = []
    insp = inspect(engine)
    dialect = engine.dialect.name
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in have:
                continue
            ctype = col.type.compile(dialect=engine.dialect)
            sql = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ctype}'
            default = _default_sql(table.name, col.name, dialect)
            if default is not None:
                sql += f" DEFAULT {default}"
            try:
                with engine.begin() as conn:
                    conn.execute(text(sql))
                added.append(f"{table.name}.{col.name}")
            except Exception as exc:  # another worker may have added it first
                log.warning("could not add %s.%s: %s", table.name, col.name, exc)
    return added


def backfill() -> None:
    db = SessionLocal()
    try:
        # normalised phone numbers for the exact `mobile_number` filter
        for p in db.query(models.Patient).filter(models.Patient.mobile_normalized.is_(None)):
            p.mobile_normalized = normalize_phone(p.mobile_number)

        # serial numbers for appointments made before 1.1.0: per doctor & date, in id order
        legacy = (
            db.query(models.Appointment)
            .filter(models.Appointment.serial_number.is_(None))
            .order_by(
                models.Appointment.doctor_id, models.Appointment.appointment_date,
                models.Appointment.id,
            )
            .all()
        )
        counters = {}
        for a in legacy:
            k = (a.doctor_id, a.appointment_date)
            counters[k] = counters.get(k, 0) + 1
            a.serial_number = counters[k]
            a.visit_type = a.visit_type or "new"
            a.queue = a.queue or "regular"
        db.commit()

        if db.get(models.Hospital, 1) is None:
            db.add(models.Hospital(id=1, name=settings.hospital_name))
            db.commit()
    finally:
        db.close()


def create_indexes() -> None:
    """create_all only builds indexes for tables it creates; add the new ones to old tables."""
    for table in Base.metadata.sorted_tables:
        for idx in table.indexes:
            try:
                idx.create(bind=engine, checkfirst=True)
            except Exception as exc:
                log.warning(
                    "index %s not created (%s). Existing rows probably break the rule it "
                    "enforces; the API still checks it in code.", idx.name, exc,
                )


def run() -> list:
    Base.metadata.create_all(bind=engine)
    added = add_missing_columns()
    backfill()
    create_indexes()
    return added


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    added = run()
    print("Added columns:", ", ".join(added) if added else "none")
    print("Database is up to date.")
