import io
import re

import ezdxf
import pytest
from pypdf import PdfReader

PT_PER_MM = 72.0 / 25.4


@pytest.fixture
def dxf_bytes():
    """A DXF containing one rectangle of a known width and height.

    A rectangle and not a single line, and this is not cosmetic: ``fit_to_page``
    computes ``min(sx, sy)``, and with a zero-height drawing that is a division by
    zero which falls back to "no scaling". A degenerate test drawing would therefore
    switch the feature off and the test would pass while measuring nothing. That
    happened during development, which is why it says so here.
    """
    def _bauen(breite_mm=100.0, hoehe_mm=50.0):
        doc = ezdxf.new("R2010")
        doc.header["$INSUNITS"] = 4              # millimetres
        doc.modelspace().add_lwpolyline(
            [(0, 0), (breite_mm, 0), (breite_mm, hoehe_mm), (0, hoehe_mm)], close=True)
        # KEIN $EXTMIN/$EXTMAX: ezdxf setzt die beim Schreiben auf Platzhalter
        # (1e20 / -1e20) zurueck. Die Ausdehnung wird aus der Geometrie gerechnet.
        strom = io.StringIO()
        doc.write(strom)
        return strom.getvalue().encode("utf-8")
    return _bauen


def pdf_masse(pdf: bytes):
    """Page size and drawn extent of a PDF, both in millimetres.

    Measured from the PDF content stream rather than the SVG: what matters is what
    comes out at the end of the whole chain. Two traps this walked into during
    development, both recorded here so the next reader does not repeat them:

    * cairosvg emits rectangles as the PDF ``re`` operator, not as ``m``/``l`` paths.
      Looking only for ``m``/``l`` measures nothing and reports "nothing was drawn".
    * The white page background is itself a full-page ``re``, but *filled* (``f``)
      rather than stroked (``S``). Counting it makes every drawing look page-sized.
    """
    seite = PdfReader(io.BytesIO(pdf)).pages[0]
    box = seite.mediabox
    strom = seite.get_contents().get_data().decode("latin-1", "replace")
    seitenmass = (float(box.width) / PT_PER_MM, float(box.height) / PT_PER_MM)

    ecken = []
    zahl = r"(-?\d+\.?\d*)"
    # Gestrichene Rechtecke: "x y w h re" gefolgt von S (oder s), nicht von f.
    for x, y, b, h in re.findall(r"%s\s+%s\s+%s\s+%s\s+re\s+[Ss]\b" % (zahl, zahl, zahl, zahl),
                                 strom):
        x, y, b, h = float(x), float(y), float(b), float(h)
        ecken += [(x, y), (x + b, y + h)]
    # Gezeichnete Pfade.
    ecken += [(float(x), float(y)) for x, y, _ in
              re.findall(r"%s\s+%s\s+([ml])\b" % (zahl, zahl), strom)]

    if not ecken:
        return seitenmass, None
    breite = (max(p[0] for p in ecken) - min(p[0] for p in ecken)) / PT_PER_MM
    hoehe = (max(p[1] for p in ecken) - min(p[1] for p in ecken)) / PT_PER_MM
    return seitenmass, (breite, hoehe)
