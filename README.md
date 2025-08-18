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
  - For Tibber: set `HELIOS_TIBBER_TOKEN`. The provider performs lightweight caching and retries.
- Executor backend: `HELIOS_EXECUTOR_BACKEND` = `noop` (default) or `dbus` (stub implementation in progress).
- Dwell/hysteresis:
  - `HELIOS_MINIMUM_ACTION_DWELL_SECONDS`: minimum time before switching actions.
  - `HELIOS_PRICE_HYSTERESIS_EUR_PER_KWH`: widening around the pivot price to reduce flapping.

Settings can be updated at runtime via `PUT /config` and are also loadable via environment variables (`HELIOS_` prefix). Secret fields are redacted from `GET /config` responses.