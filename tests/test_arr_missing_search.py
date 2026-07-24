import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = (
    REPOSITORY_ROOT
    / "roles"
    / "containers"
    / "media"
    / "arr-search"
    / "templates"
    / "arr-missing-search.py.j2"
)


def load_template():
    source = TEMPLATE_PATH.read_text(encoding="utf-8")
    source = source.replace("{{ arr_search_apps | to_json }}", "[]")
    source = source.replace("{{ arr_search_state_file }}", "/tmp/arr-search-test-state.json")
    namespace = {"__name__": "arr_missing_search_test"}
    exec(compile(source, str(TEMPLATE_PATH), "exec"), namespace)
    return namespace


class ArrMissingSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_template()

    def test_movie_file_id_marks_movie_as_imported_when_has_file_is_omitted(self):
        app = {"url": "http://whisparr"}
        with mock.patch.dict(
            self.module,
            {"request_json": mock.Mock(return_value={"id": 42, "movieFileId": 99})},
        ):
            self.assertTrue(self.module["whisparr_movie_has_file"](app, "key", 42))

    def test_explicit_has_file_value_takes_precedence(self):
        app = {"url": "http://whisparr"}
        with mock.patch.dict(
            self.module,
            {"request_json": mock.Mock(return_value={"hasFile": False, "movieFileId": 99})},
        ):
            self.assertFalse(self.module["whisparr_movie_has_file"](app, "key", 42))

    def test_directory_creation_repairs_every_component_below_storage_root(self):
        with tempfile.TemporaryDirectory() as directory:
            storage_root = Path(directory).resolve()
            existing_parent = storage_root / "Media"
            existing_parent.mkdir()
            target = existing_parent / "Whisparr" / "scenes" / "Performer" / "Movie"
            app = {
                "storage_container_path": "/storage",
                "storage_host_path": str(storage_root),
                "storage_host_uid": 1234,
                "storage_host_gid": 100,
            }

            with mock.patch.object(self.module["os"], "chown") as chown:
                self.module["ensure_host_dir_for_container_path"](
                    app,
                    "/storage/Media/Whisparr/scenes/Performer/Movie",
                )

            self.assertTrue(target.is_dir())
            expected = [
                mock.call(path, 1234, 100)
                for path in (
                    existing_parent,
                    existing_parent / "Whisparr",
                    existing_parent / "Whisparr" / "scenes",
                    existing_parent / "Whisparr" / "scenes" / "Performer",
                    target,
                )
            ]
            self.assertEqual(chown.call_args_list, expected)
            for path in expected[1:]:
                self.assertEqual(os.stat(path.args[0]).st_mode & 0o777, 0o775)

    def test_sab_history_cleanup_preserves_downloaded_files(self):
        app = {
            "sabnzbd_url": "http://sabnzbd:8080",
            "cleanup_sabnzbd_history": True,
        }
        request = mock.Mock(return_value={"status": True})
        with mock.patch.dict(self.module, {"request_json_url": request}):
            removed = self.module["sabnzbd_archive_history"](
                app,
                "sab-key",
                ["SABnzbd_nzo_1", "SABnzbd_nzo_2"],
            )

        self.assertEqual(removed, 2)
        url = request.call_args.args[0]
        self.assertIn("mode=history", url)
        self.assertIn("name=delete", url)
        self.assertNotIn("archive=", url)
        self.assertIn("del_files=0", url)

    def test_completed_import_is_marked_and_removed_from_sab_history(self):
        app = {
            "name": "Whisparr",
            "url": "http://whisparr",
            "sabnzbd_url": "http://sabnzbd:8080",
            "cleanup_sabnzbd_history": True,
        }
        app_state = {
            "downloaded_release_guids": {
                "release": {
                    "movie_id": 42,
                    "sabnzbd_ids": ["SABnzbd_nzo_1"],
                }
            }
        }
        history = [
            {
                "nzo_id": "SABnzbd_nzo_1",
                "status": "Completed",
                "storage": "/storage/usenet/complete/whisparr/release",
            }
        ]
        delete_history = mock.Mock(return_value=1)

        with mock.patch.dict(
            self.module,
            {
                "sabnzbd_history": mock.Mock(return_value=history),
                "sabnzbd_queue": mock.Mock(return_value=[]),
                "whisparr_download_queue": mock.Mock(return_value=[]),
                "whisparr_movie_has_file": mock.Mock(return_value=True),
                "sabnzbd_archive_history": delete_history,
            },
        ):
            changed = self.module["process_completed_whisparr_downloads"](
                app,
                "whisparr-key",
                "sab-key",
                app_state,
            )

        self.assertTrue(changed)
        self.assertIn(
            "imported_at",
            app_state["downloaded_release_guids"]["release"],
        )
        delete_history.assert_called_once_with(
            app,
            "sab-key",
            ["SABnzbd_nzo_1"],
        )

    def test_exhausted_import_is_left_for_manual_import_and_queue_is_cleaned(self):
        with tempfile.TemporaryDirectory() as directory:
            storage_root = Path(directory).resolve()
            completed = storage_root / "usenet" / "complete" / "whisparr" / "release"
            completed.mkdir(parents=True)
            app = {
                "name": "Whisparr",
                "url": "http://whisparr",
                "sabnzbd_url": "http://sabnzbd:8080",
                "cleanup_sabnzbd_history": True,
                "import_scan_attempts": 5,
                "storage_container_path": "/storage",
                "storage_host_path": str(storage_root),
            }
            app_state = {
                "downloaded_release_guids": {
                    "release": {
                        "movie_id": 42,
                        "sabnzbd_ids": ["SABnzbd_nzo_1"],
                        "import_scan_attempts": 5,
                    }
                }
            }
            history = [
                {
                    "nzo_id": "SABnzbd_nzo_1",
                    "status": "Completed",
                    "storage": "/storage/usenet/complete/whisparr/release",
                }
            ]
            delete_history = mock.Mock(return_value=1)
            trigger_scan = mock.Mock()

            with mock.patch.dict(
                self.module,
                {
                    "sabnzbd_history": mock.Mock(return_value=history),
                    "sabnzbd_queue": mock.Mock(return_value=[]),
                    "whisparr_download_queue": mock.Mock(return_value=[]),
                    "whisparr_movie_has_file": mock.Mock(return_value=False),
                    "sabnzbd_archive_history": delete_history,
                    "trigger_downloaded_movie_scan": trigger_scan,
                },
            ):
                changed = self.module["process_completed_whisparr_downloads"](
                    app,
                    "whisparr-key",
                    "sab-key",
                    app_state,
                )

        self.assertTrue(changed)
        self.assertIn(
            "manual_import_required_at",
            app_state["downloaded_release_guids"]["release"],
        )
        delete_history.assert_called_once_with(
            app,
            "sab-key",
            ["SABnzbd_nzo_1"],
        )
        trigger_scan.assert_not_called()

    def test_terminal_title_mismatch_is_archived_for_manual_import(self):
        app = {
            "name": "Whisparr",
            "url": "http://whisparr",
            "sabnzbd_url": "http://sabnzbd:8080",
            "cleanup_sabnzbd_history": True,
        }
        app_state = {
            "downloaded_release_guids": {
                "release": {
                    "movie_id": 42,
                    "sabnzbd_ids": ["SABnzbd_nzo_1"],
                    "import_scan_attempts": 1,
                }
            }
        }
        history = [
            {
                "nzo_id": "SABnzbd_nzo_1",
                "status": "Completed",
                "storage": "/storage/usenet/complete/whisparr/release",
            }
        ]
        queue = [
            {
                "downloadId": "SABnzbd_nzo_1",
                "trackedDownloadState": "importBlocked",
                "statusMessages": [
                    {
                        "messages": [
                            "Movie title mismatch, automatic import is not possible. "
                            "Manual Import required."
                        ]
                    }
                ],
            }
        ]
        delete_history = mock.Mock(return_value=1)
        trigger_scan = mock.Mock()

        with mock.patch.dict(
            self.module,
            {
                "sabnzbd_history": mock.Mock(return_value=history),
                "sabnzbd_queue": mock.Mock(return_value=[]),
                "whisparr_download_queue": mock.Mock(return_value=queue),
                "whisparr_movie_has_file": mock.Mock(return_value=False),
                "sabnzbd_archive_history": delete_history,
                "trigger_downloaded_movie_scan": trigger_scan,
            },
        ):
            changed = self.module["process_completed_whisparr_downloads"](
                app,
                "whisparr-key",
                "sab-key",
                app_state,
            )

        self.assertTrue(changed)
        self.assertIn(
            "manual_import_required_at",
            app_state["downloaded_release_guids"]["release"],
        )
        delete_history.assert_called_once_with(
            app,
            "sab-key",
            ["SABnzbd_nzo_1"],
        )
        trigger_scan.assert_not_called()


if __name__ == "__main__":
    unittest.main()
