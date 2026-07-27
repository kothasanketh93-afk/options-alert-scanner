import os
import tempfile
import unittest

from ..utils.config_loader import get, is_enabled, load_config, reload_config
from ..utils.exceptions import ConfigError


class TestConfigLoader(unittest.TestCase):
    def test_default_config_loads(self):
        config = load_config()
        self.assertIn("probability", config)
        self.assertIn("risk", config)
        self.assertIn("engines", config)

    def test_seed_weights_sum_close_to_one(self):
        config = load_config()
        weights = config["probability"]["seed_weights"]
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=2)

    def test_get_dotted_path(self):
        config = load_config()
        value = get(config, "risk.atr_sl_multiplier")
        self.assertIsInstance(value, float)

    def test_get_missing_key_returns_default(self):
        config = load_config()
        self.assertEqual(get(config, "nonexistent.key", "fallback"), "fallback")

    def test_is_enabled_defaults_true_when_missing(self):
        config = {"engines": {}}
        self.assertTrue(is_enabled(config, "probability"))

    def test_is_enabled_respects_false(self):
        config = {"engines": {"probability": {"enabled": False}}}
        self.assertFalse(is_enabled(config, "probability"))

    def test_missing_file_raises_config_error(self):
        with self.assertRaises(ConfigError):
            load_config("/nonexistent/path/settings.yaml", force_reload=True)

    def test_malformed_yaml_raises_config_error(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
            fh.write("not: a: valid: yaml: [")
            path = fh.name
        try:
            with self.assertRaises(ConfigError):
                load_config(path, force_reload=True)
        finally:
            os.unlink(path)

    def test_config_is_cached_and_reload_forces_refresh(self):
        first = load_config()
        second = load_config()
        self.assertEqual(first, second)
        third = reload_config()
        self.assertEqual(first, third)

    def test_returned_config_is_a_copy(self):
        config = load_config()
        config["risk"]["atr_sl_multiplier"] = 999.0
        fresh = load_config()
        self.assertNotEqual(fresh["risk"]["atr_sl_multiplier"], 999.0)


if __name__ == "__main__":
    unittest.main()
