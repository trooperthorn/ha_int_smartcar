# Vehicle status and battery status

Every signal this integration reads for vehicle state and battery state: the code,
the permission, the body shape, the units and enums, the entity key it becomes, and
whether a 2025 US Volkswagen ID. Buzz can ever produce it.

Companion to [communication.md](communication.md), which covers how the data gets
here at all. The complete 93-signal catalogue, including the groups this document
does not cover, is in [api-reference.md](api-reference.md).

Claim labels are as in [communication.md](communication.md): `S-spec`, `D-docs`,
`C-code`, `M-matrix`, `L-portal`, `L-live`, `U-unver`.

## Two things to know before reading the tables

**A signal being mapped says almost nothing about whether it will ever have a
value.** Four independent gates stand between a signal existing in the API and an
entity in Home Assistant showing a number:

1. The **vehicle** must support it. Support varies by make, model, year **and
   region**, and the variation is large.
2. The **owner** must have granted the permission at Connect time.
3. The **plan** must allow the signal into a webhook's data list, or the signal
   cannot be delivered even when it is granted. These are two separate capability
   sets; see "Granted scope is not the same as configurable signal" in
   [communication.md](communication.md).
4. The **webhook** must be verified, enabled and subscribed, because the v3 signals
   endpoint reads a store that only a webhook fills.

**The body is not a scalar.** Most signals wrap their value as `{"value": ...}`,
but several do not: `tractionbattery-nominalcapacity` uses `capacity`, the closure
signals use `isOpen` and `isLocked` with no wrapper at all, and a handful use
`values` as a plural array. A parser that reads only `body.value` drops them
silently.

The "ID. Buzz" column is `M-matrix`, from a 2026-09-20 dashboard export that could
**not** be re-checked against the live portal, because Vehicle Access shows plan
capability rather than vehicle capability. Treat it as a strong lead and re-derive
it from `GET /connections` plus a signals read before relying on it.

The "Plan" column is `L-portal`: whether a Free plan can put the signal into a
webhook at all.

Permissions are `D-docs` from the permissions list. **The signal reference pages
publish no signal-to-scope mapping**, so the permission column is the documented
association rather than a per-page statement, and is `U-unver` per individual code.

## Battery status

| Signal code | Permission | Body | Units and enums | Entity key | ID. Buzz | Plan |
| --- | --- | --- | --- | --- | --- | --- |
| `tractionbattery-stateofcharge` | `read_battery` | `{value, unit}` | `percent` | `battery_level`, sensor, device class BATTERY, state class MEASUREMENT | yes | **enabled** |
| `tractionbattery-range` | `read_battery` | `{value, type, additionalValues[], unit}` | `km`. `type` is `DEFAULT`; each `additionalValues[].type` is `IDEAL_CONDITIONS`, `ESTIMATED` or `RATED`. `value` is the best real-world estimate | `range`, sensor, device class DISTANCE | yes | not in the 9 |
| `tractionbattery-nominalcapacity` | `read_battery` | **`{source, capacity, availableCapacities[], unit}`** | `kWh`. `source` is `SMARTCAR` or `USER_SELECTED`. `availableCapacities[]` entries are `{capacity, description}` | `battery_capacity`, sensor, device class ENERGY_STORAGE | yes | not in the 9 |
| `tractionbattery-isheateractive` | `read_battery` | `{value}` boolean | | `battery_heater_active`, binary sensor | no | not in the 9 |
| `tractionbattery-maxrangechargecounter` | `read_battery` | `{value, unit}` integer | | unmapped | no | not in the 9 |
| `lowvoltagebattery-stateofcharge` | `read_battery` | `{value, unit}` | percent | `low_voltage_battery_level`, sensor | no | **locked** |
| `lowvoltagebattery-status` | `read_battery` | | | unmapped | no | **locked** |

Three traps in three rows.

**`battery_level` expects a fraction.** `sensor.py` applies
`lambda pct: pct and round(pct * 100)` to `tractionbattery-stateofcharge.value`
`C-code`, while the spec documents the value as a percentage from 0 to 100
`S-spec`. One of the two is wrong, and which one is `U-unver` until a real payload
is seen. The coordinator does carry a divide-by-100 normalisation path for
percent-united bodies, so the two may meet in the middle.

