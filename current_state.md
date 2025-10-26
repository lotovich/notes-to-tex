# Notes-to-TeX — Current State (v0.1)

## 1) Purpose
The system takes **a single input file** (`.pdf` or `.jpg/.jpeg/.png`) and builds a structured LaTeX document `content.tex` in a “book-like” format while preserving the content.  
The output is a **ZIP archive** containing the LaTeX source and related artifacts. By default, **PDF is not compiled** (optional via the `compile_pdf` flag).

**Runtime model:** Google **Gemini 2.5 Pro**  
**Architecture:** Two-stage pipeline — **Composer** (strict transcription → JSON blocks) and **Editor** (validation/refinement of LaTeX).

---

## 2) Current Stack and Structure
**Stack:** Python 3.11+, FastAPI, PyMuPDF (PDF→PNG), Google GenAI SDK (Gemini).  
**Development tools (not used in runtime):** ChatGPT 5, Claude Sonnet 4.5/Opus 4.1, VSCode Codex/Claude.

```
backend/
  app.py                  # FastAPI: handles upload, orchestration, ZIP packaging
  gemini_client.py        # model requests, response parsing, meta/body assembly
  prompts/
    composer.md           # strict transcription → JSON blocks
    editor.md             # editing/refinement → clean LaTeX
  utils/
    pdf.py                # PDF → list of PNGs
    postprocess.py        # LaTeX cleanup (chapter→section, align*, headers, Cyrillic in math)
    validators.py         # structure validation/finalization
  latex/
    main-template.tex
    notes-core.sty

golden/                   # reference inputs and metadata
results/, jobs/           # working/output directories
```

---

## 3) Data Flow
1) **Upload** a single file. If it's a PDF — pages are rendered to PNG (`pdf_to_images(..., dpi=220, max_pages=100)` in `app.py`).  
2) **Composer (Gemini):** creates **JSON** with structural blocks (`section | paragraph | equation | list | figure`, etc.) using strict transcription, without paraphrasing.  
3) **LaTeX Assembly:** builds `content.tex` from JSON (removing preamble; strips `\documentclass`, `\begin{document}`, `\end{document}`).  
4) **Editor (Gemini):** checks correctness/style/completeness, applies changes automatically (no user confirmation yet).  
5) **Post-processing:** normalization to `article` class, merging display equations into `align*`, fixing theorem environments, headers, and Cyrillic in math.  
6) **Validators:** structure counting and verification, noise filtering, finalization.  
7) **Packaging:** generates the ZIP output.

**Known limitations:** Possible **synonymization** (meaning shifts) in long texts; small detail loss; no multi-upload support; figure extraction still in development.

---

## 4) Output Archive Structure (Example)
```
content.tex
meta.json
editor_raw.txt
editor_decision.json
model_raw_gemini-2.5-pro_attempt1.txt
main-template.tex
notes-core.sty
figures/                 # if extracted/saved
```

---

## 5) Prompts and Model Behavior
- **Composer** (`prompts/composer.md`): “YOU ARE A TRANSCRIBER, NOT A WRITER.” Strict transcription with full preservation of math, proofs, and examples; no translation or paraphrasing; length ≥ 95% of the original.  
- **Editor** (`prompts/editor.md`): checks correctness, style (`article`), completeness; normalizes structure and environments.  
- Code uses regex-based parsing to extract LaTeX/JSON blocks and strip preambles or `\end{document}` markers.

**Note on “verbatim mode”:** This mode ensures **maximum content fidelity** (no stylistic improvements or word changes). Currently enabled via the `mode=strict` parameter (see endpoint). Post-processing does not alter meaning — it only normalizes LaTeX syntax.

---

## 6) Validators (Currently Implemented)
- Formula detection (`\[...\]`, `equation*?`, `align*?`) and consistency check with `meta`.  
- Counting of blocks/paragraphs/lists (`meta.blocks`).  
- Filtering of personal notes/noise.  
- `run_validators(...)` aggregates all checks; `finalize_content(...)` produces the final output.

**Planned Additions (near-term):**
- **Verbatim similarity metric** (sentence-level or character-level comparison with thresholds).  
- **Figure validation/reporting** (count, references, bounding box quality).  
- **HTML/Markdown validation report** for test outputs and metrics.

---

## 7) API Endpoints (from `backend/app.py`)

### `POST /process`
**Purpose:** Accepts a file, runs the pipeline, and returns the resulting ZIP archive (either directly or via a job ID).

**Query Parameters:**
- `mode: str = "book"` — output style:  
  - `"book"` — “book-like/enhanced” layout (Editor may slightly improve style within limits)  
  - `"strict"` — **verbatim mode** (maximally faithful to original, no paraphrasing).  
- `use_editor: bool = true` — whether to run the Editor stage.  
- `compile_pdf: bool = false` — whether to compile PDF (requires `latexmk` or `tectonic`).

**Request Body (multipart/form-data):**
- `file: UploadFile` — single input file (`.pdf` or `.jpg/.jpeg/.png`).

**Notes:**  
- Internal PDF rendering currently uses `dpi=220, max_pages=100`. For a 25-page limit, update `max_pages` or move it to config.

---

### `GET /download/{job_id}`
**Purpose:** Download a previously generated ZIP file by `job_id` (from the `jobs/` folder).

### `GET /health`
Simple health check endpoint.

---

## 8) Local Launch
```bash
uvicorn backend.app:app --reload
```
Endpoints are then available at:  
- `POST http://127.0.0.1:8000/process`  
- `GET  http://127.0.0.1:8000/download/{job_id}`  
- `GET  http://127.0.0.1:8000/health`

---

## 9) Limitations and Road‑to‑Fix
- **Synonymization / meaning drift** → add verbatim metrics and soft anti‑paraphrasing rules for Editor.  
- **Multi‑upload support** → extend endpoint (`files[]`) with page/size limits.  
- **Figure extraction** → implement heuristic module + validation + save to `figures/`.  
- **PDF compilation** → optional flag with compile error diagnostics.
