import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from recap_glm import ROOT, default_data_root, require_external_path
from core import RecapError
from glm_runtime import require_sdk_runtime


class InstallPathTests(unittest.TestCase):
    def test_default_data_root_uses_user_data_area(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(
                os.environ, {"LOCALAPPDATA": folder}, clear=True):
            self.assertEqual(default_data_root(), Path(folder) / "ScreenAssistant" / "backstage-recap")

    def test_missing_home_environment_still_defaults_outside_install(self):
        with patch.dict(os.environ, {}, clear=True):
            default = default_data_root().resolve()
        with self.assertRaises(ValueError):
            default.relative_to(ROOT.resolve())

    def test_writable_runtime_paths_must_stay_outside_install_tree(self):
        with tempfile.TemporaryDirectory() as folder:
            external = Path(folder) / "records"
            self.assertEqual(require_external_path(external), external.resolve())
        with self.assertRaisesRegex(RecapError, "runtime_path_inside_install"):
            require_external_path(ROOT / "scratch")

    def test_sdk_interpreter_must_be_configured_before_desktop_start(self):
        with patch.dict(os.environ, {"BACKSTAGE_SDK_PYTHON": "missing-sdk-python.exe"}, clear=True):
            with self.assertRaisesRegex(ValueError, "agent_sdk_runtime_missing"):
                require_sdk_runtime()


if __name__ == "__main__":
    unittest.main()
