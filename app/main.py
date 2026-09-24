"""HTTP front end for the converter.

Three endpoints, no state. Anything larger — queues, retries, storage, history —
belongs in the caller, not here. A service this small can be read in one sitting,
and that is its main feature.

There is deliberately **no authentication and no TLS**. This belongs on an internal
network; whoever exposes it puts a reverse proxy in front. Half a security mechanism
built in would be worse than none, because it would look like protection.
"""
from __future__ import annotations

import logging
import os

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import convert as conv

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("cad2pdf")

#: Refuse anything larger, in bytes. A CAD file past this is either a mistake or an
#: attempt to exhaust memory; both deserve the same answer.
MAX_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(64 * 1024 * 1024)))


async def convert_endpoint(request: Request) -> Response:
    form = await request.form()
    datei = form.get("file")
    if datei is None or not hasattr(datei, "filename"):
        return JSONResponse({"error": "No file in field 'file'."}, status_code=400)

    name = datei.filename or ""
    if not conv.is_supported(name):
        return JSONResponse(
            {"error": "Unsupported format.", "supported": list(conv.SUPPORTED)},
            status_code=415)

    daten = await datei.read()
    if len(daten) > MAX_BYTES:
        return JSONResponse({"error": "File too large.", "limit_bytes": MAX_BYTES},
                            status_code=413)

    frage = request.query_params
    if frage.get("page", "auto") not in ("auto",) + tuple(conv.PAGES):
        return JSONResponse({"error": "Unknown page format.",
                             "pages": ["auto"] + list(conv.PAGES)}, status_code=400)
    try:
        pdf, format_name, (breite_mm, hoehe_mm), zu_gross = conv.convert(
            name, daten,
            page=frage.get("page", "auto"),
            orientation=frage.get("orientation", "auto"),
            scale=frage.get("scale", "1"))
    except conv.ConversionError as fehler:
        # 422, not 500: the request was well formed, the file was not usable. The
        # caller can show this text to a person.
        return JSONResponse({"error": str(fehler)}, status_code=422)
    except Exception:                                    # noqa: BLE001
        log.exception("unexpected failure converting %r", name)
        return JSONResponse({"error": "Internal error."}, status_code=500)

    # The format travels back in headers, not only in the bytes: the caller has to
    # route the sheet to a device that can take it, and reading the page size out of
    # a PDF just to learn that is work it should not have to do.
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": 'inline; filename="converted.pdf"',
        "X-Page-Format": format_name,
        "X-Page-Width-Mm": "%.1f" % breite_mm,
        "X-Page-Height-Mm": "%.1f" % hoehe_mm,
        # The drawing did not fit on the largest sheet offered and is therefore cut.
        # Said out loud rather than handing over a fragment as if it were whole -- such
        # a drawing belongs on a plotter.
        "X-Page-Overflow": "true" if zu_gross else "false",
    })


async def formats_endpoint(request: Request) -> Response:
    return JSONResponse({"formats": list(conv.SUPPORTED),
                         "dwg": conv.dwg_available(),
                         "pages": list(conv.PAGES)})


async def health_endpoint(request: Request) -> Response:
    return JSONResponse({"status": "ok"})


app = Starlette(routes=[
    Route("/convert", convert_endpoint, methods=["POST"]),
    Route("/formats", formats_endpoint, methods=["GET"]),
    Route("/health", health_endpoint, methods=["GET"]),
])
