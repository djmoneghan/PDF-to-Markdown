# tests/test_config.py
# Real (non-stub) tests for ingestor.load_config().
# No mocking required — exercises the actual config.yaml on disk.

import textwrap
import unittest
from pathlib import Path

import yaml


def _write_config(tmp_path: Path, content: str) -> Path:
    """Helper: write a YAML string to a temp file and return its path."""
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


class TestLoadConfigImport(unittest.TestCase):
    def test_load_config_importable(self):
        from ingestor import load_config
        assert callable(load_config)


class TestLoadConfigHappyPath(unittest.TestCase):
    """load_config() returns correct values when given the real config.yaml."""

    @classmethod
    def setUpClass(cls):
        from ingestor import load_config
        # Resolve config.yaml relative to this file's grandparent directory.
        config_path = Path(__file__).parent.parent / "config.yaml"
        cls.cfg = load_config(config_path)

    def test_returns_dict(self):
        self.assertIsInstance(self.cfg, dict)

    # ollama section
    def test_ollama_endpoint_is_localhost(self):
        self.assertEqual(self.cfg["ollama"]["endpoint"], "http://localhost:11434")

    def test_ollama_model_present(self):
        self.assertIsInstance(self.cfg["ollama"]["model"], str)
        self.assertTrue(len(self.cfg["ollama"]["model"]) > 0)

    def test_ollama_fallback_model_present(self):
        self.assertIsInstance(self.cfg["ollama"]["fallback_model"], str)

    def test_ollama_timeout_is_positive_int(self):
        self.assertIsInstance(self.cfg["ollama"]["timeout_seconds"], int)
        self.assertGreater(self.cfg["ollama"]["timeout_seconds"], 0)

    # extraction section
    def test_extraction_engine_is_valid(self):
        self.assertIn(self.cfg["extraction"]["engine"], ("docling", "pymupdf"))

    def test_confidence_threshold_in_range(self):
        t = self.cfg["extraction"]["confidence_threshold"]
        self.assertIsInstance(t, float)
        self.assertGreaterEqual(t, 0.0)
        self.assertLessEqual(t, 1.0)

    # chunking section
    def test_split_levels_is_list(self):
        self.assertIsInstance(self.cfg["chunking"]["split_levels"], list)
        self.assertGreater(len(self.cfg["chunking"]["split_levels"]), 0)

    def test_split_levels_contains_h1(self):
        self.assertIn("#", self.cfg["chunking"]["split_levels"])

    def test_min_chunk_tokens_positive(self):
        self.assertGreater(self.cfg["chunking"]["min_chunk_tokens"], 0)

    def test_max_chunk_tokens_greater_than_min(self):
        self.assertGreater(
            self.cfg["chunking"]["max_chunk_tokens"],
            self.cfg["chunking"]["min_chunk_tokens"],
        )

    # output section
    def test_processed_root_is_string(self):
        self.assertIsInstance(self.cfg["output"]["processed_root"], str)

    def test_manifest_filename_ends_with_json(self):
        self.assertTrue(self.cfg["output"]["manifest_filename"].endswith(".json"))

    def test_corrections_filename_ends_with_json(self):
        self.assertTrue(self.cfg["output"]["corrections_filename"].endswith(".json"))

    # hitl section
    def test_auto_accept_threshold_in_range(self):
        t = self.cfg["hitl"]["auto_accept_above"]
        self.assertIsInstance(t, float)
        self.assertGreaterEqual(t, 0.0)
        self.assertLessEqual(t, 1.0)

    def test_auto_accept_above_confidence_threshold(self):
        # auto_accept threshold must be strictly higher than the flag threshold
        # so there is a band of chunks that go to HITL review.
        self.assertGreater(
            self.cfg["hitl"]["auto_accept_above"],
            self.cfg["extraction"]["confidence_threshold"],
        )

    def test_show_raw_markdown_is_bool(self):
        self.assertIsInstance(self.cfg["hitl"]["show_raw_markdown"], bool)


