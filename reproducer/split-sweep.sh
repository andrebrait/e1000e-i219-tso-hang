#!/bin/bash
# Drive the TEST-ONLY split sweep (fix/e1000e-split-sweep.py) from a third machine.
# For each N: set split_before=N on the test host and wait until either the NIC hangs (the debug
# watcher reboots the host, which also resets split_before to 0) or TARGET packets were placed
# with no hang. Prints one line per N: N, result, packets placed, seconds, payload mismatches.
# Usage: split-sweep.sh <target-placed> <max-seconds> N...
set -u
host=10.0.255.35; peer=10.0.255.34
target=$1; maxs=$2; shift 2
h() { timeout 15 ssh -o ConnectTimeout=8 -o BatchMode=yes -p 2223 root@$host "$@" 2>/dev/null; }
mism() { timeout 15 ssh -o BatchMode=yes -p 2223 root@$peer "grep MISMATCH /var/lib/e1000e-hang-test/verify.log | awk '!seen[\$0]++' | awk '\$1 >= \"$1\"' | wc -l"; }
wait_up() {
    for _ in $(seq 1 90); do
        [ "$(h cat /sys/module/e1000e/parameters/split_before)" = 0 ] && sleep 60 && return 0
        sleep 10
    done
    return 1
}
for n in "$@"; do
    wait_up || { echo "N=$n host not back"; exit 1; }
    c0=$(h cat /sys/module/e1000e/parameters/split_count); t0=$(date +%s); ts=$(date -u +%FT%T)
    h "echo $n > /sys/module/e1000e/parameters/split_before" || { echo "N=$n could not set"; exit 1; }
    result=timeout; placed=0
    while [ $(( $(date +%s) - t0 )) -lt "$maxs" ]; do
        sleep 10
        if ! c=$(h cat /sys/module/e1000e/parameters/split_count) || [ -z "$c" ]; then
            result=hang; break
        fi
        placed=$((c - c0))
        if [ "$placed" -ge "$target" ]; then result=ok; break; fi
    done
    [ "$result" != hang ] && h "echo 0 > /sys/module/e1000e/parameters/split_before"
    echo "$ts N=$n result=$result placed=$placed seconds=$(( $(date +%s) - t0 )) mismatches=$(mism "$ts")"
done
