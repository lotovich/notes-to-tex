# backend/app.py
from fastapi import FastAPI, UploadFile, Request, Query
from fastapi.responses import FileResponse
from pathlib import Path
import os, shutil, uuid, zipfile, subprocess, re
import fitz  # PyMuPDF
import json

from backend.gemini_client import compose_latex, editor_review
from backend.utils.postprocess import enforce_latex_conventions
from backend.utils.pdf import pdf_to_images
from backend.utils.validators import run_validators


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "latex"
JOBS_DIR = BASE_DIR.parent / "jobs"
JOBS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="notes-to-tex")

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

    latex_body = compose_latex(
        text_blocks=text_blocks,
        figures=figures_info,    # теперь даём реальные метаданные вытянутых картинок
        mode=mode,
        images=images,           # Передаём в модель байты страниц/изображений
        meta_out_path=meta_path,
        job_dir=job_dir
    )

    latex_body = enforce_latex_conventions(latex_body)  # << постпроцессор

    # прочитать meta.json
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}

    latex_body, val_report = run_validators(latex_body, meta)

    # 5) optional editor pass
    if use_editor:
        latex_body = editor_review(latex_body, job_dir=job_dir)

    latex_body = ensure_figure_blocks(latex_body, figures_info)

    # 6) write content.tex
    (job_dir / "content.tex").write_text(latex_body, encoding="utf-8")

    # 7) copy templates
    # Вариант с notes-core.sty
    src_main = TEMPLATES_DIR / "main-template.tex"
    assert src_main.exists(), f"main-template.tex not found at {src_main}"
    shutil.copy(src_main, job_dir / "main-template.tex")

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
