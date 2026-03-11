# tests/test_metadata.py
# Tests for ingestor/metadata.py
# AC references map to REQUIREMENTS.md Feature 3.

import unittest
import urllib.error
from unittest.mock import MagicMock, call, patch

import ingestor.metadata as metadata_module
from ingestor import Chunk
from ingestor.metadata import (
    TECHNICAL_LEVELS,
    TOPIC_CATEGORIES,
    MetadataGenerationError,
    generate_metadata,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_CONFIG = {
    "ollama": {
        "endpoint": "http://localhost:11434",
        "model": "test-model",
        "fallback_model": "test-fallback",
        "timeout_seconds": 30,
    },
    "extraction": {"engine": "docling", "confidence_threshold": 0.75},
    "chunking": {"split_levels": ["#", "##"], "min_chunk_tokens": 10, "max_chunk_tokens": 500},
    "output": {
        "processed_root": "processed",
        "manifest_filename": "manifest.json",
        "corrections_filename": "corrections.json",
    },
    "hitl": {"auto_accept_above": 0.92, "show_raw_markdown": True},
}

_ENDPOINT = "http://localhost:11434"


def _make_chunk(**kwargs):
    defaults = dict(
        chunk_id="chunk_001",
        content="This is test content about reactor design and safety.",
        page_range=[1, 2],
        breadcrumb="Section 1",
        parent_header="Section 1",
        source_file="test.pdf",
        source_id="test-uuid-1234",
        extraction_engine="docling",
        hitl_status="pending",
    )
    defaults.update(kwargs)
    return Chunk(**defaults)


class _HealthBypassMixin:
    """Pre-seed the endpoint cache so health-check is skipped for these tests."""

    def setUp(self):
        metadata_module._checked_endpoints.add(_ENDPOINT)

    def tearDown(self):
        metadata_module._checked_endpoints.discard(_ENDPOINT)


class _HealthRequiredMixin:
    """Clear the endpoint cache so the health-check path runs for these tests."""

    def setUp(self):
        metadata_module._checked_endpoints.discard(_ENDPOINT)

    def tearDown(self):
        metadata_module._checked_endpoints.discard(_ENDPOINT)


# ---------------------------------------------------------------------------
# Import tests
# ---------------------------------------------------------------------------

class TestMetadataImports(unittest.TestCase):

    def test_module_importable(self):
        import ingestor.metadata  # noqa: F401

    def test_generate_metadata_function_exists(self):
        self.assertTrue(callable(generate_metadata))

    def test_metadata_generation_error_exists(self):
        self.assertTrue(issubclass(MetadataGenerationError, Exception))


# ---------------------------------------------------------------------------
# AC-3.1 — Ollama connectivity check
# ---------------------------------------------------------------------------

class TestAC31_OllamaConnectivityCheck(_HealthRequiredMixin, unittest.TestCase):

    def _ok_resp(self):
        resp = MagicMock()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        resp.read.return_value = b'{"models": []}'
        return resp

    def test_health_check_called_on_first_invocation(self):
        with patch("urllib.request.urlopen", return_value=self._ok_resp()) as mock_urlopen:
            with patch("ingestor.metadata._call_ollama",
                       side_effect=["Summary.", "0.85", "Reactor Design", "Specialist"]):
                generate_metadata(_make_chunk(), _BASE_CONFIG)
        mock_urlopen.assert_called_once()
        req_arg = mock_urlopen.call_args[0][0]
        self.assertIn("api/tags", req_arg.full_url)

    def test_connection_error_raised_when_ollama_unreachable(self):
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("Connection refused")):
            with self.assertRaises(ConnectionError):
                generate_metadata(_make_chunk(), _BASE_CONFIG)

    def test_connection_error_message_contains_endpoint_url(self):
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("refused")):
            try:
                generate_metadata(_make_chunk(), _BASE_CONFIG)
                self.fail("Expected ConnectionError")
            except ConnectionError as exc:
                self.assertIn("localhost:11434", str(exc))

    def test_no_metadata_generated_when_health_check_fails(self):
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("refused")):
            with patch("ingestor.metadata._call_ollama") as mock_call:
                try:
                    generate_metadata(_make_chunk(), _BASE_CONFIG)
                except ConnectionError:
                    pass
        mock_call.assert_not_called()


