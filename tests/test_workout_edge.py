"""Workout edge planning and the scoped apply's safety boundaries.

These tests do not need a vault password, Cloudflare, or the home network.
They do not prove the live nginx reload, DNS API, or Pi-hole write.
"""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml
from jinja2 import Environment


ROOT = Path(__file__).parents[1]
ROLE = ROOT / "roles/network/workout-edge"
SCRIPT = ROLE / "files/workout_edge.py"


def load_script():
    spec = importlib.util.spec_from_file_location("workout_edge", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def task_files():
    return sorted((ROLE / "tasks").glob("*.yml"))


class WorkoutEdgeLogicTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_script()
        cls.defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())

    def test_defaults_match_the_planner(self):
        self.assertEqual(self.mod.WORKOUT_FQDN, self.defaults["workout_fqdn"])
        self.assertEqual(self.mod.WORKOUT_CNAME_TARGET, self.defaults["workout_cname_target"])
        self.assertEqual(self.mod.WORKOUT_SPLIT_IP, self.defaults["workout_split_ip"])
        self.assertEqual(self.mod.WORKOUT_RECORD_NAME, self.defaults["workout_record_name"])
        self.assertEqual(self.defaults["workout_upstream_ip"], "192.168.50.40")
        self.assertEqual(self.defaults["workout_coolify_app_uuid"], "t1dy1s25oevzdserakz3otdm")

    def test_pihole_merge_keeps_unrelated_rows_and_is_idempotent(self):
        original = [
            "192.168.50.10 pihole.home.local",
            "192.168.50.20 plex.batjaa.site",
            "192.168.50.20 notworkout.batjaa.site",
        ]
        merged, changed = self.mod.merge_pihole_hosts(original)
        self.assertTrue(changed)
        self.assertEqual(
            merged,
            original + ["192.168.50.20 workout.batjaa.site"],
        )
        again, changed_again = self.mod.merge_pihole_hosts(merged)
        self.assertFalse(changed_again)
        self.assertEqual(again, merged)

    def test_pihole_merge_corrects_only_the_workout_address(self):
        merged, changed = self.mod.merge_pihole_hosts(
            [
                "10.0.0.8 workout.batjaa.site",
                "10.0.0.8 workout.batjaa.site extra",
                "192.168.50.20 plex.batjaa.site",
            ]
        )
        self.assertTrue(changed)
        self.assertEqual(
            merged,
            [
                "192.168.50.20 workout.batjaa.site",
                "192.168.50.20 plex.batjaa.site",
            ],
        )

    def test_merge_command_writes_a_private_plan_without_changing_a_current_list(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = Path(directory) / "hosts.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "merge-pihole",
                    str(plan),
                    "workout.batjaa.site",
                    "192.168.50.20",
                ],
                input='["192.168.50.20 plex.batjaa.site", "192.168.50.20 workout.batjaa.site"]\n',
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "UNCHANGED\n")
            self.assertEqual(
                json.loads(plan.read_text()),
                ["192.168.50.20 plex.batjaa.site", "192.168.50.20 workout.batjaa.site"],
            )
            self.assertEqual(plan.stat().st_mode & 0o777, 0o600)

    def test_pihole_parser_accepts_ftl_output_and_refuses_prose(self):
        raw = 'dns.hosts = ["192.168.50.20 plex.batjaa.site"]\n'
        self.assertEqual(
            self.mod.parse_pihole_hosts(raw),
            ["192.168.50.20 plex.batjaa.site"],
        )
        with self.assertRaises(ValueError):
            self.mod.parse_pihole_hosts("permission denied")

    def test_cname_decision_ignores_other_names_and_ttl(self):
        plan = self.mod.decide_cname(
            [
                {
                    "id": "plex",
                    "type": "CNAME",
                    "name": "plex.batjaa.site",
                    "content": "ddns.batjaa.site",
                    "proxied": False,
                    "ttl": 1,
                },
                {
                    "id": "workout",
                    "type": "CNAME",
                    "name": "workout.batjaa.site",
                    "content": "ddns.batjaa.site",
                    "proxied": False,
                    "ttl": 300,
                    "comment": "do-not-copy",
                },
            ]
        )
        self.assertEqual(plan["action"], "none")
        self.assertNotIn("comment", plan["previous"])
        self.assertNotIn("do-not-copy", json.dumps(plan))

    def test_cname_create_and_update_are_limited_to_workout(self):
        created = self.mod.decide_cname([])
        self.assertEqual(created["action"], "create")
        self.assertEqual(created["method"], "POST")
        self.assertEqual(created["body"]["proxied"], False)
        self.assertEqual(created["body"]["ttl"], 1)
        self.assertNotIn("id", created)

        updated = self.mod.decide_cname(
            [
                {
                    "id": "abc",
                    "type": "CNAME",
                    "name": "workout.batjaa.site",
                    "content": "example.invalid",
                    "proxied": True,
                    "ttl": 120,
                }
            ]
        )
        self.assertEqual(updated["action"], "update")
        self.assertEqual(updated["method"], "PUT")
        self.assertEqual(updated["id"], "abc")
        self.assertFalse(updated["body"]["proxied"])
        self.assertEqual(updated["body"]["content"], "ddns.batjaa.site")
        self.assertEqual(updated["body"]["ttl"], 120)

    def test_cname_decision_refuses_other_types_and_duplicates(self):
        with self.assertRaises(ValueError):
            self.mod.decide_cname(
                [{"id": "a", "type": "A", "name": "workout.batjaa.site", "content": "192.168.50.20"}]
            )
        with self.assertRaises(ValueError):
            self.mod.decide_cname(
                [
                    {"id": "a", "type": "CNAME", "name": "workout.batjaa.site", "content": "ddns.batjaa.site", "proxied": False},
                    {"id": "b", "type": "CNAME", "name": "workout.batjaa.site", "content": "ddns.batjaa.site", "proxied": False},
                ]
            )

    def test_host_vars_edit_preserves_unrelated_lines_and_is_idempotent(self):
        original = (
            "# keep this comment\n"
            "cloudflare_records:\n"
            "  # plex stays\n"
            "  - { name: plex, type: CNAME, content: ddns.batjaa.site, proxied: false }\n"
            "\n"
            "pihole_split_dns:\n"
            "  - { domain: plex.batjaa.site, ip: '192.168.50.20' }\n"
            "unrelated_token: keep-me\n"
        )
        updated, changed = self.mod.ensure_host_vars_text(original)
        self.assertTrue(changed)
        self.assertIn("# keep this comment\n", updated)
        self.assertIn("# plex stays\n", updated)
        self.assertIn("unrelated_token: keep-me\n", updated)
        self.assertIn("{ name: plex, type: CNAME, content: ddns.batjaa.site, proxied: false }", updated)
        data = yaml.safe_load(updated)
        self.assertEqual([item["name"] for item in data["cloudflare_records"]], ["plex", "workout"])
        self.assertEqual(data["cloudflare_records"][1]["content"], "ddns.batjaa.site")
        self.assertIs(data["cloudflare_records"][1]["proxied"], False)
        self.assertEqual(data["pihole_split_dns"][0]["domain"], "plex.batjaa.site")
        self.assertEqual(data["pihole_split_dns"][1]["domain"], "workout.batjaa.site")
        self.assertEqual(data["pihole_split_dns"][1]["ip"], "192.168.50.20")
        self.assertEqual(data["unrelated_token"], "keep-me")
        again, changed_again = self.mod.ensure_host_vars_text(updated)
        self.assertFalse(changed_again)
        self.assertEqual(again, updated)

    def test_host_vars_block_style_and_empty_list(self):
        original = (
            "cloudflare_records:\n"
            "  - name: plex\n"
            "    type: CNAME\n"
            "    content: ddns.batjaa.site\n"
            "    proxied: false\n"
            "pihole_split_dns: []\n"
        )
        updated, changed = self.mod.ensure_host_vars_text(original)
        self.assertTrue(changed)
        self.assertIn("  - name: workout\n", updated)
        self.assertIn("    content: ddns.batjaa.site\n", updated)
        self.assertIn("  - { domain: workout.batjaa.site, ip: '192.168.50.20' }\n", updated)
        self.assertIn("  - name: plex\n", updated)
        data = yaml.safe_load(updated)
        self.assertEqual(data["cloudflare_records"][1]["name"], "workout")
        self.assertEqual(data["pihole_split_dns"][0]["ip"], "192.168.50.20")

    def test_host_vars_refuses_to_rewrite_a_different_workout_value(self):
        original = (
            "cloudflare_records:\n"
            "  - { name: workout, type: CNAME, content: example.invalid, proxied: false }\n"
            "pihole_split_dns:\n"
            "  - { domain: plex.batjaa.site, ip: '192.168.50.20' }\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vars.yml"
            path.write_text(original)
            with self.assertRaises(ValueError):
                self.mod.ensure_host_vars_file(path)
            self.assertEqual(path.read_text(), original)
            self.assertEqual(list(Path(directory).glob("*.bak")), [])

    def test_host_vars_backup_is_only_written_when_the_file_changes(self):
        original = (
            "cloudflare_records:\n"
            "  - { name: plex, type: CNAME, content: ddns.batjaa.site, proxied: false }\n"
            "pihole_split_dns:\n"
            "  - { domain: plex.batjaa.site, ip: '192.168.50.20' }\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vars.yml"
            path.write_text(original)
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "ensure-host-vars", str(path)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "CHANGED\n")
            backups = list(Path(directory).glob("vars.yml.workout-edge-*.bak"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), original)
            self.assertNotIn("CHANGED", backups[0].read_text())
            again = subprocess.run(
                [sys.executable, str(SCRIPT), "ensure-host-vars", str(path)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(again.stdout, "UNCHANGED\n")
            self.assertEqual(len(list(Path(directory).glob("*.bak"))), 1)


class WorkoutEdgeRenderTest(unittest.TestCase):
    def test_vhost_points_at_coolify_with_sni_and_unverified_upstream(self):
        rendered = Environment(autoescape=False).from_string(
            (ROLE / "templates/workout.subdomain.conf.j2").read_text()
        ).render(
            workout_fqdn="workout.batjaa.site",
            workout_upstream_ip="192.168.50.40",
            workout_upstream_port=443,
            workout_coolify_app_uuid="t1dy1s25oevzdserakz3otdm",
        )
        self.assertIn("server_name workout.batjaa.site;", rendered)
        self.assertIn("include /config/nginx/proxy.conf;", rendered)
        self.assertIn("Host $host", rendered)
        self.assertIn("X-Forwarded-For $proxy_add_x_forwarded_for", rendered)
        self.assertIn("X-Forwarded-Proto https", rendered)
        self.assertIn("X-Forwarded-Host $host", rendered)
        self.assertIn("proxy_ssl_server_name on;", rendered)
        self.assertIn("proxy_ssl_name workout.batjaa.site;", rendered)
        self.assertIn("proxy_ssl_verify off;", rendered)
        self.assertNotIn("proxy_ssl_verify on", rendered)
        self.assertIn("set $upstream_app 192.168.50.40;", rendered)
        self.assertIn("set $upstream_port 443;", rendered)
        self.assertIn("set $upstream_proto https;", rendered)
        self.assertIn("proxy_pass $upstream_proto://$upstream_app:$upstream_port;", rendered)
        self.assertIn("t1dy1s25oevzdserakz3otdm", rendered)
        self.assertNotIn("proxy_set_header Upgrade", rendered)
        self.assertNotIn("proxy_set_header Connection", rendered)
        self.assertEqual(
            list((ROOT / "roles/network/swag/templates/proxy-confs").glob("*workout*")),
            [],
        )


class WorkoutEdgeApplyBoundaryTest(unittest.TestCase):
    def test_tracked_roles_are_unsafe_for_this_apply(self):
        swag = (ROOT / "roles/network/swag/tasks/main.yml").read_text()
        handler = (ROOT / "roles/network/swag/handlers/main.yml").read_text()
        pihole = (ROOT / "roles/network/pihole/tasks/main.yml").read_text()
        zone = (ROOT / "roles/network/cloudflare-dns/tasks/zone.yml").read_text()
        record = (ROOT / "roles/network/cloudflare-dns/tasks/record.yml").read_text()
        self.assertIn("notify: restart swag", swag)
        self.assertIn("restart: yes", handler)
        self.assertIn("docker restart", pihole)
        self.assertIn("cloudflare_managed_zone.records", zone)
        self.assertIn("'PUT'", record)
        self.assertIn("Authorization:", record)
        self.assertIn("no_log: true", record)
        self.assertNotIn("no_log: false", record)

    def test_workout_apply_does_not_use_those_roles(self):
        plays = yaml.safe_load((ROOT / "workout-edge.yml").read_text())
        main = (ROOT / "main.yml").read_text()
        self.assertIn("import_playbook: workout-edge.yml", main)
        imported = []
        for play in plays:
            for task in play["tasks"]:
                imported.append(task["ansible.builtin.import_role"]["name"])
                self.assertEqual(task["ansible.builtin.import_role"]["name"], "network/workout-edge")
        self.assertEqual(
            [play["hosts"] for play in plays],
            ["localhost", "localhost", "tentomon", "andromon"],
        )
        self.assertNotIn("network/swag", imported)
        self.assertNotIn("network/pihole", imported)
        self.assertNotIn("network/cloudflare-dns", imported)
        self.assertFalse((ROLE / "handlers").exists())

    def test_cloudflare_tasks_are_narrow_and_do_not_log_the_token(self):
        path = ROLE / "tasks/cloudflare.yml"
        text = path.read_text()
        tasks = yaml.safe_load(text)

        def walk(items):
            for task in items:
                yield task
                for key in ("block", "rescue", "always"):
                    if key in task:
                        yield from walk(task[key])

        flat = list(walk(tasks))
        uri_tasks = [task for task in flat if "ansible.builtin.uri" in task]
        self.assertGreaterEqual(len(uri_tasks), 4)
        for task in uri_tasks:
            self.assertIs(task["no_log"], True)
            url = task["ansible.builtin.uri"]["url"]
            self.assertNotIn("cloudflare_dns_token", url)
        self.assertIn("name={{ workout_fqdn | urlencode }}", text)
        self.assertNotIn("cloudflare_managed_zone.records", text)
        self.assertNotIn("per_page=200", text)
        self.assertNotIn("no_log: false", text)
        debugs = [task for task in flat if "ansible.builtin.debug" in task]
        self.assertEqual(len(debugs), 1)
        self.assertEqual(debugs[0]["ansible.builtin.debug"]["msg"], "workout CNAME {{ workout_dns_plan.action }}")

    def test_swag_and_pihole_tasks_reload_or_preserve_hosts(self):
        swag = (ROLE / "tasks/swag.yml").read_text()
        pihole = (ROLE / "tasks/pihole.yml").read_text()
        for text in (swag, pihole, (ROLE / "tasks/host_vars.yml").read_text()):
            self.assertNotIn("docker restart", text)
            self.assertNotIn("restart swag", text)
            self.assertNotIn("docker_container", text)
            self.assertNotIn("notify:", text)
            self.assertNotIn("no_log: false", text)
        self.assertIn("nginx", swag)
        self.assertIn("-t", swag)
        self.assertIn("reload", swag)
        self.assertIn("backup: true", swag)
        self.assertIn("backup_file", swag)
        self.assertIn("dns.hosts", pihole)
        self.assertNotIn("pihole restartdns", pihole)
        self.assertNotIn("setpassword", pihole)


if __name__ == "__main__":
    unittest.main()
