### Helios Backlog (Open Features Only)

- **Core optimization engine**
  - Implement cost-optimization across 24–48h horizon using constraints (battery, grid limits, SoC bounds, reserve)
  - Support explicit buy/sell price formulas (multiplier + fixed fee) applied to raw price inputs
  - Enforce battery operational strategy: min/max SoC and self-consumption reserve vs arbitrage window
  - Extend hysteresis/dwell (configurable thresholds per action) and add power ramping
  - Respect grid import/export caps and battery charge/discharge power limits in planning and execution
  

- **Victron GX (D-Bus) integration**
  - Implement robust D-Bus client:
    - Write ESS grid setpoint at `/Settings/CGwacs/AcPowerSetPoint` (clamped to configured limits)
    - Read telemetry (PV, grid, battery, load) from `com.victronenergy.system`
  - Control loop with watchdog:
    - Publish setpoints at configured cadence; re-assert with bounded retries
    - On pause/stop/error, set grid setpoint to 0 and stop writing; revert to standard ESS behavior
  - Service discovery and retries/backoff for transient D-Bus errors
  - Executor backend selection via `HELIOS_EXECUTOR_BACKEND`; implement D-Bus executor with failsafe behaviors
  - Add metrics for apply latency/success/failure; misfires

- **Price provider: Tibber**
  - Fetch day-ahead and real-time hourly prices (timezone-aware)
  - Use raw pre-tax/fee price; apply local buy/sell formulas
  - Caching, rate limiting, and resilience for provider outages
    - Basic caching and retries implemented; extend with token-bucket rate limiting and proper home/timezone selection
  - Provider selection via `HELIOS_PRICE_PROVIDER`; `HELIOS_TIBBER_HOME_ID` selection and timezone handling for Tibber

- **Forecasting**
  - Solar production forecast:
    - [DONE] Basic external forecast via OpenWeather One Call 3.0 (clouds → PV) using `HELIOS_OPENWEATHER_API_KEY` and location
    - Learn from historical GX PV data; blend with external forecast
    - Clear-sky model and plane-of-array correction for improved accuracy
  - Household consumption forecast:
    - Learn baseload + daily/weekly patterns from historical GX data stored in local SQLite
    - Add model training job and persistence

- **EV integrations**
  - Tesla API: read SoC/charging status/location; start/stop charging; geofence home
  - Kia Connect / Hyundai Blue Link: same capabilities as above
  - Scheduling policy for EV charging (target SoC by time/departure, price-aware, solar surplus-aware)

- **EVSE control (Alfen via GX)**
  - Read charger status and set allowable current/power
  - Align charger control with plan and EV constraints

- **Web UI (local)**
  - Configuration forms: grid/pricing formulas, grid limits, battery params, SoC bounds/reserve, EV/EVSE settings, location
  - Dashboard: live status (grid price, solar, battery SoC, current action), and 24h plan visualization
  - Analytics: historical savings, ROI (optional battery cost), export graphs
  - Manual controls: force modes (charge/discharge/idle) with safety guards; pause/resume implemented
  - Authentication/authorization for UI (optional; local-only default)
  - [DONE] Plan charts: energy prices, energy management (stacked flows), costs & savings
  - [DONE] Hover tooltips and axes labels

- **Persistence & storage**
  - Persist configuration (including secrets) securely on device
  - [DONE] Store time-series telemetry (SQLite) for analytics and model training
  - Export plan data via API for offline analysis (`GET /export`)
  - Implement `.env`/YAML config writer under `/data/helios` and load-on-start; avoid returning secrets via API
  - Ensure secure file permissions for secrets at rest

- **Scheduling & orchestration**
  - Safe startup/shutdown sequences; ensure single-instance control
  - Rescheduling on `/config` updates implemented; validate overlapping job behavior
  - Add jitter and misfire grace to interval schedules; metrics for job runs/misfires

- **Safety & failsafes**
  - Degraded mode: prioritize self-consumption if price/forecast providers fail
  - Hard limits enforcement and sanity checks before issuing setpoints
  - Alerting hooks for persistent failures (future)

- **Observability**
  - Structured logging with redaction
  - Duration histograms for planning/control; error counters; Prometheus metrics extensions
  - Request/response access logs with sensitive field filtering
  - Add metrics for provider requests, executor apply time/failures, plan age, scheduler runs/misfires

- **Packaging & deployment**
  - Build/install instructions for Venus OS (GX): dependencies, venv under `/data/helios`, rc.local autostart
  - Resource footprint optimizations for embedded environment; avoid compiled extras

- **Testing**
  - Unit tests for planner, providers, and config logic
    - Added: planner horizon, API wiring, Tibber caching
  - Integration tests with simulated D-Bus and recorded traces
  - Scenario-based simulation harness for end-to-end validation
  - Tests for dwell/hysteresis behavior and thresholds
  - Provider adapter tests (Tibber happy-path/error-path, timezone handling)
  - Executor behavior tests (including dwell), DST/timezone boundary tests
  - Simulated D-Bus integration tests
  - Tests for provider swap preservation of cache across config updates

- **Documentation**
  - User guide for configuration and UI
  - Provider setup (Tibber, OpenWeather One Call), EV APIs
  - Architecture and extension points (modular providers, forecast engines)
  - Cerbo GX setup guide: D-Bus permissions, `/data` layout, startup via `/data/rc.local`
  - [DONE] README updated for configuration options and new endpoints/charts

- **Developer experience & CI**
  - Pre-commit hooks for ruff and black
  - Keep CI checks for ruff, black, mypy, bandit; fast-fail on lint/type errors
  - Add CI job running tests with dbus-sim mode