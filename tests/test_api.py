"""The HTTP surface. Small on purpose, so this stays small too."""
import io

import pytest
from starlette.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_health(client):
    assert client.get("/health").status_code == 200


def test_formats_nennt_beide_und_sagt_ob_dwg_geht(client):
    daten = client.get("/formats").json()
    assert set(daten["formats"]) == {"dxf", "dwg"}
    assert daten["pages"] == ["a4", "a3"]
    # Outside the image dwg2dxf is absent; the field says so instead of pretending.
    assert isinstance(daten["dwg"], bool)


def test_konvertiert_eine_dxf(client, dxf_bytes):
    antwort = client.post("/convert", files={"file": ("probe.dxf", dxf_bytes(100.0, 50.0))})
    assert antwort.status_code == 200, antwort.text
    assert antwort.headers["content-type"] == "application/pdf"
    assert antwort.content.startswith(b"%PDF")


def test_die_antwort_nennt_das_papierformat(client, dxf_bytes):
    """The caller routes the sheet to a printer by this header. Reading the page size
    back out of the PDF just to learn it would be work it should not have to do."""
    antwort = client.post("/convert?page=a3&orientation=portrait",
                          files={"file": ("probe.dxf", dxf_bytes(100.0, 50.0))})
    assert antwort.headers["X-Page-Format"] == "a3"
    assert antwort.headers["X-Page-Width-Mm"] == "297.0"
    assert antwort.headers["X-Page-Overflow"] == "false"


def test_die_ausrichtung_folgt_der_zeichnung(client, dxf_bytes):
    """Wider than tall means landscape, without anyone having to say so. The format
    stays A3 either way -- the printer is chosen by format, not by which edge is
    longer."""
    quer = client.post("/convert?page=a3",
                       files={"file": ("breit.dxf", dxf_bytes(100.0, 50.0))})
    hoch = client.post("/convert?page=a3",
                       files={"file": ("hoch.dxf", dxf_bytes(50.0, 100.0))})
    assert quer.headers["X-Page-Width-Mm"] == "420.0"
    assert hoch.headers["X-Page-Width-Mm"] == "297.0"
    assert quer.headers["X-Page-Format"] == hoch.headers["X-Page-Format"] == "a3"


def test_unbekanntes_papierformat_gibt_400(client, dxf_bytes):
    antwort = client.post("/convert?page=a7",
                          files={"file": ("probe.dxf", dxf_bytes(100.0, 50.0))})
    assert antwort.status_code == 400
    assert "a3" in antwort.json()["pages"]


def test_unbekanntes_format_gibt_415(client):
    antwort = client.post("/convert", files={"file": ("teil.step", b"irgendwas")})
    assert antwort.status_code == 415
    assert "dxf" in antwort.json()["supported"]


def test_kaputte_datei_gibt_422_mit_grund(client):
    """422 and not 500: the request was fine, the file was not. The caller can show
    this text to a person."""
    antwort = client.post("/convert", files={"file": ("kaputt.dxf", b"nicht wirklich dxf")})
    assert antwort.status_code == 422
    assert antwort.json()["error"]


def test_ohne_datei_gibt_400(client):
    assert client.post("/convert").status_code == 400
