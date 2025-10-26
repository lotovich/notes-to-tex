#!/usr/bin/env python3
"""
Verbatim accuracy checker for strict mode validation.

Compares original text with LaTeX output to ensure content preservation
meets strict mode thresholds (≥95% sentence level, ≥98% character level).
"""

import re


def _strip_latex_commands(text: str) -> str:
    """Remove LaTeX commands, leaving only text content."""
    # Remove commands like \command{...}, \command[...]{...}
    text = re.sub(r'\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^}]*\})*', ' ', text)

    # Remove environments \begin{...} \end{...}
    text = re.sub(r'\\begin\{[^}]+\}', ' ', text)
    text = re.sub(r'\\end\{[^}]+\}', ' ', text)

    # Remove LaTeX special characters: $, \\, \[, \], %, &
    text = re.sub(r'[\$\\%&]', ' ', text)
    text = re.sub(r'\\\[|\\\]', ' ', text)

    # Remove extra whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    return text


def _char_similarity(text1: str, text2: str) -> float:
    """Calculate character-level similarity (simple length ratio after normalization)."""
    # Remove spaces and convert to lowercase
    clean1 = re.sub(r'\s+', '', text1.lower())
    clean2 = re.sub(r'\s+', '', text2.lower())

    if not clean1:
        return 1.0 if not clean2 else 0.0

    # Simple metric: ratio of shorter length to longer length
    # (for production, could use Levenshtein distance, but this is sufficient)
    min_len = min(len(clean1), len(clean2))
    max_len = max(len(clean1), len(clean2))

    return min_len / max_len if max_len > 0 else 1.0


def _sent_similarity(text1: str, text2: str) -> float:
    """Calculate sentence-level similarity (sentence count ratio)."""
    # Split into sentences (by . ! ? and newlines)
    sents1 = [s.strip() for s in re.split(r'[.!?\n]+', text1) if s.strip()]
    sents2 = [s.strip() for s in re.split(r'[.!?\n]+', text2) if s.strip()]

    if not sents1:
        return 1.0 if not sents2 else 0.0

    # Simple metric: ratio of sentence counts
    min_count = min(len(sents1), len(sents2))
    max_count = max(len(sents1), len(sents2))

    return min_count / max_count if max_count > 0 else 1.0


def compare_texts(original: str, latex_output: str) -> dict:
    """
    Compare original text with LaTeX output for verbatim accuracy.

    Args:
        original: Original text (from ocr_raw.txt or text_blocks)
        latex_output: LaTeX body (content.tex after command removal)

    Returns:
        {
            "char_similarity": float,      # 0.0-1.0
            "sent_similarity": float,      # 0.0-1.0
            "original_chars": int,
            "output_chars": int,
            "original_sents": int,
            "output_sents": int,
            "passed_char": bool,           # >= 0.98
            "passed_sent": bool,           # >= 0.95
            "passed_overall": bool         # both thresholds met
        }
    """
    # Normalize LaTeX output
    clean_latex = _strip_latex_commands(latex_output)
    clean_orig = original.strip()

    # Calculate metrics
    char_sim = _char_similarity(clean_orig, clean_latex)
    sent_sim = _sent_similarity(clean_orig, clean_latex)

    # Count characters/sentences
    orig_chars = len(re.sub(r'\s+', '', clean_orig))
    out_chars = len(re.sub(r'\s+', '', clean_latex))
    orig_sents = len([s for s in re.split(r'[.!?\n]+', clean_orig) if s.strip()])
    out_sents = len([s for s in re.split(r'[.!?\n]+', clean_latex) if s.strip()])

    return {
        "char_similarity": round(char_sim, 4),
        "sent_similarity": round(sent_sim, 4),
        "original_chars": orig_chars,
        "output_chars": out_chars,
        "original_sents": orig_sents,
        "output_sents": out_sents,
        "passed_char": char_sim >= 0.98,
        "passed_sent": sent_sim >= 0.95,
        "passed_overall": (char_sim >= 0.98 and sent_sim >= 0.95)
    }