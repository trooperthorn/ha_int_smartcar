# Security Policy

## Reporting a vulnerability

Do not open a public issue containing exploit details, credentials, private
addresses, or logs. Use GitHub's private vulnerability-reporting feature for
this repository. If private reporting is unavailable, open a minimal issue
asking the maintainer to establish a private channel; omit technical details.

Include the affected version or commit, prerequisites, impact, a minimal
reproduction, and suggested remediation. Remove tokens, API keys, cookies,
Smartcar client credentials, vehicle identification numbers, location
history, and private network details before sending anything.

## Response targets

These are project targets, not an SLA: acknowledge critical and high reports
in three business days, establish severity and containment in seven, and
publish a coordinated fix or advisory as soon as it is safely validated.
Lower-severity issues are prioritized by exploitability and impact.

## Supported version

Only the latest published release and the default branch receive security
fixes. Operators should keep Home Assistant and this integration current and
retain a tested backup.

## Security boundaries

This is a Home Assistant custom integration. It holds Smartcar OAuth client
credentials and vehicle access tokens in the Home Assistant configuration
entry store, and it exposes a webhook endpoint that Smartcar posts to. It
runs in the same Python process as Home Assistant and every other
integration, so it cannot protect its stored tokens from another integration
running in that process, and it cannot make the Home Assistant instance
itself safe to expose to the internet. The integration verifies the
Smartcar webhook signature, but the operator owns the transport: the webhook
URL must be reachable only over TLS.

Releases are built by the Release workflow from a protected `main` and carry
an SPDX SBOM, SHA-256 checksums, and build provenance attestations. HACS
does not check those attestations; they let an operator verify a download by
hand. The verification commands are in `docs/releasing.md`.
