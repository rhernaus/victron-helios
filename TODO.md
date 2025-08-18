### Helios Backlog (Open Features Only)

- **Core optimization engine**
  - Implement cost-optimization across 24–48h horizon using constraints (battery, grid limits, SoC bounds, reserve)
  - Support explicit buy/sell price formulas (multiplier + fixed fee) applied to raw price inputs
  - Enforce battery operational strategy: min/max SoC and self-consumption reserve vs arbitrage window
  - Extend hysteresis/dwell (configurable thresholds per action) and add power ramping
  - Respect grid import/export caps and battery charge/discharge power limits in planning and execution
  

- **Victron GX (D-Bus) integration**
  - Implement robust D-Bus client: read telemetry (PV, grid, battery, load), write grid setpoint
  - High-frequency control loop publishing setpoints at configured interval; watchdog to maintain control
  - Graceful fallback to standard ESS on pause/stop/error
  - Device/service discovery and retries/backoff for transient D-Bus errors
  - Executor backend selection via `HELIOS_EXECUTOR_BACKEND`; implement D-Bus executor; watchdog/failsafe behaviors
    - Stub `DbusExecutor` is wired; replace with real D-Bus operations and watchdog

- **Price provider: Tibber**
  - Fetch day-ahead and real-time hourly prices (timezone-aware)
  - Use raw pre-tax/fee price; apply local buy/sell formulas
  - Caching, rate limiting, and resilience for provider outages
    - Basic caching and retries implemented; extend with rate limiting and proper home/timezone selection
  - Provider selection via `HELIOS_PRICE_PROVIDER`; home selection and timezone handling for Tibber

- **Forecasting**
  - Solar production forecast:
    - Learn from historical GX PV data
    - Optional external forecast integration (Solcast or OpenWeatherMap)
    - Auto-detect location from GX; allow manual lat/lon override
  - Household consumption forecast:
    - Learn baseload + daily/weekly patterns from historical GX data

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

- **Persistence & storage**
  - Persist configuration (including secrets) securely on device
  - Store time-series telemetry and plan history for analytics and model training
  - Implement `.env`/YAML config writer and load-on-start; avoid returning secrets via API
  - Ensure secure file permissions for secrets at rest

- **Scheduling & orchestration**
  - Safe startup/shutdown sequences; ensure single-instance control
  - Rescheduling on `/config` updates implemented; validate overlapping job behavior

- **Safety & failsafes**
  - Degraded mode: prioritize self-consumption if price/forecast providers fail
  - Hard limits enforcement and sanity checks before issuing setpoints
  - Alerting hooks for persistent failures (future)

- **Observability**
  - Structured logging with redaction
  - Duration histograms for planning/control; error counters; Prometheus metrics extensions
  - Request/response access logs with sensitive field filtering

- **Packaging & deployment**
  - Build/install instructions for Venus OS (GX): dependencies, service unit/supervisor, autostart
  - Resource footprint optimizations for embedded environment

- **Testing**
  - Unit tests for planner, providers, and config logic
    - Added: planner horizon, API wiring, Tibber caching
  - Integration tests with simulated D-Bus and recorded traces
  - Scenario-based simulation harness for end-to-end validation
  - Tests for dwell/hysteresis behavior and thresholds
  - Provider adapter tests (Tibber happy-path/error-path, timezone handling)
  - Executor behavior tests (including dwell), DST/timezone boundary tests
  - Simulated D-Bus integration tests

- **Documentation**
  - User guide for configuration and UI
  - Provider setup (Tibber, weather APIs, EV APIs)
  - Architecture and extension points (modular providers, forecast engines)
  - README updated for configuration options (planning horizon, provider/executor selection)

- **Developer experience & CI**
  - Pre-commit hooks for ruff and black
  - Keep CI checks for ruff, black, mypy, bandit; fast-fail on lint/type errors