**`nominalcapacity` reads `.capacity`, not `.value`.** It is the only scalar signal
in the API that breaks the pattern, and `DATAPOINT_ENTITY_KEY_MAP` handles it
`C-code`.

**`range` discards its variants.** `additionalValues` carries the ideal, estimated
and rated figures and nothing reads them `C-code`. The compatibility matrix lists
`tractionbattery-estimatedrange`, `-idealrange` and `-ratedrange` as separate
signals with no path in the OpenAPI document `M-matrix` `S-spec`; the spec folds
them into `additionalValues` instead. Neither source is complete alone.

On a Free plan, **`tractionbattery-stateofcharge` is the only battery signal a
webhook can deliver.** Range and capacity are supported by the car and unavailable
to the transport.

## Charging status

The whole `Charge` group is **locked** on a Free plan `L-portal`. None of these can
be configured into a webhook on that plan, whatever the vehicle supports and
whatever a grandfathered grant still carries.

| Signal code | Permission | Body | Units and enums | Entity key | ID. Buzz |
| --- | --- | --- | --- | --- | --- |
| `charge-ischarging` | `read_charge`, `control_charge` | `{value}` boolean | | `charging`, switch. Also read at setup | yes |
| `charge-detailedchargingstatus` | `read_charge` | `{value}` string | **open string**, no enum published | `charging_state`, sensor | yes |
| `charge-ischargingcableconnected` | `read_charge` | `{value}` boolean | | `plug_status`, binary sensor, device class PLUG | yes |
| `charge-ischargingcablelatched` | `read_charge` | `{value}` boolean | | unmapped | yes |
| `charge-chargelimits` | `read_charge`, `control_charge` | `{activeLimit, values[], unit}` | `percent`. `values[].type` is `GLOBAL`, `LOCATION` or `CHARGING_TYPE`, each with `limit` and an optional `condition` | `charge_limit`, number, min 50, max 100, read from `.values` | yes |
| `charge-wattage` | `read_charge` | `{value, unit}` | `watts` | `charge_wattage`, sensor, device class POWER, divided by 1000 to kW | yes |
| `charge-chargerate` | `read_charge` | `{value, unit}` | `km/h` | `charge_chargerate`, sensor, device class SPEED | yes |
| `charge-timetocomplete` | `read_charge` | `{value, unit}` | `min` | `charge_timetocomplete` and `charge_time_to_complete`, sensors, device class DURATION | yes |
| `charge-chargingconnectortype` | `read_charge` | `{value}` string | open string; example `J1772` | unmapped | yes |
| `charge-voltage` | `read_charge` | `{value, unit}` | `volts` | `charge_voltage`, sensor | no |
| `charge-energyadded` | `read_charge` | `{value, unit}` | `kWh` | `charge_energyadded`, sensor | no (US), yes (EU) |
| `charge-amperage` | `read_charge` | `{value, unit}` | `ampere` | `charge_amperage`, sensor | **no** |
| `charge-amperagemax` | `read_charge` | `{value, unit}` | `ampere` | `charge_amperage_max`, sensor | **no** |
| `charge-amperagerequested` | `read_charge` | `{value, unit}` | `ampere` | unmapped | **no** |
| `charge-isfastchargerpresent` | `read_charge` | `{value}` boolean | | `charge_fast_charger_present`, binary sensor | no |
| `charge-fastchargertype` | `read_charge` | `{value}` string | open string; example `CCS` | unmapped | no |
| `charge-chargeportstatuscolor` | `read_charge` | `{value}` string | open string; example `Green` | unmapped | no (US), yes (EU) |
| `charge-ischargingportflapopen` | `read_charge` | `{value}` boolean | | unmapped | no |
| `charge-chargerphases` | `read_charge` | `{value, unit}` | | unmapped | no |
| `charge-chargetimers` | `read_charge` | `{values[]}` | `type` is `GLOBAL`, `LOCATION` or `CHARGING_TYPE`; `scheduleType` is `DAILY`, `WEEKLY` or `WORKWEEK`; entries carry `condition`, `schedules[]`, `departureTimers[]`, `isOEMOptimizationEnabled`, `scheduleId` | unmapped, follow-up item 2 | no (US), yes (EU) |
| `charge-chargerecords` | `read_charge_records` | `{values[]}` | entries carry `id`, `cost`, `startTime`, `endTime`, `location`, `energyAdded`, `chargingType`, `isPublicCharger`, `startStateOfCharge`, `endStateOfCharge` | unmapped | no |

