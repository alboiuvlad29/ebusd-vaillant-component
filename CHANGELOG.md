# Changelog

Changes in this fork (alboiuvlad29/ebusd-vaillant-component) on top of upstream v1.0.0.

## 1.14.1

- Fix: the **Noise reduction active** schedule was incomplete. `SilentTimer_<Day>` holds only
  the slot that was read last, so the integration requested no slots and saw a random
  subset. It now publishes each slot number to `.../SilentTimer_<Day>/get` (slots 0 to
  `slotcount - 1`, one per second, at startup and hourly) and stays unknown until all
  slots of the day are known.
- Fix: the Release workflow lacked `contents: write`, so no `ebusd_vaillant.zip` was attached
  and HACS could not install the release. The workflow now has the permission and
  publishes the release with the zip.
- `FaultHistory0` is optional (it shares its ID with `LastError` and is gone from the local
  definitions); docs updated.
- Entity IDs depend on the device areas in your Home Assistant (for example
  `sensor.back_garden_heat_pump_last_fault`); the IDs in the 1.14.0 notes assume no area.

## 1.14.0

New features. Everything below needs ebusd messages that are optional: when a topic never
appears, no entity is created and nothing is logged. Several messages are not in the
upstream ebusd definitions yet (see `docs/mapping.md`); users on upstream definitions see
no change.

- **Hot water preset:** select **Hot water preset** (Comfort / Eco, `HwcPreset`) and number
  **Hot water eco temperature** (`HwcEcoTempDesired`). The **water heater target is the
  effective target**: the eco temperature while the preset is Eco, `HwcTempDesired` while
  Comfort, and `set_temperature` writes to the one in use. Both raw values are attributes.
- **Heat pump fault history:** sensor **Last fault** (`F.022`, with timestamp, meaning and the
  whole stored history as attributes) from `LastError` and `FaultHistory0` to `9`
  (requested one per second at startup and when `LastError` changes). Known aroTHERM codes
  have a meaning, others show "Unknown fault". A new fault fires the `ebusd_vaillant_fault`
  event and creates a Repairs issue; the last seen fault is stored, so restarts do not repeat
  it. The current error sensors are diagnostic entities now.
- **Green iQ** switch (config) on the system device.
- **Outside temperature** sensor on the system device (passive `broadcast/outsidetemp`,
  controller `OutsideTemp` as fallback), **Outside temperature average** (diagnostic) and an
  `outdoor_temperature` attribute on the zones.
- **Noise reduction:** binary sensor **Noise reduction active** from the `SilentTimer_<Day>`
  schedule and local time, sensor **Noise reduction level**.
- **Heat pump sensors:** compressor speed, high pressure, superheat, fan speed, EEV
  position, building circuit flow, heat output, compressor utilisation; diagnostic: charging
  mode, compressor hysteresis, start heating from, remaining pressure difference, pump outputs.
- **Hot water installer values** (cylinder charging hysteresis and offset, maximum charging
  time, anti-cycling time, eco parameters): read-only diagnostic sensors, number entities
  with the new option **Allow installer settings to be changed**.
- **Heating boost switch** turns on immediately instead of waiting for the next poll.
- `MultiInputSetting` is not read by the integration. The register is UIN (2 bytes): the
  upstream definition (UCH plus IGN:3) fails to decode, use the corrected line from the local
  definitions.
- Not included yet: condensation temperature (needs the gauge or absolute pressure check
  against T.0.86), instantaneous COP, editing the noise reduction schedule.

### For the HA side to verify

After installing v1.14.0 and restarting Home Assistant:

1. **Hot water preset:** `select.vaillant_hot_water_hot_water_preset` exists. Switch it to
   Eco: the water heater target changes to the eco temperature (40 °C) and the attributes show
   `comfort_temperature: 50`, `eco_temperature: 40`. Set a new target in Eco and check that
   `ebusd/ctlv3/HwcEcoTempDesired/set` is written, not `HwcTempDesired/set`. Switch back
   to Comfort: the target returns to 50.
