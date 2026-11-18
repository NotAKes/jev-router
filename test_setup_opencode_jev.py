import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from jev_router import router_port
from setup_opencode_jev import configure


class OpenCodeSetupTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / 'opencode'
        self.path = self.root / 'opencode.json'

    def test_new_config_and_idempotence(self):
        self.assertEqual(configure(self.root, 8787), (self.path, None))
        before = self.path.read_bytes()
        config = json.loads(before)
        self.assertEqual(config['model'], 'jev/auto')
        self.assertEqual(config['providers']['jev']['settings'], {'baseURL': 'http://127.0.0.1:8787/v1'})
        self.assertEqual(configure(self.root, 8787), (self.path, None))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_preserves_existing_settings_and_backs_up_each_edit(self):
        self.root.mkdir()
        previous = b'\xef\xbb\xbf{"model":"other/model","providers":{"other":{"name":"Other"}},"autoupdate":false}\r\n'
        self.path.write_bytes(previous)
        _, first_backup = configure(self.root, 8787)
        first_edit = self.path.read_bytes()
        _, second_backup = configure(self.root, 9876)
        config = json.loads(self.path.read_bytes())
        self.assertEqual((config['model'], config['autoupdate']), ('other/model', False))
        self.assertEqual(config['providers']['other'], {'name': 'Other'})
        self.assertEqual(config['providers']['jev']['settings']['baseURL'], 'http://127.0.0.1:9876/v1')
        self.assertNotEqual(first_backup, second_backup)
        self.assertEqual(first_backup.read_bytes(), previous)
        self.assertEqual(second_backup.read_bytes(), first_edit)
        self.assertEqual(configure(self.root, 9876), (self.path, None))
        _, third_backup = configure(self.root, 9876, make_default=True)
        self.assertEqual(json.loads(third_backup.read_bytes()), config)
        self.assertEqual(json.loads(self.path.read_bytes())['model'], 'jev/auto')

    def test_rejects_jsonc_without_writing(self):
        self.root.mkdir()
        jsonc = self.path.with_suffix('.jsonc')
        original = b'{ // keep this comment\n}\n'
        jsonc.write_bytes(original)
        with self.assertRaisesRegex(ValueError, 'opencode.jsonc'):
            configure(self.root, 8787)
        self.assertEqual(list(self.root.iterdir()), [jsonc])
        self.assertEqual(jsonc.read_bytes(), original)

    def test_rejects_custom_provider_even_on_loopback(self):
        configure(self.root, 8787)
        config = json.loads(self.path.read_bytes())
        config['providers']['jev']['settings']['baseURL'] = 'http://127.0.0.1:8787/custom/v1'
        previous = json.dumps(config).encode()
        self.path.write_bytes(previous)
        with self.assertRaisesRegex(ValueError, 'custom Jev'):
            configure(self.root, 9876)
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_rejects_invalid_json_or_shape_without_writing(self):
        self.root.mkdir()
        for previous in (b'{', b'[]', b'{"providers":null}'):
            with self.subTest(previous=previous):
                self.path.write_bytes(previous)
                with self.assertRaises(ValueError):
                    configure(self.root, 8787)
                self.assertEqual(self.path.read_bytes(), previous)
                self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_port_validation(self):
        self.assertEqual(router_port({}), 8787)
        self.assertEqual(router_port({'JEV_ROUTER_PORT': '9876'}), 9876)
        for port in ('abc', '', '-1', '0', '65536'):
            with self.subTest(port=port), self.assertRaisesRegex(ValueError, 'JEV_ROUTER_PORT'):
                router_port({'JEV_ROUTER_PORT': port})


if __name__ == '__main__':
    unittest.main()
