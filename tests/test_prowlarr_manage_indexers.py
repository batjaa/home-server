import os
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    REPOSITORY_ROOT
    / "roles"
    / "containers"
    / "media"
    / "prowlarr"
    / "files"
    / "manage-indexers.py"
)


def load_script():
    namespace = {"__name__": "prowlarr_manage_indexers_test"}
    with mock.patch.dict(os.environ, {"PROWLARR_API_KEY": "test-key"}):
        exec(compile(SCRIPT_PATH.read_text(encoding="utf-8"), str(SCRIPT_PATH), "exec"), namespace)
    return namespace


class ProwlarrManageIndexersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_script()

    def test_ensure_app_profile_creates_missing_profile(self):
        profile = {
            "name": "Interactive only",
            "enableRss": False,
            "enableAutomaticSearch": False,
            "enableInteractiveSearch": True,
            "minimumSeeders": 1,
        }
        request = mock.Mock(side_effect=[[], {**profile, "id": 7}])

        with mock.patch.dict(self.module, {"call": request}):
            profile_id, changed = self.module["ensure_app_profile"](profile)

        self.assertEqual(profile_id, 7)
        self.assertTrue(changed)
        request.assert_has_calls(
            [
                mock.call("GET", "/api/v1/appprofile"),
                mock.call("POST", "/api/v1/appprofile", profile),
            ]
        )

    def test_ensure_app_profile_reuses_matching_profile(self):
        profile = {
            "name": "Interactive only",
            "enableRss": False,
            "enableAutomaticSearch": False,
            "enableInteractiveSearch": True,
            "minimumSeeders": 1,
        }
        request = mock.Mock(return_value=[{**profile, "id": 7}])

        with mock.patch.dict(self.module, {"call": request}):
            profile_id, changed = self.module["ensure_app_profile"](profile)

        self.assertEqual(profile_id, 7)
        self.assertFalse(changed)
        request.assert_called_once_with("GET", "/api/v1/appprofile")

    def test_indexer_profile_change_requires_update(self):
        existing = {
            "enable": True,
            "appProfileId": 1,
            "fields": [
                {"name": "baseUrl", "value": "https://nzbfinder.ws"},
                {"name": "apiPath", "value": "/api"},
            ],
        }
        desired = {
            "enable": True,
            "appProfileId": 7,
            "fields": [
                {"name": "baseUrl", "value": "https://nzbfinder.ws"},
                {"name": "apiPath", "value": "/api"},
            ],
        }

        self.assertTrue(self.module["needs_update"](existing, desired))

    def test_newznab_payload_uses_selected_profile(self):
        payload = self.module["newznab_payload"](
            "NZBFinder",
            "https://nzbfinder.ws",
            "secret",
            app_profile_id=7,
        )

        self.assertEqual(payload["appProfileId"], 7)


if __name__ == "__main__":
    unittest.main()
