# Implementation Plan — The Kinetic Ingestor

*Derived from `CLAUDE.md` and `REQUIREMENTS.md`. Read alongside both spec files.*

---

## Build Order Overview

The repo currently contains only `CLAUDE.md` and `REQUIREMENTS.md` — no code exists yet.
Build order is driven by the **"tests before features"** rule in `CLAUDE.md`.

---

## Phase 0 — Project Scaffolding

Create the directory tree and stub files exactly as specified:

```
kinetic-ingestor/
├── config.yaml
├── main.py
├── ingestor/
│   ├── __init__.py
│   ├── extractor.py
│   ├── chunker.py
│   ├── metadata.py
│   ├── hitl.py
│   ├── exporter.py
│   └── corrections.py
└── tests/
    ├── test_extractor.py
    ├── test_chunker.py
    └── test_metadata.py
```

Also create a `.gitignore` covering `processed/`, `__pycache__/`, `.venv/`, and `*.pyc`.

---

## Phase 1 — Shared Data Contracts (`ingestor/__init__.py`)

Define the two core dataclasses everything else passes around:

- **`DocumentContent`** — output of the extractor: `text`, `headers` (list of `(level, text, page)`),
  `tables` (list of GFM strings + page), `images` (page + bbox), `formula_blocks`, `extraction_engine`.
- **`Chunk`** — output of the chunker: `chunk_id`, `content` (Markdown string), `page_range`,
  `breadcrumb`, `parent_header`, `metadata` (dict, filled later), `hitl_status`.

These are pure dataclasses with no logic — they form the contract between all modules.

---

## Phase 2 — Test Scaffolds (write first, per rule 6)

Write `tests/test_extractor.py`, `tests/test_chunker.py`, `tests/test_metadata.py` with:

- Import assertions (modules importable)
- Stub `assert True` placeholders for each acceptance criterion
- Named test functions matching each AC (e.g., `test_ac1_1_docling_primary_path`)

This ensures structure is validated before logic is added.

---

## Phase 3 — Config Loader

Write `config.yaml` with all defaults from `CLAUDE.md`. Write a `load_config()` function
(in `ingestor/__init__.py`) that reads and validates the YAML, raising `ValueError` for missing
required keys. All other modules call this rather than reading the file themselves.

---

## Phase 4 — `ingestor/extractor.py` (Feature 1)

Implement `extract(pdf_path, config) -> DocumentContent`:

| AC | Implementation |
|---|---|
| AC-1.1 | Try `import docling`; if `engine: pymupdf` in config or import fails, fall through to fallback |
| AC-1.2 | Post-process Docling tables → GFM; raise `ExtractionWarning` on parse failure |
| AC-1.3 | Detect formula blocks, wrap verbatim in `$$...$$` |
| AC-1.4 | Record image page + bbox; emit `<!-- IMAGE: page N, position bbox -->` placeholder |
| AC-1.5 | PyMuPDF path: `fitz.open()`, text only, log `WARNING`, set engine field |
| AC-1.6 | Validate path exists and is `.pdf` before any extraction |

Define `ExtractionWarning(Exception)` in this module.

---

## Phase 5 — `ingestor/chunker.py` (Feature 2)

Implement `chunk(doc: DocumentContent, config) -> list[Chunk]`:

| AC | Implementation |
|---|---|
| AC-2.1 | Split on configured `split_levels` headers (`#`, `##`) |
| AC-2.2 | Track header stack → build breadcrumb string and `parent_header` |
| AC-2.3 | Token count each candidate chunk; merge short ones (log DEBUG), split long ones at `\n\n`; warn on single-paragraph overflow |
| AC-2.4 | Detect `$$...$$` spans; never place a split inside one |
| AC-2.5 | Assign `chunk_001`, `chunk_002`, ... zero-padded to three digits |
| AC-2.6 | Track page numbers through the document → record `[start, end]` per chunk |

Token counting uses `len(text.split())` as a simple proxy (acceptable for v1; no tokenizer
dependency required).

---

## Phase 6 — `ingestor/metadata.py` (Feature 3)

Implement `generate_metadata(chunk: Chunk, config) -> dict`:

