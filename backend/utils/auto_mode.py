"""
Auto-Mode Selector for Notes-to-TeX (ЭТАП 2)

This module automatically determines whether to use 'book' or 'strict' mode
based on analysis of the raw OCR text content.

Logic:
1. Check BLOCKERS (→ BOOK if triggered)
2. If no blockers → Analyze content type for STRICT
3. Default: BOOK (safe choice)

Author: AI Assistant
Version: 1.0
"""

import re
from typing import Dict, List, Set


# ========================================
# CONSTANTS - BLOCKERS (→ BOOK)
# ========================================

INFORMAL_ABBREVIATIONS = {
    "ru": [
        # Базовые
        "т.к.", "т.е.", "т.п.", "т.д.", "и т.д.", "и т.п.",
        "=> ", "-> ", "~",

        # Интернет-сленг
        "и тд", "итд", "кмк", "кст", "мб", "имхо",
        "спс", "пжл", "пж", "норм", "ок",

        # Дополнительные
        "в-общем", "в общем", "ну тип", "чет", "чё"
    ],
    "en": [
        # Arrows and symbols
        "=> ", "-> ", "~",

        # Common abbreviations
        "btw", "imo", "fyi", "aka", "asap",
        "etc.", "w/", "w/o", "thx", "pls", "plz",

        # Internet slang
        "lol", "idk", "tbh", "smth", "sth",
        "gonna", "wanna", "kinda", "sorta"
    ]
}

PERSONAL_MARKERS = {
    "ru": [
        # TODO-подобные
        "TODO", "todo", "тудушка",

        # Восклицания
        "!!!", "???", "!?",

        # Императивы (более специфичные для заметок)
        "ВАЖНО", "важно", "ВАЖНО:",
        "НЕ ЗАБЫТЬ", "не забыть",
        "разобраться", "понять это",
        "спросить", "уточнить",
        "проверить", "посмотреть",
        "REMINDER", "напоминание",

        # Оценки (неформальные)
        "круто", "фигня", "непонятно"
    ],
    "en": [
        # TODO-like
        "TODO", "todo", "FIXME", "NOTE",

        # Exclamations
        "!!!", "???", "!?",

        # Imperatives (more specific to notes)
        "IMPORTANT", "important", "NOTE",
        "DON'T FORGET", "don't forget",
        "figure out", "understand this",
        "ask about", "clarify",
        "check", "review",
        "REMINDER", "reminder",

        # Evaluations
        "cool", "bad", "unclear"
    ]
}

LECTURE_META_PATTERNS = {
    "ru": [
        r"Лекция\s+\d+",           # "Лекция 5"
        r"Пара\s+\d+",             # "Пара 3"
        r"Занятие\s+\d+",          # "Занятие 7"
        r"Семинар\s+\d+",          # "Семинар 2"
        r"Конспект\s+от",          # "Конспект от 12.03"
        # Date ONLY in lecture context
        r"(?:Лекция|Пара|Занятие|Семинар|Препод|Преподаватель|Курс).*?\d{2}\.\d{2}\.\d{4}",
        r"Преподаватель:",
        r"Препод:",
    ],
    "en": [
        r"Lecture\s+\d+",          # "Lecture 5"
        r"Class\s+\d+",            # "Class 3"
        r"Seminar\s+\d+",          # "Seminar 2"
        r"Notes\s+from",           # "Notes from 03/12"
        # Date ONLY in lecture context
        r"(?:Lecture|Class|Seminar|Professor|Instructor).*?(?:[A-Z][a-z]+\s+\d{1,2},\s+\d{4}|\d{2}/\d{2}/\d{4})",
        r"Professor:",
        r"Instructor:",
    ]
}

CASUAL_PHRASES = {
    "ru": [
        "короче", "в общем", "типа", "типо",
        "ну вот", "ну", "блин", "короч",
        "как бы", "чет", "чёт",
        "кароч", "вообще", "вобще",
        "ваще", "прост", "просто"
    ],
    "en": [
        "basically", "kinda", "sorta",
        "you know", "whatever", "stuff",
        "gonna", "wanna"
    ]
}

