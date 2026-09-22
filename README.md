# Smartcar Integration for Home Assistant

[![HACS](https://img.shields.io/badge/default-grey?logo=homeassistantcommunitystore&logoColor=white)][hacs-repo]
[![HACS installs](https://img.shields.io/github/downloads/wbyoung/smartcar/latest/total?label=installs&color=blue)][hacs-repo]
[![Version](https://img.shields.io/github/v/release/wbyoung/smartcar)][releases]
![Downloads](https://img.shields.io/github/downloads/wbyoung/smartcar/total)
![Build](https://img.shields.io/github/actions/workflow/status/wbyoung/smartcar/pytest.yml)
[![Github Sponsors](https://img.shields.io/badge/GitHub%20Sponsors-grey?&logo=GitHub-Sponsors&logoColor=EA4AAA)][gh-sponsors]

Connect your compatible vehicle to Home Assistant using the [Smartcar API](https://smartcar.com/).

This integration provides various sensors and controls for vehicles linked through the Smartcar platform, allowing you to monitor and interact with your car directly within Home Assistant.

**Note:** This integration relies on the Smartcar service. Availability of specific features depends on your vehicle's make, model, year, your Smartcar account plan (especially signal/sensor availability and API rate limits), and the permissions granted during authentication.

<img src="images/device_page.png" alt="Example Device Page Screenshot" width="600"/>

_Example showing entities for a Volkswagen ID.4_

## Prerequisites

1.  **Compatible Vehicle:** Your car must be [compatible with Smartcar](https://smartcar.com/product/compatible-vehicles) and the API must also be [supported in your country](https://smartcar.com/global).
2.  **Smartcar Developer Account:** You need a free developer account from Smartcar.
    - Go to [developer.smartcar.com](https://developer.smartcar.com/) and sign up.
    - Log in to your Developer Dashboard.
3.  **Ensure a Smartcar Application exists:**
    - In the dashboard, go to "Applications" and ensure an application was automatically created for you.
    - Rename your application if you want to (e.g., "Home Assistant Connect").

## Installation

### HACS

Installation through [HACS][hacs] is the preferred installation method.

[![Open the Smartcar integration in HACS][hacs-badge]][hacs-open]

1. Click the button above or go to HACS &rarr; Integrations &rarr; search for
   "Smartcar" &rarr; select it.
1. Press _DOWNLOAD_.
1. Select the version (it will auto select the latest) &rarr; press _DOWNLOAD_.
1. Restart Home Assistant then continue to [the setup section](#setup).

### Manual Download

1. Go to the [release page][releases] and download the `smartcar.zip` attached
   to the latest release.
1. Unpack the zip file and move `custom_components/smartcar` to the following
   directory of your Home Assistant configuration: `/config/custom_components/`.
1. Restart Home Assistant then continue to [the setup section](#setup).

## Setup

Setup has three parts, and they must be done in this order:

1. [Smartcar Dashboard configuration](#smartcar-dashboard-configuration) - collect three values and register one redirect URI.
2. [Home Assistant configuration flow](#configuration-flow) - paste those values and authorize the vehicle.
3. [Webhooks](#webhooks) - paste the URL Home Assistant prints back into the dashboard, then verify.

Nothing works until all three are done. A vehicle that is connected but not subscribed to a verified webhook produces entities that are created and never get a value, and no error anywhere. See [docs/communication.md](docs/communication.md) for why.

### Smartcar Dashboard Configuration

Everything in this section is in the [Smartcar dashboard][smartcar-dashboard], inside your application, on the **Configuration** tab. Open it in a second browser tab and keep it open: the Home Assistant config flow asks for these values one screen at a time.

There are **three** values to collect, and they are not interchangeable:

| Value | Where it is | What it is for | Goes into |
| --- | --- | --- | --- |
| **Client ID** and **Client Secret** (API credentials) | Configuration &rarr; _API credentials_ | The application token used for every data read and command | Home Assistant's _Application Credentials_ dialog |
| **Application ID** | Configuration &rarr; _Application details_ | Identifies the application to Smartcar Connect during authorization | The config flow's _Application ID_ field |
| **Application Management Token** | Configuration &rarr; _API credentials_, at the very bottom of the page | The HMAC key that signs webhook deliveries. Nothing else. | The _Application Management Token_ field in the config flow and in Configure |

#### Redirect URIs

In _Application details_, find **Redirect URIs** and make sure the list contains exactly this value:

```text
https://my.home-assistant.io/redirect/oauth
```

- This is **not a placeholder**. It is Home Assistant's standard OAuth redirect, used by every Home Assistant integration that authorizes through an external service, and it is correct even though it is not your own instance's address. Use it verbatim unless you have removed `default_config:` from your configuration and disabled the [My Home Assistant integration](https://www.home-assistant.io/integrations/my/).
- **Do not use this URL anywhere else in the dashboard.** In particular it is **not** the webhook callback URI. Pasting it into a webhook callback field is the single most common way this setup fails, and the failure is silent: see [the three wrong callback values](#the-three-wrong-callback-values).
- Adding a redirect URI takes effect immediately. If the authorization step later reports a redirect mismatch, it is because this value differs by even one character.

#### Legacy credentials vs API credentials

The Configuration tab shows two credential pairs, and choosing the wrong one silently puts the integration on the deprecated v2 API:

- **Legacy credentials** give a **Legacy Client ID** that is a plain UUID. That UUID is the same value as the Application ID, which is exactly why people paste it into the wrong box. It drives the old authorization-code flow against the v2 API.
- **API credentials** give a **Client ID that begins with `client_`**, plus a **Client Secret**. You may hold up to **three** secrets at once, which is what lets you rotate one without downtime. This is the pair the integration wants.

The integration decides which API version to use purely by looking at the prefix: an ID beginning `client_` selects v3, anything else selects v2. So a Client ID without the `client_` prefix does not produce an error, it produces a v2 entry. If you are unsure which you entered, open the integration's diagnostics: the resolved API version is reported there.

Copy a Client Secret when it is created. The dashboard shows it once.

#### Vehicle Access signal selection

Still under Configuration, open **Vehicle access**. This is where you choose which signal groups your application is allowed to request. Enable every group you want, keeping in mind that on the Free plan most groups are locked behind **Upgrade** (see [Plan notes](#plan-notes)).

The important warning on this page is easy to miss:

> **Changing the signal selection only affects new connections.** A vehicle that is already connected keeps the signal set it was connected with.

So if you enable a signal group after your car is already connected, you must disconnect the vehicle and re-run Smartcar Connect before that group does anything. Get this page right before you authorize.

### Configuration Flow

Once the dashboard side is ready, add the integration in Home Assistant.

1. Navigate to _Settings_ &rarr; _Devices & Services_
1. Click _+ Add Integration_
1. Search for and select &rarr; _Smartcar_

Or you can use the My Home Assistant Button below.

[![Add Integration](https://my.home-assistant.io/badges/config_flow_start.svg)][config-flow-start]

#### Authorization Data Entry

1. **Application Credentials dialog.** The first time you add Smartcar, Home Assistant asks for credentials. Give the credential set a name you will recognize (for example `Smartcar v3`), then enter:
   - **Client ID**: the **API credentials** Client ID, the one that **begins with `client_`**. Not the Application ID, and not the Legacy Client ID.
   - **Client Secret**: the matching API credentials secret.

   These are stored under [Application Credentials][ha-application-credentials] and can be reused by later entries.

1. **Smartcar configuration screen.** This screen asks for:
   - **Application ID**: the UUID from Configuration &rarr; _Application details_. This is required for v3 and is a **different field from the Client ID**, even though it looks identical to the Legacy Client ID.
   - **Use webhooks**: turn this on. It is Smartcar's preferred delivery method and, on v3, the only thing that keeps the signal store filled.
   - **Application Management Token**: the token from the bottom of _API credentials_. Only provide it when _Use webhooks_ is on; the flow rejects it otherwise with "Do not provide application management token unless using webhooks". Leaving it out while webhooks are on fails with "Missing application management token".

1. **Permission selection.** Tick the permissions Home Assistant should request. To enable every entity this integration offers, select all of them:
   - Get total distance traveled
   - Get the vehicle's location
   - Get EV/PHEV battery level, capacity & current range
   - Get details on whether the car is plugged in and charging
   - Get details on whether doors, windows & more are enabled
   - Get engine oil health
   - Get tire pressure details
   - Get fuel tank level
   - Get diagnostic trouble codes and system health
   - Get cabin temperature and climate control status
   - Control charging (start/stop & target charge)
   - Lock or unlock vehicle

   Two things to know about this screen:
   - **Plan-locked scopes.** On the Free plan, groups such as Charge, Location, Climate, Diagnostics and Wheel are not available to your application at all. You can still tick the matching permission and Smartcar can still grant it, but no webhook will ever be able to carry those signals. Entities for them are created and stay unavailable. Untick them if you would rather not see unavailable entities.
   - **Vehicle support.** A permission your car does not implement can also be granted and still return nothing.

   On a **reconfigure**, this screen starts from what Smartcar has actually granted rather than what was originally asked for, so it is a reliable way to see your real scope set.

1. Continue to the [next section](#authorization-via-smartcar-connect) which explains the steps to authorize your vehicle via [Smartcar Connect](https://smartcar.com/docs/connect/what-is-connect).

#### Authorization via Smartcar Connect

1. Home Assistant shows a **Connect** link. Clicking it **opens Smartcar Connect in a new browser tab**; the original Home Assistant tab stays open and waits. Do not close it, and do not reload it while Connect is running.
1. Log in using the credentials for your **vehicle's connected services account** (for example your Volkswagen ID, FordPass account, or Tesla account), **NOT** your Smartcar developer account credentials.
1. Review the permissions requested and press **Allow**.
1. Connect hands back to `https://my.home-assistant.io/redirect/oauth`, which forwards to your instance and closes the loop. Return to the original Home Assistant tab; it should have advanced.

Connect runs once. The only value the integration keeps from it is the Smartcar **user ID**, which becomes the `sc-user-id` header on every later request. If a request ever fails with `missing userId`, re-running Connect is the fix.

#### Setup Complete

If successful, the integration will be added, and Home Assistant will create devices and entities for your connected vehicle(s). The final screen prints the **webhook URL**. Copy it now; the next section needs it.

From here:

- Complete the [webhook setup](#webhooks). Without it, on v3, there is nothing to read.
- Enable entities you want to access after understanding [the impact on rate limits](#rate-limits--polling) if you're using polling.
- Consider creating a [customized polling setup](#customized-polling) via automations.

### Webhooks

On the v3 API this is not optional decoration. `GET /vehicles/{id}/signals` reads a server-side store, and the only thing that fills that store is a webhook that is verified, is enabled, carries data signals, and has your vehicle subscribed to it. Until that is true the endpoint answers with an empty result, every entity stays blank, and nothing raises an error.

**Prerequisite:** your Home Assistant instance must be reachable from the internet, either through Home Assistant Cloud or another method. Without Home Assistant Cloud you must set _Settings_ &rarr; _System_ &rarr; _Network_ &rarr; **Internet URL** (see the [Remote Access documentation][ha-remote-access]), or the integration has no URL to print.

#### Step 1: Get the webhook URL from Home Assistant

1. Go to _Settings_ &rarr; _Devices & Services_ &rarr; _Smartcar_.
1. Click **Configure** on the integration entry.
1. Make sure **Use webhooks** is on and paste the **Application Management Token** (Configuration &rarr; _API credentials_, bottom of the page in the Smartcar dashboard) into the token field.
1. Press **Submit**.
1. The screen shows the line **"Use the following URL:"** followed by the URL in a code block. Copy it exactly, including the scheme and any trailing characters.

What that URL looks like:

| Setup | URL shape | Notes |
| --- | --- | --- |
| Home Assistant Cloud (Nabu Casa) | `https://hooks.nabu.casa/<opaque id>` | A cloudhook. Long, base64-looking, and frequently ends in `=`. Copy the whole thing. |
| Your own remote access | `https://<your external URL>/api/webhook/<webhook id>` | Requires _Settings_ &rarr; _System_ &rarr; _Network_ &rarr; **Internet URL** to be set, or you get a "No URL available" error instead. |

If the screen says webhooks are not enabled rather than showing a URL, finish the rest of setup and come back to this Configure screen; the URL appears once the webhook is registered.

#### Step 2: Create or edit the webhook in the Smartcar dashboard

From the [Smartcar dashboard][smartcar-dashboard], open **Integrations**. If a webhook already exists, click its name and edit it. Otherwise click **Create integration** and choose **Webhook**.

Set these fields:

| Field | Value |
| --- | --- |
| Name | Anything recognizable, for example `Home Assistant` |
| **Vehicle data callback URI** | The URL copied in step 1, pasted verbatim |
| **Vehicle error callback URI** | The **same** URL. The integration handles both event types on one endpoint. |
| **Vehicle subscription** | **Automatically subscribe all vehicles** |
| Units | Metric or Imperial, your preference. The integration converts either way. |

Use **Automatically subscribe all vehicles** unless you have a reason not to. It removes the separate per-vehicle subscription step, and an unsubscribed vehicle produces a verified webhook that never delivers anything, which is a confusing place to end up.

**Triggers.** A trigger is the event that causes a delivery. Tick _Only show signals included in my plan_, then enable every trigger offered. The three that earn their place on almost any vehicle are:

- `tractionbattery-stateofcharge`
- `closure-islocked`
- `odometer-traveleddistance`

Avoid weighting the trigger list toward signals your vehicle does not implement. Triggers such as `connectivitystatus-isonline`, `connectivitystatus-isasleep`, `vehicleidentification-nickname`, `vehicleuseraccount-role` and `vehicleuseraccount-permissions` simply never fire on many cars, which produces a verified but very quiet webhook.

**Data signals.** A data signal is what gets included in the delivery. Again use _Only show signals included in my plan_ and enable all of them. Every delivery carries **all** configured data signals, not just the one that triggered it, so there is no cost to enabling them all.

Save the webhook.

#### Step 3: Verify

1. From **Integrations**, open your webhook.
1. In the ellipsis menu at the top right, choose **Verify**.
1. Press **Verify this webhook**.

Smartcar posts a one-time challenge to the callback URI, the integration answers it with an HMAC of the Application Management Token, and the status flips from **Unverified** to **Verified**. If it does not, nothing further in this document will work; go to [the three wrong callback values](#the-three-wrong-callback-values) below.

#### Step 4: Test webhook

With the status showing Verified, use **Test webhook** from the same menu. This sends a synthetic event. See [Verify Communication](#verify-communication) for exactly what should come back.

#### The three wrong callback values

Three values get pasted into the callback URI fields by mistake. Each fails differently, and none of them produce an obvious message in Home Assistant:

| What was pasted | What comes back | Why |
| --- | --- | --- |
| `https://my.home-assistant.io/redirect/oauth`, the OAuth redirect | A Netlify **"Page not found"** 404 | That host is a static redirector run by the Home Assistant project. It has no webhook endpoint, and the request never reaches your instance at all. This value is correct for Redirect URIs and **only** for Redirect URIs. |
| A Home Assistant **page** URL, such as `https://<external>/config/integrations` | The raw **HTML** of the Home Assistant frontend, and verification fails because the response is not the expected JSON challenge | The frontend serves a web page on that path. Webhooks live under `/api/webhook/<id>` only. |
| The right URL, but with a **stale Application Management Token** in Home Assistant | **401**, reported as an **invalid signature** | The token is the HMAC key on both ends. Regenerating it in the dashboard, or a typo on paste, makes every signature comparison fail. Re-paste the current token into the Configure screen and verify again. |

### Verify Communication

Work through these in order. Each step proves a different link in the chain, so the first one that fails tells you where the problem is.

#### 1. Verify says Verified

The dashboard's **Verify** action should report success and the webhook's status should read **Verified**. This proves that the callback URL reaches your instance and that the Application Management Token in Home Assistant matches the one in the dashboard.

#### 2. Test webhook returns 202

Trigger **Test webhook**. The integration accepts it, validates the signature, recognizes it as a test and deliberately does nothing further. The dashboard gets a **202** back, and the Home Assistant log records that no action was taken, quoting `mode=TEST`.

This response is the useful part: it is generated by the integration itself and it sits **after** the signature check. A 202 here proves the URL, the token, the signature verification and the payload parser all work. It does not prove anything about your vehicle, because the test payload describes a synthetic one.

#### 3. The first LIVE delivery arrives within minutes

Once the vehicle is subscribed, Smartcar sends a first real delivery with the trigger `FIRST_DELIVERY` and `mode` set to `LIVE`. On a Volkswagen this landed within a few minutes of subscribing. It carries the VIN and the data signals the vehicle can answer.

It is normal for that same delivery to be accompanied by a `VEHICLE_ERROR` event listing signals your car does not implement, with the code `VEHICLE_NOT_CAPABLE`. The integration logs those and ignores them. Five such signals on a single car is an ordinary result, not a fault.

#### 4. `sensor.<make_model>_last_webhook_received` moves

This sensor exists precisely for this check. It updates on **every** inbound webhook, including ones that are rejected, and its attributes carry `response_status` and `response_data`. If it never moves, nothing is arriving and the problem is upstream of Home Assistant. If it moves but shows a 401, the token is wrong.

#### 5. Entities populate

Within a delivery or two, expect values on:

- **Battery Level** (`sensor.<make_model>_battery`), a percentage
- **Door Lock** (`lock.<make_model>_door_lock`)
- **Odometer** (`sensor.<make_model>_odometer`), which you have to enable first as it is off by default

If the webhook is delivering but these stay empty, check that the matching permissions were granted and that your vehicle supports those signals.

#### 6. Read the debug log

Turn on debug logging from the integration's own page: _Settings_ &rarr; _Devices & Services_ &rarr; _Smartcar_ &rarr; **Enable debug logging**. Reproduce, then download the log. These are the lines that matter, in the order they should appear.

Registration, once per start:

```text
[custom_components.smartcar] Registering webhook at url: https://hooks.nabu.casa/...
```

The verification challenge:

```text
[custom_components.smartcar.webhooks] Received JSON from Smartcar: '{"eventId":"...","eventType":"VERIFY","data":{"challenge":"challenge_..."},...}'
```

A test delivery, acknowledged and deliberately dropped:

```text
[custom_components.smartcar.webhooks] Validating signature
[custom_components.smartcar.webhooks] mode=TEST; no action taken for vehicle with id: ...
```

The first live delivery, carrying `"triggers":[{"type":"FIRST_DELIVERY"}]` and `"mode":"LIVE"`, followed by a signature check with no complaint after it:

```text
[custom_components.smartcar.webhooks] Received JSON from Smartcar: '{"eventId":"...","eventType":"VEHICLE_STATE",...,"mode":"LIVE","sequence":...,"signalCount":9}'
[custom_components.smartcar.webhooks] Validating signature
```

Signals a vehicle cannot answer, logged and ignored:

```text
[custom_components.smartcar.webhooks] ignoring error in webhook: {'type': 'COMPATIBILITY', 'code': 'VEHICLE_NOT_CAPABLE', ...}
```

And the line that shows the store filling up. Before the webhook is delivering, a poll reports zero:

```text
[custom_components.smartcar.coordinator] Coordinator smartcar_...: vehicle cannot answer 0 of 0 signals
```

After deliveries have started, the same poll reports real numbers, and the total is usually **larger than the number of data signals on the webhook**:

```text
[custom_components.smartcar.coordinator] Coordinator smartcar_...: vehicle cannot answer 6 of 25 signals
```

#### Symptom to fix

| Symptom | Most likely cause | Fix |
| --- | --- | --- |
| Webhook status stays **Unverified** | Callback URI is not the URL Home Assistant printed | Re-copy from the Configure screen, paste into **both** callback fields, verify again |
| Verify returns a Netlify **"Page not found"** 404 | The OAuth redirect was pasted into the callback URI | Use the Home Assistant webhook URL. `my.home-assistant.io/redirect/oauth` belongs in Redirect URIs only |
| Verify returns **HTML** | A Home Assistant frontend page URL was used | Use the printed `/api/webhook/<id>` URL or the cloudhook |
| Verify or deliveries return **401 invalid signature** | Application Management Token mismatch | Re-copy the token from the bottom of _API credentials_ into Configure, Submit, verify again |
| Configure screen shows **"No URL available"** | No external URL | Set _Settings_ &rarr; _System_ &rarr; _Network_ &rarr; **Internet URL**, or enable Home Assistant Cloud |
| **Verified**, but no deliveries ever | Vehicle is not subscribed, or every trigger is a signal the car cannot report | Set **Automatically subscribe all vehicles**, and add `odometer-traveleddistance` and `closure-islocked` as triggers |
| Authorization fails with a redirect mismatch | The Redirect URIs list does not contain the exact value | Add `https://my.home-assistant.io/redirect/oauth` verbatim |
| The entry behaves like the old API | Client ID has no `client_` prefix | Re-add Application Credentials with the **API credentials** pair, see [Upgrading from Legacy `v2` API to `v3`](#upgrading-from-legacy-v2-api-to-v3) |
| A request fails with `missing userId` | The Smartcar user ID was never captured | Re-run Smartcar Connect |
| Entities created but permanently unavailable | Scope not granted, plan-locked, or the vehicle does not implement the signal | Reconfigure to see the granted scopes, then check the plan and your vehicle's compatibility |
| A newly enabled signal group changes nothing | Vehicle Access changes apply to **new connections only** | Disconnect the vehicle and re-run Connect |

### Plan Notes

The Free tier is workable, but it is worth knowing exactly where its edges are:

- **500 API calls per vehicle per month.** Commands count against the same allowance, so every reading you take is a lock or a charge stop you cannot send later. Webhook deliveries are **not** billed against it.
- **One vehicle.**
- **A webhook can carry at most 9 signals**, plus a small number of attributes, and the richer signal groups (Charge, Location, Climate, Diagnostics, HVAC, Motion, Service, Surveillance, Transmission, Wheel, LowVoltageBattery) are locked behind Upgrade in Vehicle Access.

There is one very useful consequence of how the store works, and it is not obvious from the plan page: **once a vehicle is subscribed, Smartcar collects every signal your grant covers into the store, not only the ones on the webhook's data list.** So charge state and location, which the Free plan cannot push through a webhook, still arrive through a normal polled read of the signals endpoint. In a live check, a webhook configured with 9 data signals produced a store holding 25.

That makes "granted scope" and "configurable webhook signal" two different sets. The webhook decides what gets pushed and how often the store refreshes; the grant decides what the store is allowed to contain.

## FAQ's and Troubleshooting

See the [FAQ](FAQ.md) for more help with various topics.

## Entities

Several entities are created for for each connected vehicle (subject to vehicle compatibility and granted permissions) across the _device tracker_, _sensor_, _binary sensor_, _number_, _switch_, and _lock_ platforms:

- [`device_tracker.<make_model>_location`](#device_trackermake_model_location)
- [`sensor.<make_model>_battery_capacity`](#sensormake_model_battery_capacity)
- [`sensor.<make_model>_battery`](#sensormake_model_battery)
- [`sensor.<make_model>_charge_rate`](#sensormake_model_charge_rate)
- [`sensor.<make_model>_energy_added`](#sensormake_model_energy_added)
- [`sensor.<make_model>_time_to_complete`](#sensormake_model_time_to_complete)
- [`sensor.<make_model>_low_voltage_battery`](#sensormake_model_low_voltage_battery)
- [`sensor.<make_model>_charging_status`](#sensormake_model_charging_status)
- [`sensor.<make_model>_engine_oil_life`](#sensormake_model_engine_oil_life)
- [`sensor.<make_model>_fuel`](#sensormake_model_fuel)
- [`sensor.<make_model>_fuel_percent`](#sensormake_model_fuel_percent)
- [`sensor.<make_model>_fuel_range`](#sensormake_model_fuel_range)
- [`sensor.<make_model>_odometer`](#sensormake_model_odometer)
- [`sensor.<make_model>_range`](#sensormake_model_range)
- [`sensor.<make_model>_gear_state`](#sensormake_model_gear_state)
- [`sensor.<make_model>_tire_pressure_back_left`](#sensormake_model_tire_pressure_back_left)
- [`sensor.<make_model>_tire_pressure_back_right`](#sensormake_model_tire_pressure_back_right)
- [`sensor.<make_model>_tire_pressure_front_left`](#sensormake_model_tire_pressure_front_left)
- [`sensor.<make_model>_tire_pressure_front_right`](#sensormake_model_tire_pressure_front_right)
- [`sensor.<make_model>_charging_voltage`](#sensormake_model_charging_voltage)
- [`sensor.<make_model>_charging_current`](#sensormake_model_charging_current)
- [`sensor.<make_model>_charging_power`](#sensormake_model_charging_power)
- [`sensor.<make_model>_charging_time_remaining`](#sensormake_model_charging_time_remaining)
- [`sensor.<make_model>_charging_current_max`](#sensormake_model_charging_current_max)
- [`sensor.<make_model>_firmware_version`](#sensormake_model_firmware_version)
- [`binary_sensor.<make_model>_charging_cable_plugged_in`](#binary_sensormake_model_charging_cable_plugged_in)
- [`binary_sensor.<make_model>_battery_heater_active`](#binary_sensormake_model_battery_heater_active)
- [`binary_sensor.<make_model>_front_trunk`](#binary_sensormake_model_front_trunk)
- [`binary_sensor.<make_model>_front_trunk_lock`](#binary_sensormake_model_front_trunk_lock)
- [`binary_sensor.<make_model>_rear_trunk`](#binary_sensormake_model_rear_trunk)
- [`binary_sensor.<make_model>_rear_trunk_lock`](#binary_sensormake_model_rear_trunk_lock)
- [`binary_sensor.<make_model>_sunroof`](#binary_sensormake_model_sunroof)
- [`binary_sensor.<make_model>_engine_cover`](#binary_sensormake_model_engine_cover)
- [`binary_sensor.<make_model>_door_back_left`](#binary_sensormake_model_door_back_left)
- [`binary_sensor.<make_model>_door_back_left_lock`](#binary_sensormake_model_door_back_left_lock)
- [`binary_sensor.<make_model>_door_back_right`](#binary_sensormake_model_door_back_right)
- [`binary_sensor.<make_model>_door_back_right_lock`](#binary_sensormake_model_door_back_right_lock)
- [`binary_sensor.<make_model>_door_front_left`](#binary_sensormake_model_door_front_left)
- [`binary_sensor.<make_model>_door_front_left_lock`](#binary_sensormake_model_door_front_left_lock)
- [`binary_sensor.<make_model>_door_front_right`](#binary_sensormake_model_door_front_right)
- [`binary_sensor.<make_model>_door_front_right_lock`](#binary_sensormake_model_door_front_right_lock)
- [`binary_sensor.<make_model>_window_back_left`](#binary_sensormake_model_window_back_left)
- [`binary_sensor.<make_model>_window_back_right`](#binary_sensormake_model_window_back_right)
- [`binary_sensor.<make_model>_window_front_left`](#binary_sensormake_model_window_front_left)
- [`binary_sensor.<make_model>_window_front_right`](#binary_sensormake_model_window_front_right)
- [`binary_sensor.<make_model>_online`](#binary_sensormake_model_online)
- [`binary_sensor.<make_model>_asleep`](#binary_sensormake_model_asleep)
- [`binary_sensor.<make_model>_digital_key_paired`](#binary_sensormake_model_digital_key_paired)
- [`binary_sensor.<make_model>_surveillance_enabled`](#binary_sensormake_model_surveillance_enabled)
- [`binary_sensor.<make_model>_fast_charger_connected`](#binary_sensormake_model_fast_charger_connected)
- [`number.<make_model>_charge_limit`](#numbermake_model_charge_limit)
- [`switch.<make_model>_charging`](#switchmake_model_charging)
- [`lock.<make_model>_door_lock`](#lockmake_model_door_lock)

All entities have the following attributes:

- `age` The date at which the data was recorded by the vehicle\*; _corresponds to [`meta.oemUpdatedAt`](https://smartcar.com/docs/api-reference/get-signal#response-data-meta-oem-updated-at) or [`sc-data-age`](https://smartcar.com/docs/api-reference/headers#param-sc-data-age)_
- `fetched_at` The date at which Smartcar fetched the data\*; _corresponds to [`meta.retrievedAt`](https://smartcar.com/docs/api-reference/get-signal#response-data-meta-retrieved-at) or [`sc-data-fetched-at`](https://smartcar.com/docs/api-reference/headers#param-sc-fetched-at)_

\* _These will only be present when included in the API response._

Links to relevant API documentation are provided for each entity described below as well as the [permissions each entity requires](https://smartcar.com/docs/api-reference/permissions). When the required permissions are [not requested during setup](#authorization-data-entry), those entities will not be created.

Which entities arrive switched on is decided by your vehicle, not by a fixed
list. Setup reads the vehicle's signal store once, and every signal it answered
gets an enabled entity, including the charge signals that answer
`NOT_CHARGING`: those are readings, and they fill in the moment the car is
plugged in. A signal the store did not mention still gets an entity, switched
off, and that entity switches itself on if the signal ever starts arriving. A
signal the vehicle reported it is incapable of gets no entity at all. An entity
**you** switch off stays off. If the capability read cannot happen, because the
entry is a legacy v2 one or polling is disabled, the old static default list is
used instead.

In addition to the above, there are sensors to aid in setting up the
integration and in watching what setup costs:

- [`sensor.<make_model>_last_webhook_received`](#sensormake_model_last_webhook_received)
- [`sensor.<make_model>_next_scheduled_poll`](#sensormake_model_next_scheduled_poll)

### `device_tracker.<make_model>_location`

The GPS [location](https://smartcar.com/docs/api-reference/signals/location#preciselocation) of the vehicle.

Enabled by default: :white_check_mark:  
Requires permissions: `read_location`

### `sensor.<make_model>_battery_capacity`

The [battery capacity](https://smartcar.com/docs/api-reference/signals/tractionbattery#nominalcapacity) of this vehicle in kWh.

Enabled by default: :x:  
Requires permissions: `read_battery`

### `sensor.<make_model>_battery`

The [state of charge](https://smartcar.com/docs/api-reference/signals/tractionbattery#stateofcharge) of the vehicle as a percentage.

Enabled by default: :white_check_mark:  
Requires permissions: `read_battery`  
Obtained concurrently with: [`sensor.<make_model>_range`](#sensormake_model_range)

### `sensor.<make_model>_charge_rate`

The [charge rate](https://smartcar.com/docs/api-reference/signals/charge#chargerate) of the vehicle.

Enabled by default: :x:  
Webhooks only: :link: _currently only available via webhooks_

### `sensor.<make_model>_energy_added`

The [amount of energy added](https://smartcar.com/docs/api-reference/signals/charge#energyadded) in the current or most recent charging session.

Enabled by default: :x:  
Webhooks only: :link: _currently only available via webhooks_

### `sensor.<make_model>_time_to_complete`

The [estimated time remaining](https://smartcar.com/docs/api-reference/signals/charge#timetocomplete) until charging is complete.

Enabled by default: :x:  
Webhooks only: :link: _currently only available via webhooks_

### `sensor.<make_model>_low_voltage_battery`

The [state of charge of the low voltage battery](https://smartcar.com/docs/api-reference/signals/lowvoltagebattery#stateofcharge).

Enabled by default: :x:  
Webhooks only: :link: _currently only available via webhooks_

### `sensor.<make_model>_charging_status`

The [charging status](https://smartcar.com/docs/api-reference/signals/charge#detailedchargingstatus) of the vehicle.

Possible values:

- `​CHARGING`
- `FULLY_CHARGED`
- `NOT_CHARGING`

Enabled by default: :white_check_mark:  
Requires permissions: `read_charge`  
Obtained concurrently with: [`binary_sensor.<make_model>_charging_cable_plugged_in`](#binary_sensormake_model_charging_cable_plugged_in), [`switch.<make_model>_charging`](#switchmake_model_charging)

### `sensor.<make_model>_engine_oil_life`

The [estimated engine oil life](https://smartcar.com/docs/api-reference/signals/internalcombustionengine#oillife) remaining for the vehicle.

Enabled by default: :x:  
Requires permissions: `read_engine_oil`

### `sensor.<make_model>_fuel`

The [volume of fuel](https://smartcar.com/docs/api-reference/signals/internalcombustionengine#amountremaining) remaining for the vehicle.

**Note:** This value is frequently `null` for many vehicles. Consider using [`sensor.<make_model>_fuel_percent`](#sensormake_model_fuel_percent) or [`sensor.<make_model>_fuel_range`](#sensormake_model_fuel_range) for more reliable fuel information.

Enabled by default: :x:  
Requires permissions: `read_fuel`  
Obtained concurrently with: [`sensor.<make_model>_fuel_percent`](#sensormake_model_fuel_percent), [`sensor.<make_model>_fuel_range`](#sensormake_model_fuel_range)

### `sensor.<make_model>_fuel_percent`

The [fuel level as a percentage](https://smartcar.com/docs/api-reference/signals/internalcombustionengine#fuellevel) remaining for the vehicle (0-100%).

This sensor provides more reliable fuel information than the amount-based sensor, as percentage values are more consistently available from vehicle APIs.

Enabled by default: :x:  
Requires permissions: `read_fuel`  
Obtained concurrently with: [`sensor.<make_model>_fuel`](#sensormake_model_fuel), [`sensor.<make_model>_fuel_range`](#sensormake_model_fuel_range)

### `sensor.<make_model>_fuel_range`

The [estimated driving range](https://smartcar.com/docs/api-reference/signals/internalcombustionengine#range) remaining for the vehicle based on current fuel level.

Enabled by default: :x:  
Requires permissions: `read_fuel`  
Obtained concurrently with: [`sensor.<make_model>_fuel`](#sensormake_model_fuel), [`sensor.<make_model>_fuel_percent`](#sensormake_model_fuel_percent)

### `sensor.<make_model>_odometer`

The [odometer reading](https://smartcar.com/docs/api-reference/signals/odometer#traveleddistance) of the vehicle.

Enabled by default: :x:  
Requires permissions: `read_odometer`

### `sensor.<make_model>_range`

The [estimated range remaining](https://smartcar.com/docs/api-reference/signals/tractionbattery#range) for the vehicle.

Enabled by default: :white_check_mark:  
Requires permissions: `read_battery`  
Obtained concurrently with: [`sensor.<make_model>_battery`](#sensormake_model_battery)

### `sensor.<make_model>_gear_state`

The [gear state](https://smartcar.com/docs/api-reference/signals/transmission#gearstate) for the vehicle.

Possible values:

- `PARK`
- `DRIVE`
- `REVERSE`
- `NEUTRAL`

Enabled by default: :x:  
Webhooks only: :link: _currently only available via webhooks_

### `sensor.<make_model>_tire_pressure_back_left`

The [back left tire pressure](https://smartcar.com/docs/api-reference/signals/wheel#tires) of the vehicle.

Enabled by default: :x:  
Requires permissions: `read_tires`  
Obtained concurrently with: [`sensor.<make_model>_tire_pressure_back_right`](#sensormake_model_tire_pressure_back_right), [`sensor.<make_model>_tire_pressure_front_left`](#sensormake_model_tire_pressure_front_left), [`sensor.<make_model>_tire_pressure_front_right`](#sensormake_model_tire_pressure_front_right)

### `sensor.<make_model>_tire_pressure_back_right`

The [back right tire pressure](https://smartcar.com/docs/api-reference/signals/wheel#tires) of the vehicle.

Enabled by default: :x:  
Requires permissions: `read_tires`  
Obtained concurrently with: [`sensor.<make_model>_tire_pressure_back_left`](#sensormake_model_tire_pressure_back_left), [`sensor.<make_model>_tire_pressure_front_left`](#sensormake_model_tire_pressure_front_left), [`sensor.<make_model>_tire_pressure_front_right`](#sensormake_model_tire_pressure_front_right)

### `sensor.<make_model>_tire_pressure_front_left`

The [front left tire pressure](https://smartcar.com/docs/api-reference/signals/wheel#tires) of the vehicle.

Enabled by default: :x:  
Requires permissions: `read_tires`  
Obtained concurrently with: [`sensor.<make_model>_tire_pressure_back_left`](#sensormake_model_tire_pressure_back_left), [`sensor.<make_model>_tire_pressure_back_right`](#sensormake_model_tire_pressure_back_right), [`sensor.<make_model>_tire_pressure_front_right`](#sensormake_model_tire_pressure_front_right)

### `sensor.<make_model>_tire_pressure_front_right`

The [front right tire pressure](https://smartcar.com/docs/api-reference/signals/wheel#tires) of the vehicle.

Enabled by default: :x:
Requires permissions: `read_tires`
Obtained concurrently with: [`sensor.<make_model>_tire_pressure_back_left`](#sensormake_model_tire_pressure_back_left), [`sensor.<make_model>_tire_pressure_back_right`](#sensormake_model_tire_pressure_back_right), [`sensor.<make_model>_tire_pressure_front_left`](#sensormake_model_tire_pressure_front_left)

### `sensor.<make_model>_charging_voltage`

The [current voltage](https://smartcar.com/docs/api-reference/signals/charge#voltage) supplied during charging in volts.

Enabled by default: :x:
Webhooks only: :link: _currently only available via webhooks_

### `sensor.<make_model>_charging_current`

The [current amperage](https://smartcar.com/docs/api-reference/signals/charge#amperage) flowing to the vehicle during charging in amps.

Enabled by default: :x:
Webhooks only: :link: _currently only available via webhooks_

### `sensor.<make_model>_charging_power`

The [current power delivery rate](https://smartcar.com/docs/api-reference/signals/charge#wattage) during charging in kilowatts.

Enabled by default: :x:
Webhooks only: :link: _currently only available via webhooks_

### `sensor.<make_model>_charging_time_remaining`

The [estimated time remaining](https://smartcar.com/docs/api-reference/signals/charge#time-to-complete) until the vehicle reaches its charge limit in minutes.

Enabled by default: :x:
Webhooks only: :link: _currently only available via webhooks_

### `sensor.<make_model>_charging_current_max`

The [maximum available amperage](https://smartcar.com/docs/api-reference/signals/charge#amperage-max) for charging the vehicle in amps.

Enabled by default: :x:
Webhooks only: :link: _currently only available via webhooks_

### `sensor.<make_model>_firmware_version`

The [current firmware version](https://smartcar.com/docs/api-reference/signals/connectivitysoftware#current-firmware-version) installed on the vehicle.

Enabled by default: :x:
Webhooks only: :link: _currently only available via webhooks_

### `binary_sensor.<make_model>_charging_cable_plugged_in`

Whether the vehicle [is currently plugged in](https://smartcar.com/docs/api-reference/signals/charge#ischargingcableconnected).

Enabled by default: :white_check_mark:  
Deprecated: This is deprecated and will be removed when the v2 API is no longer being used  
Requires permissions: `read_charge`  
Obtained concurrently with: [`sensor.<make_model>_charging_status`](#sensormake_model_charging_status), [`switch.<make_model>_charging`](#switchmake_model_charging)

### `binary_sensor.<make_model>_battery_heater_active`

Whether the vehicle [battery heater is active](https://smartcar.com/docs/api-reference/signals/tractionbattery#is-heater-active).

Enabled by default: :x:  
Webhooks only: :link: _currently only available via webhooks_

### `binary_sensor.<make_model>_front_trunk`

Whether the [front trunk is open](https://smartcar.com/docs/api-reference/signals/closure#front-trunk).

Enabled by default: :x:  
Webhooks only: :link: _currently only available via webhooks_

### `binary_sensor.<make_model>_front_trunk_lock`

Whether the [front trunk is locked](https://smartcar.com/docs/api-reference/signals/closure#front-trunk).

Enabled by default: :x:  
Webhooks only: :link: _currently only available via webhooks_

### `binary_sensor.<make_model>_rear_trunk`

Whether the [rear trunk is open](https://smartcar.com/docs/api-reference/signals/closure#rear-trunk).

Enabled by default: :x:  
Webhooks only: :link: _currently only available via webhooks_

### `binary_sensor.<make_model>_rear_trunk_lock`

Whether the [rear trunk is locked](https://smartcar.com/docs/api-reference/signals/closure#rear-trunk).

Enabled by default: :x:  
Webhooks only: :link: _currently only available via webhooks_

### `binary_sensor.<make_model>_sunroof`

Whether the [sunroof is open](https://smartcar.com/docs/api-reference/signals/closure#sunroof).

Enabled by default: :x:  
Webhooks only: :link: _currently only available via webhooks_

### `binary_sensor.<make_model>_engine_cover`

Whether the [engine cover is open](https://smartcar.com/docs/api-reference/signals/closure#engine-cover).

Enabled by default: :x:  
Webhooks only: :link: _currently only available via webhooks_

### `binary_sensor.<make_model>_door_back_left`

Whether the [back left door is open](https://smartcar.com/docs/api-reference/signals/closure#doors).

Enabled by default: :x:  
Webhooks only: :link: _currently only available via webhooks_

### `binary_sensor.<make_model>_door_back_left_lock`

Whether the [back left door is locked](https://smartcar.com/docs/api-reference/signals/closure#doors).

Enabled by default: :x:  
Webhooks only: :link: _currently only available via webhooks_

### `binary_sensor.<make_model>_door_back_right`

Whether the [back right door is open](https://smartcar.com/docs/api-reference/signals/closure#doors).

Enabled by default: :x:  
Webhooks only: :link: _currently only available via webhooks_

### `binary_sensor.<make_model>_door_back_right_lock`

Whether the [back right door is locked](https://smartcar.com/docs/api-reference/signals/closure#doors).

Enabled by default: :x:  
Webhooks only: :link: _currently only available via webhooks_

### `binary_sensor.<make_model>_door_front_left`

Whether the [front left door is open](https://smartcar.com/docs/api-reference/signals/closure#doors).

Enabled by default: :x:  
Webhooks only: :link: _currently only available via webhooks_

### `binary_sensor.<make_model>_door_front_left_lock`

Whether the [front left door is locked](https://smartcar.com/docs/api-reference/signals/closure#doors).

Enabled by default: :x:  
Webhooks only: :link: _currently only available via webhooks_

### `binary_sensor.<make_model>_door_front_right`

Whether the [front right door is open](https://smartcar.com/docs/api-reference/signals/closure#doors).

Enabled by default: :x:  
Webhooks only: :link: _currently only available via webhooks_

### `binary_sensor.<make_model>_door_front_right_lock`

Whether the [front right door is locked](https://smartcar.com/docs/api-reference/signals/closure#doors).

### `binary_sensor.<make_model>_window_back_left`

Whether the [back left window is open](https://smartcar.com/docs/api-reference/signals/closure#windows).

Enabled by default: :x:  
Webhooks only: :link: _currently only available via webhooks_

### `binary_sensor.<make_model>_window_back_right`

Whether the [back right window is open](https://smartcar.com/docs/api-reference/signals/closure#windows).

Enabled by default: :x:  
Webhooks only: :link: _currently only available via webhooks_

### `binary_sensor.<make_model>_window_front_left`

Whether the [front left window is open](https://smartcar.com/docs/api-reference/signals/closure#windows).

Enabled by default: :x:  
Webhooks only: :link: _currently only available via webhooks_

### `binary_sensor.<make_model>_window_front_right`

Whether the [front right window is open](https://smartcar.com/docs/api-reference/signals/closure#windows).

Enabled by default: :x:  
Webhooks only: :link: _currently only available via webhooks_

### `binary_sensor.<make_model>_online`

Whether the vehicle is [online](https://smartcar.com/docs/api-reference/signals/connectivitystatus#is-online).

Enabled by default: :x:  
Webhooks only: :link: _currently only available via webhooks_

### `binary_sensor.<make_model>_asleep`

Whether the vehicle is [asleep](https://smartcar.com/docs/api-reference/signals/connectivitystatus#is-asleep).

Enabled by default: :x:  
Webhooks only: :link: _currently only available via webhooks_

### `binary_sensor.<make_model>_digital_key_paired`

Whether the vehicle is [has a digital key that has been successfully paired](https://smartcar.com/docs/api-reference/signals/connectivitystatus#is-digital-key-paired).

Enabled by default: :x:  
Webhooks only: :link: _currently only available via webhooks_

### `binary_sensor.<make_model>_surveillance_enabled`

Whether the vehicle's [surveillance system is enabled](https://smartcar.com/docs/api-reference/signals/surveillance#is-enabled).

Enabled by default: :x:
Webhooks only: :link: _currently only available via webhooks_

### `binary_sensor.<make_model>_fast_charger_connected`

Whether a [DC fast charger is connected](https://smartcar.com/docs/api-reference/signals/charge#is-fast-charger-present) to the vehicle.

Enabled by default: :x:
Webhooks only: :link: _currently only available via webhooks_

### `number.<make_model>_charge_limit`

Change the [charge limit](https://smartcar.com/docs/api-reference/signals/charge#chargelimits) by [setting it to a specific value](https://smartcar.com/docs/api-reference/charging/set-charge-limit).

Enabled by default: :x:  
Requires permissions: `read_charge`, `control_charge`

### `switch.<make_model>_charging`

Change whether the vehicle is [currently charging](https://smartcar.com/docs/api-reference/signals/charge#detailedchargingstatus) by [starting](https://smartcar.com/docs/api-reference/charging/start-charging) or [stopping charging](https://smartcar.com/docs/api-reference/charging/stop-charging).

Enabled by default: :white_check_mark:  
Requires permissions: `read_charge`, `control_charge`  
Obtained concurrently with: [`sensor.<make_model>_charging_status`](#sensormake_model_charging_status), [`binary_sensor.<make_model>_charging_cable_plugged_in`](#binary_sensormake_model_charging_cable_plugged_in)

### `lock.<make_model>_door_lock`

Change whether the vehicle is [currently locked](https://smartcar.com/docs/api-reference/get-lock-status) by [locking](https://smartcar.com/docs/api-reference/security/lock-doors) or [unlocking](https://smartcar.com/docs/api-reference/security/unlock-doors).

Enabled by default: :white_check_mark:  
Requires permissions: `read_security`, `control_security`

_Note: some models, e.g., VW ID.4 2023+ do not have this functionality._

### `sensor.<make_model>_last_webhook_received`

The time the last webhook was received. This is set even when a received webhook is not valid (i.e. an unauthenticated request or for an unknown vehicle).

Enabled by default: :white_check_mark:

#### Attributes

- `response_status`: The status code used to respond to the webhook.
- `response_data`: The data sent in the response (when available).

### `sensor.<make_model>_next_scheduled_poll`

When the next scheduled read of this vehicle is due. That read is one call from
the 500 per vehicle monthly allowance, so this is the sensor an automation asks
before deciding to force a poll of its own with
`smartcar.refresh_vehicle`. It is `unknown` when
nothing is scheduled, and `paused_reason` says why.

Enabled by default: :white_check_mark:

#### Attributes

- `poll_profile`: The configured polling profile.
- `interval_seconds`: The scheduled cadence, or `None` when there is none.
- `last_poll_at`: When this vehicle was last read.
- `calls_reserved`: Calls kept back from polling so commands can still be sent.
- `paused_reason`: `webhook_only`, `no_interval`, `reserve_reached` or `None`.
- `next_poll_billed`: Always true. A scheduled read costs a call.

See [docs/polling.md](docs/polling.md) for an example automation.

## Actions

Smartcar provides the following actions:

- [`smartcar.lock_doors`](#smartcarlock_doors)
- [`smartcar.unlock_doors`](#smartcarunlock_doors)

### `smartcar.lock_doors`

Lock the doors of a vehicle. In most cases, the `lock.lock` action should be used on [`lock.<make_model>_door_lock`](#lockmake_model_door_lock) instead of using this action. It is only is provided for the case that the [`lock.<make_model>_door_lock`](#lockmake_model_door_lock) entity is not available due to unique permissions available for a vehicle in Smartcar. This occurs for a small subset of vehicles when Smartcar will only grant the `control_security` permission, but not the `read_security` permission.

#### Service Data Attributes

- `config_entry`: **required** Config entry to use. Example: `1b4a46c6cba0677bbfb5a8c53e8618b0`.
- `vin`: The VIN of the vehicle to target. If not provided, the first VIN for the config entry will be assumed.

### `smartcar.unlock_doors`

Lock the doors of a vehicle. In most cases, the `lock.unlock` action should be used on [`lock.<make_model>_door_lock`](#lockmake_model_door_lock) instead of using this action. It is only is provided for the case that the [`lock.<make_model>_door_lock`](#lockmake_model_door_lock) entity is not available due to unique permissions available for a vehicle in Smartcar. This occurs for a small subset of vehicles when Smartcar will only grant the `control_security` permission, but not the `read_security` permission.

#### Service Data Attributes

- `config_entry`: **required** Config entry to use. Example: `1b4a46c6cba0677bbfb5a8c53e8618b0`.
- `vin`: The VIN of the vehicle to target. If not provided, the first VIN for the config entry will be assumed.

## Rate Limits & Polling

- Consider setting up [webhooks](#webhooks). With webhooks enabled, polling will no longer occur avoiding most rate limit issues. Additionally, Smartcar is moving away from [their v2 API](https://smartcar.com/docs/api-reference/v2-overview) and polling may not be the best way to use the service.
- Smartcar's free developer tier typically has a limit of **500 API calls per vehicle per month**. Exceeding this may incur costs or stop the integration from working.
- By default, it uses **6 hour polling interval** and only fetches data required for enabled entities.
- Polling can be [customized as well](#customized-polling).

### Customized Polling

To customize polling, you can disable polling on the integration and write your own automation.

- First, configure the integration as described above.
- Go to _Settings_ &rarr; _Integartions_ (under _Devices & services_) &rarr; _Smartcar_
- Click the three dots to the right of the integration.
- Choose _System options_.
- Disable _Enable polling for changes_ and then click _Save_.
- Create an automation using [`homeassistant.update_entity`](https://www.home-assistant.io/integrations/homeassistant/#action-homeassistantupdate_entity) to refresh the desired value(s).

Examples are provided:

- [`examples/poll-smartcar-simple.yaml`](examples/poll-smartcar-simple.yaml)
- [`examples/poll-smartcar-custom.yaml`](examples/poll-smartcar-custom.yaml)
- [`examples/poll-smartcar-excessive.yaml`](examples/poll-smartcar-excessive.yaml)

When updating an entity via `homeassistant.update_entity`:

- A request to update an entity will also update related entities (see the _Obtained concurrently with_ notes on each entity above).
- Requests to update several entities at once will be [batched](https://smartcar.com/docs/api-reference/batch), reducing excessive network requests and potentially limiting the number of API calls counted against your account.

For instance:

- `homeassistant.update_entity` on [`sensor.<make_model>_battery`](#sensormake_model_battery) and [`sensor.<make_model>_range`](#sensormake_model_range) will make a single batch request that counts as **one** API call because the entities are related.
- `homeassistant.update_entity` on [`sensor.<make_model>_battery`](#sensormake_model_battery) and [`sensor.<make_model>_odometer`](#sensormake_model_odometer) will make a single batch request that counts as **two** API calls since they are unrelated.

## Blueprints

This repository ships ready-made automation and script blueprints under
[`blueprints/`](blueprints), for things like checking tomorrow's calendar
trips against your current range, a plug-in reminder, charging from solar
surplus, and warning before the monthly API allowance runs out.

See [docs/blueprints.md](docs/blueprints.md) for the full catalogue: every
blueprint's inputs, what it costs in Smartcar API calls, and a My Home
Assistant import badge for each.

Notifying blueprints share a common set of Android Companion app inputs
(tag, channel, importance, icon, click action, and an optional
text-to-speech announcement), documented in
[docs/blueprints.md#android-notification-options](docs/blueprints.md#android-notification-options).
Where a blueprint already knows the notified condition has cleared, it also
clears the notification instead of leaving it stale on the phone. These
fields are ignored on iOS, so the blueprints work there unchanged.

## Upgrading from Legacy `v2` API to `v3`

For now, you can continue to use the `v2` API as long as it is supported by Smartcar, but [Smartcar documents the deprecation thusly](https://smartcar.com/docs/getting-started/how-to/m2m/migration-guide#overview):

> The Vehicles API v2.0 will be deprecated by Q4 of 2026. We recommend migrating to the latest version as soon as possible to ensure continued support and access to new features.

To upgrade from `v2` to `v3`, you need to use the _API credentials_ rather than the _Legacy credentials_. In Home Assistant, you will need to:

- Remove all Smartcar integrations that use v2. _Note: this is required in order to remove credentials in the next step_.  
  Before removing the items, you may want to note any customizations to entities such as entity IDs or areas in which entities are located so that you can re-create them once the migration is complete.
- Remove all [_Application Credentials_][ha-application-credentials] that use a v2 `client_id`. To be safe, you can remove all Smartcar credentials from the _Application Credentials_.
- Re-setup each smart car integration, ensuring that you use the new v3 _API credentials_ and _Application ID_ rather than _Legacy Credentials_.

## Known Issues / Limitations

- **Vehicle Compatibility:** Not all features are supported by all vehicle makes/models/years via the Smartcar API. Entities for unsupported features (e.g., [fuel status](#sensormake_model_fuel) for EVs or [lock control](#lockmake_model_door_lock) for VW ID.4 2023+) may or may still be created, but not function. Check the Smartcar compatibility details for your specific vehicle.
- **Fuel Data Availability:** The [`sensor.<make_model>_fuel`](#sensormake_model_fuel) (amount in litres) is frequently `null` for many vehicles. However, [`sensor.<make_model>_fuel_percent`](#sensormake_model_fuel_percent) and [`sensor.<make_model>_fuel_range`](#sensormake_model_fuel_range) are typically more reliable and provide comprehensive fuel monitoring even when the amount is unavailable.
- **API Latency:** There can be significant delays (seconds to minutes) between sending a command (e.g., start charging) and the vehicle executing/reporting the change back through the API. The state in Home Assistant will update after the next successful data poll.
- **Rate Limits:** Be mindful of the 500 calls/vehicle/month limit on the free tier.
- **Single User:** Smartcar applications must be configured with only a single user connected to vehicles.
- **FAQ:** In case ou haven't found the [FAQ](FAQ.md) yet, it is a great resource for troubleshooting and discovering if you're running up against an issue with this integration vs. a limitation in Smartcar's platform.

## Reference Documentation

If entities are created but never get a value, or a command never lands, start with the reference docs rather than the entity list above:

- [docs/communication.md](docs/communication.md) - how this integration talks to Smartcar end to end: the v3 chain, the three credentials and which Smartcar dashboard tab each one lives in, the webhook model that actually feeds the signal store, the ways it fails silently, and a numbered dashboard checklist for fixing a live account.
- [docs/vehicle-and-battery-status.md](docs/vehicle-and-battery-status.md) - every vehicle-status and battery-status signal field by field, with units, enum values, the entity key each one becomes, what ships enabled by default, and what a given vehicle can never populate.
- [docs/api-reference.md](docs/api-reference.md) - the full Smartcar API surface and which parts this integration calls.
- [docs/polling.md](docs/polling.md) - the polling profiles and the request allowance that shapes them.

## Support / Issues

Please report any issues you find with this integration by opening an issue on the [GitHub Issues page](https://github.com/wbyoung/smartcar/issues).

[hacs]: https://hacs.xyz/
[hacs-repo]: https://github.com/hacs/integration
[hacs-badge]: https://my.home-assistant.io/badges/hacs_repository.svg
[hacs-open]: https://my.home-assistant.io/redirect/hacs_repository/?owner=wbyoung&repository=smartcar&category=integration
[releases]: https://github.com/wbyoung/smartcar/releases
[config-flow-start]: https://my.home-assistant.io/redirect/config_flow_start/?domain=smartcar
[smartcar-dashboard]: https://dashboard.smartcar.com/team/applications
[ha-remote-access]: https://www.home-assistant.io/docs/configuration/remote/
[ha-application-credentials]: https://www.home-assistant.io/integrations/application_credentials/
[gh-sponsors]: https://github.com/sponsors/wbyoung
