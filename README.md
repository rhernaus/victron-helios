# Helios: Advanced Dynamic Energy Management for Victron GX

Helios is a highly configurable and intelligent energy management system for Victron GX devices (Venus OS). It aims to minimize electricity costs and maximize self-consumption by planning ahead and continuously optimizing actions based on real-time energy prices, predicted solar generation, and forecasted household consumption. Helios is designed as a more powerful and flexible alternative to standard Dynamic ESS functionality.

Status: Pre-alpha. This repository currently contains the project plan and architecture outline; implementation is in progress.

## 1. Project Vision & Core Objective

To develop a modular, reliable, and transparent control system that:
- Optimizes home energy cost and carbon impact.
- Maximizes self-consumption without compromising resilience.
- Intelligently arbitrages energy prices while respecting user constraints.
- Integrates smoothly with Victron GX (Venus OS) using D-Bus.

## 2. Core Logic & Optimization Engine

### Planning & Recalculation Cycle
- Generates a new 24-48 hour operational plan at a default interval of 300 seconds.
- The recalculation interval is user-configurable and must be less than or equal to the planning window.

### Time-Sliced Planning Window
- Plans are built using discrete time windows (default: 900 seconds) to evaluate predictions and schedule actions.

### Control & Execution Loop
- Sends control commands (for example, grid setpoint) to Victron D-Bus at a high frequency to maintain active control and avoid fallback to default ESS.
- Default D-Bus update interval: 10 seconds (user-configurable).

### Decision Making
Helios executes the most cost-effective action for the current window, such as:
- Charging the battery from the grid during low-price periods.
- Discharging the battery to power the home during high-price periods.
- Selling excess battery or solar energy to the grid when profitable and allowed.
- Prioritizing solar for direct home/EV use, then battery charging, then grid export.
- Intelligently scheduling EV charging.

## 3. Data Sources & Integrations (Inputs)

### Energy Pricing
- Primary provider: Tibber API (day-ahead and real-time hourly prices).
- Data requirement: Retrieve the raw, pre-tax/fee energy price as baseline.

### Solar Production Forecasting
- Methodology: Predictive model trained on historical Victron PV data.
- External data enhancement: Weather/solar forecast API (for example, Solcast, OpenWeatherMap) using system geographic coordinates for 24-48 hour forecast.
- Location: Attempt to retrieve GPS coordinates from the Victron GX device; allow manual entry if unavailable.

### Energy Consumption Forecasting
- Methodology: Model that learns household baseload and temporal patterns (daily peaks, weekday/weekend variance) from historical Victron data to predict demand.

### Electric Vehicle (EV) Integration
- Objective: Treat EV charging as a deferrable load, scheduling for low-cost or high-solar periods.
- Supported platforms (initial targets):
  - Tesla Vehicle API
  - Kia Connect / Hyundai Blue Link API
- Data requirement: Read SoC, charging status, and location (to confirm at-home charging); start/stop charging remotely.

### EV Charger (EVSE) Integration
- Primary support: Control a custom-integrated Alfen charger via the Victron GX interface.

## 4. System Configuration & User Controls (Web Interface)

Helios exposes a local web UI for configuration and visibility.

### Grid & Pricing Configuration
- Buy price calculation: Allow a formula such as `(TibberPrice * multiplier) + fixedFee`.
- Sell price calculation: Allow deductions or separate rates for export.
- Grid feed-in: Master toggle to enable/disable selling energy to the grid.
- Grid power limits: Configure max import/export power (in Amps or Watts).

### Battery Configuration
- Physical parameters:
  - Total capacity (kWh)
  - Maximum charge power (A or W)
  - Maximum discharge power (A or W)
- Operational strategy:
  - Minimum SoC (for example, 10%) for resilience/outages
  - Maximum SoC (for example, 95%) to retain solar buffer
  - Self-Consumption Reserve (for example, 40%): capacity below this level powers the home; capacity above is the "Arbitrage Window".
- Financials (optional but recommended): Battery system cost for ROI analysis.

### System Behavior
- Planning window duration (default: 900 seconds).
- Recalculation interval (default: 300 seconds).
- D-Bus update interval (default: 10 seconds).

## 5. Expanded Features & Considerations

### Dashboard & Analytics
- Live status: grid price, solar production, battery SoC, and current action (for example, "Charging from Grid - Low Price").
- Planned schedule visualization for the next 24 hours.
- Historical savings and performance tracking.

### Manual Override & Failsafes
- "Pause Automation" to temporarily revert to standard ESS mode.
- Robust error handling and degraded modes (for example, fall back to self-consumption priority if Tibber is unreachable).

### Modularity
- Pluggable architecture for price providers, weather services, EV brands, and EVSE integrations.

---

## Architecture (Planned)

- Core services:
  - Planner and Optimizer: Builds rolling 24-48h plan using forecasts and constraints.
  - Executor: Applies setpoints to D-Bus at the configured control frequency.
  - Forecast Engines: Solar and demand prediction using historical and external data.
  - Integrations: Tibber pricing, weather/solar APIs, EV APIs, GX D-Bus client, EVSE control.
  - Web UI & API: Configuration, telemetry, and observability.
  - Storage: Time-series and configuration persistence (implementation TBD).
- Principles:
  - Deterministic, auditable decisions with clear inputs/outputs.
  - Safety guards for SoC, grid limits, and device constraints.
  - Testability via simulation and recorded traces.

## Getting Started (Development)

Implementation is in progress. A development guide will be added when initial modules land. Planned first milestones:
1. D-Bus client scaffold and telemetry ingestion
2. Tibber price provider integration
3. Basic planning loop with time slicing and setpoint output
4. Web UI skeleton for configuration and live status
5. Forecast stubs (solar and demand) with pluggable providers
6. Tesla EV integration (read SoC, start/stop charging)
7. Persistence and metrics
8. Safety, simulation, and unit/integration tests

## Configuration Overview (Preview)

- Credentials: Tibber token, weather API key(s), EV API credentials stored securely (TBD).
- Location: Auto from GX if available; manual override supported.
- Pricing formulas:
  - Buy example: `(TibberPriceRaw * 1.21) + 0.15 €/kWh`
  - Sell example: `(TibberPriceRaw * 0.85) - 0.02 €/kWh`
- Limits: Grid import/export caps; battery charge/discharge caps; SoC min/max; reserve SoC.

## Security & Privacy

- Store API tokens and credentials securely; never log secrets.
- Prefer local-only control paths; cloud only where required by vendor APIs.
- Provide clear data retention and export options (TBD).

## Contributing

Contributions are welcome once the initial structure is published:
- Open an issue to discuss sizeable changes.
- Follow clean code practices, tests, and documentation updates.
- Keep modules decoupled and integrations pluggable.

## Disclaimer

This project is not affiliated with or endorsed by Victron Energy, Tibber, Tesla, Kia/Hyundai, or any referenced providers. Use at your own risk. Ensure compliance with local regulations and utility/contract terms, especially regarding grid export and EV control.

## License

TBD.

---

## Quick Glossary
- ESS: Energy Storage System
- SoC: State of Charge
- D-Bus: Message bus used by Victron GX/Venus OS for device communication
- EVSE: Electric Vehicle Supply Equipment (charger)
