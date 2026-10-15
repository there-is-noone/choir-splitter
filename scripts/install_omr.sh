#!/usr/bin/env bash
# Install the Audiveris optical music recognition engine for choirsplit, user-locally.
#
# No root needed: the official Audiveris Ubuntu .deb (a self-contained jpackage bundle
# with its own Java runtime) is downloaded from the Audiveris GitHub releases, its
# SHA-256 is checked against the digest GitHub publishes, and it is unpacked with
# `ar` + `tar` into
#
#     ${CHOIRSPLIT_HOME:-~/.local/share/choirsplit}/audiveris/bin/Audiveris
#
# Tesseract OCR language data (used for part names, titles, tempo text and lyrics) goes to
#
#     ${CHOIRSPLIT_HOME:-~/.local/share/choirsplit}/tessdata/
#
# choirsplit finds both there automatically. To uninstall, delete that directory.
#
# Options (environment variables):
#   AUDIVERIS_VERSION   release tag to install (default: 5.11.0, "latest" for the newest)
#   AUDIVERIS_DEB       use this already-downloaded .deb instead of downloading
#   OCR_LANGS           tesseract languages to fetch, space separated (default: "eng")
#   CHOIRSPLIT_HOME     install prefix (default: ~/.local/share/choirsplit)
#
# Requires: curl, ar (binutils), tar with zstd support, sha256sum, python3.
set -euo pipefail

VERSION="${AUDIVERIS_VERSION:-5.11.0}"
PREFIX="${CHOIRSPLIT_HOME:-$HOME/.local/share/choirsplit}"
OCR_LANGS="${OCR_LANGS:-eng}"
REPO="Audiveris/audiveris"

for tool in curl ar tar sha256sum python3; do
    command -v "$tool" >/dev/null || { echo "error: '$tool' is required" >&2; exit 1; }
done

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

if [[ "$VERSION" == latest ]]; then
    api="https://api.github.com/repos/$REPO/releases/latest"
else
    api="https://api.github.com/repos/$REPO/releases/tags/$VERSION"
fi

if [[ -n "${AUDIVERIS_DEB:-}" ]]; then
    deb="$AUDIVERIS_DEB"
    echo "Using local package $deb"
else
    echo "Querying $api"
    curl -fsSL "$api" -o "$work/release.json"
    # Prefer the newest Ubuntu build (its bundled libraries are the most recent).
    read -r name url digest < <(python3 -I - "$work/release.json" <<'EOF'
import json, sys
rel = json.load(open(sys.argv[1]))
debs = [a for a in rel["assets"] if a["name"].endswith("-x86_64.deb")]
if not debs:
    sys.exit("no x86_64 .deb asset in release " + rel.get("tag_name", "?"))
debs.sort(key=lambda a: a["name"], reverse=True)
a = debs[0]
print(a["name"], a["browser_download_url"], a.get("digest") or "-")
EOF
    )
    case "$url" in
        https://github.com/$REPO/releases/download/*) ;;
        *) echo "error: unexpected download URL $url" >&2; exit 1 ;;
    esac
    deb="$work/$name"
    echo "Downloading $url"
    curl -fsSL "$url" -o "$deb"
    if [[ "$digest" == sha256:* ]]; then
        echo "${digest#sha256:}  $deb" | sha256sum -c -
    else
        echo "warning: GitHub published no digest for $name; SHA-256 not verified" >&2
    fi
fi

echo "Unpacking"
mkdir -p "$work/x"
(cd "$work/x" && ar x "$deb")
data="$(ls "$work"/x/data.tar.* | head -n1)"
tar -xf "$data" -C "$work/x"
if [[ ! -x "$work/x/opt/audiveris/bin/Audiveris" ]]; then
    echo "error: package layout not recognised (no opt/audiveris/bin/Audiveris)" >&2
    exit 1
fi

mkdir -p "$PREFIX"
rm -rf "$PREFIX/audiveris.new"
mv "$work/x/opt/audiveris" "$PREFIX/audiveris.new"
rm -rf "$PREFIX/audiveris"
mv "$PREFIX/audiveris.new" "$PREFIX/audiveris"

mkdir -p "$PREFIX/tessdata"
for lang in $OCR_LANGS; do
    target="$PREFIX/tessdata/$lang.traineddata"
    if [[ -s "$target" ]]; then
        echo "OCR language $lang already present"
        continue
    fi
    echo "Downloading OCR language $lang"
    curl -fsSL \
        "https://github.com/tesseract-ocr/tessdata/raw/main/$lang.traineddata" -o "$target.part"
    mv "$target.part" "$target"
done

echo
echo "Audiveris installed: $PREFIX/audiveris/bin/Audiveris"
"$PREFIX/audiveris/bin/Audiveris" -help 2>&1 | head -n 3 || true
echo "choirsplit will now accept PDF/PNG/JPG/TIFF scores."
