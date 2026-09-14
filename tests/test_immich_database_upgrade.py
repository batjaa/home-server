import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


SCRIPT = (Path(__file__).resolve().parents[1] / 'roles/containers/media/immich/files/prepare-database-upgrade.py')
spec = importlib.util.spec_from_file_location('immich_database_upgrade', SCRIPT)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class ImmichDatabaseUpgradeTests(unittest.TestCase):
    def test_existing_target_is_rejected_before_stopping_services(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'existing'
            target.mkdir()
            sentinel = target / 'PG_VERSION'
            sentinel.write_text('14\n')
            args = ['prepare', '--backup-dir', directory + '/backup', '--target-dir', str(target),
                    '--redis-dir', directory + '/redis', '--image', 'test-image']
            with mock.patch('sys.argv', args), mock.patch.object(migration, 'run') as run:
                with self.assertRaisesRegex(AssertionError, 'Target directory exists'):
                    migration.main()
            run.assert_not_called()
            self.assertEqual(sentinel.read_text(), '14\n')

    def test_existing_restore_container_is_not_stopped_or_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(migration.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
                with self.assertRaisesRegex(RuntimeError, 'Restore container already exists'):
                    migration.restore('test-image', Path(directory) / 'target', Path('backup'), {}, 'restore-check')
            self.assertEqual(run.call_count, 1)
            self.assertFalse((Path(directory) / 'target').exists())

    def test_failed_database_start_retains_data_for_inspection_and_cleans_up_container(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'target'
            calls = []

            def process(args, **kwargs):
                calls.append(args)
                if args[:2] == ['docker', 'inspect'] and '--format' not in args:
                    return subprocess.CompletedProcess(args, 1, b'', b'not found')
                if 'pg_isready' in args:
                    return subprocess.CompletedProcess(args, 1, b'', b'not ready')
                if '--format' in args:
                    return subprocess.CompletedProcess(args, 0, b'false\n', b'')
                return subprocess.CompletedProcess(args, 0, b'', b'')

            with mock.patch.object(migration.subprocess, 'run', side_effect=process):
                with self.assertRaisesRegex(RuntimeError, 'Target database exited'):
                    migration.restore('test-image', target, Path('backup'), {'POSTGRES_PASSWORD': 'test'}, 'restore-check')
            self.assertTrue(target.exists())
            self.assertEqual(target.stat().st_mode & 0o777, 0o755)
            self.assertIn(['docker', 'stop', '-t', '60', 'restore-check'], calls)
            self.assertIn(['docker', 'rm', 'restore-check'], calls)
            self.assertFalse((target / '.immich-migration-verified').exists())


if __name__ == '__main__':
    unittest.main()
