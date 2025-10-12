# backend/app.py
from fastapi import FastAPI, UploadFile, Request, Query
from fastapi.responses import FileResponse
from pathlib import Path
import os, shutil, uuid, zipfile, subprocess, re, logging
import fitz  # PyMuPDF
import json

from backend.gemini_client import compose_latex, editor_review
from backend.utils.postprocess import enforce_latex_conventions, fix_cyrillic_in_math, fix_dano_environment
from backend.utils.pdf import pdf_to_images
from backend.utils.validators import run_validators, finalize_content

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "latex"
JOBS_DIR = BASE_DIR.parent / "jobs"
JOBS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="notes-to-tex")

# Setup logging
logger = logging.getLogger(__name__)

# ---------- PDF helpers ----------
_INCLUDEGRAPHICS_RE = re.compile(r"\\includegraphics\s*\[", re.I)

def ensure_figure_blocks(latex_body: str, figures: list[dict]) -> str:
    if not figures:
        return latex_body
    if _INCLUDEGRAPHICS_RE.search(latex_body):
        return latex_body

    appended = "\n\n% === Auto-inserted figures ===\n"
    for f in figures:
        path = f.get("filename") or f.get("path")
        if not path:
            continue
        caption = (
            f"Auto-inserted figure from page {f.get('page')}"
            if f.get("page") else "Auto-inserted figure"
        )
        appended += (
            "\\begin{figure}[h]\n"
            "\\centering\n"
            f"\\includegraphics[width=0.8\\textwidth]{{{path}}}\n"
            f"\\caption{{{caption}}}\n"
            "\\end{figure}\n\n"
        )
    return latex_body + appended

def extract_text_blocks_pdf(pdf_path: Path) -> list[str]:
    blocks = []
    with fitz.open(pdf_path) as doc:
        for page in doc:
            text = page.get_text("text")
            if text and text.strip():
                blocks.append(text)
    return blocks

def extract_images_pdf(pdf_path: Path, out_dir: Path) -> list[dict]:
    figures_dir = out_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    infos: list[dict] = []
    with fitz.open(pdf_path) as doc:
        for pno, page in enumerate(doc, start=1):
            for i, img in enumerate(page.get_images(full=True), start=1):
                xref = img[0]
                try:
                    base = doc.extract_image(xref)
                    image_bytes = base["image"]
                    ext = (base.get("ext") or "png").lower()
                    # Нормализуем в PNG
                    if ext not in ("png",):
                        # fitz.Pixmap вариант — но у нас уже есть bytes
                        ext = "png"
                    fname = f"fig_p{pno}_i{i}.png"
                    fpath = figures_dir / fname
                    with open(fpath, "wb") as f:
                        f.write(image_bytes)
                    w = base.get("width")
                    h = base.get("height")
                    infos.append({
                        "filename": f"figures/{fname}",
                        "page": pno,
                        "w": w,
                        "h": h
                    })
                except Exception:
                    # пропускаем, если не удалось
                    continue
    return infos

def maybe_compile_pdf(job_dir: Path) -> bool:
    """
    Если latexmk установлен и ENABLE_LATEXMK=1, собираем PDF.
    """
    if os.getenv("ENABLE_LATEXMK", "0") != "1":
        return False
    if shutil.which("latexmk") is None:
        return False
    try:
        # Собираем в job_dir, output попадёт в build/ если есть latexmkrc
        subprocess.run(
            ["latexmk", "-pdf", "-interaction=nonstopmode", "main-template.tex"],
            cwd=job_dir, check=True
        )
        return True
    except Exception:
        return False

# ---------- Helpers to ensure TeX output ----------

def _try_parse_json_maybe_wrapped(payload) -> dict | None:
    """Try to parse JSON from a dict/str and also from strings like '```json\n{...}\n```' or 'json\n{...}'."""
    import json as _json, re as _re

    # Already a dict
    if isinstance(payload, dict):
        return payload
    # Not a string
    if not isinstance(payload, str):
        return None

    s = payload.strip()

    # 0) Strip fenced code blocks ```json ... ``` or ``` ... ``` and standalone 'json' prefix
    s = _re.sub(r'^\s*```(?:json)?\s*', '', s, flags=_re.IGNORECASE)
    s = _re.sub(r'\s*```\s*$', '', s)
    if s.lower().startswith('json'):
        i = s.find('{')
        if i != -1:
            s = s[i:]

    # 1) Direct parse
    try:
        obj = _json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # 2) Largest {...} slice
    i = s.find('{')
    j = s.rfind('}')
    if i != -1 and j != -1 and j > i:
        cand = s[i:j+1]
        try:
            obj = _json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except Exception:
            return None

    return None


