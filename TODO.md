### Helios Backlog (Open Features Only)

- **Core optimization engine**
  - Implement cost-optimization across 24–48h horizon using constraints (battery, grid limits, SoC bounds, reserve)
  - Add time-sliced planning with configurable slice duration and recalc interval validation (recalc <= window)
  - Support explicit buy/sell price formulas (multiplier + fixed fee) applied to raw price inputs
  - Enforce battery operational strategy: min/max SoC and self-consumption reserve vs arbitrage window
  - Add ramping/hysteresis to prevent oscillations and honor minimum dwell times for actions
  - Respect grid import/export caps and battery charge/discharge power limits in planning and execution
  - Enforce configuration invariants at runtime (already applied for recalc <= planning window and SoC bounds)

- **Victron GX (D-Bus) integration**
  - Implement robust D-Bus client: read telemetry (PV, grid, battery, load), write grid setpoint
  - High-frequency control loop publishing setpoints at configured interval; watchdog to maintain control
  - Graceful fallback to standard ESS on pause/stop/error
  - Device/service discovery and retries/backoff for transient D-Bus errors

- **Price provider: Tibber**
  - Fetch day-ahead and real-time hourly prices (timezone-aware)
  - Use raw pre-tax/fee price; apply local buy/sell formulas
  - Caching, rate limiting, and resilience for provider outages

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
  - Manual controls: pause automation, force modes (charge/discharge/idle) with safety guards
  - Authentication/authorization for UI (optional; local-only default)

- **Persistence & storage**
  - Persist configuration (including secrets) securely on device
  - Store time-series telemetry and plan history for analytics and model training
  - Implement `.env`/YAML config writer and load-on-start; avoid returning secrets via API

- **Scheduling & orchestration**
  - Dynamic reschedule when configuration changes
  - Safe startup/shutdown sequences; ensure single-instance control

- **Safety & failsafes**
  - Degraded mode: prioritize self-consumption if price/forecast providers fail
  - Hard limits enforcement and sanity checks before issuing setpoints
  - Alerting hooks for persistent failures (future)

- **Observability**
  - Structured logging with redaction
  - Metrics endpoint (Prometheus) for control loop timing, plan quality, API latency
  - Request/response access logs with sensitive field filtering

- **Packaging & deployment**
  - Build/install instructions for Venus OS (GX): dependencies, service unit/supervisor, autostart
  - Resource footprint optimizations for embedded environment

- **Testing**
  - Unit tests for planner, providers, and config logic
  - Integration tests with simulated D-Bus and recorded traces
  - Scenario-based simulation harness for end-to-end validation
  - Add pytest/coverage and a CI test job

- **Documentation**
  - User guide for configuration and UI
  - Provider setup (Tibber, weather APIs, EV APIs)
  - Architecture and extension points (modular providers, forecast engines)
  - Expand README with quickstart, API docs, and environment variables (added)
