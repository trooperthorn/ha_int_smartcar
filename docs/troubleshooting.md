# Troubleshooting

This integration fails quietly more often than it fails loudly. A v3 setup can
have correct credentials, a correct Application ID, a completed Connect flow and
a green config entry, and still show nothing at all, because the thing that is
wrong is a webhook in the Smartcar dashboard that nobody in Home Assistant can
see. The debug log is where the chain becomes visible.

The step by step proof that each link works lives in the README, under
[Verify Communication](../README.md#verify-communication). Work through that
first. This page is about getting the log that tells you which step failed, and
about reading it.

## Turning on debug logging

**From the integration page, which is the easier of the two.** _Settings_
&rarr; _Devices & Services_ &rarr; _Smartcar_ &rarr; the three dot menu &rarr;
**Enable debug logging**. Reproduce the problem, then use **Disable debug
logging** on the same menu: Home Assistant downloads the captured log for you
at that point, already filtered to this integration.

**From `configuration.yaml`, when you want it across restarts.** Debug logging
enabled from the integration page is turned off again by a restart, and a
problem that only happens at startup is exactly the problem you cannot catch
that way.

```yaml
logger:
  default: warning
  logs:
    custom_components.smartcar: debug
```

Restart, reproduce, then read _Settings_ &rarr; _System_ &rarr; _Logs_ &rarr;
**Load full logs**, or the file at `config/home-assistant.log`.

Narrower scopes are available when the full log is too noisy:

| Logger | What it shows |
| --- | --- |
| `custom_components.smartcar` | Everything below |
| `custom_components.smartcar.webhooks` | Inbound deliveries, signature checks, the raw payloads |
| `custom_components.smartcar.coordinator` | Polls, what the signal store held, per signal errors |
| `custom_components.smartcar.auth` | Every outbound request, with the token redacted |
| `custom_components.smartcar.config_flow` | The setup flow |

**What is not in the log.** The access token and the Application Management
Token are redacted before anything is written, so a debug log is safe to attach
to an issue. The webhook URL, the vehicle id and the VIN are present: they are
not secrets, but a VIN identifies your car, so redact it if that matters to you.

## What the key lines mean

### Startup

```text
[custom_components.smartcar] Registering webhook at url: https://hooks.nabu.casa/...
```

The webhook receiver is listening. **This URL is the one that belongs in both
callback fields in the Smartcar dashboard.** If the line says "Webhooks are not
enabled" instead, no Application Management Token is configured and nothing can
arrive.

```text
[custom_components.smartcar] Using token with scopes: ['read_battery', ...]
```

What Smartcar reports it granted, read from `GET /connections`. Not a list this
integration chose: change it in the **Vehicle Access** tab of your Smartcar
application's Configuration page and reconfigure. A permission missing here is
a permission missing in the dashboard.

### Outbound requests

```text
[custom_components.smartcar.auth] HTTP get request https://vehicle.api.smartcar.com/v3/vehicles/<id>/signals {} headers={'authorization': '<redacted>', 'sc-user-id': '...'}
```

Every billed call appears as one of these. A `/signals` line is 1 call from the
500 per vehicle monthly allowance; a `/connections` line is not billed. If you
see `/signals` lines you did not expect, the polling profile is the place to
look ([polling.md](polling.md)).

A missing `sc-user-id` header is why a request comes back `400 missing userId`.
Re-running Connect is the fix.

### The signal store

```text
[custom_components.smartcar.coordinator] Coordinator smartcar_...: vehicle cannot answer 0 of 0 signals
```

**The store is empty.** In v3 this endpoint reads a server side cache that only
a verified, enabled webhook with the vehicle subscribed to it ever fills. Zero
of zero is not an error from the car; it means nothing has ever been collected.
See [communication.md](communication.md).

```text
[custom_components.smartcar.coordinator] Coordinator smartcar_...: vehicle cannot answer 6 of 25 signals
```

Healthy. The total is usually **larger** than the number of data signals
configured on the webhook: subscribing a vehicle makes Smartcar collect
everything the grant allows, not only the webhook's list.

```text
[custom_components.smartcar.coordinator] Coordinator smartcar_...: poll totalCount=25 returned=25 mapped=19 unmapped=[...] errored=[...]
```

`unmapped` are signals Smartcar returned that this integration has no entity
for, which is informational. `errored` names each signal that came back with an
error rather than a value.

```text
[custom_components.smartcar.coordinator] error for signal Sunroof: COMPATIBILITY:VEHICLE_NOT_CAPABLE
[custom_components.smartcar.coordinator] error for signal ChargeRate: VEHICLE_STATE:NOT_CHARGING
```

Both are logged at debug because both are expected. `COMPATIBILITY` is
permanent: the car does not have that feature, and no entity is created for it.
`VEHICLE_STATE` is temporary: charge rate reads as an error while the car is
unplugged, and the entity exists and is enabled because it works the moment the
cable goes in.

```text
[custom_components.smartcar.coordinator] error for signal FrontTrunk: PERMISSION:UNKNOWN
```

Logged at **error**, because this one is actionable: Smartcar is saying the
grant does not cover that signal. Reconfigure to re-run Connect.

### Inbound webhooks

```text
[custom_components.smartcar.webhooks] Received JSON from Smartcar: '{"eventId":"...","eventType":"VERIFY",...}'
```

The dashboard's **Verify** reached your instance. If pressing Verify produces
no line at all, the callback URI is not pointing here.

```text
[custom_components.smartcar.webhooks] Validating signature
[custom_components.smartcar.webhooks] mode=TEST; no action taken for vehicle with id: ...
```

A **Test webhook** from the dashboard, accepted and deliberately dropped. This
pair is worth more than it looks: it is produced after the signature check, so
it proves the URL, the token, the signature and the parser all work.

```text
[custom_components.smartcar.webhooks] ignoring error in webhook: {'type': 'COMPATIBILITY', 'code': 'VEHICLE_NOT_CAPABLE', ...}
```

A `VEHICLE_ERROR` event listing signals the car does not implement. Five of
them on one vehicle is an ordinary result, not a fault.

```text
[custom_components.smartcar.webhooks] Ignoring webhook for unknown vehicle: ...
```

A delivery arrived for a vehicle this config entry does not hold. Usually a
second Smartcar application, or a vehicle that was set up in a different entry.

## Symptom to fix

The webhook symptoms, and the three wrong callback values that cause most of
them, are in the README's
[symptom to fix table](../README.md#symptom-to-fix). This table covers what is
left: the ones that look like a working setup.

| Symptom | Most likely cause | Fix |
| --- | --- | --- |
| Every entity is unavailable, no errors anywhere | The signal store is empty; the log says "0 of 0 signals" | The webhook is not verified, not enabled, or the vehicle is not subscribed to it. Work through [Verify Communication](../README.md#verify-communication) |
| Setup aborts with "Smartcar did not grant ..." | `read_vehicle_info` or `read_vin` is missing from the grant | Enable them in the **Vehicle Access** tab of your application's Configuration page and set up again |
| A permission you expected is missing from "Using token with scopes" | Vehicle Access does not list it, or the consent screen trimmed it | Fix Vehicle Access, then reconfigure the entry to re-run Connect |
| An entity you want exists but is switched off | The signal was not in the store when the entity was created | It switches itself on the first time that signal arrives. To force the question, reload the entry once the vehicle is delivering |
| An entity you want does not exist at all | The vehicle reported `COMPATIBILITY` for its signal, or its permission was not granted | Check the debug log for `VEHICLE_NOT_CAPABLE` on that signal. If it is there, the car cannot do it and no setting will change that |
| Charge rate, wattage and time to complete are unavailable | The car is not charging | Expected. They fill in when the cable is connected |
| The allowance is disappearing | Scheduled polls, or an event driven poll firing on every departure | Check **API Calls Used** and the **Next Scheduled Poll** sensor's `poll_profile`, then see [polling.md](polling.md) |
| `430 BILLING / VEHICLE_REQUEST_LIMIT` | The 500 calls for this vehicle are spent for the period | Subscribe the vehicle to a webhook, which is free, and lower the polling profile |
| Values are hours stale with no errors | The vehicle is not subscribed, so Smartcar refreshes it about once a day | Subscribe it. Polling harder cannot produce data the source does not have |
| Setup was fine, then everything stopped after a token regeneration | The Application Management Token is the HMAC key on both ends | Re-paste the current token into the Configure screen and verify the webhook again |

## Reporting a problem

`python script/smartcar_doctor.py` checks the credential shape, the v3 token,
`/connections`, both Management API hosts and one vehicle's signals, and writes
a redacted report. It runs on the machine where the problem is and needs
nothing but the standard library. Attach that report and a debug log covering
one restart and one reproduction.

## See also

- [communication.md](communication.md): the v3 chain end to end, and why an
  unverified webhook produces an empty store rather than an error.
- [polling.md](polling.md): what each call costs and which ones are free.
- [vehicle-and-battery-status.md](vehicle-and-battery-status.md): what a given
  signal means and which entity carries it.
