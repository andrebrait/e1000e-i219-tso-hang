#!/bin/bash
# Summary of the long soak of the fixed e1000e (run from any machine with SSH access).
# Counts since /var/lib/e1000e-hang-test/soak-start on the test host: hangs, reboots, transmit
# timeouts, payload mismatches at the peer, verified TCP bytes and UDP datagrams, phases, and the
# NIC's hardware TSO segment counter. Usage: soak-status.sh
set -u
host=10.0.255.35; peer=10.0.255.34
h() { timeout 30 ssh -o ConnectTimeout=8 -o BatchMode=yes -p 2223 root@$host "$@"; }
p() { timeout 30 ssh -o ConnectTimeout=8 -o BatchMode=yes -p 2223 root@$peer "$@"; }
start=$(h cat /var/lib/e1000e-hang-test/soak-start) || { echo "test host unreachable"; exit 1; }
since=$(date -u -r "$start" +%FT%T 2>/dev/null || date -u -d "@$start" +%FT%T)
now=$(date +%s)
echo "soak since ${since}Z ($(( (now - start) / 3600 )) h $(( (now - start) % 3600 / 60 )) min)"
h "echo \"module now:        \$(cat /sys/module/e1000e/srcversion) (boot \$(uptime -s))\"
   echo \"hangs:             \$(awk '\$1 >= \"$since\"' /var/lib/e1000e-hang-test/debug/watch.log | grep -c 'hang, link dead')\"
   echo \"boots since start: \$(journalctl --list-boots --no-pager | awk 'NR>1' | while read -r _ _ _ d t _; do [ \"\$(date -d \"\$d \$t\" +%s 2>/dev/null)\" -ge $start ] && echo x; done | grep -c x)\"
   echo \"tx timeouts:       \$(journalctl -k --since @$start --no-pager | grep -c 'NETDEV WATCHDOG')\"
   echo \"unit hang msgs:    \$(journalctl -k --since @$start --no-pager | grep -c 'Detected Hardware Unit Hang')\"
   echo \"tx_dropped (boot): \$(cat /sys/class/net/eno1/statistics/tx_dropped)\"
   echo \"tso segs (load):   \$(ethtool -S eno1 | awk '/tx_tcp_seg_good/{print \$2}')\"
   echo \"phases:\"; sed 1d /var/lib/e1000e-hang-test/soak.log | awk '{print \"  \" \$1, \$2, \$3, \$4, \$5}' | tail -6"
p "awk -v s='$since' '\$1 >= s' /var/lib/e1000e-hang-test/verify.log > /tmp/soak-window.log
   echo \"payload mismatches: \$(grep MISMATCH /tmp/soak-window.log | awk '!seen[\$0]++' | wc -l)\"
   grep totals /tmp/soak-window.log | awk '{for(i=3;i<=NF;i++){split(\$i,a,\"=\"); v[a[1]]=a[2]}
       b=v[\"tcp_bytes\"]; u=v[\"udp_ok\"]; g=v[\"udp_gaps\"]; x=v[\"udp_bad\"]
       if (n++) { tb += (b>=pb ? b-pb : b); tu += (u>=pu ? u-pu : u); tg += (g>=pg ? g-pg : g); tx += (x>=px ? x-px : x) }
       pb=b; pu=u; pg=g; px=x}
       END {printf \"verified TCP:       %.2f TB\\nverified UDP:       %d datagrams (%d bad, %d lost)\\n\", tb/1e12, tu, tx, tg}'"
