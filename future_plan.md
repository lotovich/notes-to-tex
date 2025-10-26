# Notes‑to‑TeX — Development Plan (v0.2)

> Project roadmap: goals, priorities, completion criteria, and long‑term directions.

---

## v0.2 — Pipeline Stabilization and Image Extraction

**Goals:**  
- eliminate text distortions (synonymization, OCR loss);  
- implement image extraction;  
- improve verbatim metric accuracy.

**Completion Criteria:**  
- ≥95% sentence‑level match;  
- ≥98% character‑level match;  
- correct `figures/` generation;  
- 12 tests passing consecutively.

---

## v0.3 — OCR Improvements and Text Cleanliness

**Goals:**  
- add OCR validation module;  
- improve noise/abbreviation filtering;  
- implement semi‑automatic content validation.

**Completion Criteria:**  
- no visual defects >5%;  
- valid LaTeX for all tests;  
- automatic quality report.

---

## v0.4 — Modes and Quality Improvements

**Goals:**  
- introduce `book` and `strict` modes;  
- implement enhanced verbatim analysis;  
- add a post‑editor for lists, formulas, and examples formatting.

**Completion Criteria:**  
- modes work correctly on ≥90% of tests;  
- formula formatting accuracy ≥98%;  
- user can pick a mode at upload time.

---

## v0.5 — Frontend and Public Beta

**Goals:**  
- lightweight web interface for upload, mode selection, and result preview;  
- error checking and preview integration;  
- prepare for public testing.

**Completion Criteria:**  
- stable backend with `/process`, `/validate`, `/preview` APIs;  
- error‑free UX in major browsers;  
- 50+ successful uploads in a row.

---

## Long‑term ideas

- Additional languages (first: Chinese, Korean).  
- Overleaf and VS Code plugin integration.  
- Automatic extraction of TikZ figures and diagrams.  
- Collaboration mode and “AI editing assistant”.  
- Export to EPUB / DOCX.  
- Quality review system (AI‑reviewer / human‑reviewer).  

---

*English version*
