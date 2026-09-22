# Bus events

This integration fires four Home Assistant bus events so an automation, a
script, or a phone notification can close the loop on a command or a
problem without polling entity state. None of them ever carry a token, a
signature, or a full callback URL.

See [blueprints/automation/smartcar/command_and_issue_feedback.yaml](../blueprints/automation/smartcar/command_and_issue_feedback.yaml)
for a ready-made automation built on all four, and
[blueprints.md](blueprints.md) for the rest of the catalogue.

## `smartcar_command_result`

Fired exactly once after every command this integration sends (lock,
unlock, charge start/stop, set charge limit, climate start/stop, from both
the entities and the `smartcar.lock_doors`/`smartcar.unlock_doors`
services), whatever the outcome. A command that takes longer than about 175
seconds gets a `202 Accepted` and the real result streams in on the same
connection afterwards; this event still fires exactly once, after that
result is known, not after the `202`.

| Field | Type | Description |
| --- | --- | --- |
| `entry_id` | string | The config entry the command was sent from. |
| `vehicle_id` | string | The Smartcar vehicle id. |
| `vin` | string \| null | The vehicle's VIN, if known. |
| `device_id` | string \| null | The device registry id for this vehicle, if registered. |
| `command` | string | `lock`, `unlock`, `charge_start`, `charge_stop`, `set_charge_limit`, `climate_start`, or `climate_stop`. |
| `success` | boolean | Whether the command actually completed successfully. |
| `http_status` | int \| null | The HTTP status Smartcar actually returned (the status line, not the streamed body's own `status`). |
| `error_type` | string \| null | The Smartcar error type (for example `VEHICLE_STATE`, `PERMISSION`), or `http_error` for a plain HTTP failure with no structured body. |
| `error_code` | string \| null | The Smartcar error code, or the HTTP status as a string for a plain HTTP failure. |
| `resolution_type` | string \| null | Smartcar's suggested resolution (for example `RECONNECT`), when it supplied one. |
| `suggested_user_message` | string \| null | A short, human readable description of the failure suitable for a notification. |
| `took_ms` | int | Wall clock time from sending the request to this event firing. |

## `smartcar_webhook_received`

Fired for every webhook delivery that passes signature validation and is
for a known vehicle (a dashboard `mode=TEST` delivery and an unsigned
`VERIFY` challenge never reach this point, so neither fires the event).

| Field | Type | Description |
| --- | --- | --- |
| `vehicle_id` | string | The Smartcar vehicle id the delivery was for. |
| `device_id` | string \| null | The device registry id for this vehicle, if registered. |
| `event_type` | string | `VEHICLE_STATE` or `VEHICLE_ERROR`. |
| `mode` | string \| null | `LIVE` or `TEST` (only a `LIVE` delivery reaches this event; `TEST` deliveries are acknowledged earlier without being processed). |
| `delivery_id` | string \| null | Smartcar's `deliveryId` for this message, for matching against the Smartcar dashboard. |
| `sequence` | int \| null | The delivery's sequence number. |
| `signal_count` | int \| null | Smartcar's own `signalCount` for this delivery. |
| `trigger_codes` | list[string] | The signal codes that triggered this delivery. |
| `error_codes` | list[string] | For a `VEHICLE_ERROR` delivery, one `type:code` string per error (for example `PERMISSION:MISSING_PERMISSION`); empty for `VEHICLE_STATE`. |

## `smartcar_issue_raised` / `smartcar_issue_cleared`

Fired whenever this integration creates or deletes one of its own repair
issues: `legacy_client_id`, `webhook_unhealthy`, `no_matching_webhook`, and
`empty_signal_store`. `smartcar_issue_cleared` only fires when the issue
actually existed, so a call site that clears defensively on every pass does
not spam an event for an issue nobody ever saw.

| Field | Type | Description |
| --- | --- | --- |
| `issue_id` | string | The repair issue's id, as shown in **Settings → System → Repairs**. |
| `translation_key` | string | Which of the four issues this is. |
| `severity` | string | The issue registry severity (`warning`, `error`, or `critical`). |
| `entry_id` | string | The config entry the issue belongs to. |
| `placeholders` | object | *(`smartcar_issue_raised` only.)* The issue's translation placeholders. A `callback_url` or `docs_url` placeholder that looks like a Nabu Casa cloudhook URL is reduced to its host; every other placeholder passes through unchanged. |

## Example automation

Notify a phone when a command fails, without waiting for the entity to
settle:

```yaml
automation:
  - alias: Smartcar command failed
    trigger:
      - trigger: event
        event_type: smartcar_command_result
        event_data:
          success: false
    action:
      - action: notify.mobile_app_phone
        data:
          title: "{{ trigger.event.data.command }} failed"
          message: >-
            {{ trigger.event.data.suggested_user_message
               or trigger.event.data.error_code }}
          data:
            tag: "smartcar_cmd_{{ trigger.event.data.command }}"
```
