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
        ("equation_drop_guard", v_equation_drop_guard),
        ("figure_placeholder_guard", v_figure_placeholder_guard),
        ("personal_notes_filter", v_personal_notes_filter),
    ]:
        latex_body, info = v(latex_body, meta)
        report[name] = info
    return latex_body, report
