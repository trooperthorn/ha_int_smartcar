# Report for upstream: v3 API defects and suggested fixes

A review of `wbyoung/smartcar` at `6234add` against Smartcar's **published
OpenAPI documents** rather than the prose reference, plus a per-vehicle
compatibility export from the Smartcar dashboard.

This is a report, not a pull request. Everything below is written so it can be
applied directly upstream, and every patch in the "Defects" section works on
the core this repository currently pins (`pytest-homeassistant-custom-component
0.13.252`, core 2025.6.1) as well as on current core. Nothing here requires the
version bump discussed at the end.

## How this was produced

| Source | What it is |
| --- | --- |
| `smartcar.com/docs/specs/auth.yaml`, `full-vehicle.yaml`, `management.yaml` | The published OpenAPI documents. Machine generated, and the most reliable source. |
| `smartcar.com/docs/llms-full.txt` | The whole prose reference in one file. Useful, but it lags the specs. |
| Compatibility matrix export | Dashboard, Compatible Vehicles. One row per make, model, powertrain, region and year range; one column per signal and per command. |
| This integration's source and test fixtures | What the code actually sends. |

**What is not verified:** none of this was probed against a live Smartcar
account. The spec rows are as good as the published specification; the code
rows say what the integration sends, not what the service accepts. Where that
distinction matters it is called out.

A fuller write-up of the whole API surface, including a 93 row signal
catalogue and the endpoints the integration does not use, is in
[docs/api-reference.md](docs/api-reference.md) in this fork.

## Summary

| # | Defect | Impact | Size of fix |
| --- | --- | --- | --- |
| 1 | A rejected v3 credential raises `AssertionError` | Config flow aborts with an unknown error and no reauth | 6 lines |
| 2 | The climate switch posts to a path that does not exist | Default enabled entity fails on every press | ~10 lines |
| 3 | A `202` is treated as success without reading the body | Failed long running commands reported as succeeded | ~30 lines |
| 4 | The access token is written to the debug log | Application level credential in logs pasted into issues | 1 line |
| 5 | `/connections` is read without pagination | Vehicles past the first ten are lost | ~35 lines |
| 6 | `504` is not in the handled command statuses | Gateway timeout escapes as a raw error | 2 lines |
| 7 | A truncated signals response is silent | Entities go stale with no explanation | ~10 lines |

Plus one deprecation with a hard deadline (2026.12), and one larger proposal
about entity creation.

## Defects

### 1. A rejected v3 credential raises `AssertionError`

`custom_components/smartcar/application_credentials.py`.

`_token_request` never checks the HTTP status. Smartcar answers
`401 invalid_client` for a credential it does not recognise and
`400 invalid_request` for a malformed call `S-spec`. Both fall through to:

```python
data = await response.json()

assert data.get("token_type", "").lower() == "bearer", "Invalid token type."
assert data.get("access_token"), "Invalid access token."
```

`async_step_creation` catches `TimeoutError`, `ClientResponseError`,
`ClientError` and `ValueError`. It does not catch `AssertionError`, so the flow
raises out and the user sees an unknown error with no reauth offered and
nothing actionable in the log.

**Why this matters more than it looks.** The Smartcar dashboard issues two
different credentials that are easy to confuse:

- Home Assistant's Application Credentials dialog wants the **API credential**,
  whose client ID begins `client_` (Dashboard, API Credentials)
- the config flow separately wants the **Application ID** (Dashboard,
  Configuration), which is used only to build the Connect URL

`api_version_for_client_id` decides v2 versus v3 purely on the `client_`
prefix. Paste the Application ID into the Application Credentials dialog and
the entry silently becomes a v2 entry and talks to `auth.smartcar.com`. That is
a very plausible route into this code path for anyone migrating, and right now
it produces an error message that does not help them find it.

**Minimal fix.** `raise_for_status()` alone is enough: on the core this repo
pins, `async_step_creation` maps a resulting `ClientResponseError` with status
401 to `oauth_unauthorized` and everything else to `oauth_failed`.

