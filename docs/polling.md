# Polling, webhooks and the API budget

Smartcar's free tier allows **500 API calls per vehicle per month**. Not per
account: per vehicle. That is about 16 a day for a single car, and commands
come out of the same allowance, so every reading you take is a lock or a charge
stop you cannot send later.

The number that actually shapes this is less obvious. From Smartcar's own
integration guidance:

> The Vehicles API is designed primarily for exploration and non-frequent data
> retrieval. This API is not designed for continuous polling or real-time
> monitoring. Data is typically updated once every 24 hours unless the vehicle
> is actively subscribed to a webhook.

So polling an unsubscribed vehicle every hour does not give hourly data. It
gives the same daily data sixteen times, and then a `430 BILLING /
VEHICLE_REQUEST_LIMIT` for the rest of the month.

## The order of preference

1. **A webhook subscription.** Updates as often as the manufacturer allows,
   pushed, and it costs nothing from the allowance. This is the only way to
   have both fresh data and a working budget.
2. **An event worth asking about.** A phone leaving home is free to observe and
   says more about whether the car moved than a timer does.
3. **A schedule**, as a floor, not as the mechanism.

The integration is built in that order.

## Webhooks

Create a webhook in the Smartcar dashboard whose callback URL is this Home
Assistant instance, and put the Application Management Token into the
integration's options. The token is the HMAC key that verifies inbound
payloads, and nothing else.

With **Subscribe vehicles to the webhook automatically** enabled (the default),
setup then finds the webhook whose callback URL matches this instance and
subscribes each vehicle to it. That replaces the manual per-vehicle step in the
dashboard. It matches on the URL rather than taking the first webhook, because
an application can have several for other consumers and subscribing to the
wrong one sends this instance nothing.

While a management token is configured, scheduled polling is **off entirely**.
The webhook is the data source.

## Polling profiles

Set under Settings, Devices and services, Smartcar, Configure.

| Profile | Scheduled calls | Use when |
| --- | --- | --- |
| Webhooks only | none | A webhook subscription exists. Cheapest and freshest. |
| On demand | none | Automations and the Refresh action decide, plus presence events. |
| Once a day | ~30 a month | The data only refreshes daily anyway without a webhook. |
| Twice a day | ~60 a month | Default for new entries. Comfortable margin on the free tier. |
| Custom interval | your choice | A paid plan, or a specific need. Floors at one hour. |

The floor exists because an hourly interval is 720 calls a month against an
allowance of 500: a guaranteed billing error rather than a choice.

Entries created before this existed were migrated to **Custom interval at six
hours**, which is exactly the cadence they already had. Changing someone's
behaviour silently on upgrade is not the integration's call to make.

## Presence

Pick any `person` or `device_tracker` entities under **People or device
trackers to watch**. Crossing the home boundary then triggers a refresh.

Watching them costs nothing. The rules:

- Only a real crossing counts. Home to away, or away to home if you enable it.
- A tracker going `unknown` or `unavailable` is **not** a departure, so a phone
  rebooting does not spend a call.
- A daily cap (default four) stops a tracker flapping at the edge of the home
  zone from draining a month of allowance in an afternoon.

Arrival is off by default: leaving is when the car's location and state change,
arriving usually just confirms what you already know.

## The reserve

**Calls reserved for commands** (default 50) are never spent on polling. When
the remaining allowance drops to the reserve, scheduled and event driven polls
stop and say so in the log, but locking the doors still works.

The `smartcar.refresh_vehicle` action refuses for the same reason, with an
error naming the counts. Pass `force: true` to spend the reserve deliberately.

## Watching the budget

Two diagnostic sensors per vehicle: **API Calls Used** and **API Calls
Remaining**, with the period, allowance, reserve and the profile's own monthly
estimate as attributes.

These are Home Assistant's tally of the calls **it** made. Smartcar does not
publish a remaining count, and anything else using the same Smartcar
application is invisible from here, so treat it as a good estimate for staying
clear of the ceiling rather than as a reconciliation of the bill.

The count is kept in Home Assistant storage, survives restarts, and resets on
the calendar month in your local timezone. Smartcar does not publish its
billing period boundary, so a period that does not start on the 1st will drift
from this by a few days.

## Knowing when the next call is coming

A third diagnostic sensor per vehicle, **Next Scheduled Poll**, is the
timestamp of the next scheduled read. Its attributes are `poll_profile`,
`interval_seconds`, `last_poll_at`, `calls_reserved`, `paused_reason` and
`next_poll_billed`, which is always true: a scheduled read is one call out of
the 500 per vehicle monthly allowance.

It is `unknown` when nothing is scheduled, and `paused_reason` says why:

| `paused_reason` | Meaning |
| --- | --- |
| `webhook_only` | The Webhooks only profile, or a management token is configured, so pushes feed this vehicle |
| `no_interval` | The On demand profile, or polling is disabled for the entry |
| `reserve_reached` | A poll is scheduled but the allowance is down to the reserve, so it will be skipped |

The value follows the timer rather than the interval, so a
`smartcar.refresh_vehicle` call moves it: that is what makes it usable as the
input to a decision about whether to force one.

```yaml
automation:
  - alias: Refresh the car when we leave and the next poll is hours away
    triggers:
      - trigger: state
        entity_id: person.sean
        from: home
    conditions:
      - condition: template
        value_template: >-
          {{ as_timestamp(states('sensor.vw_id_buzz_next_scheduled_poll'),
                          default=0) - as_timestamp(now()) > 4 * 3600 }}
    actions:
      - action: smartcar.refresh_vehicle
        data:
          config_entry: !input config_entry
```

The condition is the whole point. Without it the automation spends a call every
time somebody leaves, including the times the scheduled poll was a minute away.

## If the allowance runs out anyway

Smartcar answers `430` for the rest of the billing period, for reads and
commands alike. Options, in order: subscribe the vehicle to a webhook, lower
the profile, or contact Smartcar, who state the per-vehicle limit can be
raised.

## Restarts

Setup reads every signal once. That single request learns what the vehicle can
answer and gives the entities their first values, and it is billed like any
other: one call out of five hundred, every restart, every reload, every
reconfigure.

Home Assistant restarts often. An evening of updates and configuration changes
was observed spending fifty calls, a tenth of the month, without the vehicle
being asked anything new.

The last signal response is now kept, and a setup within thirty minutes of the
previous read reuses it instead of making the request. Nothing has changed in
the car between a restart at one minute past and the same restart a minute
later. Past that window the stored copy is ignored and the vehicle is read as
before, so a restart after a real gap is still accurate.

The window is deliberately much shorter than any polling interval. It exists to
make restarts free, not to replace polling.
