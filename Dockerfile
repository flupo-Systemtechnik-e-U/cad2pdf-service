# Two stages: LibreDWG is not packaged in Debian or Ubuntu and has no PyPI release,
# so it is built from a pinned source tag here. Only the resulting binaries travel
# into the runtime image, which keeps the build toolchain out of what is shipped.
#
# Pinned on purpose: a distributed image whose rendering changes between two pulls
# is not reproducible, and reproducibility is the whole point of shipping an image.

ARG UBUNTU=24.04
ARG LIBREDWG_TAG=0.13.3

# --------------------------------------------------------------------------- #
FROM ubuntu:${UBUNTU} AS libredwg
ARG LIBREDWG_TAG
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git autoconf automake libtool pkg-config \
        texinfo swig python3-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch ${LIBREDWG_TAG} \
        https://github.com/LibreDWG/libredwg.git /src
WORKDIR /src
# --disable-bindings: only the dwg2dxf binary is wanted, the language bindings would
# drag swig and a Python of their own into the runtime image.
RUN sh autogen.sh \
    && ./configure --prefix=/opt/libredwg --disable-bindings --disable-shared \
    && make -j"$(nproc)" \
    && make install

# --------------------------------------------------------------------------- #
FROM ubuntu:${UBUNTU} AS runtime
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# fonts-dejavu-core and fonts-liberation are NOT optional. Without any font on the
# system, text is dropped from the rendering without an error and the sheet comes out
# as empty frames. SHX fonts are mapped onto TTF, so the metrics differ slightly from
# the CAD original; that is documented, not fixable here.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv \
        libcairo2 \
        fonts-dejavu-core fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY --from=libredwg /opt/libredwg/bin/dwg2dxf /usr/local/bin/dwg2dxf

ENV VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH
RUN python3 -m venv "$VIRTUAL_ENV"
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

WORKDIR /srv
COPY app/ /srv/app/

# Unprivileged: this service parses files from strangers. Running it as root would be
# betting the host on ezdxf and LibreDWG never having a parser bug.
RUN useradd --system --uid 10001 --no-create-home cad2pdf
USER 10001

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
