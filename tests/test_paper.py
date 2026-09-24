"""Paper format choice.

The format decides which printer the sheet goes to, so getting it wrong is not
cosmetic: an A3 drawing sent to an A4 device comes out cut off.

Only A4 and A3 are offered. Larger drawings belong on a plotter and are plotted out
of the CAD software, not out of a service that feeds an office printer.
"""
import pytest

from app import convert
from conftest import pdf_masse


class TestAutomatischeWahl:

    def test_kleine_zeichnung_bekommt_a4(self):
        assert convert.choose_page((100.0, 50.0)) == "a4"

    def test_groessere_zeichnung_bekommt_a3(self):
        # 280 x 200 passt noch auf A4 quer (nutzbar 200 x 287) -- erst darueber wird es A3.
        assert convert.choose_page((280.0, 200.0)) == "a4"
        assert convert.choose_page((400.0, 250.0)) == "a3"

    def test_hochformatige_zeichnung_wird_nicht_nach_breite_beurteilt(self):
        """250 x 600 mm fits on neither. Judging by width alone would have called it
        A3 and cut 180 mm off the bottom."""
        assert convert.choose_page((250.0, 600.0)) == "a3"      # groesstes Blatt
        assert convert.fits_on((250.0, 600.0), "a3") is False   # ... aber es passt nicht

    def test_zu_grosse_zeichnung_bekommt_das_groesste_blatt(self):
        assert convert.choose_page((900.0, 600.0)) == "a3"

    def test_ohne_masse_wird_nicht_geraten(self):
        assert convert.choose_page(None) == "a4"

    def test_der_rand_zaehlt_mit(self):
        """A drawing exactly 210 mm wide does not fit on A4: 5 mm of margin on each
        side leave 200 mm."""
        assert convert.choose_page((210.0, 290.0)) == "a3"


class TestUeberlauf:

    def test_passende_zeichnung_meldet_keinen_ueberlauf(self, dxf_bytes):
        _, _, _, zu_gross = convert.convert("probe.dxf", dxf_bytes(100.0, 50.0),
                                            page="a4", orientation="portrait")
        assert zu_gross is False

    def test_zu_grosse_zeichnung_meldet_ueberlauf(self, dxf_bytes):
        """The sheet is still produced -- cut, and said so. Handing over a fragment as
        if it were whole would be the worse answer."""
        _, _, _, zu_gross = convert.convert("probe.dxf", dxf_bytes(900.0, 600.0),
                                            page="auto", orientation="landscape")
        assert zu_gross is True

    def test_auch_ein_erzwungenes_zu_kleines_blatt_meldet_ueberlauf(self, dxf_bytes):
        # 280 x 200 passt noch auf A4 quer; 400 x 250 nicht mehr.
        _, _, _, passt = convert.convert("probe.dxf", dxf_bytes(280.0, 200.0),
                                         page="a4", orientation="landscape")
        assert passt is False
        _, _, _, zu_gross = convert.convert("probe.dxf", dxf_bytes(400.0, 250.0),
                                            page="a4", orientation="landscape")
        assert zu_gross is True


class TestRueckmeldung:

    def test_das_gewaehlte_format_wird_genannt(self, dxf_bytes):
        _, name, (breite, hoehe), _ = convert.convert(
            "probe.dxf", dxf_bytes(100.0, 50.0), page="a3", orientation="portrait")
        assert name == "a3"
        assert (breite, hoehe) == (297.0, 420.0)

    def test_quer_bleibt_dasselbe_format(self, dxf_bytes):
        """Landscape A3 is still A3 -- the printer is chosen by format, not by which
        edge is longer."""
        _, name, _, _ = convert.convert("probe.dxf", dxf_bytes(100.0, 50.0),
                                        page="a3", orientation="landscape")
        assert name == "a3"

    @pytest.mark.parametrize("format_name", ["a4", "a3"])
    def test_jedes_format_kommt_massgetreu_heraus(self, dxf_bytes, format_name):
        pdf, name, (breite, hoehe), _ = convert.convert(
            "probe.dxf", dxf_bytes(100.0, 50.0), page=format_name,
            orientation="portrait", scale="1")
        assert name == format_name
        (seite_b, seite_h), gezeichnet = pdf_masse(pdf)
        assert abs(seite_b - breite) <= 0.2
        assert abs(seite_h - hoehe) <= 0.2
        # Massgetreu bleibt massgetreu, unabhaengig vom Blattformat.
        assert abs(gezeichnet[0] - 100.0) <= 0.2


