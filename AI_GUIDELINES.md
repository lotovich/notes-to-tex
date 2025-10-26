# AI_GUIDELINES.md — Rules for AI Contributions to Notes-to-TeX (v0.2)

> This document is intended for AI models (ChatGPT / Claude and others) that will assist in the development of the project.
> Follow the rules below. If something is unclear, ask first, then propose changes.

---

## 0) Mission and Scope
- **Mission:** to build a “book-style” version of notes from PDFs/images into a compilable LaTeX (`content.tex`) with maximum preservation of meaning and structure.
- **Do not:** invent content, rewrite definitions/theorems, or change the public API without explicit permission.

---

## 1) What Can / Cannot Be Changed

### 1.1. Forbidden Without Explicit Approval
- `latex/notes-core.sty` — **frozen** (must not be modified).
- Public HTTP API endpoints/signatures — **frozen**. Any modification requires an ADR and a separate issue.
  - “Frozen” means: you cannot rename/remove paths or change parameter/response formats.
- The semantics of `prompts/composer.md` and `prompts/editor.md` (strict transcription / no loss) may only be modified after discussion.

### 1.2. Allowed
- `backend/app.py`, `backend/gemini_client.py`, `backend/utils/*`, `backend/prompts/*` (within principles), `backend/latex/main-template.tex` (only **minor** fixes).
- Tests, validators, post-processing, logs, configs (without secrets).
- Documentation in `/docs` (if any).

---

## 2) AI Operating Modes
- `mode=book` — allows mild readability improvements (punctuation, line breaks, lists, unified environments, merging consecutive display-math blocks), **but cannot replace terminology or alter definitions/formulas**.
- `mode=strict` (verbatim) — maximum fidelity copying.
  - **Similarity thresholds (confirmed):** ≥95% at sentence level **and** ≥98% at character level after normalization of spaces/LaTeX commands.
  - If thresholds are not met, propose changes as a patch with a report and reasoning.

---

## 3) Standard Workflow (for Any Task)
1. **Understand** the task: briefly outline assumptions and goals in 3–7 bullets.
2. **Plan:** list files, operations, and expected results.
3. **Patch:** make minimal targeted edits according to the plan.
4. **Tests/Validation:** run checks (see §5) and collect a report.
5. **Report:** format the result in PR style (see §4).

> If clarification is required at any step — stop and ask first.

---

## 4) Result Format (PR Style)
- **Title:** `feat|fix|refactor(scope): short summary` (Conventional Commits).
- **Context:** why the change was made; what was wrong; links to issue/ADR (if any).
- **Changes:** list of modified files, short “before → after” summary.
- **Tests/Validation:** commands used, status (pass/fail), summary of metrics.
- **Risks/Rollback:** potential risks and how to revert.

---

## 5) Tests and Validators — How to Run and Interpret Results

### 5.1. Basic Method (if test scripts exist)
- Run existing test scripts and check **process exit code:** `0 = OK`, `≠0 = FAIL`.
- Example commands (if available):
  - `python backend/run_folder_tests.py`
  - `pytest -q`
- Attach in the report: command, short output, and final result (`OK/FAIL`).

### 5.2. If No Test Scripts Exist
- Evaluate success by **artifacts and invariants** (checklist):
  1) Archive contains: `content.tex`, `meta.json`, `editor_decision.json`;
  2) `content.tex` has **no preamble** (`\documentclass`, `\begin{document}`, `\end{document}`);
  3) For `mode=strict` — verbatim metric ≥95%/98% (see §2) if the source is available;
  4) No forbidden environments (`\chapter{}` etc.) — must be converted to `\section{}`;
  5) If there are `figure` blocks — files in `figures/` exist and are referenced in the text;
  6) No keys/secrets appear in code or logs.
- In the report, list which items passed/failed.

### 5.3. If Validation/Tests Fail
- Return status **FAIL** + a short fix plan. Do not mask or ignore errors.

> In the future, a separate “AI tester” may appear. For now, rely on exit codes and/or the checklist above.

---

## 6) API Contracts (Frozen)
- `POST /process`
  - **query:** `mode={"book"|"strict"}`, `use_editor=bool` (default: true), `compile_pdf=bool` (default: false)
  - **body:** `file` — `UploadFile` (single `.pdf` or `.jpg/.jpeg/.png`)
  - Internal PDF render parameters: `dpi=220`, `max_pages=100` (product policy limit: **100 pages**).
- `GET /download/{job_id}` — download zip from `jobs/`.
- `GET /health` — health check.

**Do not** change endpoint paths, parameter names, or request/response formats without ADR.

---

## 7) Figures (Image Extraction) — Criteria v1 (Confirmed)
- **Minimum bbox size:** ≥ **1.5%** of page area.
- **Aspect ratio of bbox:** **[0.2, 5.0]**.
- **Padding:** add **2–4%** margin.
- **Crop resolution:** ≥ **256×256 px** (if input allows).
- **Text density:** low text density is acceptable (heuristic).
- **Overlap:** if two bboxes overlap > **30%**, take the larger or merge.
- **Caption/link:** if a `figure` block exists — must have `\caption{...}` and a valid reference to the file in `figures/`.
- **Report:** number of figures, bbox sizes, and suspicious cases.

Threshold values can be tuned later when configs appear.

---

## 8) Configuration and Secrets
- All secrets/keys — only through `.env` (never in code or repo).
- Modifying `settings.py` and `*.example` config files is allowed **once they appear** (in future) for adding parameters (`MAX_PAGES`, `DPI`, figure thresholds, mode flags). Do **not** create them manually now.
- Artifact names and structure are **stable**:
  - `content.tex`, `meta.json`, `editor_decision.json`, `editor_raw.txt`,
  - `model_raw_*.txt`, `figures/`, `notes-core.sty`, `main-template.tex`.

---

## 9) Code Style, Commits, and Documentation
- Code style: **black/ruff/isort** (default; propose configs if needed).
- Commit format: `feat|fix|refactor(scope): message` (Conventional Commits).
- Documentation of changes — concise, include before/after examples when appropriate.

---

## 10) Large Changes
- Split large edits into small atomic PRs (1 PR = 1 task).
- Do not mix refactoring and functional changes unless absolutely necessary.

---

## 11) Questions / Clarifications
If a rule does not cover your case — **ask first**.
Any change to public contracts (style, endpoints, formats) must go through ADR and approval.
