#!/usr/bin/env python3
"""Check a Smartcar application from outside Home Assistant, and write a report.

Two jobs. The first is answering "is my configuration actually working", which
the integration can only tell you indirectly: a wrong credential shows up as a
config flow that aborts, and a wrong host shows up as data that never arrives.
This talks to Smartcar directly and says which step failed and what the service
answered. The second is collecting a description of what one vehicle can
actually do, because Smartcar's compatibility matrix is a statement about a
model and the signals response is a statement about *this* car.

It deliberately uses nothing but the standard library. The point is to be
runnable on the machine where the problem is, including a Windows box with a
bare Python and no virtualenv, without installing anything first.

Cost matters here. Smartcar bills 500 calls per vehicle per month on the free
tier and commands come out of the same allowance, so a diagnostic that quietly
spends fifty of them is its own problem. Every check below is labelled with
what it costs, the billed ones are counted before they run, and nothing billed
happens without a yes.

Usage:

    python script/smartcar_doctor.py --client-id client_... --client-secret ...

or set SMARTCAR_CLIENT_ID and SMARTCAR_CLIENT_SECRET and pass neither. Add
`--free-only` to skip everything that comes out of a vehicle's allowance, or
`--yes` to answer the confirmation in advance.

The report is written to `~/workspace/` and is redacted: identifiers are
replaced with stable short hashes and coordinates are dropped. The hashes are
consistent within a report, so the same vehicle reads the same everywhere. A
separate `-idmap.json` maps them back and is the one file not to share.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import socket
import ssl
import sys
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URL = "https://iam.smartcar.com/oauth2/token"  # noqa: S105
TOKEN_URL_LEGACY = "https://auth.smartcar.com/oauth/token"  # noqa: S105
VEHICLE_API = "https://vehicle.api.smartcar.com/v3"
VEHICLE_API_LEGACY = "https://api.smartcar.com/v2.0"

# the Management API host is the open question this tool exists partly to
# settle. `management.yaml` declares the v3 host with the same bearer token;
# the prose reference still describes the v2.0 host with a separate management
# token. Both are probed and the report says which one answered.
MANAGEMENT_HOSTS = {
    "v3 (spec)": "https://management.api.smartcar.com/v3",
    "v2.0 (prose)": "https://api.smartcar.com/management/v2.0",
}

CONNECTIONS_PAGE_SIZE = 100
CONNECTIONS_PAGE_LIMIT = 50
TIMEOUT = 30

# what each check costs against a vehicle's monthly allowance.
FREE = 0
ONE_CALL_PER_VEHICLE = 1


class Redactor:
    """Replace identifiers with stable short hashes.

    Stable so that the same vehicle id reads the same in the connections list
    and in its own signals section, which is the whole point: a report where
    every occurrence is a different opaque string cannot be reasoned about.
    """

    def __init__(self, *, enabled: bool = True) -> None:
        """Initialize the redactor."""
        self.enabled = enabled
        self.map: dict[str, str] = {}

    def __call__(self, kind: str, value: Any) -> Any:
        """Redact one identifier.

        Returns:
            The placeholder, or the value unchanged when redaction is off.
        """
        if not self.enabled or value in {None, ""}:
            return value

        text = str(value)
        digest = hashlib.sha256(text.encode()).hexdigest()[:8]
        placeholder = f"{kind}_{digest}"
        self.map[placeholder] = text

        return placeholder


class Result:
    """One check and what it found."""

    def __init__(self, name: str, cost: int = FREE) -> None:
        """Initialize the result."""
        self.name = name
        self.cost = cost
        self.ok: bool | None = None
        self.detail = ""
        self.data: Any = None

    def passed(self, detail: str, data: Any = None) -> Result:
        """Record success.

        Returns:
            This result, so a check can end in one expression.
        """
        self.ok = True
        self.detail = detail
        self.data = data

        return self

    def failed(self, detail: str, data: Any = None) -> Result:
        """Record failure.

        Returns:
            This result.
        """
        self.ok = False
        self.detail = detail
        self.data = data

        return self

    @property
    def mark(self) -> str:
        """A one character status for the console and the report.

        Returns:
            The marker.
        """
        if self.ok is None:
            return "-"

        return "ok" if self.ok else "FAIL"


def http(
    method: str,
    url: str,
    *,
    token: str | None = None,
    json_body: dict | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], str]:
    """Make one HTTP request with no third party client.

    An error status is a result, not an exception: every caller wants to report
    what the service said rather than stop.

    Returns:
        The status, the response headers, and the body as text. Status 0 means
        the request never completed, and the body carries the reason.
    """
    data = None
    request_headers = dict(headers or {})

    if json_body is not None:
        data = json.dumps(json_body).encode()
        request_headers["content-type"] = "application/json"

    if token:
        request_headers["authorization"] = f"Bearer {token}"

    request = urllib.request.Request(  # noqa: S310
        url, data=data, headers=request_headers, method=method.upper()
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            return (
                response.status,
                dict(response.headers),
                response.read().decode("utf-8", "replace"),
            )
    except urllib.error.HTTPError as err:
        return (
            err.code,
            dict(err.headers or {}),
            err.read().decode("utf-8", "replace"),
        )
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        return 0, {}, f"{type(err).__name__}: {err}"


def parse_json(body: str) -> Any:
    """Decode a body that is supposed to be JSON.

    Returns:
        The decoded value, or None when it is not JSON.
    """
    try:
        return json.loads(body)
    except ValueError:
        return None


def check_reachable(url: str) -> Result:
    """Open a TLS connection to a host without sending anything.

    Separating this from the first real request is what makes a proxy, a DNS
    failure or a TLS interception readable as itself rather than as an
    authentication problem.

    Returns:
        The result.
    """
    host = urllib.parse.urlparse(url).hostname or url
    result = Result(f"Reach {host}")

    try:
        with socket.create_connection((host, 443), timeout=TIMEOUT) as raw:
            context = ssl.create_default_context()

            with context.wrap_socket(raw, server_hostname=host) as tls:
                cipher = tls.cipher()
                version = cipher[1] if cipher else "unknown"
    except (OSError, ssl.SSLError) as err:
        return result.failed(f"{type(err).__name__}: {err}")

    return result.passed(f"TLS {version}")


def check_credential_shape(client_id: str, client_secret: str) -> Result:
    """Say which API version a credential is for, before using it.

    This is the check that catches the most common migration failure. Home
    Assistant's Application Credentials dialog wants the Smartcar **API
    credential**, whose client id begins with `client_`, while the config flow
    separately asks for the **Application ID**, which does not. Swapping the
    two produces a credential that looks valid, authenticates against nothing,
    and silently leaves the entry on the legacy v2 API.

    Returns:
        The result.
    """
    result = Result("Credential shape")

    if not client_id or not client_secret:
        return result.failed("client id or secret is empty")

    if client_id.startswith("client_"):
        return result.passed(
            f"v3 API credential (client id begins `client_`, {len(client_id)} chars)",
            {"version": "v3"},
        )

    looks_like_uuid = len(client_id) == 36 and client_id.count("-") == 4

    return result.failed(
        "not a v3 API credential: the client id does not begin with `client_`. "
        + (
            "It looks like an Application ID (a UUID). Home Assistant's "
            "Application Credentials dialog wants the API credential from "
            "Smartcar's dashboard, not the Application ID; the Application ID "
            "goes in the integration's own setup step. Swapping them leaves "
            "the entry on the legacy v2 API."
            if looks_like_uuid
            else "Smartcar treats it as a legacy v2 client, so v3 endpoints "
            "will not be used."
        ),
        {"version": "v2"},
    )


def check_token(client_id: str, client_secret: str) -> tuple[Result, str | None]:
    """Get an access token the way the integration does.

    Returns:
        The result, and the access token when one was issued.
    """
    result = Result("v3 token (client_credentials)")

    status, _, body = http(
        "post",
        TOKEN_URL,
        json_body={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
    )
    parsed = parse_json(body)

    if status == 0:
        return result.failed(f"request did not complete. {body}"), None

    if status != 200:
        detail = body[:300]

        if isinstance(parsed, dict) and parsed.get("error"):
            detail = str(parsed["error"])

            if description := parsed.get("error_description"):
                detail = f"{detail}: {description}"

        return result.failed(f"HTTP {status}. {detail}"), None

    if not isinstance(parsed, dict):
        return result.failed(f"HTTP 200 but the body is not JSON: {body[:200]}"), None

    token = parsed.get("access_token")
    kind = str(parsed.get("token_type", "")).lower()

    if not token or kind != "bearer":
        return result.failed(
            f"HTTP 200 but no usable bearer token (token_type={kind or 'missing'})"
        ), None

    summary = f"issued, expires_in={parsed.get('expires_in', 'unstated')}"

    if scope := parsed.get("scope"):
        summary = f"{summary}, scope={scope}"

    return result.passed(summary, {"expires_in": parsed.get("expires_in")}), str(token)


def check_connections(token: str, redact: Redactor) -> tuple[Result, list[dict]]:
    """Read every page of /connections.

    Application level, so it does not come out of any vehicle's allowance. This
    is also where a multi-user application shows up, which the integration
    refuses to set up.

    Returns:
        The result, and the connection resources.
    """
    result = Result("List connections", FREE)
    connections: list[dict] = []
    page = 1

    while page <= CONNECTIONS_PAGE_LIMIT:
        query = urllib.parse.urlencode(
            {"page[number]": page, "page[size]": CONNECTIONS_PAGE_SIZE}
        )
        status, _, body = http("get", f"{VEHICLE_API}/connections?{query}", token=token)

        if status != 200:
            return result.failed(f"HTTP {status} on page {page}. {body[:300]}"), []

        parsed = parse_json(body)

        if not isinstance(parsed, dict):
            return result.failed(f"page {page} is not JSON: {body[:200]}"), []

        connections.extend(parsed.get("data", []))
        total = parsed.get("meta", {}).get("totalCount")

        if not parsed.get("data") or total is None or len(connections) >= total:
            break

        page += 1

    users = {
        user_id
        for connection in connections
        if (user_id := dig(connection, "relationships", "user", "data", "id"))
    }
    summary = (
        f"{len(connections)} connection(s) across {page} page(s), {len(users)} user(s)"
    )

    if not connections:
        return result.failed(f"{summary}. The integration aborts with no_vehicles."), []

    if len(users) != 1:
        return result.failed(
            f"{summary}. The integration only supports a single user application "
            "and aborts with not_single_user_app."
        ), connections

    for connection in connections:
        redact("veh", dig(connection, "relationships", "vehicle", "data", "id"))
        redact("user", dig(connection, "relationships", "user", "data", "id"))

    return result.passed(summary), connections


def check_management(token: str) -> list[Result]:
    """Probe both Management API hosts, read only.

    Neither has been exercised from here. Whichever answers 2xx to a webhook
    list is the live one, and a 401 against one but not the other says the
    token model differs rather than the host being wrong.

    Returns:
        One result per host.
    """
    results = []

    for label, base in MANAGEMENT_HOSTS.items():
        result = Result(f"Management API {label}", FREE)
        status, _, body = http("get", f"{base}/webhooks", token=token)

        if status == 0:
            results.append(result.failed(f"request did not complete. {body}"))
            continue

        if 200 <= status < 300:
            parsed = parse_json(body)
            count = len(parsed.get("data", [])) if isinstance(parsed, dict) else "?"
            results.append(result.passed(f"HTTP {status}, {count} webhook(s) listed"))
            continue

        results.append(result.failed(f"HTTP {status}. {body[:200]}"))

    return results


def check_signals(
    token: str, user_id: str, vehicle_id: str, redact: Redactor
) -> tuple[Result, dict | None]:
    """Read one vehicle's full signal set. Costs one call.

    One request returns every signal the vehicle can answer and an error for
    each one it cannot, which is both the capability picture and a live data
    sample for the price of a single call.

    Returns:
        The result, and the parsed body.
    """
    label = redact("veh", vehicle_id)
    result = Result(f"Signals for {label}", ONE_CALL_PER_VEHICLE)
    status, _, body = http(
        "get",
        f"{VEHICLE_API}/vehicles/{vehicle_id}/signals",
        token=token,
        headers={"sc-user-id": user_id},
    )

    if status == 430:
        return result.failed(
            f"HTTP 430. This vehicle's monthly allowance is spent. {body[:200]}"
        ), None

    if status != 200:
        return result.failed(f"HTTP {status}. {body[:300]}"), None

    parsed = parse_json(body)

    if not isinstance(parsed, dict):
        return result.failed(f"HTTP 200 but the body is not JSON: {body[:200]}"), None

    signals = parsed.get("data", [])
    counts = summarise_signals(signals)
    summary = (
        f"{len(signals)} signal(s): {counts['ok']} with a value, "
        f"{counts['COMPATIBILITY']} unsupported by this vehicle, "
        f"{counts['VEHICLE_STATE']} unavailable right now, "
        f"{counts['PERMISSION']} not permitted, {counts['other']} other error"
    )

    return result.passed(summary, counts), parsed


def summarise_signals(signals: list[dict]) -> dict[str, int]:
    """Count signals by outcome.

    The three error types mean different things and the integration treats them
    differently: only COMPATIBILITY means the vehicle structurally cannot
    answer. VEHICLE_STATE is "not right now" and PERMISSION is a missing scope.

    Returns:
        The counts, keyed by outcome.
    """
    counts = {
        "ok": 0,
        "COMPATIBILITY": 0,
        "VEHICLE_STATE": 0,
        "PERMISSION": 0,
        "other": 0,
    }

    for signal in signals:
        attributes = signal.get("attributes", {})
        status = attributes.get("status", {})

        if status.get("value") != "ERROR":
            counts["ok"] += 1
            continue

        kind = str(status.get("error", {}).get("type", ""))
        counts[kind if kind in counts else "other"] += 1

    return counts


def dig(source: Any, *keys: str) -> Any:
    """Walk nested dictionaries without raising.

    Returns:
        The value, or None if any step is missing.
    """
    for key in keys:
        if not isinstance(source, dict):
            return None

        source = source.get(key)

    return source


def clean_signal(signal: dict, redact: Redactor) -> dict:
    """Strip a signal down to what a report needs, with location removed.

    Returns:
        The trimmed signal.
    """
    attributes = signal.get("attributes", {})
    status = attributes.get("status", {})
    code = attributes.get("code")
    value = attributes.get("value")

    if redact.enabled and code and "location" in str(code).lower():
        value = "<redacted>" if value is not None else None

    if isinstance(value, dict) and redact.enabled:
        value = {
            key: ("<redacted>" if key in {"latitude", "longitude"} else item)
            for key, item in value.items()
        }

    trimmed: dict[str, Any] = {"code": code, "name": attributes.get("name")}

    if status.get("value") == "ERROR":
        error = status.get("error", {})
        trimmed["error"] = {
            "type": error.get("type"),
            "code": error.get("code"),
            "description": error.get("description"),
            "resolution": dig(error, "resolution", "type"),
        }
    else:
        trimmed["value"] = value
        trimmed["unit"] = attributes.get("unit")
        trimmed["age"] = dig(attributes, "meta", "dataAge") or dig(
            signal, "meta", "dataAge"
        )

    return trimmed


def render(results: list[Result], report: dict, redact: Redactor) -> str:
    """Write the human readable report.

    Returns:
        The markdown.
    """
    lines = [
        "# Smartcar connectivity report",
        "",
        f"Generated {report['generated']} by `script/smartcar_doctor.py`.",
        "",
        "Identifiers are replaced with stable short hashes and coordinates are"
        if redact.enabled
        else "Redaction was disabled with `--no-redact`; this report contains",
        "dropped. The same hash always means the same thing within this report."
        if redact.enabled
        else "real identifiers. Treat it as sensitive.",
        "",
        f"Calls billed to a vehicle's allowance by this run: **{report['billed']}**.",
        "",
        "## Checks",
        "",
        "| Check | Result | Detail |",
        "| --- | --- | --- |",
    ]

    for result in results:
        detail = result.detail.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {result.name} | {result.mark} | {detail} |")

    for vehicle in report.get("vehicles", []):
        lines += [
            "",
            f"## Vehicle {vehicle['id']}",
            "",
            f"{vehicle['summary']}",
            "",
        ]

        if not vehicle.get("signals"):
            continue

        lines += [
            "| Signal | Value | Unit | Error |",
            "| --- | --- | --- | --- |",
        ]

        for signal in vehicle["signals"]:
            error = signal.get("error") or {}
            error_text = (
                f"{error.get('type')} {error.get('code') or ''}".strip()
                if error
                else ""
            )
            lines.append(
                f"| `{signal['code']}` | {signal.get('value', '')} | "
                f"{signal.get('unit') or ''} | {error_text} |"
            )

    lines += [
        "",
        "## What to do with this",
        "",
        "Send the `.md` and the `-raw.json`. Keep the `-idmap.json`: it is the",
        "only file that maps the hashes back to real vehicle and user ids.",
        "",
    ]

    return "\n".join(lines)


def main() -> int:
    """Run the checks and write the report.

    Returns:
        The process exit status: 0 when every check passed.
    """
    parser = argparse.ArgumentParser(
        description="Check a Smartcar application and collect a redacted report."
    )
    parser.add_argument("--client-id", default=os.environ.get("SMARTCAR_CLIENT_ID", ""))
    parser.add_argument(
        "--client-secret", default=os.environ.get("SMARTCAR_CLIENT_SECRET", "")
    )
    parser.add_argument(
        "--free-only",
        action="store_true",
        help="skip every check that costs a call against a vehicle's allowance",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="do not ask before spending calls",
    )
    parser.add_argument(
        "--no-redact",
        action="store_true",
        help="keep real identifiers and coordinates in the report",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path.home() / "workspace",
        help="directory for the report (default ~/workspace)",
    )
    args = parser.parse_args()

    if not args.client_id or not args.client_secret:
        print(
            "Need a client id and secret. Pass --client-id and --client-secret, "
            "or set SMARTCAR_CLIENT_ID and SMARTCAR_CLIENT_SECRET.",
            file=sys.stderr,
        )
        return 2

    redact = Redactor(enabled=not args.no_redact)
    results: list[Result] = []
    report: dict[str, Any] = {
        "generated": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ"),
        "billed": 0,
        "vehicles": [],
    }

    def record(result: Result) -> Result:
        results.append(result)
        report["billed"] += result.cost if result.ok else 0
        print(f"  [{result.mark:>4}] {result.name}: {result.detail}")

        return result

    print("Smartcar doctor\n")
    print("Connectivity (no credentials sent):")

    for url in (TOKEN_URL, VEHICLE_API, *MANAGEMENT_HOSTS.values(), TOKEN_URL_LEGACY):
        record(check_reachable(url))

    print("\nCredentials:")
    shape = record(check_credential_shape(args.client_id, args.client_secret))

    if not shape.ok:
        write_report(args, results, report, redact)
        return 1

    token_result, token = check_token(args.client_id, args.client_secret)
    record(token_result)

    if not token:
        write_report(args, results, report, redact)
        return 1

    print("\nApplication (free, not billed to a vehicle):")
    connections_result, connections = check_connections(token, redact)
    record(connections_result)

    for result in check_management(token):
        record(result)

    vehicles = [
        (
            dig(connection, "relationships", "user", "data", "id"),
            dig(connection, "relationships", "vehicle", "data", "id"),
        )
        for connection in connections
    ]
    vehicles = [(user, vehicle) for user, vehicle in vehicles if user and vehicle]

    if vehicles and not args.free_only:
        cost = len(vehicles)
        print(
            f"\nReading signals costs {cost} call(s), one per vehicle, out of "
            "500 per vehicle per month shared with commands."
        )

        if args.yes or input("Continue? [y/N] ").strip().lower() == "y":
            print()

            for user_id, vehicle_id in vehicles:
                result, body = check_signals(token, user_id, vehicle_id, redact)
                record(result)
                signals = [
                    clean_signal(signal, redact)
                    for signal in (body or {}).get("data", [])
                ]
                report["vehicles"].append(
                    {
                        "id": redact("veh", vehicle_id),
                        "summary": result.detail,
                        "signals": signals,
                    }
                )
        else:
            print("Skipped.")

    path = write_report(args, results, report, redact)
    failures = [result for result in results if result.ok is False]

    print(f"\nReport: {path}")
    print(f"{len(results) - len(failures)} of {len(results)} checks passed.")

    return 1 if failures else 0


def write_report(
    args: argparse.Namespace, results: list[Result], report: dict, redact: Redactor
) -> Path:
    """Write the markdown, the raw JSON and the id map.

    Returns:
        The path of the markdown report.
    """
    out: Path = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    base = out / f"smartcar-doctor-{stamp}"

    report["checks"] = [
        {"name": result.name, "ok": result.ok, "detail": result.detail}
        for result in results
    ]

    markdown = base.with_suffix(".md")
    markdown.write_text(render(results, report, redact), encoding="utf-8")
    base.with_name(f"{base.name}-raw.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )

    if redact.map:
        base.with_name(f"{base.name}-idmap.json").write_text(
            json.dumps(
                {"do_not_share": "maps report hashes to real ids", **redact.map},
                indent=2,
            ),
            encoding="utf-8",
        )

    return markdown


if __name__ == "__main__":
    sys.exit(main())
