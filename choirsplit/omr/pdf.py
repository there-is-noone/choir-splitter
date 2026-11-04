from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from . import platforms
from .types import AUDIVERIS_DEFAULT_DPI, IMAGE_SUFFIXES, _error

TOO_LARGE_IMAGE = re.compile(r"Too large image: ([\d,.]+) pixels \(vs ([\d,.]+) max\)")
NO_STAVES = re.compile(r"#(\d+)\]\s.*No regularly spaced lines found")


def pdf_page_count(path: Path) -> int | None:
    if shutil.which("pdfinfo"):
        try:
            out = subprocess.run(
                ["pdfinfo", str(path)],
                capture_output=True,
                text=True,
                timeout=60,
                **platforms.no_console(),
            ).stdout
            if m := re.search(r"^Pages:\s+(\d+)", out, re.M):
                return int(m.group(1))
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        import fitz

        with fitz.open(str(path)) as doc:
            return doc.page_count
    except Exception:
        return None


def default_timeout(path: Path) -> float:
    if env := os.environ.get("CHOIRSPLIT_OMR_TIMEOUT", "").strip():
        try:
            return float(env)
        except ValueError:
            raise _error(
                f"CHOIRSPLIT_OMR_TIMEOUT must be a number of seconds, not {env!r}"
            ) from None
    pages = 1 if path.suffix.lower() in IMAGE_SUFFIXES else (pdf_page_count(path) or 20)
    return 120.0 + 120.0 * pages


def rasterize_pdf(path: Path, outdir: Path, dpi: int = 300) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    if shutil.which("pdftoppm"):
        subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-png", str(path), str(outdir / "page")],
            check=True,
            capture_output=True,
            timeout=600,
            **platforms.no_console(),
        )
    else:
        try:
            import fitz
        except ImportError:
            raise _error(
                "rasterising a PDF needs pdftoppm (poppler-utils) or PyMuPDF"
            ) from None
        with fitz.open(str(path)) as doc:
            for i, page in enumerate(doc, 1):
                page.get_pixmap(dpi=dpi).save(str(outdir / f"page-{i:04d}.png"))
    return sorted(outdir.glob("page*.png"), key=natural_key)


def natural_key(p: Path | str) -> list:
    name = p.name if isinstance(p, Path) else str(p)
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def fitting_dpi(output: str, dpi: int | None) -> int | None:
    sizes = [
        (int(re.sub(r"\D", "", px)), int(re.sub(r"\D", "", top)))
        for px, top in TOO_LARGE_IMAGE.findall(output)
    ]
    if not sizes:
        return None
    pixels, limit = max(sizes)
    return int((dpi or AUDIVERIS_DEFAULT_DPI) * (limit / pixels) ** 0.5 * 0.99)
