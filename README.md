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

### Running on Cerbo GX

On a Victron Cerbo GX (Venus OS):

- Install to `/data/helios` with a Python venv under `/data/helios/.venv`
- Persist configuration to `/data/helios/settings.yaml` (or `.env`); secrets are not returned by the API
- Start on boot by appending a line in `/data/rc.local` to launch `uvicorn main:app --host 127.0.0.1 --port 8080`
- Ensure D-Bus write access to `/Settings/CGwacs/AcPowerSetPoint` for ESS control

Note: `uvicorn[standard]` extras are not required on device; plain `uvicorn` is sufficient.