# ---------------------------------------------------------------------------
# AC-3.2 — Summary generation
# ---------------------------------------------------------------------------

class TestAC32_SummaryGeneration(_HealthBypassMixin, unittest.TestCase):

    def test_summary_is_a_non_empty_string(self):
        with patch("ingestor.metadata._call_ollama",
                   side_effect=["A concise summary sentence.", "0.9", "Reactor Design", "Specialist"]):
            meta = generate_metadata(_make_chunk(), _BASE_CONFIG)
        self.assertIsInstance(meta["summary"], str)
        self.assertTrue(meta["summary"])

    def test_summary_whitespace_trimmed(self):
        with patch("ingestor.metadata._call_ollama",
                   side_effect=["  Leading and trailing whitespace.  ", "0.9", "Reactor Design", "Specialist"]):
            meta = generate_metadata(_make_chunk(), _BASE_CONFIG)
        self.assertEqual(meta["summary"], "Leading and trailing whitespace.")

    def test_summary_over_300_chars_truncated_at_sentence_boundary(self):
        # period at position 250 is within the first 300 chars
        long_summary = "A" * 250 + ". Extra text that pushes this well beyond three hundred characters."
        with patch("ingestor.metadata._call_ollama",
                   side_effect=[long_summary, "0.9", "Reactor Design", "Specialist"]):
            meta = generate_metadata(_make_chunk(), _BASE_CONFIG)
        self.assertLessEqual(len(meta["summary"]), 300)
        self.assertTrue(meta["summary"].endswith("."))

    def test_summary_truncation_logs_warning(self):
        long_summary = "word " * 80   # ~400 chars, no period
        with patch("ingestor.metadata._call_ollama",
                   side_effect=[long_summary, "0.9", "Reactor Design", "Specialist"]):
            with self.assertLogs("ingestor.metadata", level="WARNING") as cm:
                generate_metadata(_make_chunk(), _BASE_CONFIG)
        self.assertTrue(
            any("truncated" in msg.lower() or "summary" in msg.lower() for msg in cm.output)
        )


# ---------------------------------------------------------------------------
# AC-3.3 — Confidence score
# ---------------------------------------------------------------------------

class TestAC33_ConfidenceScore(_HealthBypassMixin, unittest.TestCase):

    def test_confidence_score_is_float(self):
        with patch("ingestor.metadata._call_ollama",
                   side_effect=["Summary.", "0.87", "Reactor Design", "Specialist"]):
            meta = generate_metadata(_make_chunk(), _BASE_CONFIG)
        self.assertIsInstance(meta["confidence_score"], float)
        self.assertAlmostEqual(meta["confidence_score"], 0.87, places=2)

    def test_confidence_score_in_range(self):
        with patch("ingestor.metadata._call_ollama",
                   side_effect=["Summary.", "0.95", "Reactor Design", "Specialist"]):
            meta = generate_metadata(_make_chunk(), _BASE_CONFIG)
        self.assertGreaterEqual(meta["confidence_score"], 0.0)
        self.assertLessEqual(meta["confidence_score"], 1.0)

    def test_unparseable_response_defaults_to_0_5(self):
        with patch("ingestor.metadata._call_ollama",
                   side_effect=["Summary.", "not a number at all", "Reactor Design", "Specialist"]):
            with self.assertLogs("ingestor.metadata", level="WARNING"):
                meta = generate_metadata(_make_chunk(), _BASE_CONFIG)
        self.assertAlmostEqual(meta["confidence_score"], 0.5)

    def test_parse_failure_logs_warning(self):
        with patch("ingestor.metadata._call_ollama",
                   side_effect=["Summary.", "garbage response", "Reactor Design", "Specialist"]):
            with self.assertLogs("ingestor.metadata", level="WARNING") as cm:
                generate_metadata(_make_chunk(), _BASE_CONFIG)
        self.assertTrue(
            any("confidence" in msg.lower() or "parse" in msg.lower() for msg in cm.output)
        )