```diff
         session = async_get_clientsession(self.hass)
         response = await session.post(
             self.token_url,
             json={
                 "client_id": self.client_id,
                 "client_secret": self.client_secret,
                 "grant_type": "client_credentials",
             },
         )
+
+        # a rejected credential is a 401 invalid_client and a malformed call a
+        # 400 invalid_request. both used to fall through to the asserts below,
+        # which the config flow does not catch.
+        response.raise_for_status()
+
         data = await response.json()
 
-        assert data.get("token_type", "").lower() == "bearer", "Invalid token type."
-        assert data.get("access_token"), "Invalid access token."
+        if str(data.get("token_type", "")).lower() != "bearer" or not data.get(
+            "access_token"
+        ):
+            msg = "Smartcar returned a token response with no usable bearer token"
+            raise ClientError(msg)
 
         return {"refresh_token": None, **data}
```

with `from aiohttp import ClientError` added to the imports.

**Optional improvement.** Smartcar's error body carries `error` and
`error_description`. Reading the body before `raise_for_status()` and logging
that text makes the difference between "invalid_client" and "invalid_request"
visible, which tells the user whether the credential is wrong or the request
was. On current core these can be raised as
`OAuth2TokenRequestReauthError` and `OAuth2TokenRequestTransientError` from
`homeassistant.exceptions`, which gives a proper reauth rather than an abort,
but those classes do not exist in 2025.6, so that part needs the version bump.

### 2. The climate switch posts to a path that does not exist

`custom_components/smartcar/switch.py`. `SmartcarClimateSwitch` builds
`/commands/climate/start` and `/commands/climate/stop` for v3.

Neither path exists. Checked against four independent sources:

- **`full-vehicle.yaml`** declares exactly eleven commands: `charge/start`,
  `charge/stop`, `charge/set-limit`, `charge-port/open`, `charge-port/close`,
  `security/lock`, `security/unlock`, `navigation/set-destination`, and the
  three `charge-schedules/*` creates plus a delete. No climate.
- **The prose reference** lists the same set under Commands Overview.
- **The v2 reference** has no standardized climate command either. The only
  climate commands that ever shipped were make specific: `POST /tesla/climate/cabin`,
  `/tesla/climate/defroster`, `/tesla/climate/steering_wheel`. Those are
  deprecated along with v2.0 on 2026-12-01.
- **The compatibility matrix** carries one column per command, eleven of them,
  matching the spec exactly. There is no climate column for any vehicle.

`control_climate` does exist as a permission, and the `hvac-*` signals exist and
are read only. That is presumably where the entity came from.

`EntityDescriptionKey.CLIMATE` is in `DEFAULT_ENABLED_ENTITY_DESCRIPTION_KEYS`,
so the entity ships enabled and every press should fail.

**Suggested fix.** Create the entity for v2 entries only, and drop the v3
command construction rather than leaving it as unreachable code:

```diff
     entities += [
         SmartcarClimateSwitch(coordinator, description)
         for coordinator in coordinators.values()
         for description in CLIMATE_ENTITY_DESCRIPTIONS
-        if coordinator.is_scope_enabled(description.key, verbose=True)
+        if coordinator.version == "v2"
+        and coordinator.is_scope_enabled(description.key, verbose=True)
     ]
```

and in both `async_turn_on` and `async_turn_off`, replace the version branch
with the v2 call directly:

```python
await self._async_send_command("/climate", {"action": "START"})
```

If you have evidence the v3 path works undocumented, that would be worth
recording, because it contradicts all four sources above. We did not probe it.

### 3. A `202` is treated as success without reading the body

`custom_components/smartcar/entity.py`, `async_send_command`.