# Пороги для блокеров
ABBREVIATION_THRESHOLD = 2
PERSONAL_MARKER_THRESHOLD = 2
LECTURE_META_THRESHOLD = 1
CASUAL_PHRASE_THRESHOLD = 2

# Уровни confidence
LECTURE_META_CONFIDENCE = 0.9


# ========================================
# TEXT EXTRACTION UTILITIES
# ========================================

def extract_text_from_meta(meta: dict) -> str:
    """
    Extract text from meta.json blocks for classification.
    Used as fallback when ocr_raw.txt is empty/failed.

    Args:
        meta: Meta dictionary with 'blocks' key

    Returns:
        Concatenated text from all paragraph and section blocks
    """
    if not isinstance(meta, dict):
        return ""

    blocks = meta.get("blocks", [])
    if not blocks:
        return ""

    text_parts = []
    for block in blocks:
        if not isinstance(block, dict):
            continue

        block_type = block.get("type")

        if block_type in ("section", "paragraph"):
            text = block.get("text", "")
            if text:
                text_parts.append(text)
        elif block_type == "list":
            items = block.get("items", [])
            if items:
                text_parts.extend(items)

    return "\n\n".join(text_parts)


# ========================================
# HELPER FUNCTIONS - BLOCKER COUNTERS
# ========================================

def count_abbreviations(text: str, lang: str) -> int:
    """Count unique abbreviations found in text."""
    if lang not in INFORMAL_ABBREVIATIONS:
        return 0

    abbr_list = INFORMAL_ABBREVIATIONS[lang]
    found = set()

    for abbr in abbr_list:
        # For short abbreviations, require word boundaries to avoid false positives
        if len(abbr.strip()) <= 3:
            import re
            # Create pattern with word boundaries for short abbreviations
            pattern = r'\b' + re.escape(abbr.strip()) + r'\b'
            if re.search(pattern, text, re.I):
                found.add(abbr)
        else:
            # For longer phrases, use simple substring search
            if abbr in text:
                found.add(abbr)

    return len(found)


def count_personal_markers(text: str, lang: str) -> int:
    """Count unique personal markers found in text."""
    if lang not in PERSONAL_MARKERS:
        return 0

    marker_list = PERSONAL_MARKERS[lang]
    found = set()

    for marker in marker_list:
        if marker in text:
            found.add(marker)

    return len(found)


def count_lecture_metadata(text: str, lang: str) -> int:
    """Count lecture metadata patterns."""
    if lang not in LECTURE_META_PATTERNS:
        return 0

    patterns = LECTURE_META_PATTERNS[lang]
    count = 0

    for pattern in patterns:
        if re.search(pattern, text, re.I | re.M):
            count += 1

    return count


def count_casual_phrases(text: str, lang: str) -> int:
    """Count unique casual phrases found in text."""
    if lang not in CASUAL_PHRASES:
        return 0

    phrase_list = CASUAL_PHRASES[lang]
    found = set()

    for phrase in phrase_list:
        if phrase in text.lower():
            found.add(phrase)

    return len(found)


# ========================================
# STRICT CONTENT DETECTORS
# ========================================

def detect_quality_latex(text: str) -> bool:
    """Detect high-quality LaTeX (already well-formatted)."""

    # Check for LaTeX commands in source
    has_sections = len(re.findall(r'\\section', text)) >= 2
    has_labels = len(re.findall(r'\\label\{', text)) >= 1
    has_refs = len(re.findall(r'\\(?:ref|eqref)\{', text)) >= 1

    # NEW: Check for numbered structure (from rendered PDF)
    has_numbered_sections = len(re.findall(r'^\d+\s+[A-Z]', text, re.M)) >= 2
    has_subsections = len(re.findall(r'^\d+\.\d+\s+[A-Z]', text, re.M)) >= 1
    has_equation_numbers = len(re.findall(r'\(\d+\)', text)) >= 2

    # Check for LaTeX-style equation references
    has_latex_refs = bool(re.search(r'Equation\s+\(\d+\)|equation\s+\(\d+\)', text, re.I))

    # Check for OCR noise
    ocr_noise_patterns = [
        r'\?\?+',
        r'â€™',
        r'\[\[illegible\]\]',
    ]
    has_noise = any(re.search(p, text) for p in ocr_noise_patterns)

    # Decision: LaTeX source OR rendered structured document
    is_latex_source = (has_sections or (has_labels and has_refs)) and not has_noise
    is_rendered_latex = (has_numbered_sections and has_equation_numbers) or \
                        (has_subsections and has_latex_refs)

    return is_latex_source or is_rendered_latex


