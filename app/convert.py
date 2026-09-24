"""Convert CAD drawings to printable PDF.

Two formats, one pipeline:

    DXF -> ezdxf -> SVG -> cairosvg -> PDF
    DWG -> dwg2dxf (LibreDWG) -> DXF -> as above

DWG therefore passes through two lossy stages. That is a known limitation, not an
oversight: no free library reads DWG directly with useful fidelity.

Deliberately NOT here:

* No STEP or IGES. Those formats carry geometry, but neither a title block nor
  dimensions nor layers. A sheet made from them is good for a visual check and never
  a manufacturing drawing, so printing one would mislead rather than help.
* No ``ezdxf.addons.odafc``. It shells out to the ODA File Converter, which per the
  ODA FAQ is free for non-commercial use only. The import looks harmless; it is not.
* No PyMuPDF. It is AGPL, which would put every operator of this service under the
  network copyleft. cairosvg (LGPL) does the same job.
"""
from __future__ import annotations

import io
import logging
import pathlib
import shutil
import subprocess
import tempfile
from typing import Optional, Tuple

import cairosvg
import ezdxf
from ezdxf.addons.drawing import Frontend, RenderContext, config, layout, svg

log = logging.getLogger(__name__)

SUPPORTED = ("dxf", "dwg")

#: Page sizes in millimetres, portrait.
PAGES = {"a4": (210.0, 297.0), "a3": (297.0, 420.0)}

#: Wider than this (drawing units, read as mm), "auto" picks A3 over A4.
A3_THRESHOLD_MM = 260.0

#: cairosvg works in CSS pixels at 96 dpi.
PX_PER_MM = 96.0 / 25.4

#: Seconds before a DWG conversion is given up on.
DWG_TIMEOUT_S = 120


class ConversionError(Exception):
    """The input could not be turned into a PDF.

    The message reaches the caller, so it says what went wrong in plain words and
    never contains a path or a stack trace.
    """


def suffix_of(filename: str) -> str:
    return pathlib.Path(filename or "").suffix.lower().lstrip(".")


def is_supported(filename: str) -> bool:
    return suffix_of(filename) in SUPPORTED


# --------------------------------------------------------------------------- #
# DWG
# --------------------------------------------------------------------------- #
def dwg_available() -> bool:
    """Whether this image can read DWG at all."""
    return shutil.which("dwg2dxf") is not None


