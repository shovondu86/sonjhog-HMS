# Hospital Management API

A simple FastAPI backend with login, patient records, doctor records, and appointments.

## Features
- **Login only** — no registration endpoint. A single user is created via a seed script.
- **Patients** — name, age, mobile number (full CRUD).
- **Doctors** — name, designation, education, specialization, description, mobile, email (full CRUD).
- **Appointments** — links a patient + doctor with date, time, status, notes (full CRUD).
- Works with **SQLite** out of the box, or **PostgreSQL** by changing one env variable.
- JWT bearer-token auth; every route except `/` and `/auth/login` requires a valid token.

## Project structure
```
app/
  main.py           FastAPI app, mounts routers, creates tables on startup
  config.py         Settings (reads .env)
  database.py       SQLAlchemy engine/session
  models.py         User, Patient, Doctor, Appointment tables
  schemas.py        Pydantic request/response models
  security.py       Password hashing + JWT create/verify
  deps.py           get_current_user dependency (JWT auth guard)
  seed.py           One-time script to create the login user
  routers/
    auth.py         POST /auth/login
    patients.py     /patients CRUD
    doctors.py      /doctors CRUD
    appointments.py /appointments CRUD
```

## Setup

1. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure environment**
   ```bash
   cp .env.example .env
   ```
   By default `DATABASE_URL` points at SQLite (`sqlite:///./hospital.db`) — no extra setup needed.

   To use PostgreSQL instead, edit `.env`:
   ```
   DATABASE_URL=postgresql://<user>:<password>@<host>:5432/<dbname>
   ```
   (Make sure the database itself already exists — the app creates tables, not the database.)

   Also set your own `SECRET_KEY`, and the login credentials `ADMIN_USERNAME` / `ADMIN_PASSWORD`.

3. **Create the login user** (there is no registration endpoint — this is the only way a user gets created)
   ```bash
   python -m app.seed
   ```

4. **Run the server**
   ```bash
   uvicorn app.main:app --reload
   ```

5. **Open the interactive docs**: http://127.0.0.1:8000/docs
   Click "Authorize" and log in with the seeded username/password to try protected routes directly from Swagger UI.

## Auth flow

`POST /auth/login` — form-encoded (`username`, `password`), returns:
```json
{ "api_key": "..." }
```
Send it back on every other request as the `X-API-Key` header (not an Authorization/Bearer token):
```
X-API-Key: <api_key>
```

## Example requests (curl)

```bash
# Login
curl -X POST http://127.0.0.1:8000/auth/login \
  -d "username=admin&password=admin123" \
  -H "Content-Type: application/x-www-form-urlencoded"

# Create a doctor (use the token from above)
curl -X POST http://127.0.0.1:8000/doctors \
  -H "X-API-Key: <KEY>" \
  -H "Content-Type: application/json" \
  -d '{
        "name": "Dr. Anika Rahman",
        "designation": "Consultant",
        "education": "MBBS, FCPS (Medicine)",
        "specialization": "Cardiology",
        "description": "15 years experience in cardiac care",
        "mobile_number": "01711111111",
        "email": "anika@example.com"
      }'

# Create a patient
curl -X POST http://127.0.0.1:8000/patients \
  -H "X-API-Key: <KEY>" \
  -H "Content-Type: application/json" \
  -d '{"name": "Karim Uddin", "age": 34, "mobile_number": "01812345678"}'

# Book an appointment
curl -X POST http://127.0.0.1:8000/appointments \
  -H "X-API-Key: <KEY>" \
  -H "Content-Type: application/json" \
  -d '{
        "patient_id": 1,
        "doctor_id": 1,
        "appointment_date": "2026-09-20",
        "appointment_time": "10:30:00",
        "notes": "Follow-up checkup"
      }'
```

## Endpoints

| Method | Path                     | Description                          |
|--------|---------------------------|---------------------------------------|
| POST   | `/auth/login`             | Log in, get JWT token                 |
| POST   | `/patients`                | Create patient                        |
| GET    | `/patients`                | List patients (`search`, pagination)  |
| GET    | `/patients/{id}`           | Get one patient                       |
| PUT    | `/patients/{id}`           | Update patient                        |
| DELETE | `/patients/{id}`           | Delete patient                        |
| POST   | `/doctors`                 | Create doctor                         |
| GET    | `/doctors`                 | List doctors (`specialization`, `search`) |
| GET    | `/doctors/{id}`            | Get one doctor                        |
| PUT    | `/doctors/{id}`            | Update doctor                         |
| DELETE | `/doctors/{id}`            | Delete doctor                         |
| POST   | `/appointments`            | Book appointment                      |
| GET    | `/appointments`            | List appointments (filter by patient/doctor/status) |
| GET    | `/appointments/{id}`       | Get one appointment (with patient+doctor details) |
| PUT    | `/appointments/{id}`       | Update/reschedule/cancel appointment  |
| DELETE | `/appointments/{id}`       | Delete appointment                    |

## Production deployment (nginx + systemd)

Config files are in `deploy/`:
- `deploy/hospital-api.service` — systemd unit running the app via gunicorn + uvicorn workers on `127.0.0.1:8000`
- `deploy/nginx.conf` — nginx reverse proxy, terminating on port 80 and forwarding to that upstream

```bash
# On the server, e.g. in /opt/hospital_api
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # edit SECRET_KEY, DATABASE_URL, admin creds
python -m app.seed

sudo cp deploy/hospital-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hospital-api

sudo cp deploy/nginx.conf /etc/nginx/sites-available/hospital-api
sudo ln -s /etc/nginx/sites-available/hospital-api /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# then HTTPS:
sudo certbot --nginx -d api.yourdomain.com
```

Edit `server_name` in `deploy/nginx.conf` before enabling it.

## Notes
- Deleting a patient or doctor also deletes their appointments (cascade).
- `appointments.status` is one of: `scheduled`, `completed`, `cancelled`.
- Tables are auto-created on startup (`Base.metadata.create_all`) — fine for development/small
  deployments. For production Postgres with evolving schema, consider adding Alembic migrations.
- If you see a bcrypt/passlib version error, make sure `bcrypt==4.0.1` (pinned in
  `requirements.txt`) is installed — newer bcrypt releases break passlib's version check.