Smartcar documents that a command taking longer than about 175 seconds returns
`202 Accepted` with **no `Content-Length`**, holds the connection open, and
streams the real outcome on that same connection when the command finishes. The
docs are explicit that a `202` is not the final result and that the full body
must be read to determine success. They also note that `status` inside an error
body "in long running commands, may differ from http response header".

The current code is:

```python
resp.raise_for_status()
success = True
```

`raise_for_status()` passes on a `202`, the body is never read, and the caller
is told the command worked. A slow command that ultimately failed is reported
to Home Assistant as having succeeded.

**Suggested fix.** Read the body on every command and fail when it carries an
error payload, whatever the status line said:

```python
async def _async_raise_for_streamed_error(
    resp: ClientResponse,
    subpath: str,
    coordinator: SmartcarVehicleCoordinator,
) -> None:
    """Fail the command when the body reports an error the status line did not."""
    try:
        body = await resp.json(content_type=None)
    except (ClientError, ValueError):
        # an empty or unreadable body with a success status is the fast path
        return

    if not isinstance(body, dict):
        return

    nested = body.get("error")
    error: dict[str, Any] = nested if isinstance(nested, dict) else body

    if not (code := error.get("code")) or not (error_type := error.get("type")):
        return

    status = error.get("status") or resp.status
    detail = error.get("detail") or error.get("title") or code

    _LOGGER.warning(
        "Command %s for %s (VIN: %s) returned HTTP %s but reported %s/%s: %s",
        subpath,
        coordinator.vehicle_id,
        coordinator.vin,
        resp.status,
        error_type,
        code,
        detail,
    )

    raise SmartcarAPIError(int(status), f"{code}: {detail}")
```

called immediately after `resp.raise_for_status()`. It handles both the
top-level and `{"error": {...}}` shapes, and leaves any body that is not
recognisably an error alone.

This pairs with the recent `4d47350 fix: report failed entity commands to Home
Assistant`: same class of problem, one layer deeper.

### 4. The access token is written to the debug log

`custom_components/smartcar/auth.py`, in `request`:

```python
_LOGGER.debug(
    "HTTP %s request %s/%s %r headers=%r",
    method,
    self._endpoints[version],
    path,
    kwargs,
    headers,
)
```

`headers` contains `authorization: Bearer <token>`. Under v3 that token is
**application level**, not per vehicle: it authorises every vehicle on the
account for its lifetime. Debug logs are routinely pasted into issue reports.

This is the same class of issue as `43e914c fix: omit management token from
webhook debug logging`.

**Fix:**

```diff
-            headers,
+            {**headers, "authorization": "<redacted>"},
```

### 5. `/connections` is read without pagination

`custom_components/smartcar/__init__.py`, `_store_all_vehicles`.

```python
connections_list_resp = await auth.request_v3("get", "connections")
```

`GET /connections` is paginated and takes `page[number]` (default 1) and
`page[size]` (**default 10**), plus `filter[userId]`, `filter[vehicleId]`,
`filter[vehicle.mode]` and `filter[user.externalId]` `S-spec`. The response
carries `meta.totalCount` and `links.next`.

The integration passes none of them and reads `data[]` from the first page. On
an account with more than ten connections that silently loses vehicles, and it
also makes the "exactly one user" check decide on a partial set, which would
report a multi-user application for an account that simply has a lot of cars.

**Suggested fix.** A helper that walks the pages, with a hard stop so a
service that always claims more pages cannot spin forever:

```python
CONNECTIONS_PAGE_SIZE = 100
CONNECTIONS_PAGE_LIMIT = 50


async def _fetch_all_connections(auth: AbstractAuth) -> list[dict]:
    """Read every page of /connections."""
    connections: list[dict] = []
    page = 1

    while page <= CONNECTIONS_PAGE_LIMIT:
        response = await auth.request_v3(
            "get",
            "connections",
            params={"page[number]": page, "page[size]": CONNECTIONS_PAGE_SIZE},
        )
        response.raise_for_status()
        body = await response.json()
        connections.extend(body.get("data", []))

        total = body.get("meta", {}).get("totalCount")

        if not body.get("data") or total is None or len(connections) >= total:
            break

        page += 1
    else:
        _LOGGER.warning(
            "Stopped reading connections after %s pages", CONNECTIONS_PAGE_LIMIT
        )

    return connections
```

