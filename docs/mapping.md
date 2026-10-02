# MQTT Entity Mapping

This page documents every MQTT topic the component subscribes to and how it
maps to Home Assistant entities and controls.

## Topic structure

All topics follow the same pattern:

```
{prefix}/{device_id}/{message_name}       # read (subscribed)
{prefix}/{device_id}/{message_name}/set   # write (published)
```

The default prefix is `ebusd`. The `device_id` is the ebusd device identifier
(e.g. `700` for a boiler). Each message payload is JSON with
dot-notation fields, typically `value.value`.

## HVAC mode translation

The component translates between ebusd operating mode values and Home
Assistant HVAC modes:

| ebusd value | HA HVAC mode |
|---|---|
| `auto` | `auto` |
| `day` | `heat` |
| `manual` | `heat` |
| `night` | `cool` |
| `heat` | `heat` |
| `cool` | `cool` |
| `off` | `off` |

For water heaters, the raw ebusd operation mode values (`auto`, `day`, `off`)
are passed through directly.  When `HwcSFMode` is available, an additional
`boost` mode is added to the operation list.

### Displayed mode names

Modes are shown with the names the sensoCOMFORT uses. The underlying values are unchanged:

| ebusd value | Climate (HVAC mode) | Water heater | Shown as |
|---|---|---|---|
| `auto` | `auto` | `auto` | Time controlled |
| `manual` / `day` | `heat` | `manual` / `day` | Manual |
| `off` | `off` | `off` | Off |
| `HwcSFMode` = `load` | | `boost` | Boost |

### Mode vocabulary

Older ebusd-configuration controller files define `Z{n}OpMode` and `HwcOpMode`
as `off`/`auto`/`day`/`night`; the newer TypeSpec-based `15.ctlv2`/`15.ctlv3`
files use `off`/`auto`/`manual` and rename `Z{n}DayTemp` to `Z{n}ManualTemp`.
The component detects the vocabulary per controller and writes the matching value:

- an OpMode value of `manual` selects the new vocabulary, `day` or `night` the old one;
- with only `auto`/`off` seen, `Z{n}ManualTemp` without `Z{n}DayTemp` selects the new one;
- otherwise the old vocabulary is assumed.

With the new vocabulary, HA `heat` writes `manual`, the water heater offers
`auto`/`manual`/`off`, and the `cool` HVAC mode is not offered (there is no `night`
value to write). A value read later from MQTT always overrides the initial guess.

## Preset modes

The component exposes two preset modes on climate entities:

| Preset | HA constant | Mechanism |
|---|---|---|
| None | `PRESET_NONE` | No active override |
| Boost | `PRESET_BOOST` | Quick veto is active  -  set via target temperature change |
| Away | `PRESET_AWAY` | Holiday period is active  -  set via `async_set_preset_mode` |

Away mode sets a 7-day holiday period starting from today. Boost is triggered
automatically when a target temperature is set in `auto` mode (writes quick
veto temperature + 3-hour duration).

## Heating zones (Z1-Z4)

Climate entities are created for each heating zone that has both a
`Z{n}OpMode` and a non-null `Z{n}RoomTemp` value. The zone index ranges from
1 to 4.

| MQTT message | Field | Access | HA control / attribute |
|---|---|---|---|
| `Z{n}OpMode` | `value.value` | read/write | HVAC mode |
| `Z{n}RoomTemp` | `value.value` | read | Current temperature |
| `Z{n}DayTemp` / `Z{n}ManualTemp` | `value.value` | read/write | Target temperature (or low in range mode) |
| `Z{n}TempDesired` | `value.value` | read | Effective target in time-controlled mode |
| `Z{n}NightTemp` | `value.value` | read/write | Target temperature low |
| `Z{n}CoolingTemp` | `value.value` | read/write | Target temperature high |
| `Z{n}HolidayStartPeriod` | `value.value` | read/write | Preset "away" start |
| `Z{n}HolidayEndPeriod` | `value.value` | read/write | Preset "away" end |
| `Z{n}QuickVetoTemp` | `value.value` | read/write | Boost target temperature |
| `Z{n}QuickVetoDuration` | `value.value` | read/write | Boost duration (hours); write `0` to cancel |
| `Z{n}QuickVetoEndDate` | `value.value` | read/write | Boost end date; write `01.01.2015` to cancel |
| `Z{n}QuickVetoEndTime` | `value.value` | read/write | Boost end time; write `00:00:00` to cancel |

