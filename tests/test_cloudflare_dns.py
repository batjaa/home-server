"""Check DNS record selection before applying Cloudflare mutations.

Run with Ansible's Python environment (PyYAML and Jinja are already installed).
"""
import unittest
from pathlib import Path

import yaml
from jinja2.nativetypes import NativeEnvironment


TASKS = yaml.safe_load(
    (Path(__file__).parents[1] / 'roles/network/cloudflare-dns/tasks/record.yml').read_text()
)


def select_record(records, record_type='CNAME'):
    context = {
        'cloudflare_managed_zone': {'name': 'plotling.app'},
        'cloudflare_dns_record': {'name': '@', 'type': record_type},
        'cloudflare_records_response': {'json': {'result': records}},
    }
    engine = NativeEnvironment()
    for task in TASKS:
        if 'set_fact' in task:
            for key, template in task['set_fact'].items():
                context[key] = engine.from_string(template).render(**context)
        elif 'assert' in task:
            for condition in task['assert']['that']:
                if not engine.from_string('{{ ' + condition + ' }}').render(**context):
                    raise ValueError('Ambiguous address records')
    return context['cloudflare_existing_record']


class CloudflareDnsTest(unittest.TestCase):
    def test_converts_single_address_record_without_selecting_mail_records(self):
        records = [
            {'id': 'mail', 'name': 'plotling.app', 'type': 'MX'},
            {'id': 'old-origin', 'name': 'plotling.app', 'type': 'A'},
            {'id': 'other', 'name': 'media.plotling.app', 'type': 'A'},
        ]
        self.assertEqual(select_record(records)['id'], 'old-origin')

    def test_existing_cname_is_updated_in_place(self):
        self.assertEqual(select_record([
            {'id': 'current', 'name': 'plotling.app', 'type': 'CNAME'},
        ])['id'], 'current')

    def test_new_hostname_creates_record(self):
        self.assertEqual(select_record([
            {'id': 'mail', 'name': 'plotling.app', 'type': 'MX'},
        ]), {})

    def test_multiple_address_records_require_explicit_resolution(self):
        with self.assertRaises(ValueError):
            select_record([
                {'id': 'ipv4', 'name': 'plotling.app', 'type': 'A'},
                {'id': 'ipv6', 'name': 'plotling.app', 'type': 'AAAA'},
            ])

    def test_other_record_types_keep_type_specific_selection(self):
        self.assertEqual(select_record([
            {'id': 'ip', 'name': 'plotling.app', 'type': 'A'},
            {'id': 'mail', 'name': 'plotling.app', 'type': 'MX'},
        ], 'MX')['id'], 'mail')


if __name__ == '__main__':
    unittest.main()
