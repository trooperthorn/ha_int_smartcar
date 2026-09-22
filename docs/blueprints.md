# Blueprints

Every blueprint this repository ships, what it costs in Smartcar API calls,
and its inputs. All of them require Home Assistant 2026.9.0 or later.

Smartcar bills reads and commands from the same 500 calls per vehicle per
month allowance (see [polling.md](polling.md)), so each entry below says
plainly whether it spends anything. "Zero reads" means the blueprint only
looks at entity state that is already populated by webhook or polling; it
never itself asks Smartcar for fresh data. Where a blueprint can call
`smartcar.refresh_vehicle` to force a fresh reading, that is one billed call
(or spends the reserve with `force: true`).

Click a badge to import that blueprint directly into your Home Assistant
instance. Each one points at the file on `main`, so it always imports the
latest released version.

## Android notification options

Every blueprint that sends a notification (all of them under Automations
except "Charge for a round trip now" and "Refresh the vehicle when someone
leaves home", which do not notify, though the latter can request a location
update, see below) shares the same set of Android Companion app inputs,
following the
[basic notifications](https://companion.home-assistant.io/docs/notifications/notifications-basic/),
[actionable notifications](https://companion.home-assistant.io/docs/notifications/actionable-notifications/)
and [notification commands](https://companion.home-assistant.io/docs/notifications/notification-commands/)
Companion app docs. These fields are ignored by iOS, so the same blueprint
still works there.

### Transport

- **Notify devices** (`notify_devices`): a device selector (multiple)
  filtered to the `mobile_app` integration. Each selected device's notify
  action is derived as `notify.mobile_app_<device name, slugified>`, using
  the templating functions
  [`device_attr`](https://www.home-assistant.io/template-functions/device_attr/)
  and [`slugify`](https://www.home-assistant.io/template-functions/slugify/).
  This is the preferred way to pick recipients: add or remove a phone from
  the input list, no YAML editing.
- **Notify action (deprecated)** (`notify_target`): the old free-text notify
  action, for example `notify.mobile_app_phone`. Empty by default; still
  called if you set it, so an automation created before this input existed
  keeps working. New setups should use Notify devices instead.
- **Also create a persistent notification** (`also_persistent`, default
  on): in addition to the devices above, also raises a
  `persistent_notification` with the same title and message. On by default
  so a fresh import behaves the way the old `notify.persistent_notification`
  default used to.
- **Notification group** (`notification_group`, default `smartcar`, on
  "Check tomorrow's trips against range" and "Evening summary" only): sets
  `data.group` so the two collapse together on the phone instead of
  stacking as unrelated notifications.

### Content

- **Notification tag** (`notification_tag`): a stable, per-purpose tag such
  as `smartcar_trip_check`. A repeat of the same notification replaces the
  previous one on the phone instead of stacking. If you use a blueprint for
  more than one vehicle, give each instance its own tag.
- **Notification channel** (`notification_channel`, default `Smartcar`):
  the Android channel the notification is posted to. Set sound, vibration
  and default importance for that channel from the phone's own
  notification settings, once, instead of per blueprint.
- **Notification importance** (`notification_importance`, default
  `default`, `high` on "Left unlocked away from home" and "Low range
  warning"): how intrusive the notification is (`min`, `low`, `default`,
  `high`, `max`).
- **Notification icon** (`notification_icon`): the status bar icon, an MDI
  name such as `mdi:car-electric` (the default everywhere), `mdi:lock-open
  -variant` on "Left unlocked away from home", or `mdi:solar-power` on
  "Charge from solar surplus".
- **Click action** (`click_action`): where tapping the notification body
  opens. Leave it blank for the default behavior, or set a Home Assistant
  path such as `/config/devices/device/<id>` to open the vehicle's device
  page, or `entityId:sensor.xxx` to open a specific entity's more-info
  dialog directly.
- **Clear on resolution**: where the blueprint already knows the condition
  that raised the notification has cleared (the car got plugged in, a lock
  action succeeded, charging resumed, range recovered, or the API budget
  recovered), it sends a second notification with `message:
  clear_notification` and the same tag to every Notify device and, if set,
  the legacy Notify action, which removes the earlier one instead of
  leaving it stale on the phone. If a persistent notification was also
  raised, it is removed the same way, with `persistent_notification
  .dismiss` and a `notification_id` equal to the tag (set on the
  notification when it was created), rather than left behind in the
  frontend.
- **Persistent (Android)** on "Left unlocked away from home": sets
  `data.persistent: true` on that notification (it already has a tag), so
  it cannot be swiped away by accident, and it is cleared the same way as
  any other, on the Lock trigger.
- **Open app** (`open_app_package`, optional, on "Plug-in reminder",
  "Charging finished or interrupted" and "Charge from solar surplus"): when
  set to an Android package name, adds an "Open app" action using the
  `URI` action type with `uri: app://<package>`, per the Companion app's
  actionable-notification URI forms. Added within the three-action limit;
  no existing action is dropped on these three blueprints since none of
  them were already at the limit.
- **Action ID inputs** (for example `lock_action_id`, `start_charging_action_id`,
  `set_limit_action_id`, `refresh_action_id`, `reply_action_id`): the
  `mobile_app_notification_action` event trigger matches on a fixed action
  string. Two vehicles using the same blueprint with the default action ID
  would both react to a tap on either notification. Give each vehicle's
  instance its own action ID. Android shows at most three action buttons
  per notification; "Check tomorrow's trips against range" now uses all
  three (Start charging, Set limit to N%, and the reply action below).
- **Charge-to reply** on "Check tomorrow's trips against range": a third
  action, titled "Charge to...", using `behavior: textInput` per the
  actionable-notifications page. Typing a number and replying fires
  `mobile_app_notification_action` with `reply_text` set to what was typed;
  "Charge for a round trip now" (given the same `reply_action_id`) parses
  it as an integer, clamps it to 50-100, and sets the charge limit number
  directly.
- **Announce with text-to-speech** (`announce_tts`, default off, on "Check
  tomorrow's trips against range", "Low range warning" and "Left unlocked
  away from home" only): when on, also sends the notification's message as
  `message: TTS` with `data.tts_text` set to that message, per the
  notification-commands page, to every Notify device and, if set, the
  legacy Notify action. This is a second notify call, not a notification
  action, so it does not count against the three-action limit.

## Android quick actions

Beyond notifications, the Companion app can surface this integration's
entities and scripts directly on the phone, without opening Home Assistant
first:

- The "Refresh, wait for fresh data, then act" and "Charge for a round
  trip" scripts can be pinned as Companion
  [quick-settings tiles](https://companion.home-assistant.io/docs/quick_settings_tile/)
  or [widgets](https://companion.home-assistant.io/docs/widgets/), so
  running either is one tap from outside the app.
- The door lock, the charging switch, and the battery sensor can be added
  as [Android Auto favourites](https://companion.home-assistant.io/docs/android-auto/),
  so they show up on the car's own screen. See those pages for exactly how
  to add a tile, widget, or Android Auto favourite; nothing on this
  repository's side is required beyond the entities and scripts already
  documented below.

## Automations

### Check tomorrow's trips against range

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Ftrooperthorn%2Fha_int_smartcar%2Fmain%2Fblueprints%2Fautomation%2Fsmartcar%2Fcheck_trips_against_range.yaml)

File: [`blueprints/automation/smartcar/check_trips_against_range.yaml`](../blueprints/automation/smartcar/check_trips_against_range.yaml)

At an evening evaluation time, reads a calendar for located events in the
lookahead window, computes each trip's round-trip distance and the same-day
total against the current range, and sends an actionable notification (with
"Start charging" and "Set limit to N%") when something does not fit. Can
optionally auto-start charging instead of waiting for the tap.

**Cost:** zero reads. One command per action taken (start charging, or set
the charge limit), whether tapped from the notification or sent by
auto-start.

Inputs: calendar, range sensor, battery level sensor, battery capacity
sensor (optional, display only), charging switch, charge limit number, home
zone (default `zone.home`), travel-distance provider (Waze or Google, with
region or config entry as needed), range sensor units (km or mi), overhead
percent (default 20), lookahead (default 24h), evaluation time (default
18:00), notify action (deprecated), notify devices, also create a
persistent notification, notification group (default `smartcar`), auto
start charging (default off), the entity ID of the "compute round trip
need" script, the start-charging, set-limit and charge-to-reply action
IDs, and the [Android notification options](#android-notification-options)
(tag, channel, importance, icon, click action, announce with TTS).

### Charge for a round trip now

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Ftrooperthorn%2Fha_int_smartcar%2Fmain%2Fblueprints%2Fautomation%2Fsmartcar%2Fcharge_for_round_trip_now.yaml)

File: [`blueprints/automation/smartcar/charge_for_round_trip_now.yaml`](../blueprints/automation/smartcar/charge_for_round_trip_now.yaml)

On-demand form of the same calculation: fires from a mobile_app notification
action, or from the automation's own "Run" button, for one destination.

**Cost:** zero reads, up to two commands (start charging, set limit), same as
the script it calls.

Inputs: destination, the entity ID of the "charge for a round trip" script,
notification action ID (default `SMARTCAR_CHARGE_FOR_TRIP`), the
charge-to-reply action ID (default `SMARTCAR_REPLY_CHARGE_TO`, must match
"Check tomorrow's trips against range"), and the charge limit number the
reply sets directly.

### Plug-in reminder

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Ftrooperthorn%2Fha_int_smartcar%2Fmain%2Fblueprints%2Fautomation%2Fsmartcar%2Fplug_in_reminder.yaml)

File: [`blueprints/automation/smartcar/plug_in_reminder.yaml`](../blueprints/automation/smartcar/plug_in_reminder.yaml)

Notifies when the car is home, the battery is below a threshold, and it
stays unplugged for a while.

**Cost:** zero. Reacts to existing entities only; the notification offers a
"Refresh status" action that costs one command if tapped.

Inputs: vehicle location, battery level sensor, plug status, threshold
(default 50%), unplugged-for duration (default 30 min), notify action
(deprecated), notify devices, also create a persistent notification, the
refresh-status action ID, an optional vehicle app package (Open app
action), and the
[Android notification options](#android-notification-options).

### Charging finished or interrupted

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Ftrooperthorn%2Fha_int_smartcar%2Fmain%2Fblueprints%2Fautomation%2Fsmartcar%2Fcharging_finished_or_interrupted.yaml)

File: [`blueprints/automation/smartcar/charging_finished_or_interrupted.yaml`](../blueprints/automation/smartcar/charging_finished_or_interrupted.yaml)

Notifies when the charging switch turns off, and says whether the battery
reached the charge limit (finished) or stopped short of it (interrupted).

**Cost:** zero.

Inputs: charging switch, battery level sensor, range sensor, charge limit
number, notify action (deprecated), notify devices, also create a
persistent notification, an optional vehicle app package (Open app
action), and the
[Android notification options](#android-notification-options).

### Left unlocked away from home

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Ftrooperthorn%2Fha_int_smartcar%2Fmain%2Fblueprints%2Fautomation%2Fsmartcar%2Fleft_unlocked_away_from_home.yaml)

File: [`blueprints/automation/smartcar/left_unlocked_away_from_home.yaml`](../blueprints/automation/smartcar/left_unlocked_away_from_home.yaml)

Notifies when the car is away from home and unlocked for a while, with a
Lock action on the notification. Respects an API call reserve: below it, the
Lock action is left off the notification instead of being offered and then
failing.

**Cost:** zero reads. One command (`smartcar.lock_doors`) if the Lock action
is tapped.

Inputs: vehicle location, door lock, Smartcar account (config entry), API
calls remaining sensor, reserve (default 50), unlocked-for duration (default
15 min), notify action (deprecated), notify devices, also create a
persistent notification, the lock action ID (also the ID this blueprint
listens for), and the
[Android notification options](#android-notification-options), which here
default to `high` importance and the `mdi:lock-open-variant` icon; this
notification is also sent with `data.persistent: true`.

### Low range warning

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Ftrooperthorn%2Fha_int_smartcar%2Fmain%2Fblueprints%2Fautomation%2Fsmartcar%2Flow_range_warning.yaml)

File: [`blueprints/automation/smartcar/low_range_warning.yaml`](../blueprints/automation/smartcar/low_range_warning.yaml)

Notifies when range drops below a threshold, at an evening time or when the
car leaves home, including the nearest located calendar event if there is
one.

**Cost:** zero.

Inputs: range sensor, threshold (default 40 km), vehicle location, evening
check time (default 18:00), calendar (optional), notify action
(deprecated), notify devices, also create a persistent notification, and
the [Android notification options](#android-notification-options), which
here default to `high` importance.

### Evening summary

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Ftrooperthorn%2Fha_int_smartcar%2Fmain%2Fblueprints%2Fautomation%2Fsmartcar%2Fevening_summary.yaml)

File: [`blueprints/automation/smartcar/evening_summary.yaml`](../blueprints/automation/smartcar/evening_summary.yaml)

One notification at a chosen time: battery percent, range, plugged and
charging state, and whether tomorrow's located calendar trips fit on the
current range. Reuses the "compute round trip need" script so its numbers
always agree with the trip-check blueprint.

**Cost:** zero.

Inputs: battery level sensor, range sensor, plug status, charging switch,
charge limit number, calendar (optional), home zone, travel-distance
provider, range sensor units, overhead percent, lookahead, summary time
(default 21:00), notify action (deprecated), notify devices, also create a
persistent notification, notification group (default `smartcar`, shared
with "Check tomorrow's trips against range"), the entity ID of the
"compute round trip need" script, and the
[Android notification options](#android-notification-options).

### Charge from solar surplus

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Ftrooperthorn%2Fha_int_smartcar%2Fmain%2Fblueprints%2Fautomation%2Fsmartcar%2Fcharge_from_solar_surplus.yaml)

File: [`blueprints/automation/smartcar/charge_from_solar_surplus.yaml`](../blueprints/automation/smartcar/charge_from_solar_surplus.yaml)

Watches a solar export or grid power sensor (either sign convention) and
either reminds you to plug in and charge when there is surplus, or in "auto"
mode starts and stops the charging switch itself while the car is home and
plugged in, comparing the battery level against the charge limit's own
current value rather than a fixed target.

**Cost:** zero reads. One command per start or stop in auto mode, capped at
a configurable number of commands per day (default 4) via an optional
counter helper; remind mode sends no commands at all.

Inputs: solar/grid power sensor, sign convention (positive-is-export or
negative-is-export), surplus threshold (default 1500 W), stop threshold
(default 500 W), start/stop persistence durations (default 10 min each),
battery level sensor, charge limit number, plug status, vehicle location,
charging switch, mode (remind or auto), daily command counter (optional),
maximum commands per day (default 4), notify action (deprecated), notify
devices, also create a persistent notification, the start-charging action
ID, an optional vehicle app package (Open app action), and the
[Android notification options](#android-notification-options), which here
default to the `mdi:solar-power` icon.

### Refresh the vehicle when someone leaves home

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Ftrooperthorn%2Fha_int_smartcar%2Fmain%2Fblueprints%2Fautomation%2Fsmartcar%2Frefresh_on_leaving_home.yaml)

File: [`blueprints/automation/smartcar/refresh_on_leaving_home.yaml`](../blueprints/automation/smartcar/refresh_on_leaving_home.yaml)

Asks Smartcar for fresh data when a person or device tracker leaves home,
instead of paying for a fixed polling schedule.

**Cost:** one `smartcar.refresh_vehicle` call per qualifying departure, capped
by the integration's own daily limit and reserve.

Inputs: people/trackers to watch, Smartcar account, settle time (default 5
min), request a location update first (default off), notify devices
(asked for a location update when that option is on), and the location
update wait (default 15s). When the location-first option is on, it sends
`message: request_location_update` to each notify device, waits, then
re-checks that the person is still away before spending the billed
refresh, trading a free location push for a chance to skip a refresh that
a phone wandering back inside the settle window would otherwise waste.

### Warn before the monthly API allowance runs out

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Ftrooperthorn%2Fha_int_smartcar%2Fmain%2Fblueprints%2Fautomation%2Fsmartcar%2Fapi_budget_warning.yaml)

File: [`blueprints/automation/smartcar/api_budget_warning.yaml`](../blueprints/automation/smartcar/api_budget_warning.yaml)

Notifies when a vehicle has spent most of its Smartcar API allowance for the
month.

**Cost:** zero.

Inputs: API calls remaining sensor, warn-below threshold (default 100),
notify action (deprecated), notify devices, also create a persistent
notification, and the
[Android notification options](#android-notification-options).

### Charge to a target only while power is cheap

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Ftrooperthorn%2Fha_int_smartcar%2Fmain%2Fblueprints%2Fautomation%2Fsmartcar%2Fcharge_to_target_when_cheap.yaml)

File: [`blueprints/automation/smartcar/charge_to_target_when_cheap.yaml`](../blueprints/automation/smartcar/charge_to_target_when_cheap.yaml)

Starts charging when a price or tariff entity says power is cheap and the
battery is below target, stops at target or when the cheap window ends.

**Cost:** one command per actual state change, not per price tick.

Inputs: battery level sensor, charging switch, cheap-power indicator, target
charge level (default 80%).

### Smartcar + Emporia: only charge when the car is actually home

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Ftrooperthorn%2Fha_int_smartcar%2Fmain%2Fblueprints%2Fautomation%2Fsmartcar%2Femporia_charge_when_car_is_home.yaml)

File: [`blueprints/automation/smartcar/emporia_charge_when_car_is_home.yaml`](../blueprints/automation/smartcar/emporia_charge_when_car_is_home.yaml)

Turns an Emporia EV charger off when Smartcar says the vehicle is not home,
restores current when it returns.

**Cost:** zero. Reads the Smartcar device tracker, which is refreshed by
webhook or the presence-driven refresh blueprint, not by this one.

Inputs: Smartcar device tracker, Emporia charger switch, Emporia charging
current number, home current (default 32 A).

### Smartcar + Emporia: taper charging current to state of charge

File: [`blueprints/automation/smartcar/emporia_taper_current_to_state_of_charge.yaml`](../blueprints/automation/smartcar/emporia_taper_current_to_state_of_charge.yaml)

See the blueprint's own description for its inputs and API cost; it predates
this catalogue and is unchanged.

## Scripts

### Compute round trip need

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Ftrooperthorn%2Fha_int_smartcar%2Fmain%2Fblueprints%2Fscript%2Fsmartcar%2Fcompute_round_trip_need.yaml)

File: [`blueprints/script/smartcar/compute_round_trip_need.yaml`](../blueprints/script/smartcar/compute_round_trip_need.yaml)

Given a destination, a starting zone, a travel-distance provider, the
current range and battery level, returns whether the round trip fits and
the state of charge percent that would be needed if it does not. Used by
the trip-check, on-demand charging and evening summary blueprints so all
three agree on one calculation.

**Cost:** zero Smartcar calls. It calls a travel-distance provider
(`waze_travel_time.get_travel_times` or `google_travel_time.get_travel_times`),
not Smartcar.

Returns (response variable): `distance_km`, `round_trip_km`, `fits`,
`needed_percent`.

Inputs: destination, starting zone (default `zone.home`), provider, Waze
region or Google config entry as needed, overhead percent (default 20),
current range in km, current battery level percent, current charge limit
percent (needed_percent is never returned below this).

### Charge for a round trip

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Ftrooperthorn%2Fha_int_smartcar%2Fmain%2Fblueprints%2Fscript%2Fsmartcar%2Fcharge_for_round_trip.yaml)

File: [`blueprints/script/smartcar/charge_for_round_trip.yaml`](../blueprints/script/smartcar/charge_for_round_trip.yaml)

Calls the "compute round trip need" script for one destination, and if the
trip does not fit, sets the charge limit to the needed percent and starts
charging. Callable from any automation, dashboard button, or notification
action, with a `destination` field.

**Cost:** zero reads, up to two commands (set limit, start charging), only
when the trip does not already fit.

Inputs: destination, plus the same provider, range, battery, charging
switch, charge limit and "compute round trip need" script inputs as above.

### Refresh, wait for fresh data, then act

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Ftrooperthorn%2Fha_int_smartcar%2Fmain%2Fblueprints%2Fscript%2Fsmartcar%2Frefresh_then_act.yaml)

File: [`blueprints/script/smartcar/refresh_then_act.yaml`](../blueprints/script/smartcar/refresh_then_act.yaml)

Refreshes one vehicle, waits for a chosen sensor to actually change (or
times out), and only then runs whatever action sequence you provide.

**Cost:** one `smartcar.refresh_vehicle` call per run.

Inputs: Smartcar account, a sensor to watch for freshness, wait timeout
(default 120s), the action sequence to run once fresh.

## A note on data freshness

Every blueprint above that reads battery, range, plug, charging or lock
state is reading whatever Home Assistant last received, which can be
minutes old if a webhook just pushed it or up to a day old on the daily
polling profile with no webhook (see [polling.md](polling.md)). None of that
data is stale in the sense of being wrong, only in the sense of being a
snapshot. Call `smartcar.refresh_vehicle` (one billed call, or `force: true`
to spend the reserve) before anything that needs a guaranteed-current
reading, such as confirming a lock right after leaving the car.
