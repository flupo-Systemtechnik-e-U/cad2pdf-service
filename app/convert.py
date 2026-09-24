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

#: Page sizes in millimetres, portrait. Ordered smallest first -- "auto" walks this
#: list and takes the first page the drawing fits on.
PAGES = {
    "a4": (210.0, 297.0),
    "a3": (297.0, 420.0),
}

#: Millimetres kept clear on each edge.
MARGIN_MM = 5.0

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
def paper_from_layout(doc) -> Optional[Tuple[float, float]]:
    """The sheet the drawing was laid out for, in mm, or None.

    A paper-space layout states this explicitly -- it is what the draughtsman chose,
    and therefore a better answer than anything computed from the geometry.

    **Only counted when the layout actually contains something.** Every DXF carries a
    default ``Layout1``, and ezdxf's default happens to be A3 landscape. Trusting it
    blindly would put every model-space-only drawing on A3, which is precisely the
    kind of confident wrong answer this service must not give.
    """
    try:
        for layout in doc.layouts:
            if layout.name.lower() == "model":
                continue
            if not any(True for _ in layout):            # leeres Layout: Vorgabe, kein Wille
                continue
            unten_links, oben_rechts = layout.get_paper_limits()
            breite = abs(float(oben_rechts.x) - float(unten_links.x))
            hoehe = abs(float(oben_rechts.y) - float(unten_links.y))
            if breite > 1.0 and hoehe > 1.0:
                return (breite, hoehe)
    except Exception:                                    # noqa: BLE001
        log.debug("paper space layout not usable", exc_info=True)
    return None


def model_extents(doc) -> Optional[Tuple[float, float]]:
    """Width and height of what is actually drawn in model space, or None.

    Computed from the entities rather than read from ``$EXTMIN``/``$EXTMAX``. Those
    header variables are not merely "often stale": ezdxf resets them to the sentinel
    values 1e20 / -1e20 when writing, so a file that has passed through any tool can
    carry pure nonsense there. Measured, not assumed -- a round trip through
    ``doc.write()`` reproduces it every time.
    """
    try:
        import ezdxf.bbox

        box = ezdxf.bbox.extents(doc.modelspace())
        if not box.has_data:
            return None
        groesse = box.size
        if groesse.x <= 0 or groesse.y <= 0:
            return None
        return (float(groesse.x), float(groesse.y))
    except Exception:                                    # noqa: BLE001
        log.debug("model extents not computable", exc_info=True)
        return None


def extents_of(doc) -> Optional[Tuple[float, float]]:
    """What to size the page against: the intended sheet, else what is drawn.

    The order is the order of authority. A paper-space layout is a statement; a
    bounding box is a measurement; the header variables are neither and are not
    consulted at all.
    """
    return paper_from_layout(doc) or model_extents(doc)


def fits_on(masse: Optional[Tuple[float, float]], page: str) -> bool:
    """Does the drawing fit on that page at 1:1, margins included?"""
    if masse is None or page not in PAGES:
        return True
    kurz, lang = PAGES[page]
    kurz_noetig, lang_noetig = min(masse), max(masse)
    return (kurz_noetig <= kurz - 2 * MARGIN_MM) and (lang_noetig <= lang - 2 * MARGIN_MM)


def choose_page(masse: Optional[Tuple[float, float]]) -> str:
    """The smallest page the drawing fits on at 1:1, or the largest offered.

    Compared on BOTH dimensions, not on width: a drawing 250 mm wide and 600 mm tall
    fits on neither A4 nor A3, and picking by width alone would have put it on A3 and
    cut 180 mm off the bottom.

    Only A4 and A3 are offered. Anything larger belongs on a plotter and is plotted
    out of the CAD software, not out of a service that feeds an office printer.
    Such a drawing still gets a sheet -- the largest one -- and ``page_overflow``
    says that it did not fit, so the caller can say so instead of quietly handing
    over a fragment.

    Without extents there is nothing to go on, so A4: a default, stated as such, not
    a guess dressed up as a decision.
    """
    if masse is None:
        return "a4"
    for name in PAGES:
        if fits_on(masse, name):
            return name
    return list(PAGES)[-1]


def page_overflow(doc, page: str) -> bool:
    """True when what is drawn is larger than the sheet it is being put on.

    Measured against the GEOMETRY, never against a declared sheet. If the draughtsman
    laid the drawing out on A3, it fits on A3 by definition -- checking the declared
    size against the same format minus margins would report every such drawing as too
    large, which is what an earlier version did.
    """
    return not fits_on(model_extents(doc), page)


def nearest_page(masse: Tuple[float, float]) -> str:
    """The named format closest to these dimensions, without the margin rule.

    For a sheet the draughtsman declared: it already accounts for its own margins, so
    subtracting another five millimetres would push an exact A3 up to nothing and
    report it as too large.
    """
    kurz_ist, lang_ist = min(masse), max(masse)
    beste, abstand = list(PAGES)[-1], None
    for name, (kurz, lang) in PAGES.items():
        d = abs(kurz - kurz_ist) + abs(lang - lang_ist)
        if abstand is None or d < abstand:
            beste, abstand = name, d
    return beste


def page_size(doc, page: str = "auto", orientation: str = "auto") -> Tuple[float, float]:
    """Page width and height in mm."""
    erklaert = paper_from_layout(doc)
    masse = erklaert or model_extents(doc)
    if page in PAGES:
        kurz, lang = PAGES[page]
    elif erklaert is not None:
        kurz, lang = PAGES[nearest_page(erklaert)]
    else:
        kurz, lang = PAGES[choose_page(masse)]
    quer = orientation == "landscape" or (
        orientation == "auto" and masse is not None and masse[0] > masse[1])
    return (lang, kurz) if quer else (kurz, lang)


def page_name(breite_mm: float, hoehe_mm: float) -> str:
    """Which named format these dimensions are, or 'custom'.

    The caller needs this to pick a printer: an A0 sheet does not come out of the
    same device as an A4 one.
    """
    kurz, lang = min(breite_mm, hoehe_mm), max(breite_mm, hoehe_mm)
    for name, (k, l) in PAGES.items():
        if abs(k - kurz) < 0.5 and abs(l - lang) < 0.5:
            return name
    return "custom"


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
                            margins=layout.Margins.all(MARGIN_MM))
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
               scale: str = "1") -> Tuple[bytes, str, Tuple[float, float], bool]:
    """Parsed once, not twice: a large DXF costs real time to read.

    Returns the PDF, the page format name, its size in mm, and whether the drawing
    was larger than that sheet. The caller needs the format to route the sheet to a
    printer that can take it, and the overflow flag to say so when it could not.
    """
    doc = read_dxf(data)
    breite_mm, hoehe_mm = page_size(doc, page, orientation)
    name = page_name(breite_mm, hoehe_mm)
    pdf = svg_to_pdf(doc_to_svg(doc, breite_mm, hoehe_mm, scale), breite_mm, hoehe_mm)
    return pdf, name, (breite_mm, hoehe_mm), page_overflow(doc, name)


def convert(filename: str, data: bytes, page: str = "auto",
            orientation: str = "auto", scale: str = "1"
            ) -> Tuple[bytes, str, Tuple[float, float], bool]:
    """Entry point: file in; PDF, page format name, size in mm, overflow flag out."""
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
