# Documentation

Reference material for working on this integration. User facing setup and
troubleshooting live in the repository [README](../README.md) and
[FAQ](../FAQ.md).

- [communication.md](communication.md): how this integration talks to Smartcar.
  The v3 chain end to end, the three credentials and which dashboard tab each
  lives in, the webhook model that actually feeds the signal store, the ways it
  all fails silently, and a dashboard checklist for fixing a live account.
- [vehicle-and-battery-status.md](vehicle-and-battery-status.md): the vehicle and
  battery signals field by field, with entity keys, what ships enabled by default,
  and what a 2025 US ID. Buzz can never populate.
- [polling.md](polling.md): webhooks, the polling profiles, and the 500 calls
  per vehicle per month allowance that shapes both.
- [follow-ups.md](follow-ups.md): gaps found while building features, with what
  each one blocks.
- [../script/smartcar_doctor.py](../script/smartcar_doctor.py): a standalone
  check of the credentials, the hosts and one vehicle's capabilities, written
  for the machine where the problem is rather than for a test environment.
- [api-reference.md](api-reference.md): the Smartcar API surface across v3 and
  the legacy v2.0, what each endpoint returns, which ones this integration
  calls, and the gaps between the two. Every claim is labelled with how it was
  verified.
