# ── choir-splitter Docker image ───────────────────────────────────────────────
# Ubuntu 24.04 matches the Audiveris jpackage .deb build environment.
# The image ships:
#   • ffmpeg (audio encode/decode, also provided by imageio-ffmpeg but system
#     ffmpeg is used by the MuseScore fallback path)
#   • Audiveris 5.11.0 (self-contained jpackage bundle – no separate JRE needed)
#   • Tesseract OCR + English language data (Audiveris uses its own bundled
#     tessdata, but system Tesseract is available as a fallback)
#   • choirsplit Python package installed into an isolated uv venv
#
# Build:
#   docker build -t choir-splitter .
#
# Run (mount a directory with your scores as /data):
#   docker run --rm -v /path/to/scores:/data choir-splitter /data/score.mxl
#
# Interactive shell:
#   docker run --rm -it -v /path/to/scores:/data --entrypoint /bin/bash choir-splitter
# ──────────────────────────────────────────────────────────────────────────────

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV AUDIVERIS_VERSION=5.11.0

# ── 1. System dependencies ────────────────────────────────────────────────────
# binutils  → ar, needed to unpack .deb archives in install_omr.sh
# zstd      → modern .deb data tarballs use zstd compression
# tesseract-ocr + tesseract-ocr-eng → OCR support for Audiveris part-name/lyric recognition
# ffmpeg    → audio I/O (MuseScore export fallback, format conversion)
# curl/wget → used by installer scripts and uv bootstrap
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget \
        curl \
        ca-certificates \
        binutils \
        zstd \
        ffmpeg \
        tesseract-ocr \
        tesseract-ocr-eng \
        python3 \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*

# ── 2. Audiveris (self-contained jpackage bundle) ─────────────────────────────
# The .deb bundles its own JRE so no openjdk package is needed.
# We unpack the .deb manually with ar+tar instead of apt-get install to avoid
# the post-install script, which tries to register a desktop menu entry via
# xdg-desktop-menu and exits with code 3 in a headless container environment.
RUN wget -qO /tmp/audiveris.deb \
        "https://github.com/Audiveris/audiveris/releases/download/${AUDIVERIS_VERSION}/Audiveris-${AUDIVERIS_VERSION}-ubuntu24.04-x86_64.deb" \
    && cd /tmp \
    && ar x audiveris.deb \
    && tar -xf data.tar.* -C / \
    && ln -sf /opt/audiveris/bin/Audiveris /usr/local/bin/audiveris \
    && rm -f /tmp/audiveris.deb /tmp/data.tar.* /tmp/control.tar.* /tmp/debian-binary

# ── 3. uv (fast Python package manager) ──────────────────────────────────────
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:/root/.cargo/bin:${PATH}"

# ── 4. Install choirsplit ─────────────────────────────────────────────────────
WORKDIR /app
COPY . /app

RUN uv venv /opt/venv \
    && uv pip install --python /opt/venv/bin/python --no-cache -e .

ENV PATH="/opt/venv/bin:${PATH}"
ENV VIRTUAL_ENV=/opt/venv

# ── 5. Runtime configuration ──────────────────────────────────────────────────
# Put scores in /data (mount a host directory here at runtime).
WORKDIR /data

# choir-split CLI as the default entry point; pass --help or a score path.
# Override with --entrypoint /bin/bash for an interactive shell.
ENTRYPOINT ["choir-split"]
CMD ["--help"]
