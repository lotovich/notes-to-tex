# backend/gemini_client.py
import os, re, json
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Union
from google import genai
from google.genai import types as genai_types
from dotenv import load_dotenv
import base64

# Загружаем .env из корня проекта
load_dotenv()

def _looks_like_json_blob(txt: str) -> bool:
    if not txt or not isinstance(txt, str):
        return False
    t = txt.strip()
    json_markers = ['"blocks":', '"headers":', '"language":', '{"type":']
    return any(marker in t for marker in json_markers) or (t.startswith('{') and '":' in t)

def _is_json_truncated(raw_text: str) -> bool:
    """Check if JSON output appears truncated (incomplete)"""
    if not raw_text or not isinstance(raw_text, str):
        return False

    stripped = raw_text.rstrip()

    # Check for truncation patterns
    truncation_patterns = [
        '"**Step',  # Cut off in middle of step
        '"text": "**Step',  # Cut off in step text
    ]

    # Check if ends with truncation pattern without closing quote
    for pattern in truncation_patterns:
        if pattern in stripped and not stripped.endswith('"'):
            return True

    return False

PROMPTS_DIR = (Path(__file__).resolve().parent / "prompts")
COMPOSER_FILE = PROMPTS_DIR / "composer.md"
EDITOR_FILE   = PROMPTS_DIR / "editor.md"
COMPOSER_STRICT_FILE = PROMPTS_DIR / "composer_strict.md"
EDITOR_STRICT_FILE   = PROMPTS_DIR / "editor_strict.md"

# Вырезаем содержимое из ```latex ... ``` или ```tex ... ```
_CODE_FENCE = re.compile(r"```\s*(.*?)```", re.S | re.I)
_LATEX_FENCE = re.compile(r"```(?:latex|tex)\s*(.*?)```", re.S | re.I)
_META_FENCE = re.compile(r"```json\s*META\s*(\{[\s\S]*?\})\s*```", re.I)

# Удаляем прелюдию/доккласс, если модель вдруг их вернула
_REMOVE_PREAMBLE = re.compile(
    r"\\documentclass[\s\S]*?\\begin\{document\}|\%+\s*Preamble[\s\S]*?\\begin\{document\}",
    re.I
)
_END_DOCUMENT = re.compile(r"\\end\{document\}", re.I)

# Prefer PRIMARY JSON (single-object) for model output
def _try_parse_primary_json(raw: str) -> Optional[Dict]:
    """Try to parse PRIMARY JSON (single-object) from model output.
    Accepts plain JSON or text with leading/trailing garbage; verifies presence of `blocks` list.
    """
    if not raw or not isinstance(raw, str):
        return None
    # First attempt: direct parse
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and ("blocks" in obj or "headers" in obj):
            return obj
    except Exception:
        pass
    # Fallback: slice the largest {...} region
    i = raw.find("{")
    j = raw.rfind("}")
    if i != -1 and j != -1 and j > i:
        candidate = raw[i:j+1]
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict) and ("blocks" in obj or "headers" in obj):
                return obj
        except Exception:
            return None
    return None

_LATEX_DANGERS = [
    r"\\usepackage\{.*?\}",     # не позволяем добавлять пакеты
    r"\\documentclass\[.*?\]\{.*?\}",
    r"\\documentclass\{.*?\}",
    r"\\begin\{document\}",
    r"\\end\{document\}",
]

_EQUATION_FINDER = re.compile(
    r"(?:\\begin\{equation\*?\}[\s\S]*?\\end\{equation\*?\})|(?:\$\$[\s\S]*?\$\$)|(?:\\$begin:math:display$([\\s\\S]*?)\\\\$end:math:display$)",
    re.I
)

_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
_LATIN_RE = re.compile(r"[A-Za-z]")

def _body_insufficient(body: str) -> bool:
    if not body or not body.strip():
        return True
    # Удаляем figure-блоки и служебные команды перед оценкой
    cleaned = re.sub(r"\\begin\{figure\}[\s\S]*?\\end\{figure\}", "", body, flags=re.I)
    cleaned = re.sub(r"\\includegraphics\{.*?\}", "", cleaned)
    # Считаем информативные символы
    letters = re.findall(r"[A-Za-zА-Яа-я0-9]", cleaned)
    return len(letters) < 300

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")

def _client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in .env")
    return genai.Client(api_key=api_key)

