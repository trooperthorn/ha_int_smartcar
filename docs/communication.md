# How this integration talks to Smartcar

The v3 communication chain end to end, the three credentials it needs, the
webhook model that actually feeds it, the ways it fails silently, and a checklist
for fixing a live account from the Smartcar dashboard.

Written 2026-09-22 from the published OpenAPI documents, the prose reference, the
code in `custom_components/smartcar/`, and a live walkthrough of the Smartcar
dashboard. Claim labels are the same ones `api-reference.md` uses, with one
addition:

| Label | Source |
| --- | --- |
| `S-spec` | The OpenAPI documents at `smartcar.com/docs/specs/` |
| `D-docs` | The prose reference at `smartcar.com/docs`; append `.md` to any page for markdown |
| `C-code` | Observed in this integration |
| `M-matrix` | The per-vehicle compatibility matrix exported from the dashboard, 2026-09-20 |
| `L-portal` | A live walkthrough of the Smartcar dashboard, 2026-09-22 |
| `L-live` | Observed against a live account by `script/smartcar_doctor.py`, 2026-09-20 |
| `U-unver` | Believed, confirmed by none of the above |

Where `L-portal` disagrees with anything older, the portal wins: the account's
configuration changed between the two dates.

## The one paragraph that matters

Under v3, `GET /vehicles/{id}/signals` reads a **server-side store**. What fills
that store is a webhook that has verified, is enabled, has data signals
configured, and has the vehicle subscribed to it. If any of that is missing the
store stays empty, the endpoint answers `200` with `data: []`, every single-signal
path answers `404`, every entity is created and none ever gets a value, and
nothing raises. It looks precisely like a broken API or a broken integration, and
it is neither.

## The chain, in order

Break any link and the symptom is the same.

### 1. Credentials

Three of them, plus a legacy pair that exists to confuse you. See "Credentials"
below.

### 2. Connect, run once, for one field

```
GET https://connect.smartcar.com/oauth/authorize
    ?response_type=code
    &application_id={applicationId}
    &redirect_uri={a registered redirect URI}
    &scope={space separated permissions}
    &mode=live
    &state={csrf}
```

`S-spec` `D-docs`. `config_flow.py` builds exactly this, substituting the
**Application ID** for `client_id` on a v3 entry and passing `mode` and `scope` as
extra authorize data `C-code`.

The redirect carries `user_id`, `state`, `code`, and `external_id` if one was
sent. **Only `user_id` matters.** Under v3 the authorization code is never
exchanged for anything usable, because the token model is client credentials.
Connect creates the connection and hands back a user identity; the OAuth dance
around it is a costume. `user_id` becomes the `sc-user-id` header on every later
request.

The Flutter Connect SDK makes the same point from the other side: its success type
carries `code`, `state`, `userId` and `externalId`, and its `responseType: none`
mode completes with **no code at all**. The code is optional. The user ID is not.

### 3. The token

```
POST https://iam.smartcar.com/oauth2/token
Content-Type: application/json

{"client_id": "client_...", "client_secret": "...", "grant_type": "client_credentials"}
```

`S-spec`. Returns `access_token`, `token_type` and `expires_in` (3600). **No
refresh token**: when it expires you ask for another. One token covers the whole
application and carries no user context.

`application_credentials.py` overrides `_token_request` to post this and return
`refresh_token: None` `C-code`. Errors are `400 invalid_request` for a malformed
request and `401 invalid_client` for a bad ID or secret; both now surface as a
reauth carrying Smartcar's own error text.

### 4. Headers

| Header | Required | Notes |
| --- | --- | --- |
| `Authorization: Bearer {token}` | always | |
| `sc-user-id: {userId}` | signals and all commands | Optional on `/connections`, where it filters. Absent from `GET /vehicles/{id}` |
| `SC-Unit-System` | optional | `metric` or `imperial`, default metric |

`S-spec`. Omitting `sc-user-id` on a signals read answers
`400 VALIDATION / MISSING_PARAMETER`, detail `missing userId` `L-live`.

