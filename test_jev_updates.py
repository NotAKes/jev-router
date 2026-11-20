"""Offline tests for manual stable GUI release checks."""
import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import jev_updates as updates


def release(tag="v0.3.0", assets=None):
    return {
        "tag_name": tag,
        "html_url": f"{updates.RELEASE_BASE}/tag/{tag}",
        "draft": False,
        "prerelease": False,
        "assets": [] if assets is None else assets,
    }


def asset(name, tag="v0.3.0"):
    return {
        "name": name,
        "state": "uploaded",
        "browser_download_url": f"{updates.RELEASE_BASE}/download/{tag}/{name}",
    }


class UpdateTests(unittest.TestCase):
    def check(self, data, system="Darwin", machine="arm64"):
        with patch.object(updates, "_read_latest", return_value=data):
            return updates.check_for_updates(system=system, machine=machine)

    def test_version_comparison_and_no_downgrade(self):
        for tag, expected in [("v0.2.0", "up_to_date"), ("v0.1.99", "up_to_date"),
                              ("v0.10.0", "update_no_package"), ("v1.0.0", "update_no_package")]:
            with self.subTest(tag=tag):
                result = self.check(release(tag))
                self.assertEqual(result.status, expected)
                if expected == "up_to_date":
                    self.assertIsNone(result.release_url)

    def test_strict_stable_tags(self):
        for tag in ["0.3.0", "v0.3", "v0.3.0-rc1", "v0.3.0+build", "v00.3.0",
                    "v0.03.0", "v0.3.0\n", "V0.3.0", "v-1.0.0", "v٠.3.0", None]:
            with self.subTest(tag=tag):
                self.assertEqual(self.check(release(tag)).status, "error")

    def test_draft_prerelease_and_missing_flags_are_errors(self):
        for field in ("draft", "prerelease"):
            for value in (True, None, 0, "false"):
                data = release()
                data[field] = value
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.check(data).status, "error")
            data = release()
            del data[field]
            self.assertEqual(self.check(data).status, "error")

    def test_expected_platform_assets(self):
        cases = [
            ("Darwin", "arm64", "jev-router-gui-macos-arm64.zip"),
            ("Darwin", "aarch64", "jev-router-gui-macos-arm64.zip"),
            ("Darwin", "x86_64", "jev-router-gui-macos-x86_64.zip"),
            ("Windows", "AMD64", "jev-router-gui-windows-x86_64.zip"),
        ]
        for system, machine, name in cases:
            with self.subTest(system=system, machine=machine):
                result = self.check(release(assets=[asset(name)]), system, machine)
                self.assertEqual(result.status, "update_available")
                self.assertEqual(result.asset_name, name)
                self.assertEqual(result.release_url, f"{updates.RELEASE_BASE}/tag/v0.3.0")

    def test_unavailable_unsupported_or_unuploaded(self):
        name = updates.asset_name_for_platform("Darwin", "arm64")
        other = asset("jev-router-gui-windows-x86_64.zip")
        for system, machine, assets in [("Darwin", "arm64", [other]),
                                        ("Linux", "x86_64", [asset(name)]),
                                        ("Windows", "arm64", [asset(name)]),
                                        ("Darwin", "arm64", [{**asset(name), "state": "new"}])]:
            with self.subTest(system=system, machine=machine, assets=assets):
                result = self.check(release(assets=assets), system, machine)
                self.assertEqual(result.status, "update_no_package")
                self.assertIsNone(result.asset_name)
                self.assertEqual(result.release_url, f"{updates.RELEASE_BASE}/tag/v0.3.0")

    def test_unsafe_release_urls(self):
        urls = [
            "http://github.com/uprosti-tech/uprosti-vibe/releases/tag/v0.3.0",
            "https://github.com.evil.example/uprosti-tech/uprosti-vibe/releases/tag/v0.3.0",
            "https://github.com@evil.example/uprosti-tech/uprosti-vibe/releases/tag/v0.3.0",
            "https://github.com:443/uprosti-tech/uprosti-vibe/releases/tag/v0.3.0",
            "https://github.com/other/uprosti-vibe/releases/tag/v0.3.0",
            f"{updates.RELEASE_BASE}/tag/v0.3.0?next=https://evil.example",
            f"{updates.RELEASE_BASE}/tag/v0.3.0#fragment",
            f"{updates.RELEASE_BASE}/tag/%76%30.3.0",
        ]
        for url in urls:
            with self.subTest(url=url):
                data = release()
                data["html_url"] = url
                self.assertEqual(self.check(data).status, "error")

    def test_unsafe_asset_url_and_duplicate_asset(self):
        name = updates.asset_name_for_platform("Darwin", "arm64")
        for url in ["https://evil.example/file.zip", f"{updates.RELEASE_BASE}/download/v0.2.0/{name}",
                    f"{updates.RELEASE_BASE}/download/v0.3.0/{name}?x=1", None]:
            with self.subTest(url=url):
                bad = asset(name)
                bad["browser_download_url"] = url
                self.assertEqual(self.check(release(assets=[bad])).status, "error")
        self.assertEqual(self.check(release(assets=[asset(name), asset(name)])).status, "error")

    def test_network_errors_and_invalid_data_are_not_up_to_date(self):
        errors = [URLError("offline"), TimeoutError(), OSError("network"),
                  HTTPError(updates.API_URL, 404, "not found", {}, None),
                  ValueError("invalid JSON")]
        for error in errors:
            with self.subTest(error=error), patch.object(updates, "_read_latest", side_effect=error):
                result = updates.check_for_updates()
                self.assertEqual(result.status, "error")
                self.assertIn("Не удалось проверить", result.message)
        for data in [None, [], {}, {**release(), "assets": None}, {**release(), "assets": [None]}]:
            with self.subTest(data=data):
                self.assertEqual(self.check(data).status, "error")

    def test_request_is_fixed_https_bounded_and_redirects_are_disabled(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(release()).encode()
        opener = MagicMock()
        opener.open.return_value = response
        with patch.object(updates, "build_opener", return_value=opener) as build:
            self.assertEqual(updates._read_latest(), release())
        handler = build.call_args.args[0]
        self.assertIsInstance(handler, updates._NoRedirect)
        self.assertIsNone(handler.redirect_request(None, None, 302, None, None, "http://evil.example"))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.github.com/repos/uprosti-tech/uprosti-vibe/releases/latest")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], updates.TIMEOUT_SECONDS)
        response.read.assert_called_once_with(updates.MAX_RESPONSE_BYTES + 1)

    def test_invalid_json_and_oversized_response(self):
        response = MagicMock()
        response.__enter__.return_value = response
        opener = MagicMock()
        opener.open.return_value = response
        for body in [b"{", b"x" * (updates.MAX_RESPONSE_BYTES + 1), b"\xff"]:
            with self.subTest(body_length=len(body)), patch.object(updates, "build_opener", return_value=opener):
                response.read.return_value = body
                self.assertEqual(updates.check_for_updates().status, "error")


if __name__ == "__main__":
    unittest.main()
