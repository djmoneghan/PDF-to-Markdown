# CLAUDE.md — The Kinetic Ingestor
*Contract file for Claude Code. Read this fully before writing any code.*

---

## Project Purpose

Build a Python CLI tool that ingests PDF documents and converts them into semantically chunked, metadata-enriched Markdown files suitable for RAG (Retrieval-Augmented Generation) pipelines. The primary domain is nuclear engineering and regulatory documentation, where structural fidelity and formula preservation are non-negotiable.

This tool is called **The Kinetic Ingestor**.

---

## Environment: NVIDIA DGX Spark

| Property | Value |
|---|---|
| OS | DGX OS (Ubuntu 24.04) |
| Architecture | **x86_64** (not ARM — Blackwell GPU, standard CUDA stack) |
| Python | 3.11+ (system or venv) |
| GPU | NVIDIA Blackwell (GB10), CUDA 12.x |
| RAM | 128 GB unified memory |
| Inference server | Ollama (primary); vLLM path available |
| LLM for metadata | Ollama-hosted model via local API (`http://localhost:11434`) |

**Critical constraint:** All inference calls must target the local Ollama endpoint. No external API calls (OpenAI, Anthropic, etc.) are permitted in production paths. The metadata generation LLM is treated as a local resource.

---

## Tech Stack

### PDF Extraction
- **Primary:** `Docling` — preferred for DGX Spark (x86_64, CUDA-compatible, better table pipeline than Marker on this arch)
- **Fallback:** `PyMuPDF` (`fitz`) for simple text-only extraction when Docling is unavailable
- **Do not use:** `PyPDF2`, `pdfminer` as primary extractors — these do not preserve layout

### Terminal UI
- **`Rich`** — for chunk/YAML side-by-side display and status rendering
- **`Prompt Toolkit`** — for keystroke-driven Accept / Edit / Flag interaction
- Do not use `Inquirer` — its readline dependency behaves poorly under Rich's live display

### Metadata Generation
- Ollama Python client (`ollama` package) against `http://localhost:11434`
- Default model: `qwen3-30b-a3b` (fast, good instruction following)
- Fallback model: configurable via `config.yaml`

### Other Dependencies
- `pyyaml` — YAML serialization
- `uuid` — Source ID generation
- `json` — manifest and corrections file I/O
- `pathlib` — all file path operations (no raw string concatenation)
- `rich` — terminal rendering
- `prompt_toolkit` — HITL input handling
- `ollama` — local LLM client

---

## Project Structure

```
kinetic-ingestor/
├── CLAUDE.md                  ← this file
├── REQUIREMENTS.md            ← full feature spec with acceptance criteria
├── config.yaml                ← runtime configuration (model, paths, thresholds)
├── ingestor/
│   ├── __init__.py
│   ├── extractor.py           ← PDF → structured content (Docling wrapper)
│   ├── chunker.py             ← header-based semantic splitting
│   ├── metadata.py            ← YAML generation via local LLM
│   ├── hitl.py                ← Rich + Prompt Toolkit review interface
│   ├── exporter.py            ← .md file writer + manifest generator
│   └── corrections.py        ← corrections.json read/write
├── processed/                 ← output root (gitignored)
│   └── {project_name}/
│       ├── chunk_001.md
│       ├── chunk_002.md
│       └── ...
├── manifest.json              ← generated per run, maps chunks to source pages
├── corrections.json           ← persistent HITL learning record
├── tests/
│   ├── test_extractor.py
│   ├── test_chunker.py
│   └── test_metadata.py
└── main.py                    ← CLI entry point
```

---

## Behavioral Rules for Claude Code

These rules govern how you write and modify code in this project. They are not suggestions.

### 1. Never silently degrade
If a pipeline stage fails or produces low-confidence output, **raise an explicit exception with a descriptive message**. Do not fall back to a degraded output without logging a visible warning. Examples:
- Table detection fails → `raise ExtractionWarning("Table at page N could not be parsed to GFM. Manual review required.")` and log it; do not emit raw text silently.
- Ollama unreachable → raise immediately with connection details; do not substitute placeholder metadata.

### 2. Flag, don't fix, on ambiguity
If a requirement is ambiguous or a design decision has multiple valid implementations with meaningfully different tradeoffs, **stop and flag it** with a comment block marked `# DECISION REQUIRED:`. Do not choose silently. Example:
```python
# DECISION REQUIRED: corrections.json is currently append-only.
# If the same chunk_id is corrected twice, both records are preserved.
# Alternative: overwrite on matching chunk_id. Confirm before implementing.
```

### 3. Preserve all LaTeX exactly
Mathematical and formula content must be preserved verbatim. Wrap in `$$...$$` blocks. Do not attempt to "clean" or reformat LaTeX expressions. If extraction confidence for a formula block is below the configured threshold, flag it for HITL review — do not suppress it.

### 4. Secrets and config never in code
API endpoints, model names, paths, and thresholds live in `config.yaml`. Code reads from config. No hardcoded strings for these values (except the config file path itself).

### 5. Pathlib everywhere
All file path construction uses `pathlib.Path`. No `os.path.join`, no f-string path concatenation.

### 6. Tests before features
Before implementing any new module, write the test scaffold first (even if empty `assert True` stubs). This ensures the test file exists and the structure is validated before logic is added.

### 7. Corrections.json is append-only by default
New correction records are appended, never overwritten, unless `--overwrite-corrections` flag is explicitly passed. This preserves the full human correction history for future prompt fine-tuning.

---

## Config Schema (`config.yaml`)

```yaml
ollama:
  endpoint: "http://localhost:11434"
  model: "gpt-oss:120b"
  fallback_model: "gpt-oss:20b"
  timeout_seconds: 120

extraction:
  engine: "docling"          # or "pymupdf"
  confidence_threshold: 0.75 # below this, chunk is auto-flagged for HITL

chunking:
  split_levels: ["#", "##"]  # header levels that trigger a new chunk
  min_chunk_tokens: 100
  max_chunk_tokens: 1500

output:
  processed_root: "processed"
  manifest_filename: "manifest.json"
  corrections_filename: "corrections.json"

hitl:
  auto_accept_above: 0.92    # confidence score above which HITL is skipped
  show_raw_markdown: true
```

---

## YAML Frontmatter Schema (every chunk)

Every output `.md` file must begin with a valid YAML block conforming exactly to this schema. Missing fields are a hard failure — do not emit a chunk without complete metadata.

```yaml
---
source_id: "uuid-v4-string"
source_file: "original_filename.pdf"
chunk_id: "chunk_001"
page_range: [3, 5]
breadcrumb: "Section 2 > Subsection 2.1 > Table of Isotope Yields"
parent_header: "Subsection 2.1"
topic_category: "Nuclear Fuel Cycle"       # controlled vocabulary — see REQUIREMENTS.md
technical_level: "Specialist"             # Executive | Specialist | PhD
summary: "One to two sentence LLM-generated description of this chunk's content."
confidence_score: 0.87                    # float 0.0–1.0, LLM self-assessed
extraction_engine: "docling"
hitl_status: "accepted"                   # accepted | edited | flagged
corrections_ref: null                     # or corrections.json record ID if edited
---
```

---

## Out of Scope (v1)

Do not implement the following in this build phase:

- Vector embedding or database ingestion (this tool stops at `.md` + manifest)
- Fine-tuning pipeline consuming `corrections.json` (deferred to v2)
- Web UI or non-terminal interface
- Batch parallelism / async processing (single-document sequential pipeline only)
- Windows compatibility

---

*Last updated: 2026-03-10*
*Environment: DGX Spark / Ubuntu 24.04 / x86_64*
