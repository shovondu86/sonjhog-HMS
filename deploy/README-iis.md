# IIS reverse proxy → uvicorn

IIS forwards to the FastAPI app running on 127.0.0.1:8000 (uvicorn, kept alive as a
Windows service via NSSM — same as in `deploy/README-windows.md` step 2).

## 1. Run the app as a service

Same as before — do this first if not already done:

```powershell
cd C:\hospital_api
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
REM edit .env: SECRET_KEY, DATABASE_URL, admin creds
python -m app.seed
```

```powershell
nssm install HospitalAPI "C:\hospital_api\venv\Scripts\uvicorn.exe" "app.main:app --host 127.0.0.1 --port 8000 --workers 4"
nssm set HospitalAPI AppDirectory "C:\hospital_api"
nssm set HospitalAPI Start SERVICE_AUTO_START
nssm start HospitalAPI
```

## 2. Install IIS + the two modules IIS needs to reverse-proxy

IIS can't proxy on its own — it needs **Application Request Routing (ARR)** and
**URL Rewrite**:

1. Enable IIS: `Server Manager → Add Roles and Features → Web Server (IIS)` (or on
   desktop Windows: `Control Panel → Programs → Turn Windows features on/off → Internet
   Information Services`).
2. Install **URL Rewrite**: https://www.iis.net/downloads/microsoft/url-rewrite
3. Install **Application Request Routing**: https://www.iis.net/downloads/microsoft/application-request-routing
4. In **IIS Manager** → click the server node (top level) → **Application Request
   Routing Cache** → **Server Proxy Settings** (right panel) → check **Enable proxy** → Apply.

## 3. Point a site at the app

Use an existing site or create one (`Sites → Add Website`), bound to port 80 (and 443
once you have a cert). Set its physical path to any empty folder — IIS just needs a
site to attach the rewrite rule to; the folder's content is never served.

Add `web.config` in that site's physical path:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<configuration>
  <system.webServer>
    <rewrite>
      <rules>
        <rule name="ReverseProxyToUvicorn" stopProcessing="true">
          <match url="(.*)" />
          <action type="Rewrite" url="http://127.0.0.1:8000/{R:1}" />
        </rule>
      </rules>
    </rewrite>
  </system.webServer>
</configuration>
```

Or add the same rule via the GUI: select the site → **URL Rewrite** → **Add Rule(s)** →
**Reverse Proxy** → enter `127.0.0.1:8000` as the server → IIS Manager writes the
`web.config` above for you and prompts to enable proxy if you skipped step 2.4.

Restart the site (or `iisreset`) and browse to `http://<server>/docs` — it should hit
the FastAPI app.

## 4. Forwarded headers (so the app sees the real client IP/scheme)

ARR adds `X-Forwarded-For` automatically. Since uvicorn is bound only to
`127.0.0.1`, it's implicitly trusted, but FastAPI won't use those headers unless told
to. If the app needs the real client IP/scheme (e.g. for logging or building
absolute URLs), run uvicorn with a forwarded-allow-ips flag:

```powershell
nssm set HospitalAPI AppParameters "app.main:app --host 127.0.0.1 --port 8000 --workers 4 --forwarded-allow-ips=127.0.0.1 --proxy-headers"
nssm restart HospitalAPI
```

## 5. HTTPS

Bind port 443 on the site in IIS Manager (`Bindings → Add → https`) with a cert from
your CA or `win-acme` (https://www.win-acme.com/) for Let's Encrypt — win-acme can
install directly into IIS bindings. The same `web.config` rewrite rule handles both
HTTP and HTTPS traffic once the binding exists.

## Result

Browser/client → IIS (ARR + URL Rewrite, port 80/443) → uvicorn service on
127.0.0.1:8000 → FastAPI app. No nginx anywhere in this path.
