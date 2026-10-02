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

## Health (disabled by default)

These entities are created disabled; enable them in the entity settings if you want them.

| Entity | Source | Notes |
|---|---|---|
| **Current error** (per device, e.g. heat pump, controller) | `Currenterror` (`error` .. `error_4`, null = none) | State `none` or the codes, attribute `codes`. While a code is present, a Repairs issue is shown. |
| **Low pressure** (system device) | `hmu Status07.displaypressure` (every ~4 s), else the pressure sensor; `Status07.heatermain_b5_pressureloss` | On below the `low_pressure_threshold` option (default 1.5 bar) or when the heat pump reports a pressure loss. 0 bar counts as no reading. |
| **eBUS connected** (system device) | `ebusd/global/running`, `ebusd/global/signal` | Off when ebusd stops or loses the bus signal. |

When no other pressure message exists, a Water Pressure sensor is created from
`hmu Status07.displaypressure`.

## Operating mode and energy split (disabled by default)

| Entity | Notes |
|---|---|
| **Operating mode** (heat pump device) | `heating`, `cooling`, `hot_water`, `defrost` or `idle`, from the same signals as the climate HVAC action (see [MQTT Mapping](mapping.md)). |
| **Electrical energy heating** / **hot water** / **standby** (kWh) | The heat pump's electrical power input (`PowerConsumptionHmu` in kW, or `RunDataElectricPowerConsumption` in W) integrated over time and booked to the operating mode at that moment. Heating includes cooling and defrost; standby is idle. Gaps over 30 minutes between samples (e.g. ebusd offline) are skipped. Values survive restarts and can be used in the Energy dashboard. |

These are new entities; existing template or Riemann helpers built by hand keep working.

## Zone extras

Created per zone when the controller publishes them (newer ebusd definitions):

| Entity | Source | Notes |
|---|---|---|
| **Effective target temperature** | `Z{n}TempDesired` | The target the controller is aiming for right now, including the schedule and a heating boost. The climate entity shows it as its target in Time controlled mode. |
| **Setback temperature** | `Z{n}SetbackTemp` | The reduced temperature outside time slots. |
| **Time slot active** | `Z{n}TimeSlotActive` | On while a schedule time slot is active. |
