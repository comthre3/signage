"""Render a PDF to a sequence of fixed-size PNGs.

Single responsibility: takes a PDF path + target dimensions, writes
N PNGs (one per page) into an output directory, returns the list of
page filenames in order. Raises PdfRenderError on any failure.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List

import pypdfium2 as pdfium
from PIL import Image


class PdfRenderError(RuntimeError):
    """Raised when a PDF cannot be rasterized (corrupt, encrypted, etc.)."""


def rasterize_pdf(
    pdf_path: str,
    out_dir: str,
    *,
    width_px: int,
    height_px: int,
) -> List[str]:
    """Render every page of `pdf_path` to a PNG of (width_px, height_px) under `out_dir`.

    Letterboxes each page onto a black canvas of exactly (width_px, height_px)
    so the result is dimensionally consistent regardless of the source page
    aspect ratio.

    Returns a sorted list of filenames written (e.g. ['page_01.png', 'page_02.png']).
    Raises PdfRenderError on any failure (open, render, or write).
    """
    try:
        pdf = pdfium.PdfDocument(pdf_path)
    except Exception as exc:  # noqa: BLE001
        raise PdfRenderError(f"open failed: {exc}") from exc

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    written: List[str] = []
    try:
        for idx in range(len(pdf)):
            page = pdf[idx]
            try:
                page_w_pt, page_h_pt = page.get_size()
                scale_x = width_px / page_w_pt
                scale_y = height_px / page_h_pt
                scale = min(scale_x, scale_y)
                bitmap = page.render(scale=scale)
                pil_img = bitmap.to_pil()
                canvas = Image.new("RGB", (width_px, height_px), (0, 0, 0))
                offset = ((width_px - pil_img.width) // 2,
                          (height_px - pil_img.height) // 2)
                canvas.paste(pil_img, offset)
                fname = f"page_{idx + 1:02d}.png"
                tmp_path = Path(out_dir) / (fname + ".tmp")
                final_path = Path(out_dir) / fname
                canvas.save(tmp_path, format="PNG")
                os.replace(tmp_path, final_path)
                written.append(fname)
            finally:
                # pypdfium2 page objects support context-manager-style close
                # via .close() or are GC'd; explicit close is safest.
                try:
                    page.close()
                except Exception:
                    pass
    except PdfRenderError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PdfRenderError(f"render failed at page {len(written) + 1}: {exc}") from exc
    finally:
        try:
            pdf.close()
        except Exception:
            pass

    return written
