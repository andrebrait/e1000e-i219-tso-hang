#!/bin/bash
# Long soak of the fixed e1000e: rotate the guest traffic every 12 h between TCP timestamps on,
# TCP timestamps off, and VLAN tags inserted by the stack (timestamps on), and log each phase.
# Runs on the test host from e1000e-soak-phase.timer (also 5 min after boot, so a reboot after a
# hang resumes the right phase). Phase = (time since /var/lib/e1000e-hang-test/soak-start) / 12 h.
set -u
dir=/var/lib/e1000e-hang-test
vm=10.0.255.90
start=$(cat "$dir/soak-start")
phase=$(( ( ($(date +%s) - start) / 43200 ) % 3 ))
case $phase in
0) ts=1; vlan=0; name="timestamps on" ;;
1) ts=0; vlan=0; name="timestamps off" ;;
2) ts=1; vlan=1; name="VLAN" ;;
esac
if [ "$vlan" = 1 ]; then systemctl enable --now e1000e-vlan-test >/dev/null 2>&1
else systemctl disable --now e1000e-vlan-test >/dev/null 2>&1; fi
for _ in $(seq 1 30); do
    ssh -p 22 -o BatchMode=yes -o ConnectTimeout=10 root@$vm \
        "sysctl -q -w net.ipv4.tcp_timestamps=$ts; echo net.ipv4.tcp_timestamps=$ts > /etc/sysctl.d/90-e1000e-test.conf; systemctl restart vm-verify" && break
    sleep 10
done
echo "$(date -u +%FT%TZ) phase $phase ($name) module $(cat /sys/module/e1000e/srcversion) tso_segments_since_load $(ethtool -S eno1 | awk '/tx_tcp_seg_good/{print $2}')" >> "$dir/soak.log"