**The four string-valued charge signals publish one example each and no enum**
`S-spec`. `charge-detailedchargingstatus` in particular is the one every dashboard
wants to switch on, and a closed enum in a template or an automation will break on
the first unlisted OEM value. Its one published example is `CHARGING`.

**`charge-chargelimits` is the multi-value case the coordinator special-cases.**
`_SIGNAL_BODY_MULTIVALUE_ITEM_KEY_MAP` maps it to the item key `limit` so the
percent normalisation reaches the right field inside each entry of `values[]`
`C-code`.

**No amperage, at all.** All three amperage signals are unsupported on this vehicle
`M-matrix`, and Smartcar publishes no command to set current on any vehicle. So
"charge at 16 A" is not expressible through Smartcar, which is why follow-up item 1
exists and why the taper blueprint keys the Emporia charger's current off the
Smartcar state of charge instead.

## Vehicle status

There is no single "vehicle status" signal. On a vehicle with no
`connectivitystatus-*` support it means closures, plus the odometer, plus location.

### Closures

| Signal code | Permission | Body | Entity keys | ID. Buzz |
| --- | --- | --- | --- | --- |
| `closure-islocked` | `read_security`, `control_security` | `{value}` boolean | `door_lock`, lock | yes |
| `closure-doors` | `read_security` | `{values[], rowCount, columnCount}`, entries `{row, column, isOpen, isLocked}` | eight: `door_{front,back}_{left,right}` and the same four with `_lock` | yes |
| `closure-windows` | `read_security` | `{values[], rowCount, columnCount}`, entries `{row, column, isOpen}` | four: `window_{front,back}_{left,right}` | yes |
| `closure-fronttrunk` | `read_security` | **`{isOpen, isLocked}`**, no wrapper | `front_trunk`, `front_trunk_lock` | yes |
| `closure-reartrunk` | `read_security` | **`{isOpen, isLocked}`**, no wrapper | `rear_trunk`, `rear_trunk_lock` | yes |
| `closure-sunroof` | `read_security` | **`{isOpen}`**, no wrapper | `sunroof` | yes |
| `closure-enginecover` | `read_security` | **`{isOpen}`**, no wrapper | `engine_cover` | yes |
| `closure-tailgate` | `read_security` | `{isOpen, isLocked}`, no wrapper | unmapped | no |

The whole `Closure` group is plan-enabled, though only `closure-islocked` is among
the nine signals currently configurable into a webhook `L-portal`.

`row` runs front to back from 0 and `column` runs left to right from 0, with
`rowCount` and `columnCount` describing the layout `S-spec`. The integration maps
positions to fixed names through `VEHICLE_FRONT_ROW`, `VEHICLE_BACK_ROW`,
`VEHICLE_LEFT_COLUMN` and `VEHICLE_RIGHT_COLUMN` `C-code`, which is correct for a
2x2 layout and would need revisiting for a three-row vehicle.

**A spec self-contradiction worth knowing.** For `closure-doors` and
`closure-windows` the OpenAPI **schema** declares the array under a required key
`values`, while the **example** on the same path puts it under `doors` and
`windows` respectively `S-spec`. This integration reads `.values`, matching the
schema. Which one the live service emits is `U-unver`; check a real payload before
trusting either.

### Odometer, location, identity

| Signal code | Permission | Body | Units and enums | Entity key | ID. Buzz | Plan |
| --- | --- | --- | --- | --- | --- | --- |
| `odometer-traveleddistance` | `read_odometer` | `{value, unit}` | `km`, total since initial use | `odometer`, sensor, device class DISTANCE, state class TOTAL_INCREASING | yes | **enabled** |
| `location-preciselocation` | `read_location` | `{latitude, longitude, heading, direction, locationType}` | `direction` is N, NE, E, SE, S, SW, W or NW. **`locationType` is `CURRENT` or `LAST_PARKED`** | `location`, device tracker | yes | **locked** |
| `location-isathome` | `read_location` | `{value}` boolean | home per the OEM app | unmapped | no | **locked** |
| `vehicleidentification-vin` | `read_vin` | `{value}`, 17 characters | | read at setup only; a 404 is tolerated | yes | **enabled** |
| `vehicleidentification-nickname` | `read_vehicle_info` | `{value}` | | unmapped | unknown | **enabled** |
| `vehicleidentification-packages` | `read_vehicle_info` | **`{values[]}`** | | unmapped | unknown | group enabled |
| `service-records` | `read_service_history` | `{values[]}` | | unmapped | yes | **locked** |