def _b64(img_bytes: bytes) -> str:
    return base64.b64encode(img_bytes).decode("utf-8")

def _model() -> str:
    return os.getenv("GEMINI_MODEL", "gemini-2.5-pro")

def _strip_code_fence(text: str) -> str:
    if not text:
        return ""
    m = _LATEX_FENCE.search(text)
    if not m:
        m = _CODE_FENCE.search(text)
    if m:
        return m.group(1).strip()
    # Если есть стартовая тройная кавычка без закрытия — просто удалим первую строку
    stripped = text.lstrip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[^\n]*\n", "", stripped)
    return stripped.strip()

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


def _language_hint(text: str, sample_chars: int = 5000) -> str:
    """Черновая метка языка по преобладающему алфавиту."""
    t = text or ""
    t = re.sub(r"\\[a-zA-Z]+\{.*?\}", "", t)
    t = re.sub(r"\\[a-zA-Z]+", "", t)
    t = re.sub(r"https?://\S+", "", t)
    snippet = t[:sample_chars]
    cyr = sum(1 for c in snippet if 0x0400 <= ord(c) <= 0x04FF)
    return "ru" if cyr > 10 else "en"

def _image_parts(
    images: Optional[List[Union[str, Path, Dict, bytes, bytearray, memoryview]]],
    job_base: Optional[Path] = None,
) -> List[genai_types.Part]:
    parts: List[genai_types.Part] = []
    if not images:
        return parts

    def _resolve_path(item) -> Optional[Path]:
        if isinstance(item, (str, Path)):
            p = Path(item)
        elif isinstance(item, dict):
            candidate = item.get("path") or item.get("filename")
            if candidate:
                p = Path(candidate)
            else:
                return None
        else:
            return None
        if not p.is_absolute() and job_base:
            p = job_base / p
        return p if p.exists() else None

    def _part_from_bytes(data: bytes, mime: Optional[str] = None) -> Optional[genai_types.Part]:
        if not data:
            return None
        mime_type = (mime or "image/png")
        try:
            return genai_types.Part.from_bytes(data=data, mime_type=mime_type)
        except Exception:
            return None

    for it in images:
        # Already a Part — pass through as-is.
        if isinstance(it, genai_types.Part):
            parts.append(it)
            continue

        # Raw bytes-like attachments (pdf_to_images output etc.).
        if isinstance(it, (bytes, bytearray, memoryview)):
            part = _part_from_bytes(bytes(it))
            if part:
                parts.append(part)
            continue

        # Dict payloads may contain either a path or inline bytes/base64.
        if isinstance(it, dict):
            data_field = it.get("bytes") or it.get("data")
            if data_field and isinstance(data_field, (bytes, bytearray, memoryview)):
                part = _part_from_bytes(bytes(data_field), it.get("mime") or it.get("mime_type"))
                if part:
                    parts.append(part)
                continue
            b64_field = it.get("b64") or it.get("base64")
            if b64_field and isinstance(b64_field, str):
                try:
                    decoded = base64.b64decode(b64_field)
                except Exception:
                    decoded = b""
                part = _part_from_bytes(decoded, it.get("mime") or it.get("mime_type"))
                if part:
                    parts.append(part)
                continue

        # Fallback: treat as filesystem path.
        p = _resolve_path(it)
        if not p:
            continue
        suf = p.suffix.lower()
        mime = "image/jpeg" if suf in (".jpg", ".jpeg") else "image/png"
        part = _part_from_bytes(p.read_bytes(), mime)
        if part:
            parts.append(part)

    return parts


def _split_meta_and_body(model_text: str) -> Tuple[Dict, str]:
    """
    Ищем два блока: ```json META {..}``` и ```latex ...```; если нет META — вернём {}.
    Если нет latex-блока — заберём весь текст как латех и попытаемся очистить.
    """
    meta: Dict = {}
    body: str = model_text or ""

    m = _META_FENCE.search(model_text or "")
    if m:
        try:
            meta = json.loads(m.group(1))
        except Exception:
            meta = {}

    text_wo_meta = _META_FENCE.sub("", model_text or "")

    lb = _LATEX_FENCE.search(text_wo_meta)
    if lb:
        body = lb.group(1)
    else:
        for block in _CODE_FENCE.finditer(text_wo_meta):
            block_text = block.group(1)
            if block_text and block_text.strip() and not block_text.strip().lower().startswith("json meta"):
                body = block_text
                break

    body = _sanitize_latex_body(body)

    # Если у META нет equations_captured — доберём из тела
    if "equations_captured" not in meta or not meta.get("equations_captured"):
        eqs = []
        for match in _EQUATION_FINDER.finditer(body):
            chunk = match.group(0)
            if chunk:
                eqs.append({"latex": chunk.strip()})
        if eqs:
            meta.setdefault("equations_captured", eqs)

    return meta, body

