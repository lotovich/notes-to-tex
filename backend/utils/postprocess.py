# backend/utils/postprocess.py
# -*- coding: utf-8 -*-
"""
Lightweight LaTeX post-processing for notes-to-tex.

Goals:
- Map \chapter{...} -> \section{...} (article class)
- Merge consecutive display equations (\[...\]) into one align* block
- Enforce two braces {}{} after theorem-like boxes (if the model forgot)
- Handle lecture/course headers:
  * If the **first non-empty** line looks like a course/lecture header and there's no \section yet,
    convert it to \section{...}.
  * If similar header lines appear later **inside the body**, turn them into LaTeX comments
    (do not render as plain text).
"""

from __future__ import annotations
import re

# Environments that must use two pairs of braces after \begin{...}
BOX_ENVS = [
    "definitionbox", "theoremnox", "lemmanox",
    "corollarybox", "examplebox", "notebox", "questionbox"
]

# Patterns: course/lecture headers (extend as needed)
HEADER_RE = re.compile(
    r"^\s*(?:"
    r"(?:[A-Z]{2,}\s?\d{2,3}[\w\- ]*)|"   # e.g., MAS 201, MATH203, CS-101 ...
    r"(?:Lecture\s*\d+[^\n]*)|"           # Lecture 9-2, Lecture 12 ...
    r"(?:Лекция\s*\d+[^\n]*)"             # Russian "Лекция 3" etc.
    r")\s*$",
    re.IGNORECASE | re.MULTILINE
)

SECTION_RE = re.compile(r"\\section\*?\{", re.M)

def _map_chapter_to_section(latex: str) -> str:
    return re.sub(r'\\chapter\*?\{([^}]*)\}', r'\\section{\1}', latex)

def _ensure_two_braces_for_boxes(latex: str) -> str:
    """
    Ensure `\begin{env}{Title}{}` or `\begin{env}{}{}` form.
    Safe even if already correct.
    """
    for env in BOX_ENVS:
        # Case A: has one title brace -> add the second empty {}
        latex = re.sub(
            rf'(\\begin\{{{env}\}}\{{[^}}]*\}})(\s*?\n)',
            rf'\1{{}}\2',
            latex
        )
        # Case B: has no braces at all -> add {}{}
        latex = re.sub(
            rf'(\\begin\{{{env}\}}\s*)(\n)',
            rf'\1{{}}{{}}\2',
            latex
        )
    return latex

def _merge_consecutive_displays_to_align(latex: str) -> str:
    """
    Merge 2+ consecutive \[...\] blocks into one align*.
    Try aligning by '=' when present (and no '&' already).
    """
    # Sequence of 2+ display blocks
    pat = re.compile(r'(?:\s*\\\[(.*?)\\\]\s*){2,}', re.S)

    def to_align(match: re.Match) -> str:
        block = match.group(0)
        parts = re.findall(r'\\\[(.*?)\\\]', block, re.S)
        rows = []
        for p in parts:
            p = p.strip()
            # If there's '=', turn "lhs = rhs" to "lhs &= rhs"
            if '=' in p and '&' not in p:
                lhs, rhs = p.split('=', 1)
                rows.append(f"{lhs.strip()} &= {rhs.strip()}")
            else:
                rows.append(p)
        body = " \\\\\n    ".join(rows)
        return f"\n\\begin{{align*}}\n    {body}\n\\end{{align*}}\n"

    return pat.sub(to_align, latex)

def _promote_top_header_to_section(latex: str) -> str:
    """
    If the very first meaningful line is a header (course/lecture) and
    there is no \\section yet, convert that line to \\section{...}.
    Later occurrences of similar headers are made comments.
    """
    # Early exit if there's already a section
    has_section = bool(SECTION_RE.search(latex))

    # Split to lines for fine-grained edits
    lines = latex.splitlines()
    n = len(lines)

    # Find first non-empty, non-comment line
    idx_first = None
    for i, ln in enumerate(lines):
        if ln.strip() and not ln.strip().startswith('%'):
            idx_first = i
            break

    if idx_first is not None:
        first_line = lines[idx_first]
        if HEADER_RE.match(first_line):
            if not has_section:
                # Promote to section title
                title = first_line.strip()
                lines[idx_first] = f"\\section{{{title}}}"
            else:
                # Already have sections: don't put raw header in body
                lines[idx_first] = f"% editor note: header kept as meta: {first_line.strip()}"

    # For the rest of lines, demote raw headers to comments (avoid in-body stray text)
    for j in range((idx_first + 1) if idx_first is not None else 0, n):
        ln = lines[j]
        if ln.strip() and not ln.strip().startswith('\\') and HEADER_RE.match(ln):
            lines[j] = f"% editor note: meta header suppressed: {ln.strip()}"

    return "\n".join(lines)

def enforce_latex_conventions(latex: str) -> str:
    latex = _map_chapter_to_section(latex)
    latex = _ensure_two_braces_for_boxes(latex)
    latex = _merge_consecutive_displays_to_align(latex)
    latex = _promote_top_header_to_section(latex)
    return latex