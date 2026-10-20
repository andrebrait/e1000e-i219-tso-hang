#!/bin/bash
# Load one e1000e module on the test host, run the verified reproducer, and report the first hang
# (or none within MAXS seconds) with the stuck packet's descriptors. VM 900 is set to onboot 0
# first, so after a hang-reboot the host comes back without guest traffic.
# Usage: confirm-run.sh <label> <module path on host> <max seconds>
set -u
host=10.0.255.35; vm=10.0.255.90; peer=10.0.255.34
label=$1; ko=$2; maxs=$3
h() { timeout 20 ssh -o ConnectTimeout=8 -o BatchMode=yes -p 2223 root@$host "$@" 2>/dev/null; }
g() { timeout 20 ssh -o ConnectTimeout=8 -o BatchMode=yes root@$vm "$@" 2>/dev/null; }
up() { for _ in $(seq 1 90); do h true && return 0; sleep 10; done; return 1; }

up || { echo "$label: host not reachable"; exit 1; }
h "qm start 900 >/dev/null 2>&1; qm set 900 --onboot 0 >/dev/null"
for _ in $(seq 1 30); do g true && break; sleep 5; done
g "systemctl stop vm-verify; sysctl -n net.ipv4.tcp_timestamps" | sed "s/^/$label: guest tcp_timestamps=/"
want=$(h "modinfo -F srcversion $ko")
h "k=\$(uname -r); install -m 644 $ko /lib/modules/\$k/updates/e1000e.ko; depmod -a \$k; echo $want > /root/e1000e-fix/expected-srcversion; systemd-run --quiet --unit=e1000e-swap --collect /root/e1000e-fix/swap-module.sh $ko"
sleep 50; up
got=$(h "cat /sys/module/e1000e/srcversion")
[ "$got" = "$want" ] || { echo "$label: module $got, wanted $want"; exit 1; }
g "systemctl start vm-verify"
ts=$(date -u +%FT%T); t0=$(date +%s)
echo "$label: module $got, guest traffic from $ts"
result=none
while [ $(( $(date +%s) - t0 )) -lt "$maxs" ]; do
    sleep 10
    if ! h true; then up; result=hang; break; fi
done
g "systemctl stop vm-verify"
line=$(h "awk '\$1 >= \"$ts\"' /var/lib/e1000e-hang-test/debug/watch.log | grep -m1 'hang, link dead'")
if [ -n "$line" ]; then
    d=$(echo "$line" | grep -o '/var/lib/[^ ]*')
    echo "$label: HANG $(echo "$line" | cut -c1-20) after $(( $(date -d "$(echo "$line" | cut -c1-19)" +%s 2>/dev/null || date -j -u -f %Y-%m-%dT%H:%M:%S "$(echo "$line" | cut -c1-19)" +%s) - t0 - 130 )) s (onset estimate); capture $d"
    h "grep -E 'Tc\[|Td\[' $d/at-hang/kernel.log | grep -A1 ' NTC' | sed 's/^.*kernel: e1000e: //' | cut -c1-62"
else
    echo "$label: no hang in $(( $(date +%s) - t0 )) s (result=$result)"
fi
echo "$label: payload mismatches since $ts: $(timeout 20 ssh -p 2223 root@$peer "grep MISMATCH /var/lib/e1000e-hang-test/verify.log | awk '!seen[\$0]++' | awk '\$1 >= \"$ts\"' | wc -l")"
