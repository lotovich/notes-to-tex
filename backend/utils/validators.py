# backend/utils/validators.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import re, json
from typing import Dict, Any, List, Tuple, Callable

# --- парс уравнений из body ---
_FENCE_EQ = re.compile(
    r"\\\[(.*?)\\\]|\\begin\{equation\*?\}(.*?)\\end\{equation\*?\}|\\begin\{align\*?\}([\s\S]*?)\\end\{align\*?\}",
    re.S
)

def _as_list(val):
    """Надёжно превращает что угодно в список."""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, dict):
        return [val]
    # числа, строки и прочее — в пустой список
    return []

def _list_equations_in_body(latex_body: str) -> List[str]:
    eqs: List[str] = []
    for m in _FENCE_EQ.finditer(latex_body):
        for g in m.groups():
            if g and g.strip():
                eqs.append(g.strip())
    return eqs

def _normalize_ltx(s: str) -> str:
    return re.sub(r"\s+", "", s or "").replace("\\,", "").replace("\\;", "")

# --- helpers for completeness/summary checks ---
_SUMMARY_PAT = re.compile(r"\b(the document|this document|it explains|in summary|to summarize)\b", re.I)
_SUMMARY_PAT_RU = re.compile(r"\b(документ|в\s+итоге|подводя\s+итог|резюмируя|он\s+объясняет)\b", re.I)

_DEF_WORD_RE = re.compile(r"[\w\-]+", re.U)
_CMD_RE = re.compile(r"\\[a-zA-Z@]+\*?(?:\[[^\]]*\])?(?:\{[^}]*\})?")


def _word_count_plain(text: str) -> int:
    if not text:
        return 0
    # remove simple LaTeX commands and count words
    s = _CMD_RE.sub(" ", text)
    return len(_DEF_WORD_RE.findall(s))


def _meta_stats(meta: Dict[str, Any]) -> Dict[str, int]:
    """Return counts inferred from meta: equations, figures, paragraphs, lists."""
    stats = {"eq": 0, "fig": 0, "para": 0, "lists": 0, "blocks": 0}
    if not isinstance(meta, dict):
        return stats

    # PRIMARY JSON with blocks
    blocks = meta.get("blocks")
    if isinstance(blocks, list):
        stats["blocks"] = len(blocks)
        for b in blocks:
            t = (b or {}).get("type")
            if t == "equation":
                stats["eq"] += 1
            elif t == "figure":
                stats["fig"] += 1
            elif t == "paragraph":
                stats["para"] += 1
            elif t == "list":
                stats["lists"] += 1
        return stats

    # Legacy META keys
    stats["eq"] = len(_as_list(meta.get("equations_captured")))
    stats["fig"] = len(_as_list(meta.get("figures_captured")))
    # Paragraphs/blocks are unknown in legacy; keep as 0
    return stats