### 5. Connections

```
GET https://vehicle.api.smartcar.com/v3/connections
```

Free of any vehicle's request allowance, so it is the cheapest health check there
is: a connection coming back means credentials, token and user ID are all correct
and the problem is downstream. It carries make, model, year, `mode`,
`powertrainType` and `attributes.permissions`, **the scopes Smartcar actually
granted**, which is not the same list the config flow asked for `L-live`.

### 6. A webhook, or nothing

See "The webhook model".

### 7. Signals

```
GET https://vehicle.api.smartcar.com/v3/vehicles/{id}/signals
```

The coordinator's poll. JSON:API envelope with `data[]`, `meta.totalCount` and a
sibling `included.vehicle`. Each element carries `attributes.body` (the payload),
`attributes.status.value` (`SUCCESS`, `ERROR` or `UNAVAILABLE`, with an optional
`error` beside it) and `meta` timestamps.

**Per-signal failures do not fail the call.** Every element has to be branched on
individually, which is what `_BENIGN_SIGNAL_ERRORS` in `coordinator.py` exists to
cope with `C-code`.

Field shapes are in [vehicle-and-battery-status.md](vehicle-and-battery-status.md).

## Credentials

Smartcar issues **two unrelated credential pairs plus one shared secret**, and the
legacy client ID is the same UUID as the Application ID. That single coincidence
causes most of the misconfiguration.

| Credential | Dashboard location | Purpose | Where it goes here |
| --- | --- | --- | --- |
| **M2M client ID** (begins `client_`) and **client secret** | Configuration, API credentials | Client-credentials flow, application tokens, **v3** | Home Assistant's **Application Credentials** dialog |
| **Legacy client ID** (*is* the Application ID UUID) and secret | Configuration, Legacy credentials | Authorization-code flow, per-vehicle tokens, **v2.0** | Nowhere, on a v3 entry |
| **Application ID** (a UUID) | Configuration, Application details | Identifies the application to Connect, as `application_id` | The **config flow** field "Application ID" |
| **Application Management Token** | Configuration, API credentials, at the bottom | HMAC-SHA256 key for the webhook VERIFY answer and `SC-Signature`. Never an `Authorization` value | The integration's **options** |

`L-portal`, last column `C-code`.

`util.api_version_for_client_id` decides v2 versus v3 from the `client_` prefix
alone `C-code`. So pasting the Application ID into the Application Credentials
dialog silently produces a v2 entry that talks to `auth.smartcar.com` and fails
there, with nothing naming the cause. The client ID in that dialog must begin
`client_`.

**The redirect URI is not the webhook callback URI.** A redirect URI of
`https://my.home-assistant.io/redirect/oauth` is correct and is what
`application_credentials` uses when the instance is not directly reachable
`L-portal`. Do not "fix" it to `/auth/external/callback`. The only hard rule is
that the `redirect_uri` sent to `/oauth/authorize` byte-matches the one sent to the
token exchange and appears in the dashboard's list.

## The webhook model

### Triggers decide when, data signals decide what

Every `VEHICLE_STATE` delivery carries **all** of the webhook's configured data
signals, not only the one that triggered it `D-docs` `L-portal`. A webhook with
triggers and no data signals fires and delivers nothing useful. A webhook with data
signals and no triggers never fires at all.

### Lifecycle

1. **Create.** Dashboard, Integrations, new integration, webhook. `management.yaml`
   declares no `POST /webhooks`, so **creation is dashboard-only** `S-spec`. This is
   follow-up item 11.
2. **Verify.** Smartcar POSTs a `VERIFY` event to the Vehicle data callback URI.
   Answer within 15 seconds or the webhook never activates. Retryable from the
   dashboard.
3. **Enable.** While `isEnabled` is false Smartcar stops monitoring subscribed
   vehicles entirely and queues nothing for later `D-docs`.