def compose_latex(
    text_blocks: List[str],
    figures: List[Dict],
    mode: str = "book",
    images: Optional[List[Union[str, Path, Dict, bytes, bytearray, memoryview]]] = None,
    meta_out_path: Optional[Path] = None,
    job_dir: Optional[Path] = None,
) -> str:
    """
    Собирает content.tex из текстовых блоков и (реально прикреплённых) изображений.
    figures — метаданные для подписи/размера; images — пути к PNG/JPG (страницы сканов и т.п.).
    """
    # Select prompt based on mode
    if mode == "strict":
        system = _read(COMPOSER_STRICT_FILE) if COMPOSER_STRICT_FILE.exists() else _read(COMPOSER_FILE)
    elif mode == "book":
        system = _read(COMPOSER_FILE)

    # Жёстко подсвечиваем "полный конспект"
    system += (
        "\n\n## Output scope (DO NOT SKIP)\n"
        "- Transcribe the FULL lecture: every section of prose and math.\n"
        "- Figures are ADDITIVE. Do NOT output only figures or captions — always deliver the complete textual body.\n"
        "- Use the provided theorem-like environments (definition, theorem, lemma, example, noteenv, question, proof).\n"
        "- Don't emit preamble/documentclass; only LaTeX body for content.tex.\n"
        "- If handwriting is illegible, transcribe the glyphs you see and add a comment like `% TODO verify ...` rather than dropping the content.\n"
    )

    # Встраиваем переключатель режима
    if mode == "book":
        system += (
            "\n## Mode: book\n"
            "- Make it readable like a textbook: fix obvious mistakes, expand shorthand into proper sentences.\n"
            "- Keep math faithful. Do not invent content, but you may normalize notation and add minimal connective text.\n"
        )
    else:
        system += (
            "\n## Mode: strict\n"
            "- Transcribe strictly without paraphrasing. No additions beyond placeholders.\n"
        )

    payload = {
        "text_blocks": text_blocks or [],
        "figures": [
            {
                "path": f.get("filename") or f.get("path"),
                "page": f.get("page"),
                "width": f.get("w"),
                "height": f.get("h"),
            } for f in (figures or [])
        ],
        "note": (
            "Attached are page images (PNG/JPG). Read every paragraph, bullet point, and formula from them.\n"
            "Do not summarise away body text. If OCR text conflicts with the page, prefer the image.\n"
            "Write the LaTeX in the same language that appears in the source material (detect automatically)."
        ),
        "mode": mode,
    }

    client = _client()
    cfg = genai_types.GenerateContentConfig(
        temperature=0.2,
        top_p=0.9,
        max_output_tokens=16384,
        system_instruction=system,
    )

    # Собираем все parts: сначала системный текст, затем JSON payload/текст/изображения
    parts: List[genai_types.Part] = [
        genai_types.Part.from_text(text=system),
        genai_types.Part.from_text(text=json.dumps(payload, ensure_ascii=False)),
    ]
    if text_blocks:
        rendered_blocks = [b.strip() for b in text_blocks if b and b.strip()]
        if rendered_blocks:
            max_chars = int(os.getenv("TEXT_BLOCKS_MAX_CHARS", "60000"))
            blob = "\n\n---\n\n".join(rendered_blocks)
            truncated = blob[:max_chars]
            header = "## Extracted text blocks (OCR/parsed)\n"
            if len(blob) > max_chars:
                header += f"(truncated to {max_chars} characters)\n"
            parts.append(genai_types.Part.from_text(text=header + truncated))
    parts.extend(_image_parts(images, job_base=job_dir))

    def _call(model_name: str, attempt: int, retry_hint: Optional[str] = None) -> Tuple[Dict, str, str]:
        call_parts = list(parts)
        if retry_hint:
            call_parts.append(genai_types.Part.from_text(text=retry_hint))
        resp = client.models.generate_content(
            model=model_name,
            contents=[genai_types.Content(role="user", parts=call_parts)],
            config=cfg,
        )
        raw = getattr(resp, "text", "") or ""
        if job_dir:
            raw_path = job_dir / f"model_raw_{model_name.replace('/', '-')}_attempt{attempt}.txt"
            try:
                raw_path.write_text(raw, encoding="utf-8")
            except Exception:
                pass
        # Prefer PRIMARY JSON (single structured object with blocks)
        primary = _try_parse_primary_json(raw)
        if primary:
            return primary, "", raw
        # Fallback: dual-block META + LaTeX
        meta, body = _split_meta_and_body(raw)
        return meta, _sanitize_latex_body(body), raw

    meta: Dict = {}
    raw = ""
    retry_hint = None
    model_used = _model()

    def _enforce_retry_hint(previous_body: str) -> str:
        return (
            "RETRY DIRECTIVE: Your previous output missed the full lecture body. "
            "Return a SINGLE JSON object with all content blocks (sections, paragraphs, equations, lists, figures) — no prose outside JSON. "
            "If you cannot comply, return the legacy two fenced blocks: first ```json META { ... } then ```latex with the full LaTeX body."
        )

    try:
        meta, body, raw = _call(model_used, attempt=1)
        is_primary = isinstance(meta, dict) and bool(meta)

        # Check for JSON truncation
        if _is_json_truncated(raw):
            retry_hint = "Your output was truncated. Return COMPLETE JSON with ALL examples and steps."
            meta, body, raw = _call(model_used, attempt=2, retry_hint=retry_hint)
            is_primary = isinstance(meta, dict) and bool(meta)
        elif (not is_primary) and _body_insufficient(body):
            retry_hint = _enforce_retry_hint(body)
            meta, body, raw = _call(model_used, attempt=2, retry_hint=retry_hint)
            is_primary = isinstance(meta, dict) and bool(meta)
    except Exception as e:
        if "429" in str(e) or "quota" in str(e).lower():
            model_used = "gemini-2.5-pro"
            meta, body, raw = _call(model_used, attempt=1, retry_hint=retry_hint)
            is_primary = isinstance(meta, dict) and bool(meta)

            # Check for JSON truncation in fallback model too
            if _is_json_truncated(raw):
                retry_hint = "Your output was truncated. Return COMPLETE JSON with ALL examples and steps."
                meta, body, raw = _call(model_used, attempt=2, retry_hint=retry_hint)
                is_primary = isinstance(meta, dict) and bool(meta)
            elif (not is_primary) and _body_insufficient(body):
                retry_hint = _enforce_retry_hint(body)
                meta, body, raw = _call(model_used, attempt=2, retry_hint=retry_hint)
                is_primary = isinstance(meta, dict) and bool(meta)
        else:
            raise

    if _body_insufficient(body):
        body = (
            "\\section{Transcription Missing}\n"
            "% TODO: Model failed to return the lecture body. Inspect model_raw files for debugging.\n"
        )

    # Prefer PRIMARY JSON (meta dict) so downstream can build TeX deterministically
    if isinstance(meta, dict) and meta:
        if meta_out_path:
            try:
                meta = meta or {}
                # Дополняем обязательные поля
                if not meta.get("figures_captured"):
                    meta["figures_captured"] = figures
                meta.setdefault("figures", figures)
                meta.setdefault("figures_info", figures)
                meta.setdefault("images_attached", len(images or []))
                meta.setdefault("mode", mode)
                meta.setdefault("text_blocks", text_blocks)
                meta.setdefault("language", _language_hint(body))
                if retry_hint:
                    meta.setdefault("retry_hint_used", True)
                if raw:
                    meta.setdefault("raw_tokens", len(raw))
                meta_out_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        return meta

    return body