def detect_formal_essay(text: str, lang: str) -> bool:
    """
    Detect formal essay or article (not study notes).

    Indicators:
    - Long paragraphs (>50 words)
    - Formal connectors
    - No numbered lists like "1)", "2)"
    """
    # 1. Long paragraphs - split by multiple newlines and filter empty
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    long_paragraphs = sum(1 for p in paragraphs if len(p.split()) > 50)

    # 2. Formal connectors
    formal_connectors = {
        "ru": [
            "следовательно", "таким образом", "в результате",
            "необходимо отметить", "в заключение",
            "более того", "кроме того", "иными словами"
        ],
        "en": [
            "therefore", "consequently", "as a result",
            "it should be noted", "in conclusion",
            "moreover", "furthermore", "in other words"
        ]
    }

    if lang not in formal_connectors:
        connector_count = 0
    else:
        connector_count = sum(1 for conn in formal_connectors[lang]
                             if conn.lower() in text.lower())

    # 3. No numbered lists
    no_numbered_lists = len(re.findall(r"^\d+\)", text, re.M)) < 3

    # Adaptive threshold based on text length
    text_length = len(text)

    if text_length < 1000:
        # Short essay: 1 long para + 1 connector, OR 2 long paras
        return (long_paragraphs >= 1 and connector_count >= 1) or long_paragraphs >= 2
    else:
        # Long essay: keep requirements
        return (long_paragraphs >= 2 and connector_count >= 1) or connector_count >= 2


def detect_narrative_text(text: str, lang: str) -> bool:
    """
    Detect narrative/literary text (story, essay).

    Uses regex with word boundaries to catch past tense verbs
    even when followed by punctuation.

    CRITICAL: Uses re.UNICODE flag for Cyrillic text support.
    """

    # Past tense markers with word boundaries
    past_tense_markers = {
        "ru": [
            r"\bбыл\b", r"\bбыла\b", r"\bбыли\b", r"\bбыло\b",
            r"\bстал\b", r"\bстала\b", r"\bстали\b", r"\bстало\b",
            r"\bпошёл\b", r"\bпошла\b", r"\bпошли\b",
            r"\bсказал\b", r"\bсказала\b", r"\bсказали\b",
            r"\bпролетала\b", r"\bпролетал\b", r"\bпролетели\b",
            r"\bсмешались\b", r"\bсмешалось\b", r"\bсмешалась\b",
            r"\bоставалась\b", r"\bоставался\b", r"\bоставалось\b",
            r"\bоставались\b",
            r"\bотделились\b",
            r"\bотделилась\b",
            r"\bродила\b",
        ],
        "en": [
            r"\bwas\b", r"\bwere\b", r"\bhad\b",
            r"\bwent\b", r"\bsaid\b", r"\bbecame\b",
            r"\bwalked\b", r"\blooked\b", r"\bthought\b",
            r"\bwrote\b", r"\boffered\b", r"\bargued\b"
        ]
    }

    # Count matches with UNICODE flag for Cyrillic support
    past_count = 0
    for pattern in past_tense_markers.get(lang, []):
        # CRITICAL: re.UNICODE flag is essential for Cyrillic word boundaries!
        if re.search(pattern, text, re.IGNORECASE | re.UNICODE):
            past_count += 1

    # Check for dialogue markers
    has_quotes = ('"' in text or '«' in text or '»' in text or
                 text.count("'") >= 4)

    # Decision: ≥3 past tense markers OR quotes
    return past_count >= 3 or has_quotes


