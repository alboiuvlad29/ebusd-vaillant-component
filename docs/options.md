---
hide:
  - toc
---

# Options

Configuration options available for the ebusd Vaillant integration.

| Option Name | Description | Default Value |
|---|---|---|
| `away_mode_duration` | Number of days the away mode lasts when activated | `7` |
| `quick_veto_duration` | Number of hours the quick veto lasts when triggered | `3` |
| `max_zones` | Limits how many zone entities are created | `4` |
| `prime_poll_values` | Tries to request typical MQTT topic names to be able to show values immediately on startup. If disabled, entities become available as MQTT values are published | `on` |
| `zones_with_temp_only` | Only create climate entities for zones that report a current temperature | `on` |
| `cooling` | Whether zones offer cooling. `auto` decides from what the system reports (`Hc{n}CoolingEnabled`, `ActiveCoolingEnabled`, a cooling yield above 0, `SetMode.releasecooling`, a `cool_*` status code; a cooling yield of exactly 0 means no cooling). Without cooling, a zone has one target temperature and no `cool` mode. With no information at all, zones keep the heat/cool range | `auto` |
| `temperature_write` | What changing a zone's target temperature writes. `smart`: the permanent setpoint (`Z{n}ManualTemp`/`Z{n}DayTemp`) in Manual mode, a quick veto in Time controlled mode. `quick_veto`: always a quick veto (behaviour before 1.1.0) | `smart` |

Saving the options reloads the integration. Entity IDs, names, areas and other customizations are kept.