def build_tex_from_capture(data: dict) -> str:
    """Turn structured capture JSON into a proper LaTeX note (section, text, equations, figures)."""
    headers = data.get("headers", {}) or {}
    title = headers.get("title") or "Untitled"
    subtitle = headers.get("subtitle") or ""

    text = data.get("latex") or data.get("normalized_capture") or data.get("raw_capture") or ""
    equations = data.get("equations_captured", []) or []
    figures = data.get("figures_captured", []) or []

    lines: list[str] = []
    # Title / subtitle
    if title:
        lines.append(f"\\section*{{{title}}}\n\n")
    if subtitle:
        lines.append(f"\\textit{{{subtitle}}}\\\n\n")

    if text:
        lines.append(text.strip() + "\n\n")

    # Equations
    for eq in equations:
        eq_tex = (eq.get("latex") or "").strip()
        if eq_tex:
            lines.append("\\[\n" + eq_tex + "\n\\]\n\n")

    # Figures (use filename/path if provided)
    for i, fig in enumerate(figures, 1):
        fpath = fig.get("filename") or fig.get("path") or f"figures/fig_{i}.png"
        cap = fig.get("caption") or f"Auto-inserted figure {i}"
        lines.append(
            "\\begin{figure}[h]\n"
            "\\centering\n"
            f"\\includegraphics[width=0.75\\textwidth]{{{fpath}}}\n"
            f"\\caption{{{cap}}}\n"
            "\\end{figure}\n\n"
        )

    return "".join(lines) or "% (empty)"

