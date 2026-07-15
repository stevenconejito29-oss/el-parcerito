import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app import _asset_version


class AssetVersionTest(unittest.TestCase):
    def test_every_css_and_javascript_file_changes_the_fingerprint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            static_root = Path(temp_dir)
            (static_root / "css").mkdir()
            (static_root / "js" / "features").mkdir(parents=True)
            (static_root / "css" / "tokens.css").write_text(":root{}", encoding="utf-8")
            script = static_root / "js" / "features" / "navigation.js"
            script.write_text("export {};", encoding="utf-8")
            (static_root / "sw.js").write_text("const CACHE = 1;", encoding="utf-8")
            app = SimpleNamespace(static_folder=str(static_root))

            initial = _asset_version(app)
            script.write_text("export const ready = true;", encoding="utf-8")

            self.assertNotEqual(initial, _asset_version(app))

    def test_non_frontend_runtime_files_do_not_change_the_fingerprint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            static_root = Path(temp_dir)
            (static_root / "css").mkdir()
            (static_root / "js").mkdir()
            (static_root / "css" / "base.css").write_text("body{}", encoding="utf-8")
            (static_root / "sw.js").write_text("const CACHE = 1;", encoding="utf-8")
            app = SimpleNamespace(static_folder=str(static_root))

            initial = _asset_version(app)
            (static_root / "uploads").mkdir()
            (static_root / "uploads" / "photo.jpg").write_bytes(b"runtime image")

            self.assertEqual(initial, _asset_version(app))


if __name__ == "__main__":
    unittest.main()
