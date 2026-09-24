"""The DWG path. Skipped where LibreDWG is absent, which is every developer machine
and no production image -- the CI job that builds the image is where this actually
runs."""
import pytest

from app import convert

pytestmark = pytest.mark.skipif(not convert.dwg_available(),
                                reason="dwg2dxf not installed (expected outside the image)")


def test_unlesbare_dwg_meldet_den_grund():
    """Not a crash and not a silent empty PDF: the caller gets a sentence it can show
    to a person."""
    with pytest.raises(convert.ConversionError) as fehler:
        convert.convert("kaputt.dwg", b"das ist keine DWG-Datei")
    assert "DWG" in str(fehler.value)


def test_dwg_wird_als_unterstuetzt_gefuehrt():
    assert convert.is_supported("zeichnung.dwg")
