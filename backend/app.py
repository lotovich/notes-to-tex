from fastapi import FastAPI, UploadFile
from fastapi.responses import FileResponse
import os
import shutil
import uuid
import zipfile

app = FastAPI()

# Папка для временных задач
JOBS_DIR = "jobs"
os.makedirs(JOBS_DIR, exist_ok=True)

@app.post("/process")
async def process(file: UploadFile):
    # 1. Создаём папку под задачу
    job_id = str(uuid.uuid4())
    job_dir = os.path.join(JOBS_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    # 2. Сохраняем загруженный файл (пока не используем)
    input_path = os.path.join(job_dir, file.filename)
    with open(input_path, "wb") as f:
        f.write(await file.read())

    # 3. Заглушка "OCR"
    text_blocks = "This is a sample lecture note.\nHere is a famous formula: $E = mc^2$."
    figures = ["TODO: insert figure of mass-energy equivalence diagram"]

    # 4. Заглушка "Composer" (пока без Gemini API)
    latex_content = f"""
\\section{{Sample Section}}
{text_blocks}

\\begin{{equation}}
E = mc^2
\\end{{equation}}

% {figures[0]}
"""

    # 5. Сохраняем результат в content.tex
    tex_path = os.path.join(job_dir, "content.tex")
    with open(tex_path, "w") as f:
        f.write(latex_content)

    # 6. Копируем шаблонные файлы (main-template.tex и notes.sty)
    shutil.copy("backend/latex/main-template.tex", os.path.join(job_dir, "main-template.tex"))
    shutil.copy("backend/latex/notes-core.sty", os.path.join(job_dir, "notes-core.sty"))

    # 7. Упаковываем в ZIP
    zip_path = os.path.join(JOBS_DIR, f"{job_id}.zip")
    with zipfile.ZipFile(zip_path, "w") as zipf:
        for root, _, files in os.walk(job_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, job_dir)
                zipf.write(file_path, arcname)

    return {"job_id": job_id, "download_url": f"/download/{job_id}"}

@app.get("/download/{job_id}")
async def download(job_id: str):
    zip_path = os.path.join(JOBS_DIR, f"{job_id}.zip")
    if not os.path.exists(zip_path):
        return {"error": "Job not found"}
    return FileResponse(zip_path, filename=f"{job_id}.zip")