class TestLoadConfigMissingFile(unittest.TestCase):
    """load_config() raises FileNotFoundError for a nonexistent path."""

    def test_missing_file_raises_file_not_found(self):
        from ingestor import load_config
        with self.assertRaises(FileNotFoundError) as ctx:
            load_config(Path("/nonexistent/path/config.yaml"))
        self.assertIn("Config file not found", str(ctx.exception))

    def test_error_message_contains_path(self):
        from ingestor import load_config
        p = Path("/tmp/definitely_missing_config.yaml")
        with self.assertRaises(FileNotFoundError) as ctx:
            load_config(p)
        self.assertIn(str(p.name), str(ctx.exception))


class TestLoadConfigEmptyFile(unittest.TestCase):
    """load_config() raises ValueError for an empty or non-mapping YAML file."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_file_raises_value_error(self):
        from ingestor import load_config
        p = _write_config(self.tmp_path, "")
        with self.assertRaises(ValueError) as ctx:
            load_config(p)
        self.assertIn("empty or not a YAML mapping", str(ctx.exception))

    def test_list_root_raises_value_error(self):
        from ingestor import load_config
        p = _write_config(self.tmp_path, "- item1\n- item2\n")
        with self.assertRaises(ValueError) as ctx:
            load_config(p)
        self.assertIn("empty or not a YAML mapping", str(ctx.exception))


class TestLoadConfigMissingKeys(unittest.TestCase):
    """load_config() raises ValueError for each missing required key."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _minimal_valid_config(self) -> dict:
        return {
            "ollama": {
                "endpoint": "http://localhost:11434",
                "model": "test-model",
                "fallback_model": "test-fallback",
                "timeout_seconds": 60,
            },
            "extraction": {
                "engine": "docling",
                "confidence_threshold": 0.75,
            },
            "chunking": {
                "split_levels": ["#", "##"],
                "min_chunk_tokens": 100,
                "max_chunk_tokens": 1500,
            },
            "output": {
                "processed_root": "processed",
                "manifest_filename": "manifest.json",
                "corrections_filename": "corrections.json",
            },
            "hitl": {
                "auto_accept_above": 0.92,
                "show_raw_markdown": True,
            },
        }

    def _write(self, cfg: dict) -> Path:
        p = self.tmp_path / "config.yaml"
        p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        return p

    def test_minimal_valid_config_loads_without_error(self):
        from ingestor import load_config
        cfg = self._minimal_valid_config()
        p = self._write(cfg)
        result = load_config(p)
        self.assertIsInstance(result, dict)

    def test_missing_ollama_endpoint_raises(self):
        from ingestor import load_config
        cfg = self._minimal_valid_config()
        del cfg["ollama"]["endpoint"]
        p = self._write(cfg)
        with self.assertRaises(ValueError) as ctx:
            load_config(p)
        self.assertIn("ollama.endpoint", str(ctx.exception))

    def test_missing_ollama_section_raises(self):
        from ingestor import load_config
        cfg = self._minimal_valid_config()
        del cfg["ollama"]
        p = self._write(cfg)
        with self.assertRaises(ValueError) as ctx:
            load_config(p)
        self.assertIn("ollama", str(ctx.exception))

    def test_missing_chunking_split_levels_raises(self):
        from ingestor import load_config
        cfg = self._minimal_valid_config()
        del cfg["chunking"]["split_levels"]
        p = self._write(cfg)
        with self.assertRaises(ValueError) as ctx:
            load_config(p)
        self.assertIn("chunking.split_levels", str(ctx.exception))

    def test_missing_hitl_auto_accept_raises(self):
        from ingestor import load_config
        cfg = self._minimal_valid_config()
        del cfg["hitl"]["auto_accept_above"]
        p = self._write(cfg)
        with self.assertRaises(ValueError) as ctx:
            load_config(p)
        self.assertIn("hitl.auto_accept_above", str(ctx.exception))

    def test_missing_output_processed_root_raises(self):
        from ingestor import load_config
        cfg = self._minimal_valid_config()
        del cfg["output"]["processed_root"]
        p = self._write(cfg)
        with self.assertRaises(ValueError) as ctx:
            load_config(p)
        self.assertIn("output.processed_root", str(ctx.exception))

    def test_error_message_names_the_missing_key(self):
        from ingestor import load_config
        cfg = self._minimal_valid_config()
        del cfg["extraction"]["confidence_threshold"]
        p = self._write(cfg)
        with self.assertRaises(ValueError) as ctx:
            load_config(p)
        self.assertIn("extraction.confidence_threshold", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
