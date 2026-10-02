# Changelog

Changes in this fork (alboiuvlad29/ebusd-vaillant-component) on top of upstream v1.0.0.

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
