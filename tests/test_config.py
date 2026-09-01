from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tmem.config import load_config


class ConfigTests(unittest.TestCase):
    def test_rejects_non_object_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "config.json").write_text("[]\n", encoding="utf-8")
            with patch.dict(os.environ, {"TMEM_CONFIG_DIR": directory}):
                with self.assertRaisesRegex(ValueError, "JSON object"):
                    load_config()

    def test_rejects_invalid_field_values(self) -> None:
        invalid_configs = [
            '{"history_limit": true}',
            '{"ignore_patterns": ["["]}',
        ]
        for raw in invalid_configs:
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as directory:
                Path(directory, "config.json").write_text(raw, encoding="utf-8")
                with patch.dict(os.environ, {"TMEM_CONFIG_DIR": directory}):
                    with self.assertRaises(ValueError):
                        load_config()


if __name__ == "__main__":
    unittest.main()
