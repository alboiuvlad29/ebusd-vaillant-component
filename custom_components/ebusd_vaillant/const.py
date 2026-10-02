DOMAIN = "ebusd_vaillant"

DEFAULT_MANUFACTURER = "Vaillant"
DEFAULT_AREA = "Heating"

CONF_MQTT_PREFIX = "mqtt_prefix"
DEFAULT_MQTT_PREFIX = "ebusd"

CONF_NAME = "name"
DEFAULT_NAME = "Vaillant"

CONF_AWAY_MODE_DURATION = "away_mode_duration"
DEFAULT_AWAY_MODE_DURATION = 7

CONF_QUICK_VETO_DURATION = "quick_veto_duration"
DEFAULT_QUICK_VETO_DURATION = 3

CONF_QUICK_VETO_TEMP = "quick_veto_temp"
DEFAULT_QUICK_VETO_TEMP = 21.0

CONF_MAX_ZONES = "max_zones"
DEFAULT_MAX_ZONES = 4

CONF_ZONES_WITH_TEMP_ONLY = "zones_with_temp_only"
DEFAULT_ZONES_WITH_TEMP_ONLY = True

CONF_PRIME_VALUES = "prime_poll_values"
DEFAULT_PRIME_VALUES = True

# Whether zones offer cooling (heat/cool target range and the cool HVAC mode).
# "auto" decides from what the system reports (Hc{n}CoolingEnabled, YieldCooling, ...).
CONF_COOLING = "cooling"
COOLING_AUTO = "auto"
COOLING_ENABLED = "enabled"
COOLING_DISABLED = "disabled"
COOLING_OPTIONS = [COOLING_AUTO, COOLING_ENABLED, COOLING_DISABLED]
DEFAULT_COOLING = COOLING_AUTO

# What a zone temperature change writes. "smart": the manual setpoint in manual mode,
# a quick veto otherwise. "quick_veto": always a quick veto (behaviour before 1.1.0).
CONF_TEMPERATURE_WRITE = "temperature_write"
TEMPERATURE_WRITE_SMART = "smart"
TEMPERATURE_WRITE_QUICK_VETO = "quick_veto"
TEMPERATURE_WRITE_OPTIONS = [TEMPERATURE_WRITE_SMART, TEMPERATURE_WRITE_QUICK_VETO]
DEFAULT_TEMPERATURE_WRITE = TEMPERATURE_WRITE_SMART

# Operating mode vocabulary of the controller definitions (Z{n}OpMode, HwcOpMode).
# Older ebusd-configuration files use 0=off;1=auto;2=day;3=night, newer ones
# (TypeSpec-based 15.ctlv2/ctlv3) use 0=off;1=auto;2=manual.
MODE_VOCAB_DAY = "day"
MODE_VOCAB_MANUAL = "manual"

# ebusd → HA HVAC mode (heating zones: Z1OpMode, Z2OpMode, hmu/SetMode.hcmode)
EBUSD_TO_HA_HVAC = {
    "auto": "auto",
    "day": "heat",
    "manual": "heat",
    "night": "cool",
    "off": "off",
    "heat": "heat",
    "cool": "cool",
}
# HA → ebusd HVAC mode, per vocabulary. The manual vocabulary has no night/cool value.
HA_TO_EBUSD_HVAC = {
    MODE_VOCAB_DAY: {
        "auto": "auto",
        "heat": "day",
        "cool": "night",
        "off": "off",
    },
    MODE_VOCAB_MANUAL: {
        "auto": "auto",
        "heat": "manual",
        "off": "off",
    },
}
ZONE_HVAC_MODES = {
    MODE_VOCAB_DAY: ["auto", "heat", "cool", "off"],
    MODE_VOCAB_MANUAL: ["auto", "heat", "off"],
}

# ebusd → HA water heater operation modes (HwcOpMode), per vocabulary
HWC_OPERATION_MODES = {
    MODE_VOCAB_DAY: ["auto", "day", "off"],
    MODE_VOCAB_MANUAL: ["auto", "manual", "off"],
}

# Map RunDataStatuscode values from ebusd/hmu to HA HVAC action.
_STAT_HVAC_ACTION_HEATING = frozenset(
    {
        "heat_compressor_active",
        "heat_prerun",
        "heat_overrun",
        "heat_immersion_heater_active",
    }
)
_STAT_HVAC_ACTION_COOLING = frozenset(
    {
        "cool_compressor_active",
        "cool_prerun",
        "cool_overrun",
    }
)

# Common ebusd device names to probe for discovery priming.
# Sending ?1 to these names is harmless (unknown names are silently ignored).
DISCOVERY_DEVICE_NAMES = ["ctlv3", "ctlv2", "hmu", "bai", "bai00"]

# Human-readable labels for well-known ebusd device IDs, used to build
# device_name for discovered sensors.  Keys are lower-cased device IDs.
# Unknown device IDs fall back to device_id.upper().
DEVICE_TYPE_LABELS: dict[str, str] = {
    "hmu": "Heat Pump",
    "bai": "Boiler",
    "bai00": "Boiler",
    "ctlv2": "Controller",
    "ctlv3": "Controller",
}

# Minimal topic set needed to trigger entity discovery in _analyze().
# Once entities are discovered, full priming kicks in via _prime_values().
_DISCOVERY_TOPICS_HWC = ["HwcOpMode", "HwcTempDesired"]
_DISCOVERY_TOPICS_PRESSURE = ["WaterPressure"]
_DISCOVERY_TOPICS_ZONE = ["Z{n}OpMode", "Z{n}RoomTemp"]
_DISCOVERY_TOPICS_HC = [
    "Hc{n}MinFlowTempDesired",
    "Hc{n}MinCoolTempDesired",
    "Hc{n}MinCoolingTempDesired",
]
