# The Smartcar API, and what this integration uses

Every endpoint Smartcar publishes, what it returns, and whether this
integration calls it. Captured 2026-09-20.

## How to read this

Each claim carries where it came from, because the sources disagree in places
and the disagreements matter:

| Label | Source | Trust |
| --- | --- | --- |
| `S-spec` | The published OpenAPI documents: `auth.yaml`, `full-vehicle.yaml`, `management.yaml`, from `smartcar.com/docs/specs/` | Highest. Machine generated from the service. |
| `D-docs` | The prose reference at `smartcar.com/docs`, mirrored in `llms-full.txt` | Good, but lags the spec. One place below contradicts it. |
| `C-code` | Observed in this integration or its test fixtures | Says what we send, not what the service accepts. |
| `M-matrix` | The per-vehicle compatibility matrix exported from the Smartcar dashboard, 2026-09-20 | Authoritative for what a given make, model, year and region supports. |
| `U-unver` | Believed but confirmed by none of the above | Do not build on it without a probe. |

The matrix is not committed here. It is a 646 KB export that goes stale, and
it regenerates from Dashboard, Compatible Vehicles. Keep a copy outside the
working tree while using it.

Where a row is `C-code` only, this integration is calling something no
published source describes. There is one such row and it is a defect, not a
discovery. See "The climate command does not exist".

## Headline findings

**The v2 to v3 migration is already done here.** `application_credentials.py`
overrides `_token_request` so a v3 client posts `grant_type=client_credentials`
and returns `refresh_token: None`. That is the documented M2M model exactly.
The remaining v2 code paths are a compatibility shim for users on old
credentials, not unfinished work.

**The Connect flow is run for one field.** Under v3 the authorization code is
not exchanged for anything usable. Connect exists solely to put `user_id` in
the redirect, which then rides on every request as `sc-user-id`. The OAuth
dance is a user identity harvest wearing an OAuth costume.

**The climate command does not exist.** `switch.py` posts to
`/commands/climate/start` and `/commands/climate/stop`. Neither path is in the
spec, the prose, or the v2 reference.

**Polling fetches everything.** For v3 the coordinator issues a bare
`GET /vehicles/{id}/signals` with no filter, then discards what it does not
map. v2 sent a selective batch. There is no documented way to request a
subset, so this may be forced, but it interacts with per-vehicle request
limits and is worth a probe before raising the poll rate.

**December 1, 2026.** v2.0 is deprecated on that date, along with the
make-specific endpoints and security patches for every backend SDK.

## Hosts

| Host | Purpose | Auth | Used here |
| --- | --- | --- | --- |
| `iam.smartcar.com` | v3 token issue, client credentials only | none on the call itself | yes `S-spec` |
| `vehicle.api.smartcar.com/v3` | signals, commands, connections | `Authorization: Bearer` plus `sc-user-id` | yes `S-spec` |
| `management.api.smartcar.com/v3` | webhooks, subscriptions, applications | `Authorization: Bearer`, same token | **no** `S-spec` |
| `connect.smartcar.com` | the Connect consent flow | n/a | yes `C-code` |
| `auth.smartcar.com` | v2 token issue and refresh | HTTP Basic | legacy path only `D-docs` |
| `api.smartcar.com/v2.0` | v2 vehicle API | per vehicle bearer | legacy path only `D-docs` |
| `api.smartcar.com/management/v2.0` | v2 management API | management token | no `D-docs` |

The prose page for the Management API still names the `v2.0` host and a
management token, while `management.yaml` declares
`management.api.smartcar.com/v3` with `bearerAuth`. The spec is newer. Neither
has been exercised from here, so treat the v3 management host as `S-spec` and
probe before depending on it.

## Authentication

### v3, the current model

`S-spec`, `auth.yaml`:

```
POST https://iam.smartcar.com/oauth2/token
Content-Type: application/json

{"client_id": "client_...", "client_secret": "...", "grant_type": "client_credentials"}
```

Returns `access_token`, `token_type`, `expires_in`. The token is valid for one
hour and **there is no refresh token**. When it expires you request another.
Errors are `400 invalid_request` and `401 invalid_client`.

The spec body is JSON; the prose example sends
`application/x-www-form-urlencoded`. Both appear accepted. This integration
sends JSON `C-code`.

One token covers the whole application. It carries no user context, so every
signal read and every command must also carry:

```
sc-user-id: {userId}
```