def v_no_summary_narrative(latex_body: str, meta: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Warn if the output looks like a narrative summary rather than a transfer."""
    hits = []
    body = latex_body or ""
    for pat in (_SUMMARY_PAT, _SUMMARY_PAT_RU):
        for m in pat.finditer(body):
            frag = body[max(0, m.start()-20): m.end()+20]
            hits.append(frag)
    info = {"checked": True, "summary_hits": len(hits)}
    if hits:
        latex_body += "\n% validator: potential narrative/summary phrases detected; please revise.\n"
        for h in hits[:5]:  # cap notes
            latex_body += f"% hint: ...{h}...\n"
    return latex_body, info


def v_completeness_guard(latex_body: str, meta: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Soft completeness check:
    - Compare equations found in body vs. expected in meta (primary blocks or legacy keys).
    - Compare rough word counts between meta text (if available) and body.
    Does not delete content; only annotates if suspiciously low.
    """
    stats = _meta_stats(meta)
    body_eqs = _list_equations_in_body(latex_body)

    # Equation ratio
    expected_eq = max(stats.get("eq", 0), 0)
    got_eq = len(body_eqs)
    eq_ok = True if expected_eq == 0 else (got_eq >= max(1, int(0.6 * expected_eq)))

    # Word ratio (best-effort)
    meta_text = None
    # primary JSON paragraphs
    blocks = meta.get("blocks") if isinstance(meta, dict) else None
    if isinstance(blocks, list):
        meta_text = "\n".join([(b or {}).get("text", "") for b in blocks if (b or {}).get("type") == "paragraph"]) or None
    # legacy normalized_capture fallback
    if meta_text is None:
        meta_text = (meta or {}).get("normalized_capture") or (meta or {}).get("raw_capture") or ""

    meta_wc = _word_count_plain(meta_text)
    body_wc = _word_count_plain(latex_body)
    wc_ok = True if meta_wc == 0 else (body_wc >= max(5, int(0.7 * meta_wc)))

    info = {
        "checked": True,
        "expected_eq": expected_eq,
        "equations_in_body": got_eq,
        "eq_ok": bool(eq_ok),
        "meta_wc": meta_wc,
        "body_wc": body_wc,
        "wc_ok": bool(wc_ok),
    }

    if not eq_ok or not wc_ok:
        latex_body += "\n% validator: completeness guard triggered.\n"
        if not eq_ok:
            latex_body += f"% hint: equations present {got_eq}/{expected_eq} (>=60% expected).\n"
        if not wc_ok:
            latex_body += f"% hint: word count body/meta {body_wc}/{meta_wc} (>=70% expected).\n"

    return latex_body, info

def v_equation_drop_guard(latex_body: str, meta: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """
    Если модель заявила equations_captured в META — проверим, что каждая raw/normalized
    встречается в latex_body. Если нет — добавим её с todo в конец.
    """
    captured = (meta or {}).get("equations_captured", []) or []
    if not captured:
        return latex_body, {"checked": False}

    present = _list_equations_in_body(latex_body)
    present_norm = [_normalize_ltx(p) for p in present]

    missing: List[str] = []
    for e in captured:
        for key in ("normalized", "raw"):
            ltx = (e or {}).get(key)
            if not ltx:
                continue
            if _normalize_ltx(ltx) not in present_norm:
                missing.append(ltx)

    if not missing:
        return latex_body, {"checked": True, "missing": 0}

    patch = ["% validator: equations recovered from META (were missing in body)"]
    for ltx in missing:
        patch.append("% TODO validator: verify recovered equation below")
        patch.append("\\[ " + ltx + " \\]")

    return latex_body + "\n\n" + "\n".join(patch) + "\n", {"checked": True, "missing": len(missing)}

def v_figure_placeholder_guard(latex_body: str, meta: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """
    Если в META есть фигуры, а в тексте нет includegraphics — вставляем их.
    В strict-режиме добавляем только TODO, в book-режиме — полноценные окружения figure.
    """
    # Надёжно достаём список фигур из разных возможных ключей
    figs = _as_list(
        (meta or {}).get("figures")
        or (meta or {}).get("figures_info")
        or (meta or {}).get("figures_captured")
    )
    if not figs:
        return latex_body, {"checked": False}

    # Если уже есть картинки — уходим
    has_include = "\\includegraphics" in (latex_body or "")
    if has_include:
        return latex_body, {"checked": True, "inferred": "present"}

    # Режим (по умолчанию book)
    strict = str((meta or {}).get("mode", "book")).lower() == "strict"

    blocks: list[str] = []
    added_envs = 0

    for idx, f in enumerate(figs, start=1):
        f = f or {}
        # filename/path из META (мы ранее пишем туда "figures/fig_pX_iY.png")
        path = f.get("path") or f.get("filename")
        caption = (f.get("caption_raw") or "Insert figure from source")

        # Лёгкое экранирование, чтобы не ломать LaTeX
        caption = caption.replace("{", "\\{").replace("}", "\\}").replace("%", "\\%")
        path_tex = str(path).replace("\\", "/") if path else None

        if strict or not path_tex:
            # Строгий режим или нет нормального пути — ставим TODO-комментарий
            blocks.append(f"% TODO validator: insert figure manually (source caption: {caption})")
        else:
            # Полноценный environment
            blocks.append(
                "\\begin{figure}[h]\n"
                "    \\centering\n"
                f"    \\includegraphics[width=0.8\\textwidth]{{{path_tex}}}\n"
                f"    \\caption{{{caption}}}\n"
                f"    \\label{{fig:auto-{idx}}}\n"
                "\\end{figure}"
            )
            added_envs += 1

    if not blocks:
        return latex_body, {"checked": True, "inferred": "no-blocks"}

    new_body = (latex_body or "").rstrip() + "\n\n% validator: inserted figures\n" + "\n\n".join(blocks) + "\n"
    return new_body, {"checked": True, "placeholders_added": len(blocks), "figures_inserted": added_envs}

def v_personal_notes_filter(latex_body: str, meta: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """
    Если META указал dropped_notes, мягко предупредим, если совпадения просочились в body.
    Ничего не удаляем автоматически.
    """
    dropped = (meta or {}).get("dropped_notes", []) or []
    hits = 0
    for n in dropped:
        if n and n.strip() and n.strip() in latex_body:
            hits += 1
    if hits:
        latex_body += "\n\n% validator: personal notes were reintroduced; please double-check.\n"
        latex_body += "% TODO validator: personal notes detected in body; verify and remove if needed.\\n"
    return latex_body, {"checked": True, "notes_hits": hits}

Validator = Callable[[str, Dict[str, Any]], Tuple[str, Dict[str, Any]]]

def run_validators(latex_body: str, meta: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Запускает общий набор валидаторов (предмет-нейтральные)."""
    report: Dict[str, Any] = {}
    for name, v in [
        ("no_summary_narrative", v_no_summary_narrative),
        ("completeness_guard", v_completeness_guard),
        ("equation_drop_guard", v_equation_drop_guard),
        ("figure_placeholder_guard", v_figure_placeholder_guard),
        ("personal_notes_filter", v_personal_notes_filter),
    ]:
        latex_body, info = v(latex_body, meta)
        report[name] = info
    return latex_body, report


def finalize_content(tex_content: str) -> str:
    """Remove validator hints and comments before writing final content.tex"""
    lines = tex_content.split('\n')
    clean_lines = []

    for line in lines:
        stripped = line.strip()
        # Skip validator comments
        if stripped.startswith('% validator:') or stripped.startswith('% hint:'):
            continue
        clean_lines.append(line)

    return '\n'.join(clean_lines)
