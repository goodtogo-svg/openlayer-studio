#!/usr/bin/env bash
set -euo pipefail
# Batch core preflight only; no silent Adobe invocation. Usage: bash scripts/batch.sh ./inputs ./jobs
IN="${1:?input directory}"; OUT="${2:?jobs directory}"
mkdir -p "$OUT"; log="$OUT/batch.log"
shopt -s nullglob
for f in "$IN"/*; do
  case "${f,,}" in *.png|*.jpg|*.jpeg|*.pdf|*.psd) ;; *) continue ;; esac
  name="$(basename "$f")"; stem="${name%.*}"; stamp="$(date -u +%Y%m%dT%H%M%SZ)_$RANDOM"
  dest="$OUT/${stem}_${stamp}"
  if ali process "$f" --out "$dest" && ali validate "$dest/manifest.json"; then
     printf 'PASS\t%s\t%s\n' "$f" "$dest" | tee -a "$log"
  else
     printf 'FAIL\t%s\t%s\n' "$f" "$dest" | tee -a "$log" >&2
  fi
done
