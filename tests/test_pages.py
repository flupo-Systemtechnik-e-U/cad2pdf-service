"""Page choice from the drawing extents. Pure arithmetic, no rendering."""
from app import convert


class _Doc:
    def __init__(self, breite, hoehe):
        self.header = {"$EXTMIN": (0, 0, 0), "$EXTMAX": (breite, hoehe, 0)}


class _DocOhneMasse:
    header = {}


def test_breite_zeichnung_bekommt_a3():
    assert convert.page_size(_Doc(400, 200), "auto", "portrait")[0] == 297.0


def test_schmale_zeichnung_bleibt_a4():
    assert convert.page_size(_Doc(150, 200), "auto", "portrait")[0] == 210.0


def test_liegende_zeichnung_wird_quer():
    breite, hoehe = convert.page_size(_Doc(200, 100), "a4", "auto")
    assert breite > hoehe


def test_stehende_zeichnung_bleibt_hoch():
    breite, hoehe = convert.page_size(_Doc(100, 200), "a4", "auto")
    assert hoehe > breite


def test_ohne_masse_wird_nicht_geraten():
    """$EXTMIN/$EXTMAX are optional and often stale. Missing means A4 portrait, not a
    guess dressed up as a decision."""
    breite, hoehe = convert.page_size(_DocOhneMasse(), "auto", "auto")
    assert (breite, hoehe) == (210.0, 297.0)