**`locationType` matters and nothing reads it.** A fix may be a stale parked
position rather than a live one, and the value itself does not say so `S-spec`. The
device tracker presents either as current position.

**Location is the sharpest example of the two-capability-set split.** The vehicle
supports it, the owner may have granted `read_location`, and a Free plan cannot put
it in a webhook. A `device_tracker` entity that can never populate reads to a user
as "the integration is broken".

### Connectivity and account

| Signal code | Permission | Body | Entity key | ID. Buzz | Plan |
| --- | --- | --- | --- | --- | --- |
| `connectivitystatus-isonline` | `read_vehicle_info` | `{value}` boolean | `online`, binary sensor | **no** | **enabled**, configured as a trigger |
| `connectivitystatus-isasleep` | `read_vehicle_info` | `{value}` boolean | `asleep`, binary sensor | **no** | **enabled**, configured as a trigger |
| `connectivitystatus-isdigitalkeypaired` | `read_vehicle_info` | `{value}` boolean | `digital_key_paired`, binary sensor | **no** | **enabled** |
| `vehicleuseraccount-permissions` | `read_user_profile` | **`{values[]}`, an array** | unmapped | unknown | **enabled**, configured as a trigger |
| `vehicleuseraccount-role` | `read_user_profile` | `{value}` | unmapped | unknown | **enabled**, configured as a trigger |
| `connectivitysoftware-currentfirmwareversion` | `read_extended_vehicle_info` | `{value}` | `firmware_version`, sensor | unknown | **enabled** |

Two things collide in this table.

**There is no online or asleep signal on this vehicle** `M-matrix`, so there is no
way to tell a sleeping car from a broken integration from the signal data alone. A
`409 VEHICLE_STATE / ASLEEP` on a command is the only asleep evidence available.

**And yet three of the webhook's seven configured triggers are
`connectivitystatus-*` and `vehicleuseraccount-*`** `L-portal`. If the matrix is
right, those triggers can never fire here, and even a correctly verified webhook
would be very quiet. This is a lead from comparing two sources, not a verified
finding; see step 3 of the checklist in [communication.md](communication.md).

**`vehicleuseraccount-permissions` uses `values`, plural.** It is in the configured
data-signal list, so it will arrive, and a parser reading only `body.value` drops it
`L-portal`.

## What ships enabled by default

`DEFAULT_ENABLED_ENTITY_DESCRIPTION_KEYS` in `const.py` holds 21 keys `C-code`:

`battery_level`, `charging_state`, `charging`, `door_lock`, `location`,
`plug_status`, `range`, `diag_abs`, `diag_mil`, `diag_dtc_count`, `diag_dtc_list`,
`diag_ev_battery_conditioning`, `diag_ev_charging`, `diag_ev_drive_unit`,
`diag_ev_hv_battery`, `cabin_target_temperature`, `is_cabin_hvac_active`,
`is_front_defroster_active`, `is_rear_defroster_active`,
`is_steering_heater_active`, `climate`.

Everything else is created disabled and must be enabled by hand.

Entities are now gated on the vehicle's own `COMPATIBILITY` errors in the v3
signals response rather than on the requested scopes alone, so on a vehicle that
answers, the unsupported ones do not appear. `VEHICLE_STATE` and `PERMISSION`
errors deliberately do not count as incapacity `C-code`. That gating depends on the
signals response carrying errors to read, which an empty store does not.

## What the ID. Buzz can never populate

For a 2025 US ID. Buzz with the scopes granted on this account, ten of the
21 default-enabled entities are structurally incapable of ever having a value.

**The five diagnostics entities.** `diag_abs`, `diag_mil`, `diag_dtc_count`,
`diag_dtc_list`, `diag_ev_battery_conditioning`, `diag_ev_charging`,
`diag_ev_drive_unit` and `diag_ev_hv_battery` all map into the `diagnostics-*`
group. **All 23 diagnostics columns are unsupported on this vehicle** `M-matrix`,
`read_diagnostics` was not granted `L-live`, and the whole group is plan-locked
`L-portal`. Three independent reasons, any one of them sufficient.

