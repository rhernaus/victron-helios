from __future__ import annotations

from typing import Optional

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
	scheduler_timezone: str = Field(default="UTC")
	minimum_action_dwell_seconds: int = Field(default=0, ge=0)
	# Optional per-action dwell seconds (fallback to minimum_action_dwell_seconds if unset)
	dwell_seconds_charge_from_grid: Optional[int] = Field(default=None, ge=0)
	dwell_seconds_discharge_to_load: Optional[int] = Field(default=None, ge=0)
	dwell_seconds_export_to_grid: Optional[int] = Field(default=None, ge=0)
	dwell_seconds_idle: Optional[int] = Field(default=None, ge=0)
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
	grid_import_limit_w: Optional[int] = Field(default=None, ge=0)
	grid_export_limit_w: Optional[int] = Field(default=None, ge=0)
	# Optional ramping to avoid abrupt steps (Watts per second)
	grid_ramp_w_per_second: Optional[int] = Field(default=None, ge=1)

	# Battery characteristics and policy
	battery_capacity_kwh: Optional[float] = Field(default=None, ge=0)
	battery_charge_limit_w: Optional[int] = Field(default=None, ge=0)
	battery_discharge_limit_w: Optional[int] = Field(default=None, ge=0)
	min_soc_percent: float = Field(default=10.0, ge=0, le=100)
	max_soc_percent: float = Field(default=95.0, ge=0, le=100)
	reserve_soc_percent: float = Field(default=40.0, ge=0, le=100)
	# Best-effort assumed current SoC for simple policy heuristics (percent)
	assumed_current_soc_percent: Optional[float] = None

	# Location & providers
	location_lat: Optional[float] = None
	location_lon: Optional[float] = None
	tibber_token: Optional[str] = None
	tibber_home_id: Optional[str] = None
	openweather_api_key: Optional[str] = None

	# Execution
	executor_backend: str = Field(default="noop")  # options: noop, dbus
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
		# Persist a sanitized copy with secrets omitted
		with open(path, "w", encoding="utf-8") as f:
			json.dump(self.to_public_dict(), f, indent=2, sort_keys=True)

	@staticmethod
	def load_from_disk(data_dir: str) -> dict | None:
		import json
		import os

		path = os.path.join(data_dir, "settings.json")
		if not os.path.exists(path):
			return None
		with open(path, encoding="utf-8") as f:
			return json.load(f)

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
	planning_window_seconds: Optional[int] = None
	planning_horizon_hours: Optional[int] = None
	recalculation_interval_seconds: Optional[int] = None
	dbus_update_interval_seconds: Optional[int] = None
	scheduler_timezone: Optional[str] = None
	minimum_action_dwell_seconds: Optional[int] = None
	dwell_seconds_charge_from_grid: Optional[int] = None
	dwell_seconds_discharge_to_load: Optional[int] = None
	dwell_seconds_export_to_grid: Optional[int] = None
	dwell_seconds_idle: Optional[int] = None

	price_provider: Optional[str] = None
	price_hysteresis_eur_per_kwh: Optional[float] = None
	buy_price_multiplier: Optional[float] = None
	buy_price_fixed_fee_eur_per_kwh: Optional[float] = None
	sell_price_multiplier: Optional[float] = None
	sell_price_fixed_deduction_eur_per_kwh: Optional[float] = None
	grid_sell_enabled: Optional[bool] = None

	grid_import_limit_w: Optional[int] = None
	grid_export_limit_w: Optional[int] = None
	grid_ramp_w_per_second: Optional[int] = None

	battery_capacity_kwh: Optional[float] = None
	battery_charge_limit_w: Optional[int] = None
	battery_discharge_limit_w: Optional[int] = None
	min_soc_percent: Optional[float] = None
	max_soc_percent: Optional[float] = None
	reserve_soc_percent: Optional[float] = None
	assumed_current_soc_percent: Optional[float] = None

	location_lat: Optional[float] = None
	location_lon: Optional[float] = None
	tibber_token: Optional[str] = None
	tibber_home_id: Optional[str] = None
	openweather_api_key: Optional[str] = None
	executor_backend: Optional[str] = None
	dbus_reassert_attempts: Optional[int] = None
	dbus_write_retries: Optional[int] = None
	dbus_write_retry_delay_seconds: Optional[float] = None

	def apply_to(self, settings: HeliosSettings) -> HeliosSettings:
		"""Return a new validated settings instance with the updates applied atomically."""
		updates = {k: v for k, v in self.model_dump().items() if v is not None}
		# Build a new settings object to avoid transient invalid states during setattr
		current = settings.model_dump()
		current.update(updates)
		new_settings = HeliosSettings.model_validate(current)
		return new_settings