# ---------------------------------------------------------------------------
# AC-3.4 — Topic category (controlled vocabulary)
# ---------------------------------------------------------------------------

class TestAC34_TopicCategory(_HealthBypassMixin, unittest.TestCase):

    def test_valid_category_accepted(self):
        with patch("ingestor.metadata._call_ollama",
                   side_effect=["Summary.", "0.9", "Reactor Design", "Specialist"]):
            meta = generate_metadata(_make_chunk(), _BASE_CONFIG)
        self.assertEqual(meta["topic_category"], "Reactor Design")

    def test_invalid_category_defaults_to_general_reference(self):
        with patch("ingestor.metadata._call_ollama",
                   side_effect=["Summary.", "0.9", "Alien Technology", "Specialist"]):
            with self.assertLogs("ingestor.metadata", level="WARNING"):
                meta = generate_metadata(_make_chunk(), _BASE_CONFIG)
        self.assertEqual(meta["topic_category"], "General Reference")

    def test_invalid_category_logs_warning(self):
        with patch("ingestor.metadata._call_ollama",
                   side_effect=["Summary.", "0.9", "Completely Made Up Category", "Specialist"]):
            with self.assertLogs("ingestor.metadata", level="WARNING") as cm:
                generate_metadata(_make_chunk(), _BASE_CONFIG)
        self.assertTrue(
            any("topic_category" in msg or "non-conforming" in msg.lower() for msg in cm.output)
        )

    def test_category_is_from_controlled_list(self):
        for category in TOPIC_CATEGORIES:
            with patch("ingestor.metadata._call_ollama",
                       side_effect=["Summary.", "0.9", category, "Specialist"]):
                meta = generate_metadata(_make_chunk(), _BASE_CONFIG)
            self.assertIn(meta["topic_category"], TOPIC_CATEGORIES)


# ---------------------------------------------------------------------------
# AC-3.5 — Technical level
# ---------------------------------------------------------------------------

class TestAC35_TechnicalLevel(_HealthBypassMixin, unittest.TestCase):

    def test_valid_level_accepted(self):
        with patch("ingestor.metadata._call_ollama",
                   side_effect=["Summary.", "0.9", "Reactor Design", "Specialist"]):
            meta = generate_metadata(_make_chunk(), _BASE_CONFIG)
        self.assertEqual(meta["technical_level"], "Specialist")

    def test_invalid_level_handled(self):
        with patch("ingestor.metadata._call_ollama",
                   side_effect=["Summary.", "0.9", "Reactor Design", "Intermediate"]):
            with self.assertLogs("ingestor.metadata", level="WARNING"):
                meta = generate_metadata(_make_chunk(), _BASE_CONFIG)
        self.assertIn(meta["technical_level"], TECHNICAL_LEVELS)

    def test_technical_level_in_valid_set(self):
        for level in TECHNICAL_LEVELS:
            with patch("ingestor.metadata._call_ollama",
                       side_effect=["Summary.", "0.9", "Reactor Design", level]):
                meta = generate_metadata(_make_chunk(), _BASE_CONFIG)
            self.assertIn(meta["technical_level"], TECHNICAL_LEVELS)


# ---------------------------------------------------------------------------
# AC-3.6 — Prompt isolation
# ---------------------------------------------------------------------------