4. **Subscribe vehicles.** `autoSubscribe` on the webhook, per-vehicle in the
   dashboard, or `POST management.api.smartcar.com/v3/subscriptions` with
   `{"data":{"attributes":{"webhookId":..., "userId":..., "vehicleId":...}}}`,
   answering `202`, or `409` when it already exists. `management.py` does the third
   `C-code`.

### VERIFY

```json
{ "eventId": "...", "eventType": "VERIFY",
  "data": { "challenge": "<random string>" },
  "meta": { "version": "4.0", "webhookId": "...", "deliveredAt": 1731940328000 } }
```

Answer `200` with `{"challenge": "<hex HMAC-SHA256(management token, challenge)>"}`.

**VERIFY events are not signed**, which is why `webhooks.py` branches on
`eventType` before it checks the signature `C-code`. Older v2.0-shaped payloads put
the challenge at `payload.challenge` rather than `data.challenge` `D-docs`.

### SC-Signature

Hex HMAC-SHA256 of the **raw request body**, keyed by the same Application
Management Token, verified against the raw bytes before JSON parsing and compared
timing-safely. `webhooks.py` does exactly this and answers `401` on a mismatch
`C-code`.

### The VEHICLE_STATE payload

```json
{
  "eventId": "...", "eventType": "VEHICLE_STATE",
  "data": {
    "user": { "id": "...", "externalId": null },
    "vehicle": { "id": "...", "make": "VOLKSWAGEN", "model": "ID. Buzz",
                 "year": 2025, "mode": "live", "powertrainType": "BEV" },
    "triggers": [ { "code": "tractionbattery-stateofcharge",
                    "name": "StateOfCharge", "group": "TractionBattery" } ],
    "signals": [
      { "code": "tractionbattery-stateofcharge", "name": "StateOfCharge",
        "group": "TractionBattery",
        "body": { "unit": "percent", "value": 78 },
        "status": { "value": "SUCCESS" },
        "meta": { "oemUpdatedAt": 1731940328000, "retrievedAt": 1731940330000 } }
    ]
  },
  "meta": { "version": "4.0", "deliveryId": "...", "deliveredAt": 1731940328000,
            "webhookId": "...", "webhookName": "Home Assistant",
            "sequence": 1731940327412, "signalCount": 9, "mode": "LIVE" }
}
```

`D-docs` `L-portal`.

- `triggers[].code` is either a signal code or the literal **`FIRST_DELIVERY`**,
  the initial state push right after a vehicle is subscribed. `name` and `group`
  are omitted on that one.
- A failed signal carries
  `status: {"value": "ERROR", "error": {"code": ..., "type": ...}}` instead of a
  usable body.
- `data.vehicle.mode` is lowercase; `meta.mode` is uppercase (`LIVE`, `TEST`). The
  casing genuinely differs. `webhooks.py` reads `meta.mode == "TEST"` and
  acknowledges with `202` `C-code`.
- `eventId` is stable across retries and is the correct dedup key; `deliveryId`
  changes per attempt.

`VEHICLE_ERROR` shares the envelope and replaces `signals` with `errors[]`, each
carrying `type` (`CONNECTED_SERVICES_ACCOUNT`, `VEHICLE_STATE`, `COMPATIBILITY` or
`PERMISSION`), `code`, `state` (`ERROR` or `RESOLVED`), `description`,
`suggestedUserMessage`, `docURL`, `resolution.type` and an affected `signals[]`
list. `_handle_webhook_errors` starts a reauth on `PERMISSION` plus
`REAUTHENTICATE` and ignores the rest `C-code`.

### Timestamps: two formats for the same three fields

| Field | REST signals | Webhook payload |
| --- | --- | --- |
| `oemUpdatedAt` | ISO-8601 string | unix epoch **milliseconds** |
| `retrievedAt` | ISO-8601 string | unix epoch milliseconds |
| `ingestedAt` | ISO-8601 string | not present |
| delivery time | `SC-Fetched-At` header, ISO-8601 | `meta.deliveredAt`, unix ms |