| AC | Implementation |
|---|---|
| AC-3.1 | `GET {endpoint}/api/tags` health check on first call; raise `ConnectionError` if down |
| AC-3.2 | Separate Ollama call → summary; trim whitespace; truncate at last sentence ≤ 300 chars with `WARNING` |
| AC-3.3 | Separate call → confidence float 0–1; default `0.5` + `WARNING` on parse failure |
| AC-3.4 | Separate call → topic_category from controlled vocab; default `"General Reference"` on non-match |
| AC-3.5 | Separate call → technical_level from `[Executive, Specialist, PhD]`; same enforcement |
| AC-3.6 | Four calls, never combined |
| AC-3.7 | Retry once on timeout; raise `MetadataGenerationError(chunk_id, field)` on second failure |
| AC-3.8 | Validate assembled YAML dict against schema before returning |

Define `MetadataGenerationError(Exception)` here.

---

## Phase 7 — `ingestor/hitl.py` (Feature 4)

Implement `run_review(chunks: list[Chunk], config) -> list[Chunk]`:

| AC | Implementation |
|---|---|
| AC-4.1 | `rich.layout.Layout` or `rich.columns.Columns` — left: `rich.markdown.Markdown(chunk.content)`, right: syntax-highlighted YAML |
| AC-4.2 | `prompt_toolkit.prompt()` with single-key validation; loop on invalid input |
| AC-4.3 | Check `confidence_score >= hitl.auto_accept_above`; skip UI, print `✓ Auto-accepted` line |
| AC-4.4 | Edit: inline `prompt_toolkit` buffer pre-filled with YAML; `Ctrl+D` to confirm; re-validate on submit |
| AC-4.5 | Flag: optional reason prompt; write to corrections; set `hitl_status: "flagged"` |
| AC-4.6 | `rich.progress.Progress` bar pinned at bottom: `Chunk N of M` |
| AC-4.7 | `try/except KeyboardInterrupt` → flush processed chunks; print session summary; exit clean |

---

## Phase 8 — `ingestor/exporter.py` + `ingestor/corrections.py` (Feature 5)

### `exporter.py` — `export(chunks, source_pdf_path, config, force=False)`

| AC | Implementation |
|---|---|
| AC-5.1 | Write to `.tmp`, then `Path.rename()` for atomicity; derive project_name from PDF stem |
| AC-5.2 | `---\n{yaml}\n---\n\n{content}` structure |
| AC-5.3 | Write `manifest.json` after all chunks; include ISO-8601 timestamp |
| AC-5.5 | Check for existing files; halt with list if `--force` not set |

### `corrections.py` — `append_correction(record, config)` and `load_corrections(config)`

| AC | Implementation |
|---|---|
| AC-5.4 | Read existing JSON array (or init `[]`); append new record with UUID + ISO timestamp; write back |

---

## Phase 9 — `main.py` (CLI Entry Point)

Wire all stages with `argparse`:

```
python main.py <pdf_path> [--project <name>] [--force] [--overwrite-corrections] [--debug]
```

Pipeline order per spec:

1. Load + validate `config.yaml`
2. `extract()` → `DocumentContent`
3. `chunk()` → `list[Chunk]`
4. `generate_metadata()` per chunk
5. `run_review()` HITL loop
6. `export()` + manifest

Wrap in `try/except`; print clean one-line error unless `--debug` is set; exit non-zero on failure.

---

## Phase 10 — Fill In Tests

Go back to test stubs and implement real assertions using `unittest.mock` to mock Ollama calls
and Docling/PyMuPDF so tests run without GPU or local LLM.

---

## Key Design Decisions

Two points in the spec have deliberate `# DECISION REQUIRED:` implications:

1. **`corrections.json` schema**: `REQUIREMENTS.md` shows `original_yaml` and `corrected_yaml` as
   nested objects; `CLAUDE.md` shows `corrections_ref` pointing to a record ID. The REQUIREMENTS
   schema (richer) will be implemented, and `record_id` stored as the `corrections_ref` value in
   chunk YAML — these are consistent, not contradictory.

2. **Token counting**: The spec says "tokens" but no tokenizer is specified. `len(text.split())`
   (word count) will be used as a fast, dependency-free approximation. Marked with a
   `# DECISION REQUIRED:` comment noting a proper tokenizer (e.g., `tiktoken`) can be swapped in.

---

*Last updated: 2026-03-10*
*Read alongside `CLAUDE.md` and `REQUIREMENTS.md`.*
