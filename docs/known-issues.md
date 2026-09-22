# Known issues and unverified claims

A single list, for whoever picks this repository up next, of what is known to
be wrong, what is believed but has not been observed, and what only the
repository owner can do. Each entry says where it came from. Items already
tracked as work in `docs/follow-ups.md` are referenced by number rather than
repeated. Last revised 2026-09-22 after release 2026.09.22.2.

## Needs the repository owner

1. **Release automation cannot open bump pull requests.** `prepare-release.yml`
   stops at its credential check because the Actions secret
   `RELEASE_AUTOMATION_PRIVATE_KEY` is not set and the release App is not
   installed on this repository. The variable `RELEASE_AUTOMATION_CLIENT_ID`
   is set. Until both exist, bump by hand as `docs/releasing.md` describes.
   The bump path has never run here, so its first run is itself unverified.
2. **Two of the seven webhook triggers configured in the Smartcar dashboard
   can never fire on the 2025 US ID. Buzz** (`connectivitystatus-isonline`,
   `connectivitystatus-isasleep`) and two more report `VEHICLE_NOT_CAPABLE`
   on every delivery (`vehicleuseraccount-permissions`, `-role`). Live
   deliveries on 2026-09-22 confirmed it. Replace them in the dashboard with
   `tractionbattery-stateofcharge`; the integration cannot edit webhooks
   because the Management API has no webhook create or update endpoint.

## Observed but not yet fixed

3. **A matched webhook that is not verified is invisible to the integration.**
   The repair issues added in PR #15 catch a wrong callback URL, a disabled
   webhook and empty trigger or data lists, but the Management API exposes no
   verification status, so a webhook with the right URL that never answered
   the VERIFY challenge looks healthy. The symptom is the same silence as an
   empty store. Only the dashboard shows it. Follow-up 10 and 11.
4. **The Smartcar dashboard counter read 0 of 500 calls with an empty request
   log** while the integration and the doctor script were making billed
   calls. Either the Free plan does not record requests or the counter lags.
   Not settled. The budget sensors are Home Assistant's own tally
   (follow-up 9) and must not be reconciled against that counter.
5. **`closure-doors` and `closure-windows` body shape.** Every live payload
   nests the array under `body.values`, while the OpenAPI example on the same
   path uses `body.doors` and `body.windows`. The parser accepts both since
   PR #15; which one the service emits for other makes is unverified.
6. **`resolution.type` is still ignored** for REST errors (follow-up 7), so a
   `REAUTHENTICATE` on a signal read does not start reauth. Webhook
   `VEHICLE_ERROR` payloads do trigger reauth on `PERMISSION` errors.
7. **v2 entries now request every known scope** on Connect because the
   permission form was removed in PR #19 and v2 has no dashboard
   equivalent. v2.0 is deprecated by Smartcar on 2026-12-01 along with the
   make-specific endpoints; the v2 path should be removed, not fixed.
8. **`powertrainType` is read from `/connections` and unused** (follow-up 6),
   so combustion entities are still gated only by scope and capability
   errors, not by drivetrain.

## Believed from documentation, not observed live

9. **Connect without a `scope` parameter** (PR #19) is taken from Smartcar's
   Connect documentation, which calls the parameter optional and says a
   supplied value overrides the dashboard Vehicle Access selection. No new
   Connect session has been run since the change.
10. **Capability-driven entity enabling** (PR #17) is tested against a fixture
    whose nine error signals are verbatim from a live debug log but whose
    sixteen successful bodies are representative shapes, not a capture. The
    enabled set on a live ID. Buzz after upgrading has not been observed.
11. **Android notification details** in the blueprints follow the Companion
    documentation: `clear_notification` had no worked YAML example on the
    page, the inline reply uses a custom action with `behavior: textInput`
    because no literal `REPLY` action type is documented, and the
    `mobile_app_notification_action` event shape comes from the docs rather
    than source (it lives in the Companion app, not in core). None has been
    exercised on a phone from these blueprints yet.
12. **Volkswagen US refresh cadence of 1 to 3 minutes** and the **23 of 95
    signals** figure for the 2025 US ID. Buzz come from Smartcar's frequency
    page and the dashboard compatibility export respectively, not from
    observation over time. The live store held 25 signals on 2026-09-22.

## Quality scale

13. `entity-translations`, `icon-translations`, `dynamic-devices` and
    `stale-devices` are open (follow-ups 4 and 5), so the manifest stays at
    `silver` even though every Platinum rule is met. Custom bus events added
    in PR #22 have no quality scale rule and are recorded in `docs/events.md`
    only.

## Workstation gotchas that cost time

14. On a Windows checkout `tests/components/smartcar/fixtures` is a symlink
    that materialises as a text file. Tests need it recreated in WSL, and a
    Windows worktree stages its deletion; restore the blob with
    `git update-index --cacheinfo 120000,<blob>,tests/components/smartcar/fixtures`
    before committing.
15. Several agents sharing one clone collided on branch switches. Use a git
    worktree per task. `pre-commit` cannot run inside a worktree from WSL
    because the `.git` file points at a Windows path; run ruff, mypy and
    codespell from the pre-commit cache environments instead and let CI run
    the hook.
16. The repository's pinned ruff (0.11.12) rejects the newer `# ruff: ignore`
    suppression form; use `# noqa:`.