`S-spec` for REST, `D-docs` for webhooks. **Whether this integration parses both
forms is `U-unver`** and worth a test.

Staleness comes from `oemUpdatedAt`. `retrievedAt` always looks fresh.

### Delivery behaviour

Any 2xx within **15 seconds** is success; the body is ignored except on VERIFY.
At-least-once, **4 attempts** (initial plus 3 retries) at 0s, 25s, 50s and 100s,
then the payload is dropped permanently. Retries fire on any non-2xx, timeout,
connection failure, DNS error or TLS error, and a slow endpoint that eventually
answers 200 still counts as a failure. **No ordering guarantee**: order by
`meta.oemUpdatedAt` with `meta.sequence` as a tiebreak. All `D-docs`.

The receiver must be HTTPS with a valid certificate and publicly reachable, which
in practice means a Nabu Casa cloudhook or a properly exposed
`/api/webhook/{id}`. `webhook_url_from_id` prefers the cloudhook when a cloud
subscription is active `C-code`.

## Granted scope is not the same as configurable signal

Two different capability sets, and conflating them produces entities that can never
populate.

**Granted scope** is what the owner consented to at Connect time. It lives on the
connection and is readable from `GET /connections` as
`data[].attributes.permissions`.

**Configurable signal** is what the *application's plan* allows into a webhook's
trigger and data lists. It lives in Configuration, Vehicle Access.

On a Free plan, only **9 signals and 3 attributes** are enabled, across `Closure`,
`ConnectivitySoftware`, `ConnectivityStatus`, `InternalCombustionEngine`,
`Odometer`, `TractionBattery`, `VehicleIdentification` and `VehicleUserAccount`.
`Charge`, `Climate`, `Diagnostics`, `HVAC`, **`Location`**, `LowVoltageBattery`,
`Motion`, `Service`, `Surveillance`, `Transmission` and `Wheel` are locked behind
Upgrade `L-portal`.

The two sets diverge because **changes to Vehicle Access apply only to new
connections**. A vehicle connected in 2025 keeps a grandfathered grant that may
still carry `read_location` and `read_charge` while the application can no longer
put those signals into a webhook.

**The consequence:** location and charge data can be granted, and still be
undeliverable by webhook. `DEFAULT_SCOPES` currently asks for `read_location`,
`read_charge`, `read_climate`, `control_climate` and `read_diagnostics` `C-code`,
none of which a Free plan can configure. The plan-safe set is `read_vehicle_info`,
`read_vin`, `read_battery`, `read_odometer` and `read_security`.

Verify the real grant from `GET /connections`, not from any document, this one
included.

## Failure modes

Ordered by how often they are the answer.

### The webhook callback URI is not a webhook receiver

The live blocker as of 2026-09-22 `L-portal`. The webhook's **Vehicle data
callback URI** was set to the OAuth redirect URL,
`https://my.home-assistant.io/redirect/oauth`, so Smartcar's VERIFY POST never
reached `/api/webhook/{id}`. Status: **Unverified**. Deliveries: **none, ever**.
Credentials, token, Connect and this integration's webhook handling were all
correct.

It has a second, quieter effect. `management.py` finds "our" webhook by
string-comparing `attributes.callbackUri` against the URL Home Assistant generates
`C-code`. With the OAuth redirect in that field no webhook can ever match, so
auto-subscribe silently does nothing. One setting breaks both verification and
subscription.

### The signal store is empty

Observed 2026-09-20 `L-live`: `GET /connections` healthy, `GET /vehicles/{id}`
healthy, `GET /vehicles/{id}/signals` answering `200` with `data: []` and
`meta.totalCount: 0`, and every single-signal path answering `404`.

Why it misleads: a `200` with an empty array is not an error so nothing raises;
`404` on one signal reads as "this vehicle lacks that signal"; and the same `404`
is what a **mistyped** code returns, because codes are domain-prefixed. The VIN is
`vehicleidentification-vin`, not `vin`.