def detect_formal_math(text: str, lang: str) -> bool:
    """
    Detect formal mathematical proof/theorem.

    Indicators:
    - Math keywords (Theorem, Lemma, Proof)
    - Many formulas
    - Logical connectors
    """
    # 1. Math keywords
    math_keywords = {
        "ru": [
            "Теорема", "Лемма", "Доказательство",
            "Следствие", "Утверждение",
            "необходимо и достаточно",
            "тогда и только тогда"
        ],
        "en": [
            "Theorem", "Lemma", "Proof",
            "Corollary", "Proposition",
            "if and only if",
            "necessary and sufficient"
        ]
    }

    if lang not in math_keywords:
        keyword_count = 0
    else:
        keyword_count = sum(1 for kw in math_keywords[lang] if kw in text)

    # 2. Formula count
    formula_count = text.count('$') + text.count('\\[') + text.count('\\begin{equation')

    return keyword_count >= 2 and formula_count >= 5


def detect_formulas_only(text: str) -> bool:
    """
    Detect if document is ONLY formulas with minimal text.
    This should trigger BOOK mode (needs explanations).

    Logic:
    - Simple heuristic: count math symbols vs regular words
    - If too many formulas with little explanatory text -> formulas-only
    """
    # Count formula indicators
    dollar_count = text.count('$')
    equation_count = text.count('\\begin{equation')
    display_math_count = text.count('\\[')

    total_formula_indicators = dollar_count + equation_count + display_math_count

    # Count words (rough estimate of explanatory content)
    words = re.findall(r'\b[a-zA-Zа-яё]+\b', text, re.I)
    word_count = len(words)

    # If many formula indicators but few words → formulas only
    if total_formula_indicators >= 6 and word_count < 20:
        return True

    # Alternative: if the ratio of formula indicators to words is very high
    if word_count > 0 and (total_formula_indicators / word_count) > 0.5:
        return True

    return False


# ========================================
# MAIN CLASSIFICATION FUNCTION
# ========================================