then read `vehicle_ids` and `user_ids` from its result instead of from
`connections_list_data.get("data", [])`.

### 6. `504` is not in the handled command statuses

`custom_components/smartcar/entity.py` recognises 409, 429, 430, 500, 501 and
502. `full-vehicle.yaml` lists `504` among the documented responses for every
command endpoint. A gateway timeout therefore escapes `async_send_command` as a
bare `ClientResponseError` rather than becoming a `SmartcarAPIError` and a
translated error for the user.

```diff
 ERROR_STATUS_UPSTREAM = 502
+ERROR_STATUS_GATEWAY_TIMEOUT = 504
```

and add it to the `elif err.status in {...}` set.

### 7. A truncated signals response is silent

`custom_components/smartcar/coordinator.py`, `_merge_signal_data` reads
`signal_data.get("data", [])` and ignores `meta`.

The v3 signals response is JSON:API shaped and its envelope advertises paging
(`meta.pageNumber`, `pageSize`, `totalCount`), although no page parameter is
documented on that endpoint. In every capture we have, the page held the whole
set (`pageSize` equal to `totalCount`), so this is not currently biting. But if
it ever starts truncating, entities would simply stop updating with no
explanation.

A cheap guard:

```python
signals = signal_data.get("data", [])
total = signal_data.get("meta", {}).get("totalCount")

if total is not None and total > len(signals):
    _LOGGER.warning(
        "Coordinator %s: signals response reported %s signals but returned %s; "
        "some entities will not update",
        self.name,
        total,
        len(signals),
    )
```

## Due before core 2026.12: update listener with reloading methods