2. **Fault history:** `sensor.vaillant_heat_pump_last_fault` shows `F.022` with 7 entries in
   `history` (18.09.2026 18:41 newest). The ebusd log shows `FaultHistory0` to `9` read
   requests about one second apart. Restart HA: no `ebusd_vaillant_fault` event and no new
   Repairs issue for the old faults. The `Current error` sensors (diagnostic) are disabled by
   default: enable them to check `none`. After a new fault, check that the history shifts by
   one (an empty `/get` may return ebusd's cached entries).
3. **Green iQ:** `switch.vaillant_green_iq` follows the panel (Menu, Control, Green iQ) and
   writes `on`/`off` to `ebusd/ctlv3/GreenIQ/set`.
4. **Outside temperature:** `sensor.vaillant_outside_temperature` updates about every minute
   (broadcast). The zones have an `outdoor_temperature` attribute.
5. **Noise reduction:** `binary_sensor.vaillant_noise_reduction_active` is on during the
   periods (00:00 to 08:00, 14:00 to 16:00, 18:30 to 24:00). This depends on how ebusd
   publishes the slots of `SilentTimer_<Day>`: check the `schedule` attribute shows all
   three periods. The sensor stays unknown until all slots of the day (`slotcount`) have been
   seen; if it never leaves unknown, report the MQTT payloads of `SilentTimer_Monday`.
6. **Heat pump sensors** appear on the heat pump device with plausible values (compare with the
   service menu: T.0.93, T.0.63, T.0.88, T.0.17).
7. **Installer values:** read-only sensors by default (cylinder hysteresis 10 K, charging time
   90 min, anti-cycling 30 min). Enabling the option turns them into numbers.
8. Turn on the **Heating boost** switch: it shows on at once.

## 1.13.3

- Fix: the **Heating boost** switch could not cancel a boost on the newer definitions
  after a restart or reload. It was created before `Z{n}SFMode` had arrived and kept that
  first configuration, so turning it off wrote duration 0 (ignored by the controller) and
  it did not follow `Z{n}SFMode`. The `Z{n}SFMode` topic is now always known on the newer
  definitions, and all switches (Heating boost, Away, hot water Boost and Away) follow
  later discovery updates and start from the values already received.

## 1.13.2

Fixes from the first live install on the newer controller definitions:

- **Heating boost (quick veto) on the newer definitions:** cancelling now writes
  `Z{n}SFMode = auto`; the controller ignored duration 0, and the end date/time are
  read-only there (no more "write message not found" in the ebusd log). A running boost
  is detected from `Z{n}SFMode = veto` immediately, the Heating boost switch and the
  Boost preset turn off when it returns to `auto`. Old definitions behave as before.
- **Flow temperature limits:** the newer names `Hc{n}HeatingFlowTempMin/Max` (and
  `Hc{n}CoolingFlowTempMin`) are used, ahead of stale retained values of the old names.
- **Effective target temperature** shows the boost temperature during a quick veto, the
  schedule target in time-controlled mode and the manual setpoint in Manual mode
  (`Z{n}TempDesired` is 0 outside time-controlled mode). Same entity ID as before.
- **No more "off" after a reload:** zones take their mode from the values already
  received, and show unknown (not off) until a mode is known.
- `Z{n}Status` is a special-function status (auto, veto, holidayaway, ...), not a heat
  demand; it no longer affects the HVAC action. `Hc{n}Status` still does.
- Devices link to the system device with `via_device_id` on Home Assistant versions that
  support it (deprecation of `via_device` in 2026.10). The manifest points to this fork.

## 1.13.1

- Fix: the **Low pressure** binary sensor now picks up `Status07` (fast pressure and the
  pressure-loss flag) when it appears after the polled pressure message; before, the
  pressure-loss flag could be ignored for the whole session.
- Fix: the integration no longer treats its own `.../set` writes as values from the
  controller. A write that ebusd rejected could otherwise be skipped as "unchanged" when
  retried.
- Fix: write protection no longer skips a setpoint that equals the cached value while
  ebusd has not yet confirmed a different, just-written value (e.g. 50, 51, 50 in quick
  succession now ends at 50).

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
