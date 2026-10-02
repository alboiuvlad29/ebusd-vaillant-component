---
hide:
  - toc
---

# Services

Home Assistant services available via the ebusd Vaillant integration.

## Integration Services

These services are provided by the integration itself and are not tied to a specific entity.

| Service | Description | Fields |
|---|---|---|
| [`dump_mqtt_values`](https://my.home-assistant.io/redirect/developer_call_service/?service=ebusd_vaillant.dump_mqtt_values) | Returns all ebusd MQTT values accumulated since startup as a YAML dict, keyed by device. Also creates a persistent notification with the output. | -- |
| [`record_topic_changes`](https://my.home-assistant.io/redirect/developer_call_service/?service=ebusd_vaillant.record_topic_changes) | Subscribes to the configured ebusd MQTT prefix for a configurable duration and returns all received messages as a list of {ts, topic, value} entries. | `timeout` (number, optional, default: 10) |

## Climate

Available on: `EbusdClimateEntity`

| Service | Description | Extra Fields |
|---|---|---|
| [`set_temperature`](https://my.home-assistant.io/redirect/developer_call_service/?service=climate.set_temperature) | Set target temperature. | `temperature` (float), `preset_mode` (str, optional) |
| [`set_preset_mode`](https://my.home-assistant.io/redirect/developer_call_service/?service=climate.set_preset_mode) | Set preset mode. | `preset_mode` (str) |
| [`turn_on`](https://my.home-assistant.io/redirect/developer_call_service/?service=climate.turn_on) | Turn the climate entity on. | -- |
| [`turn_off`](https://my.home-assistant.io/redirect/developer_call_service/?service=climate.turn_off) | Turn the climate entity off. | -- |

## Water Heater

Available on: `EbusdWaterHeaterEntity`

| Service | Description | Extra Fields |
|---|---|---|
| [`set_temperature`](https://my.home-assistant.io/redirect/developer_call_service/?service=water_heater.set_temperature) | Set target temperature. | `temperature` (float) |
| [`set_operation_mode`](https://my.home-assistant.io/redirect/developer_call_service/?service=water_heater.set_operation_mode) | Set operation mode. | `operation_mode` (str) |
| [`turn_on`](https://my.home-assistant.io/redirect/developer_call_service/?service=water_heater.turn_on) | Turn the water heater on. | -- |
| [`turn_off`](https://my.home-assistant.io/redirect/developer_call_service/?service=water_heater.turn_off) | Turn the water heater off. | -- |
| [`turn_away_mode_on`](https://my.home-assistant.io/redirect/developer_call_service/?service=water_heater.turn_away_mode_on) | Turn away mode on. | -- |
| [`turn_away_mode_off`](https://my.home-assistant.io/redirect/developer_call_service/?service=water_heater.turn_away_mode_off) | Turn away mode off. | -- |

## Switch

Available on: `EbusdAwayModeSwitch`, `EbusdHwcAwayModeSwitch`, `EbusdHwcBoostSwitch`

| Service | Description | Extra Fields |
|---|---|---|
| [`turn_on`](https://my.home-assistant.io/redirect/developer_call_service/?service=switch.turn_on) | Turn the switch on. | -- |
| [`turn_off`](https://my.home-assistant.io/redirect/developer_call_service/?service=switch.turn_off) | Turn the switch off. | -- |

## Datetime

Available on: `EbusdQuickVetoEndEntity`, `EbusdHolidayEntity`

| Service | Description | Extra Fields |
|---|---|---|
| [`set_value`](https://my.home-assistant.io/redirect/developer_call_service/?service=datetime.set_value) | Set the date/time value. | `datetime` (datetime), `date` (date, optional), `time` (time, optional) |

## Automation services

| Service | Target | Fields | What it writes |
|---|---|---|---|
| `ebusd_vaillant.set_quick_veto` | zone climate | `temperature` (5-30 °C), `duration_hours` (optional, 0.5-24, default from the options) | `Z{n}QuickVetoTemp`, `Z{n}QuickVetoDuration` |
| `ebusd_vaillant.cancel_quick_veto` | zone climate | | duration `0`, end date/time reset |
| `ebusd_vaillant.set_away` | zone climate or water heater | `start_date`, `end_date` | holiday start/end dates |
| `ebusd_vaillant.cancel_away` | zone climate or water heater | | holiday dates reset |
| `ebusd_vaillant.hot_water_boost` | water heater | `enable` (default on) | `HwcSFMode` = `load` / `auto` |

## Diagnostics and repairs

- **Download diagnostics** (Settings > Devices & services > ebusd Vaillant > three dots):
  versions, options, every cached ebusd message per circuit and the discovered entity
  configuration. Nothing is redacted; the topics contain no credentials.
- A **Repairs** hint appears when a zone still uses the older controller definitions
  (`Z{n}DayTemp` with the day/night modes). Everything keeps working; the hint points to
  the newer definitions from ebusd-configuration, can be ignored, and disappears by itself
  after switching.