class TestQuelleDerMasse:
    """Woher Format und Ausrichtung kommen -- in der Reihenfolge ihrer Verbindlichkeit."""

    def _doc(self, mit_layout_inhalt):
        import ezdxf
        doc = ezdxf.new("R2010", setup=True)
        doc.header["$INSUNITS"] = 4
        doc.modelspace().add_lwpolyline([(0, 0), (100, 0), (100, 50), (0, 50)], close=True)
        if mit_layout_inhalt:
            layout = doc.layouts.get("Layout1")
            layout.add_line((0, 0), (10, 0))         # ein echtes Blatt hat Inhalt
        return doc

    def test_leeres_standardlayout_zaehlt_NICHT(self):
        """Jede DXF traegt ein Layout1, und ezdxfs Vorgabe dafuer ist A3 quer. Wer das
        blind glaubt, legt jede Modellbereich-Zeichnung auf A3 -- ein selbstbewusst
        falsches Ergebnis."""
        doc = self._doc(mit_layout_inhalt=False)
        assert convert.paper_from_layout(doc) is None
        assert convert.extents_of(doc) == (100.0, 50.0)     # aus der Geometrie

    def test_gefuelltes_layout_gibt_den_ausschlag(self):
        """Ein Papierbereich mit Inhalt ist eine Aussage des Zeichners und schlaegt
        die gerechnete Ausdehnung."""
        doc = self._doc(mit_layout_inhalt=True)
        blatt = convert.paper_from_layout(doc)
        assert blatt is not None
        assert convert.extents_of(doc) == blatt
        assert blatt[0] > blatt[1]                          # ezdxf-Vorgabe ist quer

    def test_die_kopfvariablen_werden_gar_nicht_gefragt(self):
        """$EXTMIN/$EXTMAX sind nicht nur oft veraltet: ezdxf setzt sie beim Schreiben
        auf Platzhalter (1e20 / -1e20) zurueck. Gemessen, nicht vermutet."""
        import io
        doc = self._doc(mit_layout_inhalt=False)
        doc.header["$EXTMIN"] = (0, 0, 0)
        doc.header["$EXTMAX"] = (9999, 9999, 0)             # bewusst unsinnig
        strom = io.StringIO(); doc.write(strom)
        wieder = convert.read_dxf(strom.getvalue().encode())
        assert convert.extents_of(wieder) == (100.0, 50.0)  # Geometrie gewinnt

    def test_erklaertes_blatt_meldet_keinen_ueberlauf(self):
        """A drawing laid out on A3 fits on A3 by definition. Checking the declared
        size against the same format minus margins reported every such drawing as too
        large -- which an earlier version did, and the image showed it."""
        doc = self._doc(mit_layout_inhalt=True)
        breite, hoehe = convert.page_size(doc, "auto", "auto")
        assert convert.page_name(breite, hoehe) == "a3"
        assert convert.page_overflow(doc, "a3") is False

    def test_das_erklaerte_blatt_bestimmt_das_format(self):
        """100 x 50 mm of geometry would be A4. The layout says A3, and the layout wins."""
        doc = self._doc(mit_layout_inhalt=True)
        breite, hoehe = convert.page_size(doc, "auto", "auto")
        assert convert.page_name(breite, hoehe) == "a3"

