import importlib.machinery
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('registry', str(ROOT / 'bin/register-preview-project'))
spec = importlib.util.spec_from_loader(loader.name, loader)
registry = importlib.util.module_from_spec(spec)
loader.exec_module(registry)


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'vars').mkdir()
        (self.root / 'vars/preview-projects.json').write_text('{"preview_projects":{}}\n')
        (self.root / 'vars/preview-targets.json').write_bytes((ROOT / 'vars/preview-targets.json').read_bytes())
        self.server = 'b3mwnfgzbdyjtb7uuzrz0qlw'

    def test_dry_run_does_not_write(self):
        before = (self.root / 'vars/preview-projects.json').read_bytes()
        self.assertTrue(registry.register(self.root, 'sample', self.server, dry_run=True)['changed'])
        self.assertEqual(before, (self.root / 'vars/preview-projects.json').read_bytes())

    def test_register_is_idempotent_and_prevents_target_move(self):
        self.assertTrue(registry.register(self.root, 'sample', self.server)['changed'])
        self.assertFalse(registry.register(self.root, 'sample', self.server)['changed'])
        with self.assertRaisesRegex(ValueError, 'another target'):
            registry.register(self.root, 'sample', 'oj5cg3ezwioty3yv7uud1o2j')

    def test_unknown_server_and_invalid_names_do_not_write(self):
        for project in ['../bad', 'two.labels', '-bad', 'x'*64, 'bad;echo', 'Upper']:
            with self.assertRaises(ValueError):
                registry.register(self.root, project, self.server)
        with self.assertRaisesRegex(ValueError, 'Unknown Coolify'):
            registry.register(self.root, 'valid', 'unknown')
        self.assertEqual({}, registry.read_registry(self.root)[0])

    def test_check_requires_registration(self):
        with self.assertRaisesRegex(ValueError, 'not registered'):
            registry.register(self.root, 'sample', self.server, check=True)

    def test_cli_register_uses_directory_lock_and_atomic_json(self):
        result = subprocess.run([str(ROOT / 'bin/register-preview-project'), 'register', 'sample',
                                 '--server-uuid', self.server, '--root', str(self.root)], capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual({'sample': {'target': 'tech-nomads'}}, registry.read_registry(self.root)[0])
        self.assertEqual(2, len(list((self.root / 'vars').iterdir())))


if __name__ == '__main__':
    unittest.main()
