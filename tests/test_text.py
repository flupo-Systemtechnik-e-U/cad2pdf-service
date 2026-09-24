"""Text has to survive the pipeline.

Without any font installed, ezdxf drops text from the rendering **silently** and the
sheet arrives as empty frames. That is the single most likely way to break this image
by slimming it down, so it gets a guard rather than a line in the README.

The guard compares two renderings of the same drawing, one with a text entity and one
without, and counts drawing operators in the PDF. An absolute check would be brittle;
the difference is not.

Note what does NOT work as a check: counting curve operators. Glyphs come out as
polygon outlines here, not curves, so a curve count is zero either way. That was the
first attempt and it "failed" against a perfectly good PDF.
"""
import io
import re

import ezdxf
import pytest
from pypdf import PdfReader

from app import convert


def _zeichnung(mit_text: bool) -> bytes:
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 50), (0, 50)], close=True)
    if mit_text:
        msp.add_text("TEIL 4711", height=5).set_placement((5, 55))
    doc.header["$EXTMIN"] = (0, 0, 0)
    doc.header["$EXTMAX"] = (100, 62 if mit_text else 50, 0)
    strom = io.StringIO()
    doc.write(strom)
    return strom.getvalue().encode("utf-8")


def _zeichenbefehle(pdf: bytes) -> int:
    strom = PdfReader(io.BytesIO(pdf)).pages[0].get_contents().get_data().decode(
        "latin-1", "replace")
    return len(re.findall(r"\b[ml]\b", strom))


def test_text_erzeugt_zeichenbefehle():
    ohne, _fmt, _mm, _ueber = convert.convert("ohne.dxf", _zeichnung(False), page="a4",
                           orientation="portrait", scale="1")
    mit, _fmt, _mm, _ueber = convert.convert("mit.dxf", _zeichnung(True), page="a4",
                          orientation="portrait", scale="1")
    assert _zeichenbefehle(mit) > _zeichenbefehle(ohne) + 20, (
        "text produced no drawing operators -- is a font installed?")