Follow-up item 10 is the fix: raise a repair issue instead of creating a full set of
permanently unavailable entities.

### The entry is silently v2

Version comes from the `client_` prefix. The Application ID pasted into the
Application Credentials dialog makes a v2 entry. The tell is a repair issue about a
legacy client ID.

### Triggers configured for signals the vehicle does not have

A verified, enabled, subscribed webhook whose triggers are all signals the vehicle
never reports will be very quiet. Three of the seven triggers configured on this
account are `connectivitystatus-*` and `vehicleuseraccount-*`, and the
compatibility matrix says the ID. Buzz supports no `connectivitystatus-*` signals
at all `M-matrix` `L-portal`. **This is a lead, not a verified finding**: the
matrix is a 2026-09-20 export that could not be re-checked against the portal.

### Nothing updates, but setup succeeded

With a management token configured the coordinator has **no update interval at
all**: webhooks are the only data source `C-code`. If the callback URL is not
reachable from Smartcar, entities keep their last value and nothing says so. The
Last Webhook Received sensor is the tell.

### The request counter reads zero

The dashboard showed `0 / 500` with an empty request log despite reads that should
have registered `L-portal`. Two possibilities needing different fixes: the Free
plan does not record requests (log retention is a paid feature), or Home Assistant
genuinely is not issuing them and a budget or polling guard is suppressing them.
**Distinguish these before debugging anything** with the one-call probe in step 9
of the checklist. Which one applies is `U-unver`.

Note also that the counter is presented **application-wide**, not per vehicle,
which does not match the per-vehicle framing `budget.py` is built on. With one
vehicle it does not yet bite.

### A rejected credential, a 202, a 504

All previously real and now fixed: a rejected v3 credential used to raise an
`AssertionError`, a `202` was treated as success without reading the body, and
`504` was unhandled. See `api-reference.md`.

## Dashboard checklist

For a person or an agent at `https://dashboard.smartcar.com`. Steps 1 to 3 are the
fix; the rest confirm the chain. Nothing here spends a vehicle's allowance except
the explicitly marked probe in step 9.

1. **Fix the webhook callback URI and re-verify until it reads Verified.**
   Integrations, the webhook, field **Vehicle data callback URI**. Replace the OAuth
   redirect URL with the real receiver: the Nabu Casa cloudhook, or
   `https://{ha-external-url}/api/webhook/{webhook_id}`. Save, then trigger
   re-verification.
   **Expected: Status flips Unverified to Verified.** If not, the receiver is not
   reachable: HTTPS with a valid certificate, public, and any 2xx within 15 seconds.
   Nothing downstream can work until this clears.
   **Do not change the redirect URI under Application details.** It is a different
   field and its current value is correct.

2. **Subscribe the vehicle to that webhook.** Either turn on auto-subscribe on the
   webhook, or subscribe it under Vehicles, the vehicle, Webhook Subscriptions.
   **Expected: the vehicle shows as subscribed to this specific webhook**, and a
   `FIRST_DELIVERY` event arrives shortly after.

3. **Prune the triggers to signals the vehicle supports, and add the two that
   matter.** Drop triggers for signals the compatibility matrix says this vehicle
   does not report.
   **Expected afterwards: `tractionbattery-stateofcharge` and `closure-islocked`
   are triggers as well as data signals, alongside `odometer-traveleddistance`.**
   State of charge is the most useful event on a BEV and is currently a data signal
   only, so nothing fires when the battery changes. Adding a trigger changes when
   deliveries happen, not what is in them.

4. **Configuration, API credentials.** **Expected: the M2M client ID begins
   `client_`**, and that is the value in Home Assistant's Application Credentials
   dialog.

5. **Configuration, Application details.** **Expected: the Application ID matches
   the config flow's "Application ID" field**, and is not what is in the
   Application Credentials dialog.

6. **Configuration, API credentials, Application Management Token.**
   **Expected: present, and the same value is in the integration's options.**
   Without it step 1 cannot succeed even with a correct URI.

