"""The measurement that matters: is the output true to size?

A technical drawing whose scale is wrong is more dangerous than one that is visibly
cropped, because nobody measuring it can tell. So this is the guard the service
stands or falls on, and it measures the finished PDF rather than trusting any
intermediate step.
"""
import pytest

from app import convert
from conftest import pdf_masse

TOLERANZ_MM = 0.2          # a raster pixel at 254 dpi


@pytest.mark.parametrize("breite,hoehe", [(50.0, 25.0), (100.0, 50.0), (180.0, 90.0)])
def test_eine_zeichnungseinheit_wird_ein_millimeter(dxf_bytes, breite, hoehe):
    pdf = convert.convert("probe.dxf", dxf_bytes(breite, hoehe), page="a4",
                          orientation="portrait", scale="1")
    _, gezeichnet = pdf_masse(pdf)
    assert gezeichnet is not None, "nothing was drawn"
    assert abs(gezeichnet[0] - breite) <= TOLERANZ_MM
    assert abs(gezeichnet[1] - hoehe) <= TOLERANZ_MM


def test_die_seite_hat_das_verlangte_format(dxf_bytes):
    pdf = convert.convert("probe.dxf", dxf_bytes(100.0), page="a4",
                          orientation="portrait", scale="1")
    (breite, hoehe), _ = pdf_masse(pdf)
    assert abs(breite - 210.0) <= TOLERANZ_MM
    assert abs(hoehe - 297.0) <= TOLERANZ_MM


def test_a3_ist_a3(dxf_bytes):
    pdf = convert.convert("probe.dxf", dxf_bytes(100.0), page="a3",
                          orientation="portrait", scale="1")
    (breite, hoehe), _ = pdf_masse(pdf)
    assert abs(breite - 297.0) <= TOLERANZ_MM
    assert abs(hoehe - 420.0) <= TOLERANZ_MM


def test_zu_grosse_zeichnung_laeuft_bei_scale_1_ueber_die_seite(dxf_bytes):
    """The trade-off of the default, stated rather than hidden: true to size means a
    drawing larger than the page runs off it and is cut at print time. That is
    visible. The alternative -- silently shrinking it -- is not, and a technical
    drawing at the wrong scale is the more dangerous of the two."""
    pdf = convert.convert("probe.dxf", dxf_bytes(400.0, 300.0), page="a4",
                          orientation="portrait", scale="1")
    (seite_breit, _), gezeichnet = pdf_masse(pdf)
    assert gezeichnet[0] > seite_breit


def test_fit_verkleinert_eine_zu_grosse_zeichnung_auf_die_seite(dxf_bytes):
    pdf = convert.convert("probe.dxf", dxf_bytes(400.0, 300.0), page="a4",
                          orientation="portrait", scale="fit")
    (seite_breit, seite_hoch), gezeichnet = pdf_masse(pdf)
    assert gezeichnet[0] <= seite_breit
    assert gezeichnet[1] <= seite_hoch


def test_fit_skaliert_IMMER_auch_herauf(dxf_bytes):
    """Measured, not assumed: 'fit' fills the page in both directions. A small drawing
    is blown up just as a large one is shrunk, so 'fit' is never true to size except
    by coincidence.

    This is the whole reason '1' is the default. A sheet marked 100 mm that measures
    200 mm is a trap; one that is visibly cut off is not.
    """
    pdf = convert.convert("probe.dxf", dxf_bytes(100.0, 50.0), page="a4",
                          orientation="portrait", scale="fit")
    _, gezeichnet = pdf_masse(pdf)
    assert gezeichnet[0] > 100.0 + TOLERANZ_MM


def test_der_hintergrund_ist_nicht_dunkel(dxf_bytes):
    """CAD tools draw on a dark canvas. Rendered as-is, every sheet would come out of
    the printer nearly black -- this caught exactly that during development."""
    doc = convert.read_dxf(dxf_bytes(100.0, 50.0))
    svg_text = convert.doc_to_svg(doc, 210.0, 297.0, scale="1")
    assert "#ffffff" in svg_text.lower()
    assert "#212830" not in svg_text.lower()       # ezdxf's default dark canvas