def editor_review(latex_body: str, job_dir: Optional[Path] = None, mode: str = "book") -> str:
    """
    Второй проход: вычитка/исправления. Возвращает исправленное тело, но не деградирует контент.
    """
    baseline_insufficient = _body_insufficient(latex_body)
    baseline_lang = _language_hint(latex_body)

    # Select prompt based on mode
    if mode == "strict":
        system = _read(EDITOR_STRICT_FILE) if EDITOR_STRICT_FILE.exists() else _read(EDITOR_FILE)
    elif mode == "book":
        system = _read(EDITOR_FILE)
    client = _client()
    cfg = genai_types.GenerateContentConfig(
        temperature=0.2,
        top_p=0.9,
        max_output_tokens=16384,
        system_instruction=system,
    )

    parts = [genai_types.Part.from_text(text=latex_body)]

    try:
        resp = client.models.generate_content(
            model=_model(),
            contents=[genai_types.Content(role="user", parts=parts)],
            config=cfg,
        )
        raw = getattr(resp, "text", "") or ""
        decision_log: Dict[str, Union[str, int, bool]] = {
            "baseline_len": len(latex_body),
            "baseline_lang": baseline_lang,
        }
        if job_dir:
            raw_path = job_dir / "editor_raw.txt"
            try:
                raw_path.write_text(raw, encoding="utf-8")
            except Exception:
                pass

        body = _sanitize_latex_body(raw)
        edited_lang = _language_hint(body)
        decision_log["edited_len"] = len(body)
        decision_log["edited_lang"] = edited_lang

        # Если редактор убил текст — оставляем исходник
        if (not body.strip()) or (_body_insufficient(body) and not baseline_insufficient):
            decision_log["decision"] = "fallback_empty_or_insufficient"
            if job_dir:
                try:
                    (job_dir / "editor_decision.json").write_text(
                        json.dumps(decision_log, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
            return latex_body

        # Если исходный latex_body выглядит как JSON, принимаем вывод редактора
        if _looks_like_json_blob(latex_body):
            decision_log["decision"] = "accept_over_json"
            if job_dir:
                try:
                    (job_dir / "editor_decision.json").write_text(
                        json.dumps(decision_log, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
            return body

        # Предупреждение если editor сократил контент >15%
        baseline_is_json = _looks_like_json_blob(latex_body)
        if not baseline_is_json and len(body) < int(len(latex_body) * 0.85):
            decision_log["warning"] = "Editor reduced length"

        # Дополнительная страховка: если новый текст в 2 раза короче, оставим исходник
        if len(body) < max(200, int(len(latex_body) * 0.50)):
            decision_log["decision"] = "fallback_length"
            if job_dir:
                try:
                    (job_dir / "editor_decision.json").write_text(
                        json.dumps(decision_log, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
            return latex_body

        if "```" in body:
            decision_log["decision"] = "fallback_unclosed_fence"
            if job_dir:
                try:
                    (job_dir / "editor_decision.json").write_text(
                        json.dumps(decision_log, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
            return latex_body

        # SAFETY CHECK 1: Empty environments
        empty_env_patterns = [
            r'\\begin\{proofbox\}\s*\\end\{proofbox\}',
            r'\\begin\{examplebox\}[^\}]*\}\{\}\s*\\end\{examplebox\}',
            r'\\begin\{definitionbox\}[^\}]*\}\{\}\s*\\end\{definitionbox\}',
            r'\\begin\{theoremnox\}[^\}]*\}\{\}\s*\\end\{theoremnox\}',
        ]

        for pattern in empty_env_patterns:
            if re.search(pattern, body):
                decision_log["decision"] = "fallback_empty_environment"
                decision_log["reason"] = f"Found empty environment matching {pattern}"
                if job_dir:
                    try:
                        (job_dir / "editor_decision.json").write_text(
                            json.dumps(decision_log, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                    except Exception:
                        pass
                return latex_body

        # SAFETY CHECK 2: Excessive content deletion (>20%)
        baseline_word_count = len(re.findall(r'\w+', latex_body))
        edited_word_count = len(re.findall(r'\w+', body))

        if baseline_word_count > 100 and edited_word_count < baseline_word_count * 0.80:
            decision_log["decision"] = "fallback_excessive_deletion"
            decision_log["baseline_words"] = baseline_word_count
            decision_log["edited_words"] = edited_word_count
            decision_log["deletion_percent"] = int((1 - edited_word_count/baseline_word_count) * 100)
            if job_dir:
                try:
                    (job_dir / "editor_decision.json").write_text(
                        json.dumps(decision_log, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
            return latex_body

        decision_log["decision"] = "accept"
        if job_dir:
            try:
                (job_dir / "editor_decision.json").write_text(
                    json.dumps(decision_log, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass
        return body
    except Exception:
        # На ошибку просто вернём исходник
        return latex_body