def dwg_to_dxf(data: bytes) -> bytes:
    """Run LibreDWG's dwg2dxf over a DWG."""
    if not dwg_available():
        raise ConversionError("DWG support is not available in this image.")
    with tempfile.TemporaryDirectory() as tmp:
        quelle = pathlib.Path(tmp) / "in.dwg"
        ziel = pathlib.Path(tmp) / "in.dxf"
        quelle.write_bytes(data)
        try:
            ergebnis = subprocess.run(["dwg2dxf", "-o", str(ziel), str(quelle)],
                                      capture_output=True, timeout=DWG_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise ConversionError("DWG conversion timed out.")
        # dwg2dxf writes plenty to stderr even on success, and its exit code is not
        # reliable. The output file is the authority.
        if not ziel.exists() or ziel.stat().st_size == 0:
            zeilen = (ergebnis.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            raise ConversionError("DWG could not be read: %s"
                                  % (zeilen[-1] if zeilen else "unknown error"))
        return ziel.read_bytes()


# --------------------------------------------------------------------------- #
# Page geometry
# --------------------------------------------------------------------------- #
def extents_of(doc) -> Optional[Tuple[float, float]]:
    """Drawing width and height from the header, or None.

    ``$EXTMIN``/``$EXTMAX`` are optional and frequently stale. Everything here treats
    them as a hint that may be wrong or missing, never as a fact.
    """
    try:
        min_p, max_p = doc.header.get("$EXTMIN"), doc.header.get("$EXTMAX")
        if not (min_p and max_p):
            return None
        return (abs(float(max_p[0]) - float(min_p[0])),
                abs(float(max_p[1]) - float(min_p[1])))
    except Exception:                                    # noqa: BLE001
        return None


def page_size(doc, page: str = "auto", orientation: str = "auto") -> Tuple[float, float]:
    """Page width and height in mm."""
    masse = extents_of(doc)
    if page in PAGES:
        kurz, lang = PAGES[page]
    else:
        kurz, lang = PAGES["a3"] if (masse and masse[0] > A3_THRESHOLD_MM) else PAGES["a4"]

    quer = orientation == "landscape" or (
        orientation == "auto" and masse is not None and masse[0] > masse[1])
    return (lang, kurz) if quer else (kurz, lang)


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def read_dxf(data: bytes):
    """Parse DXF bytes into an ezdxf document."""
    try:
        return ezdxf.read(io.StringIO(data.decode("utf-8", "replace")))
    except Exception:                                    # noqa: BLE001
        raise ConversionError("Not a readable DXF file.")


def doc_to_svg(doc, breite_mm: float, hoehe_mm: float, scale: str = "1") -> str:
    """Render a parsed document to SVG on a page of the given size.

    ``scale='1'`` (the default) draws true to size: one drawing unit becomes one
    millimetre. A drawing larger than the page then runs off it and is cut at print
    time, and that is on purpose.

    ``scale='fit'`` fills the page instead. Measured, it scales in BOTH directions:
    a small drawing is blown up just as a large one is shrunk, so it is never true to
    size except by coincidence. That is why it is not the default — a sheet marked
    100 mm that measures 200 mm is a trap, while one that is visibly cut off is not.

    The background is forced to white and the colours to match. CAD tools draw on a
    dark canvas; rendered as-is, every sheet would come out of the printer nearly
    black.
    """
    try:
        cfg = config.Configuration(background_policy=config.BackgroundPolicy.WHITE)
        backend = svg.SVGBackend()
        Frontend(RenderContext(doc), backend, config=cfg).draw_layout(
            doc.modelspace(), finalize=True)
        seite = layout.Page(breite_mm, hoehe_mm, layout.Units.mm,
                            margins=layout.Margins.all(5))
        einstellungen = layout.Settings(fit_page=(scale == "fit"), scale=1.0)
        return backend.get_string(seite, settings=einstellungen)
    except ConversionError:
        raise
    except Exception as fehler:                          # noqa: BLE001
        log.exception("DXF could not be rendered")
        raise ConversionError("DXF could not be rendered: %s" % fehler)


def svg_to_pdf(svg_text: str, breite_mm: float, hoehe_mm: float) -> bytes:
    """SVG to PDF at a fixed page size.

    The size is passed explicitly rather than left to the SVG: a sheet that is a
    millimetre off is exactly the kind of error nobody notices until it is on paper.
    """
    try:
        return cairosvg.svg2pdf(bytestring=svg_text.encode("utf-8"),
                                output_width=breite_mm * PX_PER_MM,
                                output_height=hoehe_mm * PX_PER_MM)
    except Exception as fehler:                          # noqa: BLE001
        log.exception("SVG could not be converted")
        raise ConversionError("SVG could not be converted to PDF: %s" % fehler)


def dxf_to_pdf(data: bytes, page: str = "auto", orientation: str = "auto",
               scale: str = "1") -> bytes:
    """Parsed once, not twice: a large DXF costs real time to read."""
    doc = read_dxf(data)
    breite_mm, hoehe_mm = page_size(doc, page, orientation)
    return svg_to_pdf(doc_to_svg(doc, breite_mm, hoehe_mm, scale), breite_mm, hoehe_mm)


def convert(filename: str, data: bytes, page: str = "auto",
            orientation: str = "auto", scale: str = "1") -> bytes:
    """Entry point: file in, PDF out."""
    if not data:
        raise ConversionError("Empty file.")
    endung = suffix_of(filename)
    if endung == "dwg":
        data = dwg_to_dxf(data)
        endung = "dxf"
    if endung != "dxf":
        raise ConversionError("Unsupported format '%s'. Supported: %s."
                              % (endung or "unknown", ", ".join(SUPPORTED)))
    return dxf_to_pdf(data, page=(page or "auto").lower(),
                      orientation=(orientation or "auto").lower(),
                      scale=(scale or "1").lower())
