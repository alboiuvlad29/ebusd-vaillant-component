---
hide:
  - toc
---

# Entities

Entities provided by the ebusd Vaillant integration.

| Entity Name | Description | Type | Supported Features |
|---|---|---|---|
| `EbusdClimateEntity` | Climate entity for a heating zone: target temperature, HVAC mode, and boost/away presets. | `climate` | `TURN_ON, TURN_OFF, PRESET_MODE, TARGET_TEMPERATURE, TARGET_TEMPERATURE_RANGE` |
| `EbusdWaterHeaterEntity` | Water heater for a hot water circuit: temperature, operation mode, away mode, and on/off. | `water_heater` | `TARGET_TEMPERATURE, OPERATION_MODE, ON_OFF, AWAY_MODE` |
| `EbusdPressureSensor` | Pressure sensor measuring heating system water pressure in bar. | `sensor` | `--` |
| `EbusdAwayModeSwitch` | **Away** on a zone: away mode (holiday), setting start/end dates on ebusd. | `switch` | `--` |
| `EbusdHwcAwayModeSwitch` | **Away** on the Hot Water device: away mode (holiday) for hot water. | `switch` | `--` |
| `EbusdHwcBoostSwitch` | **Boost** on the Hot Water device: one-time cylinder charge (`HwcSFMode` = `load`); turns off by itself when the charge ends. | `switch` | `--` |
| `EbusdQuickVetoSwitch` | **Heating boost** on a zone: Vaillant's quick veto (boost temperature for N hours). Attributes `boost_temperature`, `boost_duration_hours`, `boost_ends_at`; turns off by itself when the boost ends. | `switch` | `--` |
| `EbusdQuickVetoEndEntity` | **Heating boost end**: when the zone's heating boost (quick veto) ends. | `datetime` | `--` |
| `EbusdHolidayEntity` | Datetime entity for holiday start/end dates on a heating zone or hot water circuit. | `datetime` | `--` |

## Names and entity IDs

Entity names are translated and avoid Vaillant's term "quick veto": the zone switch is
**Heating boost**, the away switches are **Away**, and the hot water boost switch is
**Boost** (shown with the device name, e.g. "Vaillant Zone 1 Heating boost",
"Vaillant Hot Water Boost"). Entity IDs keep their original form
(`switch.<zone>_quick_veto`, `switch.<zone>_away_mode`, `switch.<hot water>_boost`,
`switch.<hot water>_away_mode`, `datetime.<zone>_quick_veto_end`), so automations keep
working. The water heater has a `boost_active` attribute.
