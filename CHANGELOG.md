# Changelog

Changes in this fork (alboiuvlad29/ebusd-vaillant-component) on top of upstream v1.0.0.

## 1.13.0

- Diagnostics download with versions, options, cached ebusd messages per circuit and the
  discovered entity configuration.
- Repairs hint when a zone still uses the older controller definitions (`Z{n}DayTemp`,
  day/night modes), with a link to ebusd-configuration. It can be ignored and clears
  itself after switching to the newer definitions.

## 1.12.0

- Services for automations: `set_quick_veto` / `cancel_quick_veto` (zones, with an
  optional duration), `set_away` / `cancel_away` (zones and hot water, with dates) and
  `hot_water_boost` (start or cancel). Selectors and English/German translations included.
  Area, device and label targets act on the matching ebusd Vaillant entities and skip
  others in the same area (e.g. thermostats from other integrations).

## 1.11.0

- **Boost is no longer a hot water mode.** The water heater's operation list shows only
  the real modes (Time controlled / Manual / Off), and the current operation always shows
  the real mode, also while a boost charge runs. Changing the mode no longer cancels a
  running boost. Use the **Boost** switch or the new **Start boost** button; the water
  heater's `boost_active` attribute shows a running charge.
  The previous behaviour is available for one more release with the option
  **Show boost as a hot water mode (legacy)**.
- New hot water sensors: **Hot water status**, **Reheating active**, **Legionella
  protection day / time** (when the controller publishes them).

## 1.10.0

- Room sensors (upstream issue #14): the sensoCOMFORT's own **Room temperature** and
  **Room humidity**, **Remote {n} room temperature / humidity** for VR 92 units, and
  **Room humidity** per zone. Only created when a value is published.

## 1.9.0

- Zone extras (newer definitions): **Effective target temperature** (`Z{n}TempDesired`),
  **Setback temperature** (`Z{n}SetbackTemp`) and **Time slot active**
  (`Z{n}TimeSlotActive`) on each zone device.
- Fix: with the newer definitions, `Z{n}SetbackTemp` no longer turns a zone into a
  day/setback target range when cooling could not be detected.

## 1.8.0

- Write protection for setpoints (the controller stores them in EEPROM): the first change
  is written immediately, slider bursts are combined into one final write (1.5 s quiet,
  at most one write per topic every 10 s), and unchanged values are not written. Mode,
  boost and away writes are not delayed. Writes are logged at debug level.

## 1.7.0

- New **Operating mode** sensor on the heat pump (heating / cooling / hot water / defrost /
  idle), from the same signals as the truthful HVAC action.
- New **Electrical energy heating / hot water / standby** sensors (kWh, total increasing,
  restored after restarts) that split the heat pump's electrical power input by
  operating mode.
- Both are new entities and **disabled by default**.

## 1.6.0

- Faults and health entities, all **disabled by default** (enable them when wanted):
  - **Current error** sensor per device (heat pump, controller): `none` or the error
    codes; a Repairs issue is raised while a code is present and removed when it clears.
  - **Low pressure** binary sensor: on below the new `low_pressure_threshold` option
    (default 1.5 bar) or when the heat pump reports a pressure loss. Uses the fast
    `Status07.displaypressure` when available.
  - **eBUS connected** binary sensor from `ebusd/global/running` and `signal`.
- A Water Pressure sensor is created from `hmu Status07.displaypressure` when no other
  pressure message exists.

## 1.5.0

- Smarter polling priorities. The on/off option **Prime poll values** is replaced by
  **Polling priorities**: `essentials` (default) asks ebusd to poll modes, setpoints,
  room and hot water temperatures, boost and current power / daily yields at `?1` and
  everything else at `?5`; `all` polls everything at `?1` (the old "on"); `off` sends no
  requests (the old "off"). An existing `prime_poll_values` setting keeps working until
  the options are saved. The heat pump status messages are no longer polled, since ebusd
  overhears them. See the docs for matching ebusd add-on options.

## 1.4.0

- Truthful `hvac_action`: zones no longer show Heating while the heat pump is idle. The
  action now comes from the fastest signals ebusd overhears: `Status00` defrost and
  compressor state, `Status07` warm-water flag and compressor power, `Status01` pump state,
  and only then the polled `RunDataStatuscode` (which can lag for hours). During a hot
  water run zones show Idle, during defrost Defrosting. A zone's `Z{n}Status` (or
  `Hc{n}Status`) is used to show Idle when that zone is not asking for heat.

## 1.3.0

- Plain-language entity names without "veto": the zone quick veto switch is now
  **Heating boost**, the away mode switches are **Away**, the hot water boost switch is
  **Boost**, and "Quick veto end" is **Heating boost end**. The climate presets read
  **Heating boost** and **Away**. Entity IDs and unique IDs are unchanged, also on new
  installs.
- The Heating boost switch has `boost_temperature`, `boost_duration_hours` and
  `boost_ends_at` attributes and turns itself off when the boost ends (so HomeKit shows
  the right state). The water heater has a `boost_active` attribute.

## 1.2.0

- Modes are shown with the sensoCOMFORT names: **Time controlled** (`auto`), **Manual**
  (`heat` for zones, `manual`/`day` for hot water) and **Off**, in all languages. Only the
  displayed names change; states, attributes, entity IDs and automations are unaffected.

## 1.1.0

- Zones without cooling now show one target temperature instead of a heat/cool range,
  and no longer offer the `cool` mode. Cooling is detected from `Hc{n}CoolingEnabled`,
  `ActiveCoolingEnabled`, the cooling yield, `SetMode.releasecooling` and `cool_*` status
  codes. New option **Cooling** (`auto`/`enabled`/`disabled`) to override the detection.
  Systems that report nothing keep the range as before.
- Changing the temperature in **Manual** mode now writes the permanent setpoint
  (`Z{n}ManualTemp`/`Z{n}DayTemp`). In **Time controlled** mode it still starts a quick
  veto, and the new target is shown immediately. New option **Temperature changes**
  (`smart`/`quick_veto`); `quick_veto` restores the old behaviour.
- In Time controlled mode the climate target shows `Z{n}TempDesired`, the target the
  controller is actually aiming for, when it is available.
- Climate entities follow discovery changes: when the target layout or the setpoint topic
  changes (e.g. `Z1DayTemp` to `Z1ManualTemp` after switching ebusd definitions), the
  entity re-subscribes without a reload.
- Saving the options no longer deletes and re-creates all entities; entity IDs, areas and
  customizations are kept. Zones dropped by lowering the number of zones stay in the
  registry as unavailable until removed by hand.

## 1.0.1

- Support for the newer ebusd controller definitions (`15.ctlv2`/`15.ctlv3` from
  TypeSpec): `off`/`auto`/`manual` modes and `Z{n}ManualTemp`, next to the older
  `day`/`night` vocabulary and `Z{n}DayTemp`.
