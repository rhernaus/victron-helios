from __future__ import annotations


from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class HeliosSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="HELIOS_",
        validate_assignment=True,
    )

    # Planning & control cadence
    planning_window_seconds: int = Field(default=900, ge=60)
    planning_horizon_hours: int = Field(default=24, ge=1, le=48)
    recalculation_interval_seconds: int = Field(default=300, ge=30)
    dbus_update_interval_seconds: int = Field(default=10, ge=1)
    telemetry_update_interval_seconds: int = Field(default=10, ge=1)
    counters_update_interval_seconds: int = Field(default=60, ge=5)
    scheduler_timezone: str = Field(default="UTC")
    minimum_action_dwell_seconds: int = Field(default=0, ge=0)
    # Optional per-action dwell seconds (fallback to minimum_action_dwell_seconds if unset)
    dwell_seconds_charge_from_grid: int | None = Field(default=None, ge=0)
    dwell_seconds_discharge_to_load: int | None = Field(default=None, ge=0)
    dwell_seconds_export_to_grid: int | None = Field(default=None, ge=0)
    dwell_seconds_idle: int | None = Field(default=None, ge=0)
    log_level: str = Field(default="INFO")
    data_dir: str = Field(default="/data/helios")

    # Pricing adjustments
    price_provider: str = Field(default="stub")  # options: stub, tibber
    price_hysteresis_eur_per_kwh: float = Field(default=0.02, ge=0)
    buy_price_multiplier: float = Field(default=1.0, ge=0)
    buy_price_fixed_fee_eur_per_kwh: float = Field(default=0.0)
    sell_price_multiplier: float = Field(default=1.0, ge=0)
    sell_price_fixed_deduction_eur_per_kwh: float = Field(default=0.0)
    grid_sell_enabled: bool = Field(default=False)

    # Limits
    grid_import_limit_w: int | None = Field(default=None, ge=0)
    grid_export_limit_w: int | None = Field(default=None, ge=0)
    # Optional ramping to avoid abrupt steps (Watts per second)
    grid_ramp_w_per_second: int | None = Field(default=None, ge=1)

    # Battery characteristics and policy
    battery_capacity_kwh: float | None = Field(default=None, ge=0)
    battery_charge_limit_w: int | None = Field(default=None, ge=0)
    battery_discharge_limit_w: int | None = Field(default=None, ge=0)
    battery_roundtrip_efficiency_percent: float = Field(default=90.0, ge=0, le=100)
    battery_cycle_cost_eur_per_kwh: float = Field(
        default=0.02,
        description="Estimated battery degradation cost per kWh throughput",
        ge=0,
    )
    min_soc_percent: float = Field(default=10.0, ge=0, le=100)
    max_soc_percent: float = Field(default=95.0, ge=0, le=100)
    reserve_soc_percent: float = Field(default=40.0, ge=0, le=100)
    # Best-effort assumed current SoC for simple policy heuristics (percent)
    assumed_current_soc_percent: float | None = None

    # Location & providers
    location_lat: float | None = None
    location_lon: float | None = None
    tibber_token: str | None = None
    tibber_home_id: str | None = None
    openweather_api_key: str | None = None
    pv_peak_watts: float | None = Field(default=4000.0, ge=0)

    # Execution (write)
    executor_backend: str = Field(default="noop")  # options: noop, dbus
    # Telemetry (read)
    telemetry_backend: str = Field(default="noop")  # options: noop, dbus
    # D-Bus reliability parameters
    dbus_reassert_attempts: int = Field(default=2, ge=0)
    dbus_write_retries: int = Field(default=2, ge=0)
    dbus_write_retry_delay_seconds: float = Field(default=0.2, ge=0)

    def to_public_dict(self) -> dict:
        data = self.model_dump()
        # remove secret material; expose presence booleans instead
        data["tibber_token_present"] = bool(data.get("tibber_token"))
        data["openweather_api_key_present"] = bool(data.get("openweather_api_key"))
        data.pop("tibber_token", None)
        data.pop("openweather_api_key", None)
        return data

    def persist_to_disk(self) -> None:
        import json
        import os

        os.makedirs(self.data_dir, exist_ok=True)
        path = os.path.join(self.data_dir, "settings.json")
        # Persist a sanitized copy with secrets omitted. Do not include
        # derived "*_present" booleans which are intended for API display only.
        data = dict(self.to_public_dict())
        data.pop("tibber_token_present", None)
        data.pop("openweather_api_key_present", None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)

    @staticmethod
    def load_from_disk(data_dir: str) -> dict | None:
        import json
        import os

        path = os.path.join(data_dir, "settings.json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        # Drop any unknown/derived keys to keep forward/backward compatibility.
        try:
            valid_keys = set(HeliosSettings.model_fields.keys())
        except Exception:
            valid_keys = set()
        sanitized = {k: v for k, v in raw.items() if k in valid_keys}
        return sanitized

    @model_validator(mode="after")
    def _validate_invariants(self) -> HeliosSettings:
        # Recalc interval must be <= planning window
        if self.recalculation_interval_seconds > self.planning_window_seconds:
            raise ValueError("recalculation_interval_seconds must be <= planning_window_seconds")
        # SoC bounds sanity
        if not (0 <= self.min_soc_percent <= self.max_soc_percent <= 100):
            raise ValueError("SoC bounds must satisfy 0 <= min <= max <= 100")
        if not (self.min_soc_percent <= self.reserve_soc_percent <= self.max_soc_percent):
            raise ValueError(
                "reserve_soc_percent must be between min_soc_percent and max_soc_percent"
            )
        return self


class ConfigUpdate(BaseModel):
    planning_window_seconds: int | None = None
    planning_horizon_hours: int | None = None
    recalculation_interval_seconds: int | None = None
    dbus_update_interval_seconds: int | None = None
    telemetry_update_interval_seconds: int | None = None
    counters_update_interval_seconds: int | None = None
    scheduler_timezone: str | None = None
    minimum_action_dwell_seconds: int | None = None
    dwell_seconds_charge_from_grid: int | None = None
    dwell_seconds_discharge_to_load: int | None = None
    dwell_seconds_export_to_grid: int | None = None
    dwell_seconds_idle: int | None = None

    price_provider: str | None = None
    price_hysteresis_eur_per_kwh: float | None = None
    buy_price_multiplier: float | None = None
    buy_price_fixed_fee_eur_per_kwh: float | None = None
    sell_price_multiplier: float | None = None
    sell_price_fixed_deduction_eur_per_kwh: float | None = None
    grid_sell_enabled: bool | None = None

    grid_import_limit_w: int | None = None
    grid_export_limit_w: int | None = None
    grid_ramp_w_per_second: int | None = None

    battery_capacity_kwh: float | None = None
    battery_charge_limit_w: int | None = None
    battery_discharge_limit_w: int | None = None
    battery_roundtrip_efficiency_percent: float | None = None
    battery_cycle_cost_eur_per_kwh: float | None = None
    min_soc_percent: float | None = None
    max_soc_percent: float | None = None
    reserve_soc_percent: float | None = None
    assumed_current_soc_percent: float | None = None

    location_lat: float | None = None
    location_lon: float | None = None
    tibber_token: str | None = None
    tibber_home_id: str | None = None
    openweather_api_key: str | None = None
    pv_peak_watts: float | None = None
    executor_backend: str | None = None
    telemetry_backend: str | None = None
    dbus_reassert_attempts: int | None = None
    dbus_write_retries: int | None = None
    dbus_write_retry_delay_seconds: float | None = None

    def apply_to(self, settings: HeliosSettings) -> HeliosSettings:
        """Return a new validated settings instance with the updates applied atomically."""
        updates = {k: v for k, v in self.model_dump().items() if v is not None}
        # Build a new settings object to avoid transient invalid states during setattr
        current = settings.model_dump()
        current.update(updates)
        new_settings = HeliosSettings.model_validate(current)
        return new_settings