`sc-user-id` is declared **required** on `GET /vehicles/{id}/signals` and on
all command endpoints. It is optional on `GET /connections`, where it acts as
a filter, and it is absent from `GET /vehicles/{id}` `S-spec`.

The `userId` comes from the Connect redirect. There is no endpoint that
derives it from a vehicle, which is why the config flow must run Connect even
though it throws the resulting code away.

### Version detection

This integration decides v2 versus v3 by the client ID prefix: `client_` means
v3 `C-code`. Nothing published guarantees that prefix is stable. It is a
reasonable heuristic and it is load bearing, so it deserves a test if the
format ever moves.

### v2, until December 1 2026

`POST https://auth.smartcar.com/oauth/token` with
`Authorization: Basic base64(client_id:client_secret)` and either
`grant_type=authorization_code` or `grant_type=refresh_token`. Tokens last two
hours and a refresh token is issued `D-docs`. Per vehicle, not per
application.

## Vehicle API endpoints

Base `https://vehicle.api.smartcar.com/v3`. All rows `S-spec`.

### Connections

| Method | Path | Purpose | Used here |
| --- | --- | --- | --- |
| GET | `/connections` | list vehicle connections | yes, at setup and migration |
| GET | `/connections/{connectionId}` | one connection | no |
| DELETE | `/connections/{connectionId}` | disconnect a vehicle | no |
| DELETE | `/users/{userId}` | remove a user and every connection | no |

`GET /connections` takes `filter[userId]`, `filter[vehicleId]`,
`filter[vehicle.mode]` (`live` or `simulated`, default `live`),
`filter[user.externalId]`, `page[number]` (default 1) and `page[size]`
(default 10).

**The integration passes none of them.** It reads `data[]` from the first page
only. An account with more than ten connections would silently lose vehicles,
and the "exactly one user" check in `_store_all_vehicles` would be deciding on
a partial set. The single user case is the only supported one, so nobody has
hit this, but the pagination is unhandled rather than deliberately ignored.

The two DELETE endpoints are the clean teardown this integration does not do.
`async_remove_entry` deletes the cloudhook and nothing else, so removing the
config entry leaves the Smartcar side connected.

### Vehicle data

| Method | Path | Purpose | Used here |
| --- | --- | --- | --- |
| GET | `/vehicles/{vehicleId}` | make, model, year, powertrainType, mode | no |
| GET | `/vehicles/{vehicleId}/signals` | every signal for the vehicle | yes, the poll |
| GET | `/vehicles/{vehicleId}/signals/{signalCode}` | one signal | yes, VIN only at setup |

93 single signal paths exist, one per code. The catalogue is at the end.

Setup reads make, model and year out of the `included.vehicle.attributes`
block on the VIN signal response rather than calling `GET /vehicles/{id}`
`C-code`. That works and saves a request. It also means `powertrainType` and
`mode` are never read, and `powertrainType` is the field that would let the
integration stop creating EV entities on a combustion vehicle.

### Commands

All POST unless noted. All return `200` on a fast completion or `202` when the
command runs long, plus `400 401 403 404 409 429 430 500 501 502 504`.

| Path | Body | Used here |
| --- | --- | --- |
| `/vehicles/{id}/commands/charge/start` | none | yes, switch |
| `/vehicles/{id}/commands/charge/stop` | none | yes, switch |
| `/vehicles/{id}/commands/charge/set-limit` | `data.attributes.percent`, integer 50 to 100 | yes, number |
| `/vehicles/{id}/commands/charge-port/open` | none | **no** |
| `/vehicles/{id}/commands/charge-port/close` | none | **no** |
| `/vehicles/{id}/commands/security/lock` | none | yes, lock and service |
| `/vehicles/{id}/commands/security/unlock` | none | yes, lock and service |
| `/vehicles/{id}/commands/navigation/set-destination` | `data.attributes.latitude`, `.longitude` | **no** |
| `/vehicles/{id}/charge-schedules/daily` | `location`, `startTime`, `stopTime` | **no** |
| `/vehicles/{id}/charge-schedules/weekly` | `location`, `days` keyed by weekday name | **no** |
| `/vehicles/{id}/charge-schedules/workweek` | `location`, `weekdays`, `weekends` | **no** |
| `/vehicles/{id}/charge-schedules/{scheduleId}` (DELETE) | none | **no** |

Note the `set-limit` floor: **50 percent is the minimum the API accepts**. The
`number` entity should enforce that rather than letting the vehicle reject it.