`async_setup_entry` registers `entry.add_update_listener(...)`, and the config
flow calls `async_update_reload_and_abort` in two places. Home Assistant
deprecated that combination in 2026.6 and it becomes an **error in 2026.12**
(developer blog, 2026-05-07: "Deprecating config entry listener with reloading
methods in config flow").

In core 2026.9.2 this is still only a warning on the path you use:
`ConfigFlow.async_update_reload_and_abort` calls `report_usage(...)` with
`core_behavior=ReportBehavior.LOG` and `breaks_in_ha_version="2026.12.0"`, so it
logs today and stops working then. The subentry variant of the same method
already raises `ValueError("Cannot update and reload entry with update
listeners")`, which is where it is heading.

The blog offers three ways out. Two of them do not fit here:

- `OptionsFlowWithReload` reloads only when **options** change, and
  `SmartcarOptionsFlow` writes to `entry.data` and calls
  `async_create_entry(data={})`, so it would never fire.
- `_abort_if_unique_id_configured(reload_on_update=False)` addresses only one
  of the two call sites.

What worked cleanly was removing the listener entirely and letting whoever
changed the data own the reload:

- delete the `add_update_listener` registration and the `async_update_listener`
  function
- leave the config flow's `async_update_reload_and_abort` calls as they are,
  which become legal again once no listener exists
- in the options flow, capture the return of `async_update_entry` and call
  `self.hass.config_entries.async_schedule_reload(...)` when it reports a change

One test needs updating with this: a bare `async_update_entry` no longer
reloads by itself, which is the point.

## Larger proposal: create entities from vehicle capability, not from scopes

This one is a design change rather than a fix, so it is separated out.

Entities are currently created for every datapoint whose scopes were granted.
Scopes say what the user allowed; they say nothing about what the car can
answer, and the gap is large. From the compatibility matrix, a **2025 US
Volkswagen ID. Buzz** supports **23 of 95 signals** and 5 of 11 commands. It
reports no diagnostics at all, yet five diagnostic entities are in
`DEFAULT_ENABLED_ENTITY_DESCRIPTION_KEYS`. It reports no HVAC, no tire data, no
online or asleep status, and no amperage.

`_BENIGN_SIGNAL_ERRORS` exists precisely because of this: it demotes the
resulting permanent errors to debug so they do not flood the log. That treats
the symptom.

The information needed is already on the wire. In the v3 signals response, a
signal the vehicle cannot produce comes back with:

```json
{"value": "ERROR", "error": {"type": "COMPATIBILITY", "code": "VEHICLE_NOT_CAPABLE"}}
```

The distinction that matters, and the part worth getting right: **only
`COMPATIBILITY` means incapacity.** `VEHICLE_STATE` means "not right now"
(charge rate reads as an error while the car is unplugged) and `PERMISSION`
means the user needs to re-consent. Treating either of those as incapacity
would delete entities that work. In the `vw_id_4` fixture in this repository,
`closure-fronttrunk` carries a `PERMISSION` error and
`internalcombustionengine-range` too, while `closure-sunroof` and the
connectivity signals carry `COMPATIBILITY`.

The shape that worked, roughly:

1. a `frozenset[str]` of incapable codes on the coordinator, empty by default
2. one `GET /vehicles/{id}/signals` before `async_forward_entry_setups`, whose
   result is kept as coordinator data so it stands in for the first refresh
   rather than adding a request
3. the six platforms already share one predicate,
   `coordinator.is_scope_enabled(description.key, verbose=True)`, so extending
   that into a single `is_entity_supported` covering both questions is a
   one-line change per platform
4. failure of the capability read, or `pref_disable_polling` being set, leaves
   the set empty, which creates everything exactly as today

Two properties worth preserving if you take this on: an **unknown** result must
never hide anything, and the set should be a list of things to hide rather than
a list of things to show, so that every failure mode degrades to current
behaviour.

We have this working with tests, in this fork's `fix/v3-api-defects` branch, if
it is useful as a reference. It is not offered as a pull request.

## When you next bump core

Not urgent, but worth knowing before it surprises you. Moving the test pins
from core 2025.6.1 to 2026.9.2 surfaced exactly two breaks, both in tests:

- `device_registry.async_get_device` now raises a `RuntimeError` telling you to
  use `async_get_device_by_identifier(identifier, config_entry_id)` instead.
  Five call sites, in `tests/conftest.py` and `tests/test_init.py`.
- Entity registry snapshot serialisation changed: attribute keys are enums
  rather than plain strings, and `aliases` serialises as a list rather than a
  set. The regenerated snapshots are values identical.

Also note that `pytest-homeassistant-custom-component` resolves a beta of the
core it was built from, so `homeassistant==2026.9.2` needs pinning explicitly
after it, and core 2026.x requires Python 3.14.

The rollback guard in `async_migrate_entry`:

```python
if config_entry.version > 2:
    return False
```

is unreachable on current core. `ConfigEntry.async_migrate` checks
`self.version > handler.VERSION` itself, logs, and returns before calling
`async_migrate_entry`.

## Two things that are correct and worth keeping

Since this document is a list of problems, for balance:

**The v2 to v3 M2M migration is already done, and done right.** Overriding
`_token_request` to post `grant_type=client_credentials` and return
`refresh_token: None` is exactly the documented model, and carrying `user_id`
from the Connect redirect as `sc-user-id` is the right way to supply the
per-user context an application level token lacks. Smartcar's own guidance is
that the backend SDKs are in maintenance mode and new v3 work should call the
API directly over HTTP, which is what this integration already does.

**Webhook signature handling is correct and current.** `SC-Signature` keyed by
the Application Management Token, with the `VERIFY` event type checked before
the signature because that one event is unsigned.

## Deprecation clock

Vehicles API v2.0 is deprecated on **2026-12-01**, along with the make specific
endpoints and security patches for every backend SDK. Every `if version == "v2"`
branch becomes dead code on that date.
