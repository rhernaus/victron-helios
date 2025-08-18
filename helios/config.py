from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator


class HeliosSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="HELIOS_",
        validate_assignment=True,
    )

    # Planning & control cadence
    planning_window_seconds: int = Field(default=900, ge=60)
    recalculation_interval_seconds: int = Field(default=300, ge=30)
    dbus_update_interval_seconds: int = Field(default=10, ge=1)
    scheduler_timezone: str = Field(default="UTC")

    # Pricing adjustments
    buy_price_multiplier: float = Field(default=1.0, ge=0)
    buy_price_fixed_fee_eur_per_kwh: float = Field(default=0.0)
    sell_price_multiplier: float = Field(default=1.0, ge=0)
    sell_price_fixed_deduction_eur_per_kwh: float = Field(default=0.0)
    grid_sell_enabled: bool = Field(default=False)

    # Limits
    grid_import_limit_w: Optional[int] = Field(default=None, ge=0)
    grid_export_limit_w: Optional[int] = Field(default=None, ge=0)

    # Battery characteristics and policy
    battery_capacity_kwh: Optional[float] = Field(default=None, ge=0)
    battery_charge_limit_w: Optional[int] = Field(default=None, ge=0)
    battery_discharge_limit_w: Optional[int] = Field(default=None, ge=0)
    min_soc_percent: float = Field(default=10.0, ge=0, le=100)
    max_soc_percent: float = Field(default=95.0, ge=0, le=100)
    reserve_soc_percent: float = Field(default=40.0, ge=0, le=100)

    # Location & providers
    location_lat: Optional[float] = None
    location_lon: Optional[float] = None
    tibber_token: Optional[str] = None
    openweather_api_key: Optional[str] = None

    def to_public_dict(self) -> dict:
        data = self.model_dump()
        # remove secret material; expose presence booleans instead
        data["tibber_token_present"] = bool(data.get("tibber_token"))
        data["openweather_api_key_present"] = bool(data.get("openweather_api_key"))
        data.pop("tibber_token", None)
        data.pop("openweather_api_key", None)
        return data

    @model_validator(mode="after")
    def _validate_invariants(self) -> "HeliosSettings":
        # Recalc interval must be <= planning window
        if self.recalculation_interval_seconds > self.planning_window_seconds:
            raise ValueError(
                "recalculation_interval_seconds must be <= planning_window_seconds"
            )
        # SoC bounds sanity
        if not (0 <= self.min_soc_percent <= self.max_soc_percent <= 100):
            raise ValueError("SoC bounds must satisfy 0 <= min <= max <= 100")
        if not (self.min_soc_percent <= self.reserve_soc_percent <= self.max_soc_percent):
            raise ValueError(
                "reserve_soc_percent must be between min_soc_percent and max_soc_percent"
            )
        return self


class ConfigUpdate(BaseModel):
    planning_window_seconds: Optional[int] = None
    recalculation_interval_seconds: Optional[int] = None
    dbus_update_interval_seconds: Optional[int] = None
    scheduler_timezone: Optional[str] = None

    buy_price_multiplier: Optional[float] = None
    buy_price_fixed_fee_eur_per_kwh: Optional[float] = None
    sell_price_multiplier: Optional[float] = None
    sell_price_fixed_deduction_eur_per_kwh: Optional[float] = None
    grid_sell_enabled: Optional[bool] = None

    grid_import_limit_w: Optional[int] = None
    grid_export_limit_w: Optional[int] = None

    battery_capacity_kwh: Optional[float] = None
    battery_charge_limit_w: Optional[int] = None
    battery_discharge_limit_w: Optional[int] = None
    min_soc_percent: Optional[float] = None
    max_soc_percent: Optional[float] = None
    reserve_soc_percent: Optional[float] = None

    location_lat: Optional[float] = None
    location_lon: Optional[float] = None
    tibber_token: Optional[str] = None
    openweather_api_key: Optional[str] = None

    def apply_to(self, settings: HeliosSettings) -> HeliosSettings:
        """Return a new validated settings instance with the updates applied atomically."""
        updates = {k: v for k, v in self.model_dump().items() if v is not None}
        # Build a new settings object to avoid transient invalid states during setattr
        current = settings.model_dump()
        current.update(updates)
        new_settings = HeliosSettings.model_validate(current)
        return new_settings
