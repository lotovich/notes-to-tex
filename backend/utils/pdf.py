# backend/utils/pdf.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import fitz  # PyMuPDF
from typing import List, Tuple

def pdf_to_images(pdf_path: str, dpi: int = 200, max_pages: int = 12) -> List[bytes]:
    """
    Рендерит первые max_pages страниц PDF в PNG-байты (dpi ~ 200-300 для рукописного).
    Возвращает список bytes (каждый элемент — содержимое PNG).
    """
    images: List[bytes] = []
    zoom = dpi / 72.0  # 72pt = 1in
    mat = fitz.Matrix(zoom, zoom)

    with fitz.open(pdf_path) as doc:
        n = min(len(doc), max_pages)
        for i in range(n):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            images.append(pix.tobytes("png"))
    return images