7. **Configuration, Vehicle Access.** Record which signal groups are enabled and
   which are locked. This is what a webhook can carry, and it is not what the owner
   granted. Note the page's warning that changes apply **only to new connections**.

8. **Read the authoritative grant from the API**, not the UI:
   `GET /v3/connections` returns `data[].attributes.permissions`.
   **Expected: possibly scopes the plan can no longer configure.** That is the
   grandfathered grant, not an error.

9. **The one-call probe, once.** With a fresh token, issue a single
   `GET /v3/vehicles/{id}/signals` with `Authorization` and `sc-user-id`, then
   reload Overview and Logs.
   **Expected, if the counter records at all: it moves off 0.** If it does not, the
   empty counter is a dashboard artifact and not evidence about this integration.
   Run this before touching `polling.py` or `budget.py`.

10. **Wait a few minutes and re-check.** Three confirmations, increasing in
    strength: deliveries appear on the webhook; the **Last Webhook Received** sensor
    moves; and `GET /vehicles/{id}/signals` answers **`meta.totalCount` greater than
    zero**. The last one is what would finally confirm the store-fed-by-webhook
    model, which is still `U-unver`.

11. **Optional: add a simulated vehicle.** Driven with `mode=simulated` on the
    Connect URL, it does not consume a Free plan's single live-vehicle slot and lets
    the flow be exercised without the real car.

## Limits

- **Free plan:** 1 vehicle, 500 API requests a month, no log retention, most signal
  groups locked `L-portal`. The prose says a per-vehicle cap exists and is set by
  your plan **without publishing a number** `D-docs`; this integration is built
  around 500 per vehicle per month shared with commands `C-code`. The dashboard
  presents the counter application-wide. Overage is
  `430 BILLING / VEHICLE_REQUEST_LIMIT`.
- **Application rate limit:** a bucket of **120 requests refilling at 2 per
  minute** `D-docs`, application-wide. This is the binding constraint on any poll
  loop.
- **What counts as one request is undefined.** It is not stated whether one
  `GET /signals` call costs one or one per signal returned `U-unver`.
- **An unsubscribed vehicle refreshes about once a day** `D-docs`, so polling it
  hourly buys the same value sixteen times and then a billing error. See
  [polling.md](polling.md).
- **Subscribed refresh is per brand**, from Tesla at 1 second to 5 minutes through
  to Toyota US and Porsche at 30 to 60 minutes `D-docs`. VW US is published at
  roughly 1 to 3 minutes, faster when plugged in. These are Smartcar's published
  brand figures, not observed rates.
- **An idle or sleeping vehicle produces no updates at all** `D-docs`.

## Open questions

1. Is the signal store actually fed by webhooks? Strongly indicated, never stated,
   not yet confirmed by watching `totalCount` move off zero.
2. Does the Free plan record API requests at all? Checklist step 9 settles it.
3. Does this integration parse both timestamp formats, and does it handle
   `body.values` alongside `body.value`?
4. Can this vehicle support the `connectivitystatus-*` and `vehicleuseraccount-*`
   triggers currently configured?
5. Is the `client_` prefix on v3 client IDs guaranteed? It is load bearing here.
6. Does one `GET /signals` call cost one request or one per signal?
7. What caps the number of triggers and data signals on a webhook? No limit is
   published; the plan's enabled-signal count looks like the real cap.

## See also

- [vehicle-and-battery-status.md](vehicle-and-battery-status.md): the field
  catalogue, entity keys, defaults, and what this vehicle can never populate.
- [api-reference.md](api-reference.md): every endpoint, what it returns, which ones
  this integration calls.
- [polling.md](polling.md): the profiles and the allowance that shapes them.
- [follow-ups.md](follow-ups.md): the gaps, including items 10 and 11 which come
  straight out of this document.
- `vendor-docs-reference/docs/smartcar.md`: the same model written for an agent
  verifying the dashboard from a browser, with no Home Assistant context assumed.