def build_tex_from_blocks(data: dict) -> str:
    """Build LaTeX from the PRIMARY JSON format that contains a `blocks` list.
    Wraps appropriate content into LaTeX environments from notes-core.sty based on text triggers.
    """
    import re

    # Environment detection patterns
    _ENV_DEF = re.compile(r"^\s*(Definition|Определение)[:\-]?\s*(.*)$", re.I)
    _ENV_THM = re.compile(r"^\s*(Theorem|Теорема)[:\-]?\s*(.*)$", re.I)
    _ENV_LEM = re.compile(r"^\s*(Lemma|Лемма)[:\-]?\s*(.*)$", re.I)
    _ENV_COR = re.compile(r"^\s*(Corollary|Следствие)[:\-]?\s*(.*)$", re.I)
    _ENV_EX = re.compile(r"^\s*(Example|Illustration|Solve|Find|Пример)\s*\d*[:\-]?\s*(.*)$", re.I)
    _ENV_NOTE = re.compile(r"^\s*(Note|Remark|Principle\s+of|Observation|Important|Recall|Замечание)[:\-]?\s*(.*)$", re.I)
    _ENV_Q = re.compile(r"^\s*(Question|Exercise|Problem|Вопрос)[:\-]?\s*(.*)$", re.I)
    _ENV_PROOF = re.compile(r"^\s*(Proof|Доказательство)[:\-\.]?\s*(.*)$", re.I)
    _STEP = re.compile(r"^\s*(Step\s+\d+|Solution|Способ\s+\d+)[:\-]?\s*(.*)$", re.I)

    # Section detection
    _H_SECTION = re.compile(r"^\s*(section|subsection|subsubsection)\s*[:\-—]\s*(.+)$", re.I)
    _H_GIVEN = re.compile(r"^\s*(дано|given)\s*[:\-—]\s*(.*)$", re.I)

    # List patterns
    _BULLET = re.compile(r"^\s*[-—•]\s+(.*)$")
    _NUM = re.compile(r"^\s*(\d+)[\.\)]\s+(.*)$")

    # Environment mapping
    env_patterns = [
        (_ENV_DEF, "definitionbox"),
        (_ENV_THM, "theoremnox"),
        (_ENV_LEM, "lemmanox"),
        (_ENV_COR, "corollarybox"),
        (_ENV_EX, "examplebox"),
        (_ENV_NOTE, "notebox"),
        (_ENV_Q, "questionbox"),
    ]

    def _flush_list(buf, out, env):
        if not buf:
            return
        out.append(f"\\begin{{{env}}}\n")
        for item in buf:
            out.append(f"\\item {item}\n")
        out.append(f"\\end{{{env}}}\n\n")
        buf.clear()

    def _is_environment_stopper(block_type, text=""):
        """Check if this block should stop current environment accumulation"""
        if block_type == "section":
            return True
        if block_type == "paragraph":
            # Check if this paragraph starts a new environment
            for pattern, _ in env_patterns:
                if pattern.match(text):
                    return True
            if _H_SECTION.match(text) or _ENV_PROOF.match(text):
                return True
        return False

    def _render_content_block(block, inside_env=None):
        """Render a single content block"""
        typ = block.get("type")

        if typ == "equation":
            eq = (block.get("latex") or "").strip()
            if eq:
                return "\\[\n" + eq + "\n\\]\n\n"

        elif typ == "figure":
            p = block.get("path") or ""
            cap = block.get("caption") or ""
            result = "\\begin{figure}[h]\n\\centering\n"
            if p:
                result += f"\\includegraphics[width=0.75\\textwidth]{{{p}}}\n"
            if cap:
                result += f"\\caption{{{cap}}}\n"
            result += "\\end{figure}\n\n"
            return result

        elif typ == "list":
            env = "enumerate" if (block.get("style") == "enumerate") else "itemize"
            items = block.get("items") or []
            result = f"\\begin{{{env}}}\n"
            for it in items:
                result += f"\\item {it}\n"
            result += f"\\end{{{env}}}\n\n"
            return result

        elif typ == "paragraph":
            txt = (block.get("text") or "").strip()
            if not txt:
                return ""

            # Special handling for steps inside examples
            if inside_env == "examplebox":
                step_match = _STEP.match(txt)
                if step_match:
                    step_title = step_match.group(1)
                    step_content = step_match.group(2).strip()
                    result = f"\\paragraph{{{step_title}}}"
                    if step_content:
                        result += f" {step_content}\n\n"
                    else:
                        result += "\n\n"
                    return result

            return txt + "\n\n"

        return ""

    headers = data.get("headers", {}) or {}
    title = headers.get("title") or "Untitled"
    subtitle = headers.get("subtitle") or ""
    blocks = data.get("blocks") or []

    out: list[str] = []
    if title:
        out.append(f"\\section*{{{title}}}\n\n")
    if subtitle:
        out.append(f"\\textit{{{subtitle}}}\\\n\n")

    list_buf: list[str] = []
    list_env = "itemize"
    i = 0

    while i < len(blocks):
        block = blocks[i] or {}
        typ = block.get("type")

        # Handle sections
        if typ == "section":
            _flush_list(list_buf, out, list_env)
            lvl = int(block.get("level", 1))
            name = block.get("text", "")
            cmd = {1: "\\section", 2: "\\subsection", 3: "\\subsubsection"}.get(lvl, "\\section")
            out.append(f"{cmd}{{{name}}}\n\n")
            i += 1
            continue

        # Handle explicit non-paragraph blocks
        if typ in ["equation", "figure", "list"]:
            _flush_list(list_buf, out, list_env)
            out.append(_render_content_block(block))
            i += 1
            continue

        # Handle paragraphs
        if typ == "paragraph":
            txt = (block.get("text") or "").strip()
            if not txt:
                i += 1
                continue

            # Check for "Proof." at the beginning - CRITICAL FIX
            proof_match = _ENV_PROOF.match(txt)
            if proof_match:
                _flush_list(list_buf, out, list_env)
                out.append("\\begin{proofbox}\n")

                # Extract proof content (remove "Proof." prefix)
                proof_content = txt[proof_match.end():].strip()
                # Remove QED symbols at the end
                proof_content = re.sub(r'\s*[\$\\]?\s*(\\square|□|∎|\\blacksquare)\s*[\$\\]?\s*$', '', proof_content)

                if proof_content:
                    out.append(proof_content + "\n\n")

                # Check if next blocks continue the proof
                i += 1
                while i < len(blocks):
                    next_block = blocks[i] or {}
                    next_typ = next_block.get("type")
                    next_txt = (next_block.get("text") or "").strip() if next_typ == "paragraph" else ""

                    # Stop if we hit a section or new environment
                    if _is_environment_stopper(next_typ, next_txt):
                        break

                    # Add content to proof
                    content = _render_content_block(next_block)
                    if content:
                        out.append(content)
                    i += 1

                out.append("\\end{proofbox}\n\n")
                continue

            # Check for section headers inside paragraphs
            section_match = _H_SECTION.match(txt)
            if section_match:
                _flush_list(list_buf, out, list_env)
                lvl = 1 if section_match.group(1).lower()=="section" else 2 if section_match.group(1).lower()=="subsection" else 3
                cmd = {1:"\\section", 2:"\\subsection", 3:"\\subsubsection"}[lvl]
                out.append(f"{cmd}{{{section_match.group(2)}}}\n\n")
                i += 1
                continue

            # Check for "Given" / "Дано"
            if _H_GIVEN.match(txt):
                _flush_list(list_buf, out, list_env)
                out.append("\\begin{examplebox}{Дано}{}\n")
                out.append(_H_GIVEN.sub(r"\\textbf{\\1:} \\2", txt) + "\n")
                out.append("\\end{examplebox}\n\n")
                i += 1
                continue

            # Check for environment triggers
            env_matched = False
            for pattern, env_name in env_patterns:
                match = pattern.match(txt)
                if match:
                    _flush_list(list_buf, out, list_env)

                    # Extract title
                    title_text = match.group(2).strip()
                    title_braces = f"{{{title_text}}}" if title_text else "{}"

                    # Start environment
                    out.append(f"\\begin{{{env_name}}}{title_braces}{{}}\n")

                    # Add any remaining content from the trigger paragraph
                    remaining_content = txt[match.end():].strip()
                    if remaining_content:
                        out.append(remaining_content + "\n\n")

                    # Accumulate following blocks until we hit a stopper
                    i += 1
                    while i < len(blocks):
                        next_block = blocks[i] or {}
                        next_typ = next_block.get("type")
                        next_txt = (next_block.get("text") or "").strip() if next_typ == "paragraph" else ""

                        # Check if we should stop accumulating
                        if _is_environment_stopper(next_typ, next_txt):
                            break

                        # Handle lists specially
                        if next_typ == "paragraph":
                            # Check for ad-hoc bullets/numbering
                            mb = _BULLET.match(next_txt)
                            mn = _NUM.match(next_txt)
                            if mb or mn:
                                item = (mb.group(1) if mb else mn.group(2)).strip()
                                env = "enumerate" if mn else "itemize"
                                if not list_buf:
                                    list_env = env
                                if list_env != env:
                                    _flush_list(list_buf, out, list_env)
                                    list_env = env
                                list_buf.append(item)
                                i += 1
                                continue

                        # Flush any pending list before adding content
                        _flush_list(list_buf, out, list_env)

                        # Add the block content
                        content = _render_content_block(next_block, env_name)
                        if content:
                            out.append(content)

                        i += 1

                    # Flush any remaining list and close environment
                    _flush_list(list_buf, out, list_env)
                    out.append(f"\\end{{{env_name}}}\n\n")
                    env_matched = True
                    break

            if env_matched:
                continue

            # Handle ad-hoc bullets/numbering (not inside environments)
            mb = _BULLET.match(txt)
            mn = _NUM.match(txt)
            if mb or mn:
                item = (mb.group(1) if mb else mn.group(2)).strip()
                env = "enumerate" if mn else "itemize"
                if not list_buf:
                    list_env = env
                if list_env != env:
                    _flush_list(list_buf, out, list_env)
                    list_env = env
                list_buf.append(item)
                i += 1
                continue

            # Plain paragraph
            _flush_list(list_buf, out, list_env)
            out.append(txt + "\n\n")
            i += 1
            continue

        # Skip unknown block types
        i += 1

    # Flush any remaining list
    _flush_list(list_buf, out, list_env)

    result = "".join(out) or "% (empty)"

    # Remove trailing duplicated text
    lines = result.split('\n')
    seen_end = False
    clean_lines = []
    for line in lines:
        stripped = line.strip()
        if seen_end and stripped and not stripped.startswith('%') and not stripped.startswith('\\'):
            # Check if this looks like start of document (duplication)
            if any(marker in stripped.lower() for marker in ['this document is', 'typed latex sample', 'golden set']):
                break
        clean_lines.append(line)
        if stripped.startswith('\\end{'):
            seen_end = True

    return '\n'.join(clean_lines)