Charge schedules are make dependent and answer `501 COMPATIBILITY /
VEHICLE_NOT_CAPABLE` where unsupported. Schedules created through the API are
read back through the `charge-chargetimers` signal, which also reports
schedules set outside the API. Entries with `"type": "LOCATION"` carry a
`scheduleId` for the DELETE `D-docs`.

### The climate command does not exist

`switch.py` builds `/commands/climate/start` and `/commands/climate/stop`
`C-code`. Searching all three specs and the full prose dump finds no climate
command at any version. What exists is:

- `control_climate`, a permission `D-docs`
- `hvac-*` signals, all read only `S-spec`
- `POST /tesla/climate/cabin`, `/tesla/climate/defroster`,
  `/tesla/climate/steering_wheel`: make specific, v2 only, deprecated along
  with v2 `D-docs`

The compatibility matrix settles it from a fourth direction. It carries one
column per command, eleven in total: close charge port door, control
navigation, the three charge schedule creates, lock, open charge port door,
set charge limit, start charge, stop charge, unlock. **There is no climate
column for any vehicle** `M-matrix`. That is the same eleven commands the spec
declares, so the matrix and the spec agree with each other and disagree with
this integration.

So the standardized v3 climate command was never published. `CLIMATE` is in
`DEFAULT_ENABLED_ENTITY_DESCRIPTION_KEYS`, so the entity ships enabled and
every press should fail. Either the path is undocumented and working, in which
case it needs a probe and a `U-unver` row here, or it is dead and the entity
should go. Do not assume the first because the code exists.

## Management API