class TestAC36_PromptIsolation(_HealthBypassMixin, unittest.TestCase):

    def test_four_separate_ollama_calls_made(self):
        with patch("ingestor.metadata._call_ollama",
                   side_effect=["Summary.", "0.9", "Reactor Design", "Specialist"]) as mock_call:
            generate_metadata(_make_chunk(), _BASE_CONFIG)
        self.assertEqual(mock_call.call_count, 4)

    def test_summary_call_does_not_include_confidence_prompt(self):
        with patch("ingestor.metadata._call_ollama",
                   side_effect=["Summary.", "0.9", "Reactor Design", "Specialist"]) as mock_call:
            generate_metadata(_make_chunk(), _BASE_CONFIG)
        # _call_ollama(prompt, model, endpoint, timeout_sec) — prompt is arg [0]
        summary_prompt    = mock_call.call_args_list[0][0][0]
        confidence_prompt = mock_call.call_args_list[1][0][0]
        self.assertNotEqual(summary_prompt, confidence_prompt)
        self.assertIn("summarise", summary_prompt.lower())
        self.assertIn("rate", confidence_prompt.lower())


# ---------------------------------------------------------------------------
# AC-3.7 — Retry on timeout
# ---------------------------------------------------------------------------

class TestAC37_RetryOnTimeout(_HealthBypassMixin, unittest.TestCase):

    def test_single_timeout_retried(self):
        timeout_exc = TimeoutError("timed out")
        with patch("ingestor.metadata._call_ollama",
                   side_effect=[timeout_exc, "Summary text.", "0.9", "Reactor Design", "Specialist"]):
            meta = generate_metadata(_make_chunk(), _BASE_CONFIG)
        self.assertEqual(meta["summary"], "Summary text.")

    def test_double_timeout_raises_metadata_generation_error(self):
        timeout_exc = TimeoutError("timed out")
        with patch("ingestor.metadata._call_ollama",
                   side_effect=[timeout_exc, timeout_exc]):
            with self.assertRaises(MetadataGenerationError):
                generate_metadata(_make_chunk(), _BASE_CONFIG)

    def test_error_contains_chunk_id_and_field_name(self):
        timeout_exc = TimeoutError("timed out")
        with patch("ingestor.metadata._call_ollama",
                   side_effect=[timeout_exc, timeout_exc]):
            try:
                generate_metadata(_make_chunk(), _BASE_CONFIG)
                self.fail("Expected MetadataGenerationError")
            except MetadataGenerationError as exc:
                msg = str(exc)
                self.assertIn("chunk_001", msg)
                self.assertIn("summary", msg)


# ---------------------------------------------------------------------------
# AC-3.8 — YAML schema compliance
# ---------------------------------------------------------------------------

class TestAC38_YAMLSchemaCompliance(_HealthBypassMixin, unittest.TestCase):

    REQUIRED_FIELDS = [
        "source_id", "source_file", "chunk_id", "page_range",
        "breadcrumb", "parent_header", "topic_category", "technical_level",
        "summary", "confidence_score", "extraction_engine",
        "hitl_status", "corrections_ref",
    ]

    def test_all_required_fields_present(self):
        with patch("ingestor.metadata._call_ollama",
                   side_effect=["Summary sentence.", "0.9", "Reactor Design", "Specialist"]):
            meta = generate_metadata(_make_chunk(), _BASE_CONFIG)
        for field in self.REQUIRED_FIELDS:
            self.assertIn(field, meta, f"Required field missing: {field}")

    def test_missing_field_raises_hard_failure(self):
        with patch("ingestor.metadata._validate_schema",
                   side_effect=ValueError("Missing required fields: ['summary']")):
            with patch("ingestor.metadata._call_ollama",
                       side_effect=["Summary.", "0.9", "Reactor Design", "Specialist"]):
                with self.assertRaises(ValueError):
                    generate_metadata(_make_chunk(), _BASE_CONFIG)

    def test_yaml_is_valid_serializable(self):
        import yaml
        with patch("ingestor.metadata._call_ollama",
                   side_effect=["Summary sentence.", "0.88", "Materials Science", "PhD"]):
            meta = generate_metadata(_make_chunk(), _BASE_CONFIG)
        yaml_str = yaml.safe_dump(meta)   # must not raise
        self.assertIsInstance(yaml_str, str)


if __name__ == "__main__":
    unittest.main()
