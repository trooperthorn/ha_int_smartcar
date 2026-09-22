# Follow-up work

Gaps found while building the budget-aware polling and the blueprints. Each one
is a thing the integration cannot currently do that something else wanted it
to, ordered by how much it blocks.

## Blocking a blueprint that would otherwise exist

### 1. No charging-current control on vehicles that do not expose amperage

`charge-amperage`, `charge-amperagemax` and `charge-amperagerequested` exist in
the v3 signal schema, but support is per vehicle: a 2025 US ID. Buzz reports
none of them, and Smartcar publishes no command to set current on any vehicle.
So "charge at 16 A" is not expressible through Smartcar at all.

The `emporia_taper_current_to_state_of_charge` blueprint works around it by
keying the **Emporia** charger's current off the **Smartcar** state of charge,
which is why that blueprint has to exist. A single-integration answer is not
available and will not be until Smartcar ships a current command.

**Action:** none possible here. Documented so the next person does not go
looking for the endpoint.

### 2. No sensor for "is the charge schedule active"

Charge schedules can be created through v3 (`charge-schedules/daily`, `weekly`,
`workweek`) and read back through the `charge-chargetimers` signal, but the
integration implements neither. A blueprint cannot currently ask "is a
scheduled charge already going to handle this" before starting one itself.

**Action:** map `charge-chargetimers` to a sensor, then add the three schedule
commands and the delete. Medium sized; the request bodies are in
`docs/api-reference.md`.

### 3. Charge port open and close are unimplemented

Both commands exist in v3 and neither is wired up. On vehicles that support
them it is the difference between an automation that can fully prepare the car
and one that needs a human at the flap.

**Action:** two entities, or two actions. Small.

## Quality scale rules not yet met

Recorded in `custom_components/smartcar/quality_scale.yaml` with the same
detail. Summarised here because they are real work, not paperwork.

All three **Platinum** rules are met, as is every Bronze and Silver rule. The
four below are Gold, and they are the only thing keeping the manifest from
claiming a higher tier: the scale is contiguous, so Gold has to be complete
before Platinum can be claimed.

### 4. `entity-translations` and `icon-translations`

Most entity descriptions carry a literal `name=` and `icon=`. Gold wants a
`translation_key` with entries under `entity:` in the translations, and icons
moved into an `icons.json` keyed by the same. The two are one job because icons
key off the translation key.

**Action:** mechanical but wide, touching every entity description.

### 5. `dynamic-devices` and `stale-devices`

A vehicle added to the Smartcar account after setup is not picked up until the
entry is reconfigured, and one removed leaves its device behind. Both want the
same thing: reconcile `GET /connections` against the device registry
periodically.

**Action:** one coordinator, one reconciliation pass. `/connections` is an
application-level call and does not come out of any vehicle's allowance, so
this is cheap to run.

## Smaller, non-blocking

### 6. `powertrainType` and the granted scopes are never read, from a free call

A live run showed `GET /connections` already carries
`attributes.vehicle.powertrainType`, make, model, year and mode, plus
`attributes.permissions`: the scopes Smartcar actually granted. The integration
makes that call at setup and reads only the ids out of it.

Half of this is now done: setup identifies a vehicle from the connection and
tolerates a missing VIN signal, so an empty signal store no longer stops it.

What remains is `permissions`. Entity gating still uses the scopes the config
flow asked for rather than the ones Smartcar granted, and they are not the same
list. `powertrainType` is read but not yet used to keep combustion entities off
a BEV.

### 7. `resolution.type` in error bodies is ignored

Smartcar tells you whether an error wants `RETRY_LATER`, `REAUTHENTICATE` or
`CONTACT_SUPPORT`. The integration branches on HTTP status instead, so it
cannot tell a transient upstream failure from one that needs the user back in
Connect. A signal-level `PERMISSION` error with `REAUTHENTICATE` is the case
that matters: today it is logged and otherwise ignored.

### 8. `DELETE /connections/{id}` is never called

Removing the config entry leaves the vehicle connected on the Smartcar side.
Now that the Management API client exists, unsubscribing and disconnecting on
`async_remove_entry` is a short addition.

### 9. The budget is Home Assistant's tally, not Smartcar's

Smartcar publishes no remaining-calls endpoint, so the count cannot be
reconciled. If anything else uses the same Smartcar application, this
undercounts. A `430 VEHICLE_REQUEST_LIMIT` response could at least be used to
snap the local count to the ceiling when it arrives, which would self-correct
the drift.

## Found on a live account

The first run of `script/smartcar_doctor.py` against a real Smartcar account,
2026-09-20. These are not theoretical.

### 10. An empty signal store is silent

A live account returned `200` with `data: []` and `totalCount: 0` from
`GET /vehicles/{id}/signals`, the request the poll makes. Every entity is
created, none ever gets a value, and nothing raises: no `UpdateFailed`, no
repair issue, no log above debug. The coordinator's capability read reports
"cannot answer 0 of 0 signals" and carries on.

The cause is now known, and it is visible for free. The webhook that feeds the
store carries `isEnabled`, `triggers` and `data`; a disabled webhook, or one
with both lists empty, collects nothing and produces exactly this. The
integration already lists webhooks during auto-subscribe, so it has the answer
in hand at setup and says nothing about it.

**Action:** raise a repair issue when the webhook this instance subscribes to
is disabled, unverified, or has no triggers and no data signals, naming what to
enable. Treat an empty signal collection as a condition of its own rather than
creating a full set of permanently unavailable entities.

The full model, including why a `200` with an empty array is the worst shape this
failure could take and the dashboard checklist that clears it, is in
[communication.md](communication.md). A 2026-09-22 look at the live account
narrowed the cause further: the webhook's triggers and data signals are now
populated, but its callback URI is the OAuth redirect URL rather than a webhook
receiver, so it has never verified and has never delivered anything. A repair
issue that only checks `isEnabled` and the two lists would miss that, so check
the verification state and the callback URI too.

### 11. Auto-subscribe cannot create the webhook it needs

`async_subscribe` subscribes a vehicle to an existing webhook, found by matching
its callback URL. An application with no webhook at all, which is what a new
Smartcar account has, matches nothing, so setup completes and subscribes
nothing. The first live run against a real account listed zero webhooks, so this
is the normal case rather than an edge one.

Creating the webhook is a dashboard step today: a name, the callback URL, the
triggers and data signals to send, and a verification challenge that Home
Assistant has to answer with the Application Management Token. The Management
API declares a create endpoint, so most of that could move into setup, but the
signal selection is a real choice (the free tier carries about nine of each) and
would need a form rather than a default.

**Action:** at minimum, say so. Setup should report that no webhook points at
this instance and that scheduled polling is therefore the only source of data,
instead of leaving it silent.

Two additions from the 2026-09-22 account review, both detailed in
[communication.md](communication.md). First, `async_subscribe` matching on the
callback URL is what makes a wrong URI break subscription as well as
verification, so the no-match case should log the URL it expected alongside the
`callbackUri` values it actually saw, rather than failing invisibly. Second, the
signal selection this would have to offer is bounded by the plan, not by the
vehicle: a Free plan enables only nine signals and locks the `Charge`, `Location`,
`Climate`, `Diagnostics` and `HVAC` groups entirely. Granted scope and
configurable signal are two different capability sets, and
[vehicle-and-battery-status.md](vehicle-and-battery-status.md) lists which signals
fall on which side. Note also that the sentence above is optimistic about the
create endpoint: `management.yaml` declares no `POST /webhooks`, only the
subscription endpoints, so webhook creation is a dashboard-only step and this
item cannot be closed by calling an API.