def classify_content_mode(text: str, meta: dict) -> dict:
    """
    Classify content as 'book' or 'strict' mode.

    Args:
        text: Raw OCR text from ocr_raw.txt
        meta: Metadata dict with 'language' field

    Returns:
        {
            "mode": "book" | "strict",
            "confidence": float (0.0-1.0),
            "reasons": [list of reason strings],
            "blocked_by": str | None,  # Which blocker triggered (if any)
            "scores": {
                "abbreviations": int,
                "personal_markers": int,
                "lecture_metadata": int,
                "casual_phrases": int
            }
        }

    Logic:
        1. Check BLOCKERS (→ BOOK if triggered)
        2. If no blockers → Analyze content type
        3. Default: BOOK (safe choice)
    """
    lang = meta.get("language", "en")


    reasons = []
    scores = {
        "abbreviations": 0,
        "personal_markers": 0,
        "lecture_metadata": 0,
        "casual_phrases": 0
    }

    # ========================================
    # SPECIAL CASE: FORMULAS-ONLY → BOOK (before LaTeX check)
    # ========================================

    if detect_formulas_only(text):
        # Fill scores for statistics
        scores["abbreviations"] = count_abbreviations(text, lang)
        scores["personal_markers"] = count_personal_markers(text, lang)
        scores["lecture_metadata"] = count_lecture_metadata(text, lang)
        scores["casual_phrases"] = count_casual_phrases(text, lang)

        return {
            "mode": "book",
            "confidence": 0.9,
            "reasons": ["Formulas-only document → needs explanations"],
            "blocked_by": "formulas_only",
            "scores": scores
        }

    # ========================================
    # PRIORITY CHECK: High-quality LaTeX → STRICT immediately
    # ========================================

    if detect_quality_latex(text):
        # Fill scores for statistics
        scores["abbreviations"] = count_abbreviations(text, lang)
        scores["personal_markers"] = count_personal_markers(text, lang)
        scores["lecture_metadata"] = count_lecture_metadata(text, lang)
        scores["casual_phrases"] = count_casual_phrases(text, lang)

        return {
            "mode": "strict",
            "confidence": 0.85,
            "reasons": ["High-quality LaTeX (already well-formatted)"],
            "blocked_by": None,
            "scores": scores
        }

    # ========================================
    # ЭТАП 1: ПРОВЕРКА БЛОКЕРОВ
    # ========================================

    # Блокер 1: Сокращения
    abbr_count = count_abbreviations(text, lang)
    scores["abbreviations"] = abbr_count

    if abbr_count >= ABBREVIATION_THRESHOLD:
        return {
            "mode": "book",
            "confidence": 1.0,
            "reasons": [f"🚫 BLOCKER: Study abbreviations ({abbr_count})"],
            "blocked_by": "abbreviations",
            "scores": scores
        }

    # Блокер 2: Личные пометки
    personal_count = count_personal_markers(text, lang)
    scores["personal_markers"] = personal_count

    if personal_count >= PERSONAL_MARKER_THRESHOLD:
        return {
            "mode": "book",
            "confidence": 1.0,
            "reasons": [f"🚫 BLOCKER: Personal study markers ({personal_count})"],
            "blocked_by": "personal_notes",
            "scores": scores
        }

    # Блокер 3: Метаданные лекции
    lecture_meta = count_lecture_metadata(text, lang)
    scores["lecture_metadata"] = lecture_meta

    if lecture_meta >= LECTURE_META_THRESHOLD:
        return {
            "mode": "book",
            "confidence": LECTURE_META_CONFIDENCE,
            "reasons": ["🚫 BLOCKER: Lecture metadata"],
            "blocked_by": "lecture_metadata",
            "scores": scores
        }

    # Блокер 4: Разговорный стиль
    casual_count = count_casual_phrases(text, lang)
    scores["casual_phrases"] = casual_count

    if casual_count >= CASUAL_PHRASE_THRESHOLD:
        return {
            "mode": "book",
            "confidence": 1.0,
            "reasons": [f"🚫 BLOCKER: Casual language ({casual_count})"],
            "blocked_by": "casual_language",
            "scores": scores
        }


    # ========================================
    # ЭТАП 3: ОПРЕДЕЛЕНИЕ ТИПА STRICT КОНТЕНТА
    # ========================================

    strict_reasons = []

    if detect_quality_latex(text):
        strict_reasons.append("High-quality LaTeX (already well-formatted)")

    if detect_formal_essay(text, lang):
        strict_reasons.append("Formal essay/article")

    if detect_narrative_text(text, lang):
        strict_reasons.append("Literary/narrative text")

    if detect_formal_math(text, lang):
        strict_reasons.append("Formal mathematical proof")

    # ========================================
    # ЭТАП 4: ФИНАЛЬНОЕ РЕШЕНИЕ
    # ========================================

    if len(strict_reasons) >= 1:
        # Есть хотя бы 1 признак STRICT → выбираем STRICT
        confidence = 0.75 + len(strict_reasons) * 0.05
        confidence = min(confidence, 0.95)

        return {
            "mode": "strict",
            "confidence": round(confidence, 2),
            "reasons": strict_reasons,
            "blocked_by": None,
            "scores": scores
        }

    # ========================================
    # ЭТАП 5: ПО УМОЛЧАНИЮ BOOK (безопасный выбор)
    # ========================================

    default_reasons = []

    if abbr_count > 0:
        default_reasons.append(f"Has abbreviations ({abbr_count}), but not a blocker")
    if personal_count > 0:
        default_reasons.append(f"Has markers ({personal_count}), but not a blocker")

    if not default_reasons:
        default_reasons.append("Insufficient indicators for strict mode → safe choice: book")

    return {
        "mode": "book",
        "confidence": 0.6,
        "reasons": default_reasons,
        "blocked_by": None,
        "scores": scores
    }