Base `https://management.api.smartcar.com/v3`, same bearer token. **None of
this is called by the integration.** All rows `S-spec`.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/applications` | list applications |
| GET | `/applications/{applicationId}` | one application |
| GET | `/applications/{applicationId}/secrets` | credential material |
| GET | `/subscriptions` | list webhook subscriptions |
| POST | `/subscriptions` | subscribe a vehicle to a webhook, returns `202` |
| GET | `/subscriptions/{subscriptionId}` | one subscription |
| DELETE | `/subscriptions/{subscriptionId}` | unsubscribe |
| GET | `/webhooks` | list webhooks |
| GET | `/webhooks/{webhookId}` | one webhook |

`GET /subscriptions` takes `filter[userId]`, `filter[webhookId]`,
`filter[vehicleId]`, `filter[vehicle.mode]`, `page[number]`, `page[size]` (max
100, default 25).

Today the user subscribes vehicles by hand in the Smartcar dashboard, and the
integration collects the Application Management Token purely as an HMAC key.
These endpoints would let setup subscribe and teardown unsubscribe.

## Webhooks

Inbound only, and the integration handles this part correctly.

- Every payload carries `SC-Signature`: HMAC-SHA256 of the raw body, keyed by
  the **Application Management Token**, not the access token `D-docs`.
- `VERIFY` events are unsigned. Respond with
  `{"challenge": hmac(token, challenge)}` hex encoded. The integration checks
  the event type before the signature for exactly this reason `C-code`.
- Event types are `VERIFY`, `VEHICLE_STATE`, `VEHICLE_ERROR` `D-docs`.
- `meta.mode` of `TEST` marks setup traffic; the integration acknowledges and
  takes no action `C-code`.

## Data formats

### JSON:API envelope

Every v3 response is JSON:API shaped. Collections carry `data[]`, `meta`
(`pageNumber`, `pageSize`, `totalCount`, and on connections `orderBy` and
`orderDirection`) and `links` (`self`, `first`, `prev`, `next`, `last`, with
`prev` and `next` nullable). Single resources carry `data` and `links`.

Signals responses add a sibling `included.vehicle` holding the vehicle
resource, which is where setup reads make, model and year.

### Signal object

```
data[].id
data[].type              = "signal"
data[].attributes.code     e.g. "tractionbattery-stateofcharge"
data[].attributes.name
data[].attributes.group
data[].attributes.status
data[].attributes.body     the value, shape varies by signal
data[].meta                retrievedAt, oemUpdatedAt, ingestedAt
data[].links
```

`body` is not a scalar. Multi valued signals nest differently per code, which
is why the coordinator carries `_SIGNAL_BODY_MULTIVALUE_ITEM_KEY_MAP`.
Timestamps live in `meta`, not in the value.

### Error object

`type`, `code`, `title`, `detail`, `status`, `resolution`,
`suggestedUserMessage`, `links`, `meta` `D-docs`.

`resolution.type` is the field worth acting on: `RETRY_LATER`,
`REAUTHENTICATE`, or `CONTACT_SUPPORT`. This integration branches on HTTP
status and ignores `resolution` entirely `C-code`, which is why it cannot tell
a transient upstream failure from one needing the user back in Connect.

`status` inside the body "in long running commands, may differ from http
response header" `D-docs`. That is the 202 problem stated from the other side.

### Error taxonomy

| Type | Status | Codes |
| --- | --- | --- |
| `AUTHENTICATION` | 401, 403 | UNAUTHORIZED, ACCESS_DENIED |
| `BILLING` | 430 | INVALID_PLAN, VEHICLE_LIMIT, VEHICLE_REQUEST_LIMIT, ACCOUNT_SUSPENDED |
| `COMPATIBILITY` | 501 | MAKE_NOT_COMPATIBLE, SMARTCAR_NOT_CAPABLE, VEHICLE_NOT_CAPABLE, PLATFORM_NOT_CAPABLE |
| `CONNECTED_SERVICES_ACCOUNT` | 400 | ACCOUNT_ISSUE, AUTHENTICATION_FAILED, NO_VEHICLES, SUBSCRIPTION, VEHICLE_MISSING, PERMISSION, VIRTUAL_KEY_REQUIRED |
| `RATE_LIMIT` | 429 | SMARTCAR_API, VEHICLE |
| `RESOURCE_NOT_FOUND` | 404 | PATH, VERSION |
| `SERVER` | 500 | INTERNAL, MULTIPLE_RECORDS_FOUND, RECORD_NOT_FOUND |
| `UPSTREAM` | 502 | INVALID_DATA, KNOWN_ISSUE, NO_RESPONSE, RATE_LIMIT, UNKNOWN_ISSUE |
| `VALIDATION` | 400 | INVALID_INPUT_FORMAT |
| `VEHICLE_STATE` | 409 | ASLEEP, CHARGING_IN_PROGRESS, CHARGING_PLUG_NOT_CONNECTED, DOOR_OPEN, FULLY_CHARGED, NOT_CHARGING, CHARGE_FAULT, HOOD_OPEN, IGNITION_ON, IN_MOTION, REMOTE_ACCESS_DISABLED, TRUNK_OPEN, UNKNOWN, UNREACHABLE, VEHICLE_OFFLINE_FOR_SERVICE |

All `D-docs`. `entity.py` handles 409, 429, 430, 500, 501 and 502. **504 is
in the spec's command responses and is not in that set** `S-spec`, so a
gateway timeout escapes as a bare `ClientResponseError`.

## The 202 contract

Commands resolving in under about 175 seconds return a single complete
response. Longer ones return `202 Accepted` with no `Content-Length`, hold the
connection open, and stream the real outcome on that same connection when the
command finishes `D-docs`.

A `202` is not a result. The docs are explicit: read the full body before
deciding whether the command worked.

`async_send_command` calls `raise_for_status()` and sets `success = True`
without reading the body `C-code`. A slow command that ultimately failed is
reported to Home Assistant as having succeeded.

## Permissions

Read: `read_alerts`, `read_battery`, `read_charge`, `read_charge_locations`,
`read_charge_records`, `read_charge_events`, `read_climate`, `read_compass`,
`read_diagnostics`, `read_engine_oil`, `read_extended_vehicle_info`,
`read_fuel`, `read_location`, `read_odometer`, `read_security`,
`read_service_history`, `read_speedometer`, `read_thermometer`, `read_tires`,
`read_user_profile`, `read_vehicle_info`, `read_vin`.

Control: `control_charge`, `control_climate`, `control_navigation`,
`control_security`, `control_pin`, `control_trunk`.

All `D-docs`. The integration's `Scope` enum carries 15 of the 28. Absent and
relevant: `read_thermometer` (the two `climate-*` temperature signals),
`read_speedometer` (`motion-currentspeed`), `read_service_history`,
`read_charge_records`, `control_navigation`, `control_trunk`.

Two mechanics worth knowing. A `scope` parameter in the Connect URL overrides
the dashboard's Vehicle Access configuration for that authorization, which is
what makes the integration's per entry scope picker work. And prefixing a
permission with `required:` locks the checkbox on the consent screen so the
owner cannot clear it `D-docs`. The integration never uses the prefix, so
every scope it asks for, including the two in `REQUIRED_SCOPES`, can be
unchecked by the user at consent time.

## Gaps

| Gap | Effect | Evidence |
| --- | --- | --- |
| Climate command path is not published | The default enabled climate switch should fail on every press | `S-spec` absent, `C-code` present |
| `202` treated as success without reading the body | Failed long running commands reported as succeeded | `D-docs`, `C-code` |
| `504` not in the handled command statuses | Gateway timeout escapes as a raw error | `S-spec` |
| `/connections` read unpaginated and unfiltered | Vehicles beyond the first 10 invisible; user count check reads a partial set | `S-spec` |
| Management API never called | Webhook subscribe and unsubscribe are manual dashboard steps | `S-spec` |
| `DELETE /connections/{id}` never called | Removing the config entry leaves the Smartcar connection live | `S-spec` |
| `charge/set-limit` floor of 50 not enforced locally | Values under 50 make a round trip only to be rejected | `S-spec` |
| `resolution.type` ignored | Cannot distinguish retry from reauthenticate | `D-docs` |
| `powertrainType` never read | EV entities created on combustion vehicles | `S-spec` |
| Charge schedules unimplemented | No scheduled charging through this integration | `S-spec` |
| Charge port open and close unimplemented | No charge port control | `S-spec` |
| Entities created from scopes, not from vehicle compatibility | On an ID. Buzz, 23 of 95 signals are supported, so most default enabled entities can never populate | `M-matrix` |
| 42 signals unmapped | Listed below | `S-spec` |

## On the SDKs

Do not adopt one. Smartcar's own position is that the backend SDKs are in
maintenance mode, receive security patches only through December 1 2026, and
that new v3 work should call the API directly over HTTP `D-docs`. That is what
this integration already does, and it is why `manifest.json` can ship an empty
`requirements` list.

The Flutter SDK (`github.com/smartcar/flutter-sdk`) is Connect flow only. It
wraps the native iOS and Android auth SDKs to launch the consent sheet and
return an auth code, user ID and optional external ID. It never calls the
vehicle API, so it has no bearing on this integration, which already does that
job through Home Assistant's own OAuth handler.

## What a given vehicle actually supports

The catalogue below is the API surface. It is not what any particular car
answers. Support varies by make, model, year **and region**, and the
difference is large enough that a signal being mapped here says almost nothing
about whether it will ever produce a value.

The compatibility matrix exported from the dashboard is the authority. It
carries one row per make, model, powertrain, region and year range, and one
column per signal and per command, valued `TRUE`, `-` (not supported) or `N/A`
(not applicable to the powertrain, for example the combustion columns on a
BEV).

Three signals appear in the matrix that have no path in the OpenAPI document:
`tractionbattery-estimatedrange`, `tractionbattery-idealrange` and
`tractionbattery-ratedrange` `M-matrix`. The spec exposes only
`tractionbattery-range`. One signal goes the other way:
`vehicleuseraccount-permissions` is in the spec and absent from the matrix.
Neither source is complete on its own.

### Worked example: Volkswagen ID. Buzz, BEV, US, 2025

23 of 95 signals and 5 of 11 commands `M-matrix`.

Supported:

| Group | Signals |
| --- | --- |
| Charge | `chargelimits`, `chargerate`, `chargingconnectortype`, `detailedchargingstatus`, `ischarging`, `ischargingcableconnected`, `ischargingcablelatched`, `timetocomplete`, `wattage` |
| Closure | `doors`, `enginecover`, `fronttrunk`, `islocked`, `reartrunk`, `sunroof`, `windows` |
| Location | `preciselocation` |
| Odometer | `traveleddistance` |
| Service | `records` |
| TractionBattery | `nominalcapacity`, `range`, `stateofcharge` |
| VehicleIdentification | `vin` |

Commands: lock, unlock, start charge, stop charge, set charge limit.

What that rules out, and why each one matters here:

- **No amperage at all.** `charge-amperage`, `charge-amperagemax` and
  `charge-amperagerequested` are all unsupported. Smartcar cannot read or
  influence the current draw on this vehicle. Charge rate control has to come
  from the EVSE side, not from the car.
- **No HVAC or climate signals**, on top of the climate command not existing.
  The whole climate feature set is unreachable on this vehicle by two
  independent routes.
- **No diagnostics whatsoever.** All 23 `diagnostics-*` columns are `-`. Five
  of them are in `DEFAULT_ENABLED_ENTITY_DESCRIPTION_KEYS`, so they are
  created enabled and can never populate.
- **No `connectivitystatus-isonline` or `-isasleep`**, so there is no way to
  tell a sleeping vehicle from a broken one.
- **No `wheel-tires`**, so the four tire pressure entities cannot populate.
- **No charge schedules and no charge port commands**, so three of the
  unimplemented commands above would not work on this vehicle anyway. The
  European ID. Buzz does get `charge-chargetimers`, along with
  `charge-chargeportstatuscolor` and `charge-energyadded`; the US 2025 row does
  not. Same model, different region, different surface.

The general lesson for this integration: entities are created from the granted
scopes, not from what the vehicle can answer. On a vehicle like this one, a
large majority of the default enabled entities are structurally incapable of
ever having a value, and `_BENIGN_SIGNAL_ERRORS` in the coordinator exists to
stop that flooding the log. Reading the compatibility of the connected vehicle
at setup, and declining to create entities it cannot support, would be a
better answer than demoting the resulting errors to debug.

## Signal catalogue

93 signals, 51 reachable from this integration. "mapped" means an entry in
`DATAPOINT_ENTITY_KEY_MAP`; "used" means read directly during setup; "unused"
means the signal arrives in the poll response and is discarded.

| Signal code | Group | In the integration | Entity key |
| --- | --- | --- | --- |
| `charge-amperage` | charge | mapped | `charge_amperage` |
| `charge-amperagemax` | charge | mapped | `charge_amperage_max` |
| `charge-amperagerequested` | charge | unused | - |
| `charge-chargelimits` | charge | mapped | `charge_limit` |
| `charge-chargeportstatuscolor` | charge | unused | - |
| `charge-chargerate` | charge | mapped | `charge_chargerate` |
| `charge-chargerecords` | charge | unused | - |
| `charge-chargerphases` | charge | unused | - |
| `charge-chargetimers` | charge | unused | - |
| `charge-chargingconnectortype` | charge | unused | - |
| `charge-detailedchargingstatus` | charge | mapped | `charging_state` |
| `charge-energyadded` | charge | mapped | `charge_energyadded` |
| `charge-fastchargertype` | charge | unused | - |
| `charge-ischarging` | charge | used | config flow / setup |
| `charge-ischargingcableconnected` | charge | mapped | `plug_status` |
| `charge-ischargingcablelatched` | charge | unused | - |
| `charge-ischargingportflapopen` | charge | unused | - |
| `charge-isfastchargerpresent` | charge | mapped | `charge_fast_charger_present` |
| `charge-timetocomplete` | charge | mapped | `charge_timetocomplete`, `charge_time_to_complete` |
| `charge-voltage` | charge | mapped | `charge_voltage` |
| `charge-wattage` | charge | mapped | `charge_wattage` |
| `climate-externaltemperature` | climate | unused | - |
| `climate-internaltemperature` | climate | unused | - |
| `closure-doors` | closure | mapped | `door_back_left_lock`, `door_back_right_lock`, `door_front_left_lock`, `door_front_right_lock`, `door_back_left`, `door_back_right`, `door_front_left`, `door_front_right` |
| `closure-enginecover` | closure | mapped | `engine_cover` |
| `closure-fronttrunk` | closure | mapped | `front_trunk`, `front_trunk_lock` |
| `closure-islocked` | closure | mapped | `door_lock` |
| `closure-reartrunk` | closure | mapped | `rear_trunk`, `rear_trunk_lock` |
| `closure-sunroof` | closure | mapped | `sunroof` |
| `closure-tailgate` | closure | unused | - |
| `closure-windows` | closure | mapped | `window_back_left`, `window_back_right`, `window_front_left`, `window_front_right` |
| `connectivitysoftware-currentfirmwareversion` | connectivitysoftware | mapped | `firmware_version` |
| `connectivitystatus-isasleep` | connectivitystatus | mapped | `asleep` |
| `connectivitystatus-isdigitalkeypaired` | connectivitystatus | mapped | `digital_key_paired` |
| `connectivitystatus-isonline` | connectivitystatus | mapped | `online` |
| `diagnostics-abs` | diagnostics | mapped | `diag_abs` |
| `diagnostics-activesafety` | diagnostics | unused | - |
| `diagnostics-airbag` | diagnostics | unused | - |
| `diagnostics-brakefluid` | diagnostics | unused | - |
| `diagnostics-driverassistance` | diagnostics | unused | - |
| `diagnostics-dtccount` | diagnostics | mapped | `diag_dtc_count` |
| `diagnostics-dtclist` | diagnostics | mapped | `diag_dtc_list` |
| `diagnostics-emissions` | diagnostics | unused | - |
| `diagnostics-evbatteryconditioning` | diagnostics | mapped | `diag_ev_battery_conditioning` |
| `diagnostics-evcharging` | diagnostics | mapped | `diag_ev_charging` |
| `diagnostics-evdriveunit` | diagnostics | mapped | `diag_ev_drive_unit` |
| `diagnostics-evhvbattery` | diagnostics | mapped | `diag_ev_hv_battery` |
| `diagnostics-lighting` | diagnostics | unused | - |
| `diagnostics-mil` | diagnostics | mapped | `diag_mil` |
| `diagnostics-oillife` | diagnostics | unused | - |
| `diagnostics-oilpressure` | diagnostics | unused | - |
| `diagnostics-oiltemperature` | diagnostics | unused | - |
| `diagnostics-telematics` | diagnostics | unused | - |
| `diagnostics-tirepressure` | diagnostics | unused | - |
| `diagnostics-tirepressuremonitoring` | diagnostics | unused | - |
| `diagnostics-transmission` | diagnostics | unused | - |
| `diagnostics-washerfluid` | diagnostics | unused | - |
| `diagnostics-waterinfuel` | diagnostics | unused | - |
| `hvac-cabintargettemperature` | hvac | mapped | `cabin_target_temperature` |
| `hvac-iscabinhvacactive` | hvac | mapped | `is_cabin_hvac_active`, `climate` |
| `hvac-isfrontdefrosteractive` | hvac | mapped | `is_front_defroster_active` |
| `hvac-isreardefrosteractive` | hvac | mapped | `is_rear_defroster_active` |
| `hvac-issteeringheateractive` | hvac | mapped | `is_steering_heater_active` |
| `internalcombustionengine-amountremaining` | internalcombustionengine | mapped | `fuel` |
| `internalcombustionengine-fuellevel` | internalcombustionengine | mapped | `fuel_percent` |
| `internalcombustionengine-oillife` | internalcombustionengine | mapped | `engine_oil` |
| `internalcombustionengine-range` | internalcombustionengine | mapped | `fuel_range` |
| `location-isathome` | location | unused | - |
| `location-preciselocation` | location | mapped | `location` |
| `lowvoltagebattery-stateofcharge` | lowvoltagebattery | mapped | `low_voltage_battery_level` |
| `lowvoltagebattery-status` | lowvoltagebattery | unused | - |
| `motion-currentspeed` | motion | unused | - |
| `odometer-traveleddistance` | odometer | mapped | `odometer` |
| `service-isinservice` | service | unused | - |
| `service-records` | service | unused | - |
| `surveillance-brand` | surveillance | unused | - |
| `surveillance-isenabled` | surveillance | mapped | `surveillance_enabled` |
| `tractionbattery-isheateractive` | tractionbattery | mapped | `battery_heater_active` |
| `tractionbattery-maxrangechargecounter` | tractionbattery | unused | - |
| `tractionbattery-nominalcapacity` | tractionbattery | mapped | `battery_capacity` |
| `tractionbattery-range` | tractionbattery | mapped | `range` |
| `tractionbattery-stateofcharge` | tractionbattery | mapped | `battery_level` |
| `transmission-drivemode` | transmission | unused | - |
| `transmission-gearstate` | transmission | mapped | `gear_state` |
| `vehicleidentification-exteriorcolor` | vehicleidentification | unused | - |
| `vehicleidentification-nickname` | vehicleidentification | unused | - |
| `vehicleidentification-packages` | vehicleidentification | unused | - |
| `vehicleidentification-trim` | vehicleidentification | unused | - |
| `vehicleidentification-vin` | vehicleidentification | used | config flow / setup |
| `vehicleuseraccount-permissions` | vehicleuseraccount | unused | - |
| `vehicleuseraccount-role` | vehicleuseraccount | unused | - |
| `wheel-style` | wheel | unused | - |
| `wheel-tires` | wheel | mapped | `tire_pressure_back_left`, `tire_pressure_back_right`, `tire_pressure_front_left`, `tire_pressure_front_right` |
