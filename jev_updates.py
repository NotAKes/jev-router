"""Manual GitHub release checks for the GUI; no work is performed on import.

Call check_for_updates() in a worker thread only after the user requests it.
The caller may offer to open result.release_url after explicit confirmation.
This module neither opens a browser nor downloads, installs, or runs anything.
"""

from dataclasses import dataclass
from http.client import HTTPException
import json
import platform
import re
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


# Change this single value when preparing a new GUI release.
APP_VERSION = "v0.2.0"
API_URL = "https://api.github.com/repos/uprosti-tech/uprosti-vibe/releases/latest"
RELEASE_BASE = "https://github.com/uprosti-tech/uprosti-vibe/releases"
TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 1024 * 1024

# Exact, case-sensitive names expected in the published release.
ASSET_NAMES = {
    ("Darwin", "arm64"): "jev-router-gui-macos-arm64.zip",
    ("Darwin", "x86_64"): "jev-router-gui-macos-x86_64.zip",
    ("Windows", "x86_64"): "jev-router-gui-windows-x86_64.zip",
}
_VERSION = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z", re.ASCII)


@dataclass(frozen=True)
class UpdateResult:
    # error, up_to_date, update_available, or update_no_package
    status: str
    message: str
    current_version: str = APP_VERSION
    latest_version: str | None = None
    release_url: str | None = None
    asset_name: str | None = None


def parse_version(tag):
    """Parse a canonical stable vX.Y.Z tag or raise ValueError."""
    if not isinstance(tag, str) or len(tag) > 128:
        raise ValueError("invalid version tag")
    match = _VERSION.fullmatch(tag)
    if not match:
        raise ValueError("invalid version tag")
    return tuple(int(part) for part in match.groups())


def asset_name_for_platform(system=None, machine=None):
    """Return the expected GUI ZIP name, or None for unsupported platforms."""
    system = platform.system() if system is None else system
    machine = platform.machine() if machine is None else machine
    if not isinstance(machine, str):
        return None
    machine = machine.lower()
    machine = {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    return ASSET_NAMES.get((system, machine))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never send a request to a URL supplied by a redirect response.
        return None


def _read_latest():
    # The URL is a constant, not a caller- or response-provided URL. Redirects
    # are disabled, including HTTPS-to-HTTP and cross-host redirects.
    request = Request(API_URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "Jev-Router-GUI-Update-Check",
    })
    opener = build_opener(_NoRedirect())
    with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        data = response.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError("response too large")
    return json.loads(data)


def _evaluate_release(data, current_version, system, machine):
    current = parse_version(current_version)
    if not isinstance(data, dict):
        raise ValueError("invalid release")
    # Missing flags are not evidence that this is a published stable release.
    if data.get("draft") is not False or data.get("prerelease") is not False:
        raise ValueError("release is not published and stable")
    tag = data.get("tag_name")
    latest = parse_version(tag)
    # Exact comparison also rejects credentials, alternate ports, encoded path
    # components, query strings, fragments, and lookalike hosts/repositories.
    release_url = f"{RELEASE_BASE}/tag/{tag}"
    if data.get("html_url") != release_url:
        raise ValueError("unsafe release URL")
    if latest <= current:
        return UpdateResult("up_to_date", "Обновлений нет", current_version, tag)

    assets = data.get("assets")
    if not isinstance(assets, list) or any(not isinstance(asset, dict) for asset in assets):
        raise ValueError("invalid release assets")
    expected_name = asset_name_for_platform(system, machine)
    matches = [asset for asset in assets if asset.get("name") == expected_name] if expected_name else []
    if len(matches) > 1:
        raise ValueError("ambiguous release assets")
    if matches and matches[0].get("state") == "uploaded":
        asset_url = f"{RELEASE_BASE}/download/{tag}/{expected_name}"
        if matches[0].get("browser_download_url") != asset_url:
            raise ValueError("unsafe asset URL")
        return UpdateResult("update_available", "Доступно обновление", current_version,
                            tag, release_url, expected_name)
    return UpdateResult("update_no_package", "Обновление есть, но подходящего GUI-пакета нет",
                        current_version, tag, release_url)


def check_for_updates(*, system=None, machine=None):
    """Perform one bounded network check; call from a GUI worker thread.

    The optional platform arguments are for testing. On an error return a
    distinct error result; never treat a failed check as no updates. A newer
    release without a matching uploaded ZIP has status update_no_package.
    release_url is present only for a newer release and is safe to offer to
    the user for opening after confirmation; the asset URL is never opened.
    """
    try:
        return _evaluate_release(_read_latest(), APP_VERSION, system, machine)
    except (HTTPError, URLError, HTTPException, OSError, ValueError, UnicodeError, RecursionError):
        return UpdateResult("error", "Не удалось проверить обновления")
