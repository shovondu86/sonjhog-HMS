# Windows deployment (nginx, no IIS)

Runs the app as a background Windows service (via NSSM) on 127.0.0.1:8000,
with nginx for Windows as the reverse proxy on port 80. No IIS involved.

## 1. App setup

```powershell
cd C:\hospital_api
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
REM edit .env: SECRET_KEY, DATABASE_URL, admin creds
python -m app.seed
```

Note: `gunicorn` (in requirements.txt) is Linux-only (uses `fork`). On Windows, run
uvicorn directly instead — it supports multiple worker processes on Windows too:

```powershell
venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000 --workers 4
```

## 2. Run it as a Windows service (NSSM)

Download NSSM: https://nssm.cc/download — unzip, use the matching `win64\nssm.exe`.

```powershell
nssm install HospitalAPI "C:\hospital_api\venv\Scripts\uvicorn.exe" "app.main:app --host 127.0.0.1 --port 8000 --workers 4"
nssm set HospitalAPI AppDirectory "C:\hospital_api"
nssm set HospitalAPI AppStdout "C:\hospital_api\logs\stdout.log"
nssm set HospitalAPI AppStderr "C:\hospital_api\logs\stderr.log"
nssm set HospitalAPI Start SERVICE_AUTO_START

nssm start HospitalAPI
```

Manage it with `nssm stop HospitalAPI`, `nssm restart HospitalAPI`, `nssm remove HospitalAPI confirm`,
or via `services.msc` (it shows up as "HospitalAPI").

## 3. nginx for Windows (reverse proxy on port 80)

Download: https://nginx.org/en/download.html (stable, Windows zip) → extract to e.g. `C:\nginx`.

Replace `C:\nginx\conf\nginx.conf`'s `server { ... }` block with:

```nginx
server {
    listen 80;
    server_name api.yourdomain.com;   # or just the server IP

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```
(Same proxy settings as `deploy/nginx.conf` — just dropped into nginx-for-Windows' config instead of `/etc/nginx/`.)

Test and start:
```powershell
cd C:\nginx
nginx.exe -t
nginx.exe
```

Run nginx itself as a service too, so it survives reboots/logoff:
```powershell
nssm install nginx "C:\nginx\nginx.exe"
nssm set nginx AppDirectory "C:\nginx"
nssm start nginx
```

## 4. HTTPS

No certbot-for-nginx integration on Windows. Easiest path: get a cert from your CA (or win-acme
for Let's Encrypt — https://www.win-acme.com/), then add a `listen 443 ssl;` server block in
`nginx.conf` pointing `ssl_certificate` / `ssl_certificate_key` at the cert files, and redirect
port 80 to 443.

## Result

Browser/client → nginx (port 80/443) → uvicorn service on 127.0.0.1:8000 → FastAPI app.
Both nginx and the app run as Windows services, no IIS anywhere in the chain.
