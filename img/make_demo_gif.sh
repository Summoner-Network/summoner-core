#!/usr/bin/env bash
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "$ cd zzz/"
  echo "$ ls"
  echo "$0"
  echo "assets/<movie_basename>"
  echo ""
  echo "Usage: $0 <movie_basename>"
  echo "Example: $0 merchants_demo"
  exit 1
fi

NAME="$1"
INPUT="assets/${NAME}.mov"
PALETTE="assets/${NAME}_palette.png"
OUTPUT="assets/${NAME}.gif"

# sanity check
if [ ! -f "$INPUT" ]; then
  echo "Error: Input file not found: $INPUT"
  exit 1
fi

echo "Generating palette for $INPUT → $PALETTE"
ffmpeg -y -i "$INPUT" \
  -vf "fps=25,scale=800:-1:flags=lanczos,palettegen" \
  "$PALETTE"

echo "Encoding GIF for $INPUT → $OUTPUT"
ffmpeg -i "$INPUT" -i "$PALETTE" \
  -filter_complex "fps=25,scale=800:-1:flags=lanczos[x];[x][1:v]paletteuse" \
  "$OUTPUT"

echo "Done! → $OUTPUT"
