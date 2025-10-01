# backend/app.py
from fastapi import FastAPI, UploadFile, Request, Query
from fastapi.responses import FileResponse
from pathlib import Path
import os, shutil, uuid, zipfile, subprocess
import fitz  # PyMuPDF

from backend.gemini_client import compose_latex, editor_review
from backend.utils.postprocess import enforce_latex_conventions

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "latex"
JOBS_DIR = BASE_DIR.parent / "jobs"
JOBS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="notes-to-tex")

# ---------- PDF helpers ----------

def extract_text_blocks_pdf(pdf_path: Path) -> list[str]:
    blocks = []
    with fitz.open(pdf_path) as doc:
        for page in doc:
            text = page.get_text("text")
            if text and text.strip():
                blocks.append(text)
    return blocks

def extract_images_pdf(pdf_path: Path, out_dir: Path) -> list[dict]:
    """
    Извлекает все встроенные изображения как PNG в out_dir/figures.
    Возвращает [{filename, page, w, h}, ...]
    """
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

    if not text_blocks:
        text_blocks = [f"Input file: {file.filename}. No text extracted at this stage."]

    # 4) compose via Gemini
    latex_body = compose_latex(text_blocks=text_blocks, figures=figures_info, mode=mode)
    latex_body = enforce_latex_conventions(latex_body)  # << постпроцессор
    
    # 5) optional editor pass
    if use_editor:
        latex_body = editor_review(latex_body)

    # 6) write content.tex
    (job_dir / "content.tex").write_text(latex_body, encoding="utf-8")

    # 7) copy templates
    # Вариант с notes-core.sty (как ты хотел)
    src_main = TEMPLATES_DIR / "main-template.tex"
    assert src_main.exists(), f"main-template.tex not found at {src_main}"
    shutil.copy(src_main, job_dir / "main-template.tex")

    # Копируем стиль (любой, что у тебя есть)
    # Если у тебя notes-core.sty — копируем его. Если добавишь shim notes.sty — можно копировать оба.
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