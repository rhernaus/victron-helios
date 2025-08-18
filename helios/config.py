import json
import os
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional


DEFAULT_DATA_DIR = Path(os.environ.get("HELIOS_DATA_DIR", Path.home() / ".helios"))
DEFAULT_CONFIG_PATH = DEFAULT_DATA_DIR / "config.json"


@dataclass
class PricingConfig:
    tibber_api_token: Optional[str] = None
    buy_price_multiplier: float = 1.0
    buy_price_additive_eur_per_kwh: float = 0.0
    sell_price_multiplier: float = 1.0
    sell_price_subtractive_eur_per_kwh: float = 0.0


@dataclass
class BatteryConfig:
    battery_capacity_kwh: float = 10.0
    max_charge_w: int = 3000
    max_discharge_w: int = 3000
    min_soc_percent: float = 10.0
    max_soc_percent: float = 95.0
    self_consumption_reserve_percent: float = 40.0


@dataclass
class GridConfig:
    grid_feed_in_enabled: bool = True
    grid_import_limit_w: int = 10000
    grid_export_limit_w: int = 10000


@dataclass
class LocationConfig:
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    solcast_api_key: Optional[str] = None
    solcast_site_id: Optional[str] = None


@dataclass
class BehaviorConfig:
    planning_window_seconds: int = 900
    recalculation_interval_seconds: int = 300
    dbus_update_interval_seconds: int = 10
    planning_horizon_hours: int = 36


@dataclass
class AppConfig:
    pricing: PricingConfig = field(default_factory=PricingConfig)
    battery: BatteryConfig = field(default_factory=BatteryConfig)
    grid: GridConfig = field(default_factory=GridConfig)
    location: LocationConfig = field(default_factory=LocationConfig)
    behavior: BehaviorConfig = field(default_factory=BehaviorConfig)


def _ensure_data_dir() -> None:
    DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> AppConfig:
    _ensure_data_dir()
    if DEFAULT_CONFIG_PATH.exists():
        try:
            data = json.loads(DEFAULT_CONFIG_PATH.read_text())
            return _from_dict(data)
        except Exception:
            pass
    cfg = AppConfig()
    save_config(cfg)
    return cfg


def save_config(cfg: AppConfig) -> None:
    _ensure_data_dir()
    DEFAULT_CONFIG_PATH.write_text(json.dumps(_to_dict(cfg), indent=2))


def _to_dict(cfg: AppConfig) -> dict:
    return asdict(cfg)


def _from_dict(data: dict) -> AppConfig:
    pricing = PricingConfig(**data.get("pricing", {}))
    battery = BatteryConfig(**data.get("battery", {}))
    grid = GridConfig(**data.get("grid", {}))
    location = LocationConfig(**data.get("location", {}))
    behavior = BehaviorConfig(**data.get("behavior", {}))
    return AppConfig(
        pricing=pricing,
        battery=battery,
        grid=grid,
        location=location,
        behavior=behavior,
    )

