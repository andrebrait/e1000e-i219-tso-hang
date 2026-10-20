#!/bin/bash
# v3 test plan, run from a third machine. Results go to evidence/v3/results.txt.
#   sweep:   v3 + placement (test build) at the window values that always hang; unpatched + placement
#            at N=64 as the positive control.
#   natural: clean v3 for long runs (timestamps on, off, VLAN); unpatched controls until the first
#            hang. Every run records hangs, payload mismatches, transmit timeouts, tx_dropped and
#            the NIC's TSO counter (tx_tcp_seg_good), which proves hardware TSO stays in use.
set -u
cd "$(dirname "$0")"
host=10.0.255.35; vm=10.0.255.90; peer=10.0.255.34
out=evidence/v3/results.txt; mkdir -p evidence/v3
AB=/root/e1000e-fix/ab
h() { timeout 30 ssh -o ConnectTimeout=8 -o BatchMode=yes -p 2223 root@$host "$@" 2>/dev/null; }
g() { timeout 30 ssh -o ConnectTimeout=8 -o BatchMode=yes root@$vm "$@" 2>/dev/null; }
p() { timeout 30 ssh -o BatchMode=yes -p 2223 root@$peer "$@" 2>/dev/null; }
up() { for _ in $(seq 1 90); do h true && return 0; sleep 10; done; return 1; }
log() { echo "$*" | tee -a "$out"; }

traffic() {  # on|off
    local a=start; [ "$1" = off ] && a=stop
    for _ in $(seq 1 30); do g true && break; sleep 5; done
    g "systemctl $a vm-verify"; h "systemctl $a e1000e-verify-load"; p "systemctl $a e1000e-verify-load"
}
setup() {  # module timestamps(0|1) vlan(0|1)
    local ko=$AB/$1/e1000e.ko
    up || { log "host down"; exit 1; }
    traffic off
    if [ "$3" = 1 ]; then h "systemctl enable --now e1000e-vlan-test >/dev/null 2>&1"
    else h "systemctl disable --now e1000e-vlan-test >/dev/null 2>&1"; fi
    g "sysctl -q -w net.ipv4.tcp_timestamps=$2; echo net.ipv4.tcp_timestamps=$2 > /etc/sysctl.d/90-e1000e-test.conf"
    local want; want=$(h "modinfo -F srcversion $ko")
    if [ "$(h cat /sys/module/e1000e/srcversion)" != "$want" ]; then
        h "k=\$(uname -r); install -m 644 $ko /lib/modules/\$k/updates/e1000e.ko; depmod -a \$k; echo $want > /root/e1000e-fix/expected-srcversion; systemd-run --quiet --unit=e1000e-swap --collect /root/e1000e-fix/swap-module.sh $ko"
        sleep 50; up
        [ "$3" = 1 ] && h "systemctl restart e1000e-vlan-test"
    fi
    [ "$(h cat /sys/module/e1000e/srcversion)" = "$want" ] || { log "module mismatch for $1"; exit 1; }
    traffic on; sleep 30
}
seg() { h "ethtool -S eno1 | awk '/tx_tcp_seg_good/{print \$2}'"; }

natural() {  # label module ts vlan seconds stop_at_first_hang(0|1)
    local label=$1 secs=$5 stop=$6
    setup "$2" "$3" "$4"
    local ts t0 s0; ts=$(date -u +%FT%T); t0=$(date +%s); s0=$(seg)
    local hangs=0 segs=0 last=$s0
    while [ $(( $(date +%s) - t0 )) -lt "$secs" ]; do
        sleep 30
        if c=$(seg) && [ -n "$c" ]; then
            if [ "$c" -ge "$last" ]; then segs=$((segs + c - last)); else segs=$((segs + c)); fi
            last=$c
        else
            up; hangs=$(h "awk '\$1 >= \"$ts\"' /var/lib/e1000e-hang-test/debug/watch.log | grep -c 'hang, link dead'")
            [ "$stop" = 1 ] && [ "$hangs" -gt 0 ] && break
            last=0
        fi
    done
    hangs=$(h "awk '\$1 >= \"$ts\"' /var/lib/e1000e-hang-test/debug/watch.log | grep -c 'hang, link dead'")
    local wd mm dr
    wd=$(h "journalctl -k --since @$t0 --no-pager | grep -c 'NETDEV WATCHDOG'")
    dr=$(h "cat /sys/class/net/eno1/statistics/tx_dropped")
    mm=$(p "grep MISMATCH /var/lib/e1000e-hang-test/verify.log | awk '!seen[\$0]++' | awk '\$1 >= \"$ts\"' | wc -l")
    log "$ts natural $label module=$(h cat /sys/module/e1000e/srcversion) ts=$3 vlan=$4 seconds=$(( $(date +%s) - t0 )) hangs=$hangs mismatches=$mm tx_timeouts=$wd tx_dropped_now=$dr tso_segments=$segs"
}
sweep() {  # label module ts vlan N...
    local label=$1; setup "$2" "$3" "$4"; shift 4
    log "$(date -u +%FT%T) sweep $label: $(h "journalctl -k -b --no-pager | grep -m1 'e1000e test: readrq'" | sed 's/^.*e1000e test: //')"
    ./split-sweep.sh 1000 1500 "$@" 2>&1 | sed "s/$/ ($label)/" | tee -a "$out" evidence/split-sweep/results.txt
}

what=${1:-all}  # sweep | natural | all (no ";;&": macOS ships bash 3.2)
if [ "$what" = sweep ] || [ "$what" = all ]; then
    sweep "unpatched+placement, hdr 66" basep 1 0 64
    sweep "v3+placement, hdr 66" v3p 1 0 64 51 56 60 66
    sweep "v3+placement, hdr 54" v3p 0 0 51 64 66
    sweep "v3+placement, VLAN hdr 70" v3p 1 1 55 70
fi
if [ "$what" = natural ] || [ "$what" = all ]; then
    natural "unpatched, timestamps off" unpatched 0 0 1200 1
    natural "unpatched, VLAN" unpatched 1 1 1200 1
    natural "unpatched, timestamps on" unpatched 1 0 1800 1
    natural "v3, timestamps on" v3 1 0 3600 0
    natural "v3, timestamps off" v3 0 0 2400 0
    natural "v3, VLAN" v3 1 1 2400 0
fi
traffic off
h "systemctl disable --now e1000e-vlan-test >/dev/null 2>&1"
g "rm -f /etc/sysctl.d/90-e1000e-test.conf; sysctl -q -w net.ipv4.tcp_timestamps=1"
log "$(date -u +%FT%T) v3 test plan done"
