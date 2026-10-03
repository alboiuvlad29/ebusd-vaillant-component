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

# Legacy (before 1.11.0): show hot water boost as a fourth operation mode on the water
# heater. Boost now lives on the Boost switch and button; this option will be removed.
CONF_HWC_BOOST_AS_MODE = "hot_water_boost_as_mode"
DEFAULT_HWC_BOOST_AS_MODE = False

# Let the installer settings (hot water hysteresis, charge time, eco parameters, ...) be
# written from Home Assistant. Off: they are shown as read-only diagnostic sensors.
CONF_ALLOW_INSTALLER = "allow_installer_settings"
DEFAULT_ALLOW_INSTALLER = False

# Pressure below which the Low pressure binary sensor turns on (bar).
CONF_LOW_PRESSURE = "low_pressure_threshold"
DEFAULT_LOW_PRESSURE = 1.5

# Legacy on/off option (before 1.5.0); read when poll_priming is not set.
CONF_PRIME_VALUES = "prime_poll_values"
DEFAULT_PRIME_VALUES = True

# How the integration asks ebusd to poll the values it uses ("?1" fast, "?5" slow).
CONF_POLL_PRIMING = "poll_priming"
POLL_PRIMING_ESSENTIALS = "essentials"  # essentials at ?1, everything else at ?5
POLL_PRIMING_ALL = "all"  # everything at ?1 (prime_poll_values on)
POLL_PRIMING_OFF = "off"  # nothing (prime_poll_values off)
POLL_PRIMING_OPTIONS = [POLL_PRIMING_ESSENTIALS, POLL_PRIMING_ALL, POLL_PRIMING_OFF]
DEFAULT_POLL_PRIMING = POLL_PRIMING_ESSENTIALS
PRIORITY_FAST = "?1"
PRIORITY_SLOW = "?5"

# Sensor topics primed fast in "essentials" mode; other sensors are primed slow.
ESSENTIAL_SENSOR_TOPICS = frozenset(
    {
        "PowerConsumptionHmu",
        "CurrentConsumedPower",
        "CurrentYieldPower",
        "YieldHcDay",
        "YieldHwcDay",
        "YieldCoolDay",
    }
)


def poll_priming(options) -> str:
    """The poll priming mode, honouring the legacy prime_poll_values on/off option."""
    if CONF_POLL_PRIMING in options:
        return options[CONF_POLL_PRIMING]
    if CONF_PRIME_VALUES in options:
        return POLL_PRIMING_ALL if options[CONF_PRIME_VALUES] else POLL_PRIMING_OFF
    return DEFAULT_POLL_PRIMING


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
OLD_DEFINITIONS_URL = "https://github.com/john30/ebusd-configuration"

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

# Heat pump fault history: hmu FaultHistory0 (newest) .. FaultHistory9
FAULT_HISTORY_SLOTS = 10
FAULT_EVENT = "ebusd_vaillant_fault"