### Temperature control strategies

Depending on which temperature messages are available, the climate entity
adapts:

| Available messages | HA feature | Behavior |
|---|---|---|
| `Z{n}DayTemp`/`Z{n}ManualTemp` only, or cooling disabled | Target temperature | See "Temperature changes" below |
| `Z{n}DayTemp` + `Z{n}NightTemp`, cooling enabled | Target temperature range | High = `DayTemp`, low = `NightTemp`; low change triggers boost |
| `Z{n}DayTemp` + `Z{n}CoolingTemp`, cooling enabled | Target temperature range | High = `CoolingTemp`, low = `DayTemp`; low change triggers boost |

Ranges and the `cool` HVAC mode are only offered when the zone has cooling (option
`cooling`, see [Options](options.md)).

### Temperature changes

With the default option `temperature_write: smart`:

| Zone mode | Displayed target | Setting the temperature |
|---|---|---|
| Manual (`manual`/`day`) | `Z{n}ManualTemp`/`Z{n}DayTemp` | Writes the manual setpoint permanently |
| Time controlled (`auto`) | `Z{n}TempDesired` (the target the controller is aiming for), else the manual setpoint | Starts a quick veto (`Z{n}QuickVetoTemp` + duration); the new target is shown right away |
| Off | Manual setpoint | Starts a quick veto |

With `temperature_write: quick_veto`, every change starts a quick veto (behaviour before 1.1.0).

### HVAC action

The climate entity's action (Heating / Cooling / Idle / Defrosting / Off) comes from what
the heat pump is doing, using the freshest signal available:

| Order | Signal | Meaning |
|---|---|---|
| 1 | `hmu Status00.defrost` | Defrosting |
| 2 | `hmu Status07.heatermain_b7_warmwater`, `hmu Status01.pumpstate` = `hwc` | Hot water: zones show Idle |
| 3 | `hmu Status07.power` (compressor %) | above 0 = running, 0 = idle |
| 4 | `hmu Status00.compressorstate` | running / idle |
| 5 | `hmu Status01.pumpstate` | `on`/`overrun` = running, `off` = idle |
| 6 | `RunDataStatuscode` | polled, may lag; used only when nothing above is available |

"Running" means Cooling when the status code is a `cool_*` code, otherwise Heating. A
zone shows Idle while its `Z{n}Status` (or `Hc{n}Status`) says it is not asking for
heat. Only when none of these signals has been received does the action follow the
selected mode (Manual shows Heating).

## Water heater

A water heater entity is created when both `HwcOpMode` and `HwcTempDesired`
are present.

| MQTT message | Field | Access | HA control / attribute |
|---|---|---|---|
| `HwcOpMode` | `value.value` | read/write | Operation mode (`auto`, `day`/`manual`, `off`) |
| `HwcTempDesired` | `value.value` | read/write | Target temperature |
| `HwcStorageTemp` | `value.value` | read | Current temperature |
| `HwcStorageTempBottom` | `value.value` | read | Current temperature (fallback) |
| `HwcStorageTempTop` | `value.value` | read | Current temperature (fallback) |
| `HwcSFMode` | `value.value` | read/write | Boost operation mode (`auto`, `load`) |
| `HwcHolidayStartPeriod` | `value.value` | read/write | Away mode start |
| `HwcHolidayEndPeriod` | `value.value` | read/write | Away mode end |

Current temperature is read from the first available message in the order:
`HwcStorageTemp` → `HwcStorageTempBottom` → `HwcStorageTempTop`.

### Boost operation

When `HwcSFMode` is present, an additional `boost` operation mode is added to
the water heater's operation list, and a dedicated **Hot Water Boost** switch
entity is created.

| Control | Mechanism |
|---|---|
| Operation mode `boost` / Switch turn on | Writes `load` to `HwcSFMode/set` |
| Operation mode `auto`/`day`/`manual`/`off` / Switch turn off | Writes `auto` to `HwcSFMode/set` |
| State display | Shows `boost` when `HwcSFMode` is `load`, falls back to actual `HwcOpMode` value otherwise |

The underlying `HwcOpMode` is not changed when boost is activated; the water
heater continues to show its normal operation mode once boost is turned off.

## Pressure sensor

A pressure sensor entity is created when `WaterPressure` is present.

| MQTT message | Field | Access | HA control / attribute |
|---|---|---|---|
| `WaterPressure` | `value.value` | read | Native value (unit: bar) |
