# Let IIS own the process (HttpPlatformHandler — no NSSM)

Instead of running uvicorn as a separate Windows service (NSSM) and having IIS just
proxy to it, **HttpPlatformHandler** lets IIS itself start, stop, monitor, and
auto-restart the uvicorn process — same as it does for any .NET app pool. One thing
managing the app instead of two.

If you already set up the NSSM `HospitalAPI` service, remove it first so nothing
fights over the port:
```powershell
nssm stop HospitalAPI
nssm remove HospitalAPI confirm
```

## 1. Install HttpPlatformHandler

Download: https://www.iis.net/downloads/microsoft/httpplatformhandler
(Run the installer, then `iisreset` to load the module.)

You do **not** need ARR or URL Rewrite for this approach — HttpPlatformHandler
handles the proxying itself.

## 2. Point a site at the app folder

In IIS Manager, create/edit a site whose **physical path is `C:\hospital_api` directly**
(not an empty folder like the ARR approach — `web.config` lives next to `app\`).

Set its **Application Pool** to:
- **.NET CLR version: No Managed Code** (it's not a .NET app)
- Keep **Start Mode: AlwaysRunning** if you want IIS to launch the process at boot
  rather than on first request (`Advanced Settings` → `Start Mode`), and enable
  **Application Initialization** for the site to warm it up automatically.

## 3. Add web.config

Drop this in `C:\hospital_api\web.config`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <system.webServer>
    <handlers>
      <add name="httpplatformhandler" path="*" verb="*"
           modules="httpPlatformHandler" resourceType="Unspecified" />
    </handlers>

    <httpPlatform processPath="C:\hospital_api\venv\Scripts\uvicorn.exe"
                   arguments="app.main:app --host 127.0.0.1 --port %HTTP_PLATFORM_PORT% --workers 4"
                   workingDirectory="C:\hospital_api"
                   startupTimeLimit="60"
                   stdoutLogEnabled="true"
                   stdoutLogFile="C:\hospital_api\logs\stdout.log">
    </httpPlatform>
  </system.webServer>
</configuration>
```

Key point: **`--port %HTTP_PLATFORM_PORT%`, not a hardcoded 8000.** IIS picks a free
internal port, sets that env var, launches uvicorn with it substituted in, and proxies
incoming requests to it — you never touch the port number yourself.

Create the log folder first (the module won't create it):
```powershell
mkdir C:\hospital_api\logs
```

Make sure the app pool identity (`IIS AppPool\<YourAppPoolName>` by default) has
**read access to `C:\hospital_api`** and **write access to `C:\hospital_api\logs`**
(right-click each folder → Properties → Security → Edit → Add → type the app pool
identity name → grant permissions).

## 4. Test

```powershell
iisreset
```
Browse to `http://localhost/docs`. IIS Manager → **Worker Processes** (server-level
feature) should show a `uvicorn.exe` child process while the site is warm. If it 502s,
check `C:\hospital_api\logs\stdout.log` first — it'll show the uvicorn startup error
(missing `.env`, DB not seeded, port conflict, etc).

## Behavior you get for free

- **Crash recovery**: if uvicorn dies, IIS restarts it automatically on the next request.
- **Idle shutdown / on-demand start**: with `Start Mode: OnDemand` (the IIS default),
  the process only runs while the site has traffic — saves memory on rarely-used sites.
  Use `AlwaysRunning` + Application Initialization instead if you want it always warm.
- **App pool recycling** (scheduled restarts, memory limits) also recycles the uvicorn
  process — configurable under the app pool's **Recycling** settings.

## Result

Browser/client → IIS (port 80/443) → HttpPlatformHandler manages + proxies to →
uvicorn on an IIS-assigned localhost port → FastAPI app. No NSSM, no manually
started uvicorn window, no ARR/URL Rewrite.
