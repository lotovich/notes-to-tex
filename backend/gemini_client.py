# backend/gemini_client.py
import os, re, json
from pathlib import Path
from typing import List, Dict, Optional
from google import genai
from dotenv import load_dotenv

# Загружаем .env из корня проекта
load_dotenv()

PROMPTS_DIR = (Path(__file__).resolve().parent / "prompts")

# В твоём репо должны быть:
#  - prompts/composer.md  (универсальный "книжный" сборщик LaTeX)
#  - prompts/editor.md    (второй проход: правки/вычитка)
COMPOSER_FILE = PROMPTS_DIR / "composer.md"
EDITOR_FILE   = PROMPTS_DIR / "editor.md"

# Вырезаем содержимое из ```latex ... ``` или ```tex ... ```
_CODE_FENCE = re.compile(r"```(?:latex|tex)?\s*(.*?)```", re.S | re.I)

# Удаляем прелюдию/доккласс, если модель вдруг их вернула
_REMOVE_PREAMBLE = re.compile(
    r"\\documentclass[\s\S]*?\\begin\{document\}|\%+\s*Preamble[\s\S]*?\\begin\{document\}",
    re.I
)
_END_DOCUMENT = re.compile(r"\\end\{document\}", re.I)

_LATEX_DANGERS = [
    r"\\usepackage\{.*?\}",     # не позволяем добавлять пакеты
    r"\\documentclass\[.*?\]\{.*?\}",
    r"\\documentclass\{.*?\}",
    r"\\begin\{document\}",
    r"\\end\{document\}",
]

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")

def _client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in .env")
    return genai.Client(api_key=api_key)

def _model() -> str:
    return os.getenv("GEMINI_MODEL", "gemini-2.5-pro")

def _strip_code_fence(text: str) -> str:
    m = _CODE_FENCE.search(text or "")
    return (m.group(1) if m else text or "").strip()

def _sanitize_latex_body(text: str) -> str:
    """Возвращает только тело для content.tex (без прелюдии и documentclass)."""
    if not text:
        return ""
    t = _strip_code_fence(text)

    # Режем всё до \begin{document} и всё после \end{document}
    t = _REMOVE_PREAMBLE.sub("", t)
    t = _END_DOCUMENT.sub("", t)

    # Блокируем опасные директивы (новые пакеты и доккласс)
    for pat in _LATEX_DANGERS:
        t = re.sub(pat, "% stripped", t, flags=re.I)

    return t.strip()

def compose_latex(
    text_blocks: List[str],
    figures: List[Dict],
    mode: str = "book"  # "book" (обогащённый) или "strict" (дословно)
) -> str:
    """
    Собирает content.tex из текстовых блоков и метаданных о рисунках.
    figures: [{ "filename": "figures/fig_p1_i1.png", "page": 1, "w": 800, "h": 600 }, ...]
    """
    system = _read(COMPOSER_FILE)
    # Добавим маленький "переключатель стиля"
    if mode == "book":
        system += (
            "\n\n## Mode\n"
            "- Produce a textbook-like, readable lecture note.\n"
            "- Normalize definitions, fix mistakes, add minimal connective text if needed.\n"
        )
    else:
        system += (
            "\n\n## Mode\n"
            "- Transcribe strictly without paraphrasing. No additions beyond placeholders.\n"
        )

    payload = {
        "text_blocks": text_blocks,
        "figures": [
            {
                "path": f.get("filename"),
                "page": f.get("page"),
                "width": f.get("w"),
                "height": f.get("h")
            } for f in figures
        ],
    }

    client = _client()
    try:
        resp = client.models.generate_content(
            model=_model(),
            contents=[system, json.dumps(payload, ensure_ascii=False)]
        )
        body = _sanitize_latex_body(resp.text or "")
        if not body.strip():
            body = "\\section{Empty Output}\\todo{Model returned empty body}"
        return body
    except Exception as e:
        # Fallback на более дешёвую/лояльную модель
        if "429" in str(e) or "quota" in str(e).lower():
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[system, json.dumps(payload, ensure_ascii=False)]
            )
            body = _sanitize_latex_body(resp.text or "")
            if not body.strip():
                body = "\\section{Empty Output}\\todo{Flash returned empty body}"
            return body
        raise

def editor_review(latex_body: str) -> str:
    """
    Второй проход: вычитка/исправления. Возвращает исправленное тело.
    """
    system = _read(EDITOR_FILE)
    client = _client()
    try:
        resp = client.models.generate_content(
            model=_model(),
            contents=[system, latex_body]
        )
        body = _sanitize_latex_body(resp.text or "")
        return body if body.strip() else latex_body
    except Exception:
        # На ошибку просто вернём исходник
        return latex_body