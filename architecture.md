# Notes‑to‑TeX — Architecture (v0.2)

> Technical document. Describes the project structure, modules, data formats, API, processing pipeline, and current limitations.
> Intended for developers and AI assistants involved in maintaining and extending the codebase.

---

## 1. Project Structure

```
notes-to-tex/
│
├── backend/
│   ├── app.py                # FastAPI entry point, endpoints /process, /download, /health
│   ├── gemini_client.py      # Gemini 2.5 Pro client — interaction with the AI model
│   ├── utils/                # Validators, post-processing, helper utilities
│   ├── prompts/              # Text templates for compose / edit stages
│   ├── latex/
│   │   ├── main-template.tex # Main LaTeX template (minor changes allowed)
│   │   └── notes-core.sty    # Core style (frozen)
│   └── tests/                # Test suite (12 baseline scenarios)
│
├── input/                    # User-uploaded files (PDF, JPG)
├── output/                   # Resulting .zip archives with LaTeX content
├── figures/                  # Extracted images (generated automatically)
├── docs/                     # Technical and product documentation
└── configs/                  # (planned) YAML/ENV files for parameters
```

---

## 2. Processing Pipeline

```
[Input PDF/JPG]
   ↓
Language Detection
   ↓
Gemini Compose → JSON (content draft)
   ↓
Gemini Editor → content.tex + comments
   ↓
Validation (file check, LaTeX syntax, image links)
   ↓
Packaging → output.zip (LaTeX files + meta + logs)
```

- One file is processed per run.
- Intermediate artifacts are stored in `/output/` and cleaned up later via cron/maintenance (planned).
- Multi-file upload/concurrency support (planned).

---

## 3. File Formats

### 3.1. meta.json
```json
{
  "language": "en",
  "pages": 14,
  "mode": "book",
  "figures": 5,
  "timestamp": "2025-10-18T12:30:00Z"
}
```

### 3.2. editor_decision.json
```json
{
  "baseline_lang": "en",
  "issues_detected": ["typo", "missing_figure_link"],
  "suggestions": ["improve verbatim accuracy"],
  "verbatim_score": 0.97
}
```

### 3.3. editor_raw.txt
Raw editor logs (model response).

### 3.4. model_raw_*.txt
Gemini 2.5 Pro output (initial stage).

### 3.5. content.tex
Main LaTeX content, **without** preamble.

### 3.6. main-template.tex / notes-core.sty
LaTeX template and core style.

---

## 4. API and Endpoints

### Current Endpoints
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/process` | Process a single file. Query params: `mode`, `use_editor`, `compile_pdf`. |
| `GET`  | `/download/{job_id}` | Download the archive. |
| `GET`  | `/health` | Health check. |

### Potential (planned)
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/validate` | Run artifact checks and content validation. *(planned)* |
| `GET`  | `/preview/{job_id}` | Preview LaTeX result without zipping. *(planned)* |
| `GET`  | `/metrics` | Return quality metrics (verbatim, OCR). *(planned)* |

---

## 5. Validators

- Archive structure check (`content.tex`, `meta.json`, `editor_decision.json` must exist).  
- LaTeX syntax check (errors, missing environments).  
- Image/link consistency check (`figures/` ↔ `\includegraphics`).  
- Verbatim accuracy and text loss checks.  
- Constraints check (`max_pages=100`, valid extensions).  

---

## 6. Image Extraction

**Current status:** not implemented.  
**Planned criteria:**
- Minimum bbox area ≥ 1.5% of page.  
- Bbox aspect ratio ∈ [0.2; 5.0].  
- Padding 2–4%.  
- Minimum 256×256 px.  
- Auto-cropping and saving into `figures/` with a caption.

---

## 7. Limitations and Assumptions

| Category | Current State |
|----------|---------------|
| Supported languages | English, Russian |
| Max pages | 100 |
| Concurrency | none |
| Frontend | none |
| Autotests | manual run (pytest) |
| OCR corrections | none |
| Image extraction | in progress |
| Result verification | manual |

---

## 8. Future Changes (roadmap snippet)

- Support for multi-file, concurrent uploads.  
- Automatic test runs during build.  
- New endpoints `/validate`, `/preview`, `/metrics`.  
- YAML configurations (`configs/settings.yaml`).  
- Extending language set (via external config).  

---

## 9. System Dependencies

| Component | Version |
|-----------|---------|
| Python | 3.11+ |
| FastAPI | ≥0.110 |
| Uvicorn | ≥0.30 |
| Gemini API | 2.5 Pro |
| LaTeX | TeXLive 2023 |
| Pillow | ≥10.0 (images) |

---

## 10. CI/CD and Deployment

**Planned:**  
- Auto-build on push to `main`.  
- Validation & tests via GitHub Actions.  
- Hosting on a rented server (Ubuntu + Uvicorn).  

---

*English version*
