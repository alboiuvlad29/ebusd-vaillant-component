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
| `poll_priming` | Which values the integration asks ebusd to poll and how often. `essentials`: modes, setpoints, room and hot water temperatures, boost and current power / daily yields at priority `?1`, everything else (holiday dates, boost end, flow limits, monthly and total yields, COP) at `?5`. `all`: everything at `?1`. `off`: no polling requests, values arrive as ebusd polls them on its own. Replaces `prime_poll_values` (on = `all`, off = `off`), which is still honoured until the options are saved | `essentials` |
| `zones_with_temp_only` | Only create climate entities for zones that report a current temperature | `on` |
| `cooling` | Whether zones offer cooling. `auto` decides from what the system reports (`Hc{n}CoolingEnabled`, `ActiveCoolingEnabled`, a cooling yield above 0, `SetMode.releasecooling`, a `cool_*` status code; a cooling yield of exactly 0 means no cooling). Without cooling, a zone has one target temperature and no `cool` mode. With no information at all, zones keep the heat/cool range | `auto` |
| `temperature_write` | What changing a zone's target temperature writes. `smart`: the permanent setpoint (`Z{n}ManualTemp`/`Z{n}DayTemp`) in Manual mode, a quick veto in Time controlled mode. `quick_veto`: always a quick veto (behaviour before 1.1.0) | `smart` |

Saving the options reloads the integration. Entity IDs, names, areas and other customizations are kept.

## Polling and the ebusd add-on

ebusd polls one message per `--pollinterval` seconds, cycling through every message that
has a polling priority. Priority 1 is polled on every cycle, priority 5 only on every fifth.
ebusd's own Home Assistant discovery (`mqtt-hassio.cfg`) also requests polling of every
message it has seen, so on a busy bus each value refreshes only every few minutes.

To keep the important values fresh:

- keep `poll_priming` on `essentials`;
- lower the poll interval, e.g. `--pollinterval=2`;
- stop ebusd from publishing (and so polling) messages you never use, e.g.
  `--mqttvar=filter-non-name=Timer|^Z3|^Z4`, which drops timer programs and unused zones.

The heat pump status messages (`hmu Status00`, `Status01`, `Status07`) are never polled by
the integration: the controller requests them every few seconds and ebusd overhears them.
