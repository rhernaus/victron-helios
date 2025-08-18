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

### Provider and Executor selection

- Price provider: set `HELIOS_PRICE_PROVIDER` to `stub` (default) or `tibber`.
- Executor backend: set `HELIOS_EXECUTOR_BACKEND` to `noop` (default) or `dbus` (TBD).
- Dwell/hysteresis:
  - `HELIOS_MINIMUM_ACTION_DWELL_SECONDS`: minimum time before switching actions.
  - `HELIOS_PRICE_HYSTERESIS_EUR_PER_KWH`: widening around the pivot price to reduce flapping.