# ---------- API ----------

@app.post("/process")
async def process(
    request: Request,
    file: UploadFile,
    mode: str = Query("book", description='Output mode: "book" (enriched) or "strict" (verbatim)'),
    use_editor: bool = Query(True, description="Second pass editor/cleanup"),
    compile_pdf: bool = Query(False, description="Try to compile PDF with latexmk if available")
):
    # 1) job dir
    job_id = str(uuid.uuid4())
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # 2) save input
    input_path = job_dir / file.filename
    with open(input_path, "wb") as f:
        f.write(await file.read())

    images = []
    if input_path.suffix.lower() == ".pdf":
        try:
            images = pdf_to_images(str(input_path), dpi=220, max_pages=100)
        except Exception:
            images = []

    # 3) extract text + images
    text_blocks: list[str] = []
    figures_info: list[dict] = []

    if input_path.suffix.lower() == ".pdf":
        try:
            text_blocks = extract_text_blocks_pdf(input_path)
        except Exception:
            text_blocks = []
        try:
            figures_info = extract_images_pdf(input_path, job_dir)
        except Exception:
            figures_info = []
    elif input_path.suffix.lower() in [".jpg", ".jpeg", ".png"]:
        # Складываем исходную картинку в job_dir/figures и объявляем её как figure
        fig_dir = job_dir / "figures"
        fig_dir.mkdir(exist_ok=True)
        target = fig_dir / input_path.name
        shutil.move(str(input_path), str(target))

        images = [target]

        figures_info = []  # Для одиночных страниц не считаем их "фигурами" — нужно распознать текст
        # Минимальный текстовый блок-подсказка модели
        text_blocks = [f"Handwritten page image: {target.name}. Extract and convert to LaTeX."]

    if not text_blocks or all(not b.strip() for b in text_blocks):
        text_blocks = [f"Input file: {file.filename}. No text extracted at this stage."]

    # 4) compose via Gemini
    meta_path = job_dir / "meta.json"

    compose_result = compose_latex(
        text_blocks=text_blocks,
        figures=figures_info,
        mode=mode,
        images=images,
        meta_out_path=meta_path,
        job_dir=job_dir
    )

    # Always try to convert structured output into LaTeX
    parsed = _try_parse_json_maybe_wrapped(compose_result)
    if isinstance(parsed, dict):
        if "blocks" in parsed and isinstance(parsed["blocks"], list):
            latex_body = build_tex_from_blocks(parsed)
        else:
            latex_body = build_tex_from_capture(parsed)
    else:
        # Fallback: treat as plain LaTeX text (already-composed)
        latex_body = str(compose_result)
        # extra guard: if it still looks like META JSON inside a string, try again
        if '"blocks"' in latex_body or '"equations_captured"' in latex_body or '"headers"' in latex_body:
            parsed2 = _try_parse_json_maybe_wrapped(latex_body)
            if isinstance(parsed2, dict):
                if "blocks" in parsed2 and isinstance(parsed2["blocks"], list):
                    latex_body = build_tex_from_blocks(parsed2)
                else:
                    latex_body = build_tex_from_capture(parsed2)

    latex_body = enforce_latex_conventions(latex_body)  # << постпроцессор

    # прочитать meta.json
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}

    # Prefer rebuild from meta when structured PRIMARY JSON is available
    if isinstance(meta, dict) and isinstance(meta.get("blocks"), list) and meta["blocks"]:
        latex_body = build_tex_from_blocks(meta)

    # 5) optional editor pass (before validation, so we validate the final text)
    if use_editor:
        latex_body = editor_review(latex_body, job_dir=job_dir)

    # ensure figures if none present
    latex_body = ensure_figure_blocks(latex_body, figures_info)

    # validate the final result
    latex_body, val_report = run_validators(latex_body, meta)

    # финальные фиксы перед записью
    latex_body = fix_cyrillic_in_math(latex_body)
    latex_body = fix_dano_environment(latex_body)

    # --- SAFETY: JSON → TeX, если вдруг сюда дошла JSON-строка ---
    def _looks_like_json_blob(txt: str) -> bool:
        t = (txt or "").lstrip()
        if not ("{" in t and "}" in t):
            return False
        low = t.lower()
        json_patterns = [
            "blocks", "headers", "language", '"style":"enumerate"',
            '"type":"paragraph"', '"type":"equation"', '"type":"figure"',
            '"type":"list"', '"type":"section"', "equations_captured",
            "figures_captured", "normalized_capture"
        ]
        return any(pattern in low for pattern in json_patterns) or t.startswith("{") or low.startswith("json")

    if isinstance(latex_body, str) and _looks_like_json_blob(latex_body):
        parsed2 = _try_parse_json_maybe_wrapped(latex_body)
        if isinstance(parsed2, dict):
            if "blocks" in parsed2 and isinstance(parsed2["blocks"], list):
                latex_body = build_tex_from_blocks(parsed2)
            else:
                latex_body = build_tex_from_capture(parsed2)
    # --- end safety ---

    # 6) write content.tex
    # Очистить от validator hints
    latex_body = finalize_content(latex_body)
    (job_dir / "content.tex").write_text(latex_body, encoding="utf-8")

    # 7) copy templates
    src_main = TEMPLATES_DIR / "main-template.tex"
    assert src_main.exists(), f"main-template.tex not found at {src_main}"

    doc_language = "en"

    def guess_lang_from_text(text: str) -> str:
        cleaned = re.sub(r"\\[a-zA-Z]+\{.*?\}", "", text or "")[:5000]
        cyr = sum(1 for c in cleaned if 0x0400 <= ord(c) <= 0x04FF)
        return "ru" if cyr > 10 else "en"

    meta = None
    raw_lang = ""

    # 2) meta.json (если есть)
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            raw_lang = str(meta.get("language", "")).lower().strip()
            if raw_lang in ["ru", "russian", "cyrillic"]:
                doc_language = "ru"
            elif raw_lang in ["en", "english", "latin"]:
                doc_language = "en"
        except Exception as e:
            logger.warning(f"Could not read meta.json: {e}")

    # 3) editor_decision.json (подсказка), только если язык ещё не определён
    if doc_language == "en":
        editor_path = job_dir / "editor_decision.json"
        if editor_path.exists():
            try:
                editor_meta = json.loads(editor_path.read_text(encoding="utf-8"))
                ed_lang = str(editor_meta.get("edited_lang", "")).lower().strip()
                if ed_lang in ["ru", "russian", "cyrillic"]:
                    doc_language = "ru"
                elif ed_lang in ["en", "english", "latin"]:
                    doc_language = "en"
            except Exception as e:
                logger.warning(f"Could not read editor_decision.json: {e}")

    # 4) эвристика по тексту, если всё ещё "en"
    if doc_language == "en":
        sample_text = latex_body or ""
        doc_language = guess_lang_from_text(sample_text)

    # 5) сохраняем обратно в meta.json (создаём при необходимости)
    if not isinstance(meta, dict):
        meta = {}
    meta["language"] = doc_language
    try:
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"✅ Language set to: {doc_language} (raw meta was: {raw_lang or '∅'})")
    except Exception as e:
        logger.warning(f"Could not write meta.json: {e}")

    template_content = src_main.read_text(encoding="utf-8")
    template_content = template_content.replace("LANGUAGE_PLACEHOLDER", doc_language)
    (job_dir / "main-template.tex").write_text(template_content, encoding="utf-8")

    logger.info(f"main-template.tex created with language={doc_language}")

    src_core = TEMPLATES_DIR / "notes-core.sty"
    if src_core.exists():
        shutil.copy(src_core, job_dir / "notes-core.sty")
    src_notes = TEMPLATES_DIR / "notes.sty"
    if src_notes.exists():
        shutil.copy(src_notes, job_dir / "notes.sty")

    # 8) optionally compile pdf
    pdf_built = False
    if compile_pdf:
        pdf_built = maybe_compile_pdf(job_dir)

    # 9) zip
    zip_path = JOBS_DIR / f"{job_id}.zip"
    with zipfile.ZipFile(zip_path, "w") as zipf:
        for p in job_dir.rglob("*"):
            if p.is_file():
                zipf.write(p, arcname=p.relative_to(job_dir))

    base = str(request.base_url).rstrip("/")
    resp = {
        "job_id": job_id,
        "download_url": f"{base}/download/{job_id}",
        "stats": {
            "text_blocks": len(text_blocks),
            "figures": len(figures_info),
            "editor_used": use_editor,
            "mode": mode,
            "pdf_built": pdf_built
        }
    }
    if pdf_built:
        # latexmk обычно кладёт PDF рядом (main-template.pdf) или в build/
        pdf_candidates = list(job_dir.glob("*.pdf")) + list(job_dir.glob("build/*.pdf"))
        if pdf_candidates:
            # первой попавшейся достаточно
            resp["pdf_filename"] = pdf_candidates[0].name
    return resp

@app.get("/download/{job_id}")
async def download(job_id: str):
    zip_path = JOBS_DIR / f"{job_id}.zip"
    if not zip_path.exists():
        return {"error": "Job not found"}
    return FileResponse(str(zip_path), filename=f"{job_id}.zip")

@app.get("/health")
def health():
    return {"ok": True}
