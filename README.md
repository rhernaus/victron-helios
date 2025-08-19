## Overview

Helios is a lightweight controller intended to run on a Victron Cerbo GX (Venus OS). It fetches
dynamic electricity prices, produces a plan, and applies safe grid setpoints via D-Bus to Victron ESS.

Key design points for Cerbo GX:

- Pure-Python runtime; avoids heavy native dependencies
- Uses D-Bus on Venus OS; runs as a single process with low CPU/RAM
- Stores data and configuration under `/data/helios` on the device
- Prioritizes safety: clamps and dwell for setpoint changes; reverts cleanly on pause/errors

## API

Current endpoints:

- `GET /health`: service status
- `GET /config`: returns current config (with secrets redacted)
- `PUT /config`: updates configuration; validates invariants
- `GET /status`: returns automation state and timestamps
- `GET /plan`: returns the latest plan (404 if not ready)
- `POST /pause`: pause automation
- `POST /resume`: resume automation
- `GET /metrics`: Prometheus metrics

### Configuration

- Planning & cadence:
  - `HELIOS_PLANNING_WINDOW_SECONDS`: slot size for the plan (default 900).
  - `HELIOS_PLANNING_HORIZON_HOURS`: planning horizon hours (default 24, 1–48 allowed).
  - `HELIOS_RECALCULATION_INTERVAL_SECONDS`: plan refresh cadence (must be <= window).
  - `HELIOS_DBUS_UPDATE_INTERVAL_SECONDS`: control loop cadence.
- Provider selection:
  - `HELIOS_PRICE_PROVIDER`: `stub` (default) or `tibber`.
  - For Tibber: set `HELIOS_TIBBER_TOKEN` and optionally `HELIOS_TIBBER_HOME_ID`. The
    provider performs lightweight caching, retries, and will be rate-limited.
- Executor backend: `HELIOS_EXECUTOR_BACKEND` = `noop` (default) or `dbus` (stub implementation in progress).
- Dwell/hysteresis:
  - `HELIOS_MINIMUM_ACTION_DWELL_SECONDS`: minimum time before switching actions.
  - `HELIOS_PRICE_HYSTERESIS_EUR_PER_KWH`: widening around the pivot price to reduce flapping.
  - `HELIOS_LOG_LEVEL`: application log level (default `INFO`).

Settings can be updated at runtime via `PUT /config` and are also loadable via environment variables (`HELIOS_` prefix). Secret fields are redacted from `GET /config` responses.

### Settings storage & persistence

- Non‑secret settings are persisted to JSON at `/data/helios/settings.json` when you save via the API or Web UI.
- On startup, Helios loads that file (if present) and overlays it onto defaults and any environment variables.
- The Web UI always shows the live in‑memory configuration from `GET /config`; manual edits to the JSON file will not appear until the server is restarted.
- Secrets (e.g., `tibber_token`, `openweather_api_key`) are not written to disk and are not returned by the API; only `*_present` booleans are exposed.
- To persist secrets across restarts, set environment variables (e.g., `HELIOS_TIBBER_TOKEN`) or place them in a `.env` file in the app directory. YAML config is not used.
- The data directory is configurable via `HELIOS_DATA_DIR` (defaults to `/data/helios`).

### Running on Cerbo GX

Follow these steps on a Victron Cerbo GX (Venus OS):

1) Enable Superuser/Developer mode and SSH on the device UI:

- Settings → General → Access Level: switch to Superuser (long‑press on Access Level)
- Settings → General → Set root password
- Settings → General → Enable SSH on LAN

2) SSH into the device from your computer:

```bash
ssh root@<CERBO_IP>
```

3) Clone the repository to `/data/helios`:

```bash
git clone <REPO_URL> /data/helios
```

Alternative using a working directory:

```bash
git -C /data clone <REPO_URL> helios
```

4) Create and activate a Python virtual environment, then install dependencies:

```bash
python3 -m venv /data/helios/.venv
source /data/helios/.venv/bin/activate
pip install -U pip
pip install -r /data/helios/requirements.txt
```

5) Run the API:

```bash
cd /data/helios
uvicorn main:app --host 127.0.0.1 --port 8080
```

6) Optional: start on boot by appending to `/data/rc.local`:

```sh
. /data/helios/.venv/bin/activate
cd /data/helios
uvicorn main:app --host 127.0.0.1 --port 8080 &
```

- Data and configuration live under `/data/helios` (e.g., `/data/helios/settings.json`).
- Ensure D-Bus write access to `/Settings/CGwacs/AcPowerSetPoint` for ESS control.
- Note: `uvicorn[standard]` extras are not required on device; plain `uvicorn` is sufficient.

### Web UI

- A built-in web interface is served at `/ui`.
- If you bind Uvicorn to all interfaces, you can browse: `http://<CERBO_IP>:8080/ui/`.
- The UI provides:
  - Status and health
  - Plan viewer with auto-refresh
  - Configuration editor (quick fields and full key/value)
  - Pause/Resume controls
  - Metrics viewer and link to raw `/metrics`