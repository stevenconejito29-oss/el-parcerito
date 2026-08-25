import unittest

from flask import Flask

from security_utils import safe_local_referrer


class SafeLocalReferrerTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)

    def test_accepts_same_host_and_returns_only_local_path(self):
        with self.app.test_request_context(
            "/admin/caja", headers={"Referer": "https://example.test/admin/finanzas?dia=hoy"},
            base_url="https://example.test",
        ):
            self.assertEqual(
                safe_local_referrer("/admin", ("/admin/finanzas",)),
                "/admin/finanzas?dia=hoy",
            )

    def test_rejects_external_or_unapproved_referrer(self):
        for referrer in (
            "https://evil.test/admin/finanzas",
            "javascript:alert(1)",
            "https://example.test/superadmin/config",
        ):
            with self.app.test_request_context(
                "/admin/caja", headers={"Referer": referrer},
                base_url="https://example.test",
            ):
                self.assertEqual(
                    safe_local_referrer("/admin/finanzas", ("/admin/finanzas",)),
                    "/admin/finanzas",
                )

    def test_database_tools_never_render_password_in_command(self):
        source = (__import__("pathlib").Path(__file__).parents[1] / "routes" / "superadmin.py").read_text()
        self.assertNotIn("hide_password=False", source)
        self.assertIn('process_env["PGPASSWORD"] = password', source)
        self.assertIn("env=process_env", source)

    def test_menu_toggle_is_post_only(self):
        source = (__import__("pathlib").Path(__file__).parents[1] / "routes" / "admin.py").read_text()
        self.assertIn(
            '@admin_bp.route("/menu-config/<int:item_id>/toggle", methods=["POST"])',
            source,
        )


if __name__ == "__main__":
    unittest.main()
