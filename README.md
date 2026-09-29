# Hospital Management API — v1.2.0

FastAPI backend implementing `tests/hospital-api-v1_2_openapi.yaml`: patients, doctors,
departments, weekly OPD schedules with exceptions and holidays, computed availability,
appointments with server-assigned serials, schedule import, audit log, and the hospital
content the voice agent reads (profile, packages, services, articles).

SQLite out of the box; PostgreSQL by changing `DATABASE_URL`. Auth is an `X-API-Key` header.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env          # set ADMIN_*, optionally AGENT_*
python -m app.seed            # creates the login users and prints their API keys
uvicorn app.main:app --reload
```
Docs: `http://127.0.0.1:8000/docs`.

## Upgrading an existing 1.0.0 database

Nothing to delete. On startup (or with `python -m app.migrate`) the app **adds** the new
tables, columns and indexes and back-fills what it must; it never drops or rewrites data:

- existing users become `admin` scope,
- existing appointments get a `serial_number` (per doctor and date, in id order),
- patient phone numbers get a normalised form for the exact `mobile_number` filter,
- the hospital profile row is created.

Legacy appointments have no schedule link, so they don't count against a session's capacity.
Then `python -m app.seed` to create the `agent` user if you want one.

**On the IIS server:** activate the venv, `pip install -r requirements.txt` (new: `openpyxl`,
`xlrd`, `tzdata`), then recycle the app pool (or `iisreset`) so IIS restarts uvicorn.

## Scopes

| | `admin` | `agent` (voice agent) |
|---|---|---|
| Read | everything | everything except `/audit-log` |
| Write | everything | **only** patients and appointments — else `403 FORBIDDEN_SCOPE` |
| Doctor `mobile_number`/`email`, exception/holiday `note` | returned | **omitted** |
| Inactive / unpublished / soft-deleted content | returned by id and lists with `include_deleted` | hidden, unless `include_deleted=true` (sync) |

## Booking rules (`POST /appointments`)

One transaction: past date → `PAST_DATE`; holiday → `HOLIDAY`; resolve the session
(`NO_SESSION` / `SESSION_REQUIRED`); duplicate patient+doctor+date → `DUPLICATE_BOOKING`;
choose the regular or report queue; lowest free serial or `SESSION_FULL`; derive the time.
`NO_SESSION`, `SESSION_FULL` and `HOLIDAY` carry `next_available_date`. Partial unique indexes
back this up, so concurrent requests can't take the same serial (see `tests/test_concurrency.py`).
`Idempotency-Key` is honoured on `POST /patients` and `POST /appointments`.

## Schedule import

`POST /schedules/import?mode=preview|apply` (admin). **Always preview first** — preview runs
the identical code and rolls back. Accepts `.xlsx`, `.xls` and CSV
(`doctor_id|doctor_name, weekday, start_time, end_time, room, floor, desk, max_patients[, slot_minutes, report_capacity]`).
Apply is refused with `409 IMPORT_HAS_ERRORS` while any problem has severity `error`.

Behaviour worth knowing:
- Only doctors whose sessions actually change are touched; unchanged doctors are skipped.
- Changed doctors: current rows are end-dated (`valid_to = valid_from - 1`), new rows created.
- Future bookings on an end-dated row are **re-pointed** at the new row that contains their
  time when that is unambiguous (serial, date and time never change); the rest are returned
  in `affected_appointments`.
- `slot_minutes` and `report_capacity` are carried over from the doctor's current row for
  that weekday when the file gives none (the spreadsheet has no such columns).

## Sync (voice agent search index)

Lists of departments, doctors, packages, services and articles accept `updated_since` and
`include_deleted`, and return `X-Server-Time`. Deletes on these are soft (`deleted_at`).

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q                 # 29 tests incl. import, 1.0.0 upgrade, race
python tests/check_contract.py            # diff of the app's OpenAPI against the yaml
```

## Deployment

See `deploy/` (nginx, IIS + ARR, IIS + HttpPlatformHandler, Windows service, systemd).
On Windows use a single uvicorn process (no `--workers`).

## Assumptions where the spec is silent
- `IMPORT_HAS_ERRORS`: `candidates` carries the list of problems.
- Files over 5 MB and unreadable files return `415 UNSUPPORTED_FILE`.
- `leave` on a date also switches off an `extra_session` on that date.
- An explicit `null` on a non-nullable field in a `PUT` means "unchanged".
- Time values are serialised as `HH:MM` (as in the spec's examples).
