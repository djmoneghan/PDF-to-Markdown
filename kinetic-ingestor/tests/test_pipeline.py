# tests/test_pipeline.py
# Tests for ingestor/pipeline.py (PipelineOrchestrator).

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestPipelineOrchestrator(unittest.TestCase):
    """Tests for the PipelineOrchestrator class."""

    def test_orchestrator_importable(self):
        from ingestor.pipeline import PipelineOrchestrator
        self.assertTrue(callable(PipelineOrchestrator))

    def test_orchestrator_instantiate_with_no_args(self):
        from ingestor.pipeline import PipelineOrchestrator
        orch = PipelineOrchestrator()
        self.assertIsNotNone(orch)

    def test_orchestrator_accepts_callbacks(self):
        from ingestor.pipeline import PipelineOrchestrator
        
        mock_progress = MagicMock()
        mock_chunk_ready = MagicMock()
        mock_complete = MagicMock()
        mock_error = MagicMock()
        
        orch = PipelineOrchestrator(
            on_progress=mock_progress,
            on_chunk_ready=mock_chunk_ready,
            on_complete=mock_complete,
            on_error=mock_error,
        )
        self.assertEqual(orch.on_progress, mock_progress)
        self.assertEqual(orch.on_chunk_ready, mock_chunk_ready)
        self.assertEqual(orch.on_complete, mock_complete)
        self.assertEqual(orch.on_error, mock_error)

    def test_orchestrator_has_run_method(self):
        from ingestor.pipeline import PipelineOrchestrator
        orch = PipelineOrchestrator()
        self.assertTrue(callable(orch.run))

    def test_hitl_backend_interface_importable(self):
        from ingestor.hitl_base import HitlReviewBackend
        self.assertTrue(hasattr(HitlReviewBackend, 'review'))

    def test_cli_hitl_review_implements_interface(self):
        from ingestor.hitl import CliHitlReview
        from ingestor.hitl_base import HitlReviewBackend
        self.assertTrue(issubclass(CliHitlReview, HitlReviewBackend))

    def test_cli_hitl_review_instantiate(self):
        from ingestor.hitl import CliHitlReview
        backend = CliHitlReview()
        self.assertIsNotNone(backend)

    def test_cli_hitl_review_has_review_method(self):
        from ingestor.hitl import CliHitlReview
        backend = CliHitlReview()
        self.assertTrue(callable(backend.review))


class TestPipelineCallbacks(unittest.TestCase):
    """Tests that callbacks are invoked during pipeline execution (with mocks)."""

    def test_callbacks_invoked_on_success(self):
        """Mock entire pipeline path to verify callbacks are called."""
        from ingestor.pipeline import PipelineOrchestrator
        from unittest.mock import patch, MagicMock, call
        
        mock_progress = MagicMock()
        mock_complete = MagicMock()
        mock_error = MagicMock()
        
        orch = PipelineOrchestrator(
            on_progress=mock_progress,
            on_complete=mock_complete,
            on_error=mock_error,
        )

        # Mock all pipeline stages to avoid actual processing
        mock_config = {
            "ollama": {
                "endpoint": "http://localhost:11434",
                "model": "test",
                "timeout_seconds": 60,
            },
            "extraction": {
                "engine": "docling",
                "confidence_threshold": 0.75,
            },
            "chunking": {
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
        
        mock_doc = MagicMock()
        mock_doc.pages = [(1, "test")]
        mock_doc.tables = []
        mock_doc.formula_blocks = []
        
        mock_chunk = MagicMock()
        mock_chunk.chunk_id = "chunk_001"
        mock_chunk.content = "test content"
        mock_chunk.confidence_score = 0.95
        mock_chunk.metadata = {}
        
        mock_output_dir = Path("processed/test")
        
        with patch('ingestor.pipeline.load_config') as mock_load_config, \
             patch('ingestor.pipeline.extract') as mock_extract, \
             patch('ingestor.pipeline.chunk_doc') as mock_chunk_doc, \
             patch('ingestor.pipeline.generate_metadata') as mock_gen_meta, \
             patch('ingestor.pipeline.export_chunks') as mock_export, \
             patch('ingestor.hitl.CliHitlReview.review') as mock_hitl_review:
            
            mock_load_config.return_value = mock_config
            mock_extract.return_value = mock_doc
            mock_chunk_doc.return_value = [mock_chunk]
            mock_gen_meta.return_value = {"confidence_score": 0.95}
            mock_hitl_review.return_value = [mock_chunk]
            mock_export.return_value = mock_output_dir
            
            result = orch.run(
                pdf_path="test.pdf",
                config_path="config.yaml",
            )
            
            # Verify callbacks were called
            self.assertTrue(mock_progress.called, "on_progress should be called")
            self.assertTrue(mock_complete.called, "on_complete should be called")
            self.assertFalse(mock_error.called, "on_error should not be called on success")
            self.assertEqual(result, mock_output_dir)


if __name__ == "__main__":
    unittest.main()
