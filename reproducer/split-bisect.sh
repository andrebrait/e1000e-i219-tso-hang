#!/bin/bash
# Find the upper end of the hang window by bisection, using split-sweep.sh (one N per run).
# Usage: split-bisect.sh <label> <known-hang N> <upper N to test first>
# Appends every run to evidence/split-sweep/results.txt.
set -u
cd "$(dirname "$0")"
label=$1; lo=$2; hi=$3
run() { ./split-sweep.sh 1000 1500 "$1" 2>&1 | sed "s/$/ ($label)/" | tee -a evidence/split-sweep/results.txt; }
r=$(run "$hi"); echo "$r"
if echo "$r" | grep -q result=hang; then echo "$label: N=$hi hangs; window extends past $hi"; exit 0; fi
while [ $((hi - lo)) -gt 1 ]; do
    mid=$(( (lo + hi) / 2 )); r=$(run $mid); echo "$r"
    if echo "$r" | grep -q result=hang; then lo=$mid; else hi=$mid; fi
done
echo "$label: upper bound N=$lo hangs, N=$hi does not" | tee -a evidence/split-sweep/results.txt
