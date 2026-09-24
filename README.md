# cad2pdf-service

A small HTTP service that turns CAD drawings into printable PDF.

```
POST /convert   file=drawing.dxf   ->  application/pdf
```

That is the whole idea. It exists because network printers speak PDF and customers
send DXF and DWG.

## What it does

| Input | Path |
|---|---|
| DXF | [ezdxf](https://ezdxf.mozman.at/) → SVG → [CairoSVG](https://cairosvg.org/) → PDF |
| DWG | [LibreDWG](https://www.gnu.org/software/libredwg/) `dwg2dxf` → DXF → as above |

DWG therefore passes through two lossy stages. There is no free library that reads
DWG directly with useful fidelity, so this is a limitation, not an oversight.

## What it deliberately does not do

* **No STEP, no IGES.** Those formats carry geometry but neither a title block nor
  dimensions nor layers. A sheet made from them is good for a visual check and never
  a manufacturing drawing, so printing one would mislead rather than help.
* **No authentication, no TLS.** This belongs on an internal network. Put a reverse
  proxy in front of it if it needs to be reachable from anywhere else. Half a
  security mechanism built in would be worse than none, because it would look like
  protection.
* **No queue, no storage, no history.** One request, one answer, no state. If you
  need retries or a job list, build them in the caller.
* **No ODA File Converter.** Per the ODA FAQ it is free for non-commercial use only,
  which rules it out here. Note that `ezdxf.addons.odafc` shells out to exactly that
  tool; the import looks harmless and is not. Do not add it.
* **No PyMuPDF.** It is AGPL, which would put everyone who *runs* this service under
  the network copyleft. CairoSVG does the same job under the LGPL.

## Paper format and orientation

Both are derived from the file, in this order of authority:

1. **A paper-space layout that contains something.** That is the draughtsman saying
   which sheet this drawing belongs on, including its orientation, and it beats
   anything computed. An *empty* layout does not count: every DXF carries a default
   `Layout1`, and the default happens to be A3 landscape — trusting it blindly would
   put every model-space drawing on A3.
2. **The bounding box of what is actually drawn**, computed from the entities.

`$EXTMIN` / `$EXTMAX` are not consulted at all. They are not merely "often stale":
ezdxf resets them to the sentinel values `1e20` / `-1e20` on write, so a file that has
passed through any tool can carry pure nonsense there. Measured, not assumed — a round
trip through `doc.write()` reproduces it every time, and an earlier version of this
service sized pages against that nonsense.

Offered formats are **A4 and A3**. Anything larger belongs on a plotter and is plotted
out of the CAD software. A drawing too large for A3 still gets a sheet — the largest
one — and the response says `X-Page-Overflow: true` so the caller can report it instead
of quietly handing over a fragment.

Orientation follows the drawing: wider than tall becomes landscape. The format name
does not change with it, because the printer is chosen by format and not by which edge
is longer.

## Scale

The default is **true to size**: one drawing unit becomes one millimetre.

A drawing larger than the page then runs off it and is cut when printed. That is on
purpose. The alternative, `?scale=fit`, fills the page — and measurement shows it
scales in *both* directions, blowing up a small drawing just as it shrinks a large
one. A sheet marked 100 mm that measures 200 mm is a trap; one that is visibly cut
off is not.

Measured end to end (`tests/test_scale.py`): a 100 mm rectangle comes out of the PDF
at 100.00 mm on a 210.0 × 297.0 mm page.

What is *not* measured here is everything after the PDF. Print drivers like to apply
"fit to page" of their own. Calibrate once against the real device.

## API

```
POST /convert
     multipart/form-data, field "file"
     ?page=auto|a4|a3                 default auto, see "Paper format" above
     ?orientation=auto|portrait|landscape
     ?scale=1|fit                     default 1
  200 application/pdf
      X-Page-Format      a4 | a3
      X-Page-Width-Mm    e.g. 420.0
      X-Page-Height-Mm   e.g. 297.0
      X-Page-Overflow    true when the drawing is larger than the sheet
  400 no file, or an unknown page format
  413 file larger than MAX_UPLOAD_BYTES (default 64 MiB)
  415 unsupported file format
  422 file could not be read; the message is meant for a human
  500 anything else

GET /formats    -> {"formats": ["dxf","dwg"], "dwg": true, "pages": ["a4","a3"]}
GET /health     -> {"status": "ok"}
```

`"dwg": false` means this image was built without LibreDWG. The field says so rather
than failing later.

## Running it

```sh
docker build -t cad2pdf-service .
docker run --rm -p 8000:8000 cad2pdf-service
curl -F file=@drawing.dxf http://localhost:8000/convert -o out.pdf
```

Environment: `MAX_UPLOAD_BYTES` (default 67108864), `LOG_LEVEL` (default INFO).

The service parses files from strangers and therefore runs as an unprivileged user.
Keep it that way.

## Known limits

These come out of the rendering and cannot be fixed here. They are listed so nobody
has to rediscover them at the printer:

* **Raster images embedded in a drawing are dropped silently.** Scanned sheets or
  parts pasted in as bitmaps simply do not appear.
* **XREFs are missing** unless the sender included them. External references are not
  resolved.
* **Proxy entities** from AutoCAD vertical products render only if the file carries
  proxy graphics with them.
* **SHX fonts are mapped onto TTF.** Text lands in the right place but has different
  shapes and widths, so it can overflow title-block cells.
* **Text becomes outlines**, not text objects: the PDF is not searchable.
* **Without fonts in the image, text vanishes entirely.** The Dockerfile installs
  `fonts-dejavu-core` and `fonts-liberation` for exactly this reason. If you slim the
  image down, do not slim those away.

## Development

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

The tests run without LibreDWG; the DWG path is skipped when `dwg2dxf` is absent.

Two traps the test suite documents from experience, because both produced a green
test that measured nothing:

* A test drawing with zero height switches `fit_to_page` off (it computes
  `min(sx, sy)` and divides by zero). Use a rectangle, not a line.
* CairoSVG emits rectangles as the PDF `re` operator, and the white page background
  is itself a full-page `re`. Measuring `m`/`l` paths only finds nothing; counting
  the background finds the page size.

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).

The image ships LibreDWG, which is GPL-3.0-or-later. If you distribute the image,
the usual obligations apply: licence texts, corresponding source or a written offer,
no additional restrictions. The source of this service is here, and the Dockerfile
records the pinned LibreDWG revision, which is what makes that practical.

Calling this service over HTTP from another program does not make that program a
derivative work — separate processes, no shared address space. That is the common
reading, but it is a reading: if you ship this image as part of a product, have it
confirmed by someone qualified. Nobody here is a lawyer.