**The five climate and HVAC entities.** `cabin_target_temperature`,
`is_cabin_hvac_active`, `is_front_defroster_active`, `is_rear_defroster_active`,
`is_steering_heater_active` and the `climate` switch all need `hvac-*` signals.
The vehicle reports none, `read_climate` was not granted, and the group is
plan-locked. The `climate` **switch** has a fourth problem: the command it used to
post to does not exist at any API version, so it is now created only on a v2 entry
`C-code`. See "The climate command does not exist" in
[api-reference.md](api-reference.md).

**`location`** is supported by the vehicle but plan-locked, so it can be granted
and still never arrive by webhook.

That leaves `battery_level`, `charging_state`, `charging`, `door_lock`,
`plug_status` and `range` as the default-enabled entities that can work at all, and
on a Free plan only `battery_level` and `door_lock` are among the nine signals a
webhook can carry.

Also structurally impossible, though not enabled by default: the four tire pressure
entities (`wheel-tires` unsupported), the three amperage sensors, and both charge
port controls.

## The ID. Buzz 2025 US surface

23 of 95 signals and 5 of 11 commands `M-matrix`, from the 2026-09-20 export and
**not verified live**:

| Group | Supported signals |
| --- | --- |
| Charge | `chargelimits`, `chargerate`, `chargingconnectortype`, `detailedchargingstatus`, `ischarging`, `ischargingcableconnected`, `ischargingcablelatched`, `timetocomplete`, `wattage` |
| Closure | `doors`, `enginecover`, `fronttrunk`, `islocked`, `reartrunk`, `sunroof`, `windows` |
| Location | `preciselocation` |
| Odometer | `traveleddistance` |
| Service | `records` |
| TractionBattery | `nominalcapacity`, `range`, `stateofcharge` |
| VehicleIdentification | `vin` |

Commands: lock, unlock, start charge, stop charge, set charge limit.

Granted scopes on this account, eight `L-live`: `read_battery`, `read_charge`,
`read_location`, `read_odometer`, `read_security`, `read_vehicle_info`, `read_vin`,
`control_charge`.

Intersect that matrix row with the nine signals a Free plan can put in a webhook and
what actually reaches Home Assistant about this car is: **state of charge, lock
state, odometer, VIN**, and whatever the vehicle-identification and user-account
signals return.

The **European** ID. Buzz row differs, adding `charge-chargetimers`,
`charge-chargeportstatuscolor` and `charge-energyadded` `M-matrix`. Region is part
of a vehicle's identity here, not a detail.

Smartcar's own Volkswagen page is narrower still: it publishes charging state, plug
connection, charging power, time to complete, state of charge, remaining electric
range, nominal battery capacity and odometer, with **charge start and charge stop
as the only commands** `D-docs`. That contradicts the matrix row, which also
carries lock, unlock and set charge limit. The matrix is the more specific source;
the brand page is the more recent. Unresolved.

Two more VW constraints worth knowing before writing an automation: **only the
primary account holder can authorize**, and **VW rate-limits charge commands**,
with a lockout that requires driving the vehicle to clear `D-docs`.

## Open questions

1. Does `tractionbattery-stateofcharge` arrive as 0 to 1 or 0 to 100? The cast in
   `sensor.py` and the spec's stated range disagree.
2. `values` versus `doors` and `windows` on the two closure grid signals, where the
   spec's schema and its own example disagree.
3. Is the 2026-09-20 compatibility export still accurate? It could not be
   re-checked against the portal, because Vehicle Access shows plan capability, not
   vehicle capability.
4. Does `charge-detailedchargingstatus` have a closed value set in practice?
5. Can this vehicle support the `connectivitystatus-*` and `vehicleuseraccount-*`
   triggers currently configured on the webhook?
6. Which is right about VW commands, the brand page or the compatibility matrix?
7. Does the integration handle `body.values` alongside `body.value`?

## See also

- [communication.md](communication.md): how any of this reaches Home Assistant, the
  credentials, the webhook model, and the dashboard checklist.
- [api-reference.md](api-reference.md): the full 93-signal catalogue, every
  endpoint, and the entity key map.
- [follow-ups.md](follow-ups.md): the gaps these fields imply, in particular items
  1, 2, 6, 10 and 11.
- `vendor-docs-reference/docs/smartcar.md`: the same catalogue without Home
  Assistant context, for an agent verifying the dashboard from a browser.
