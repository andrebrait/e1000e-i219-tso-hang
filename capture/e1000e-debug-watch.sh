#!/bin/bash
# e1000e hang debugger for proxmox-6 (replaces the passive hang watch there).
# - Turns on the driver's hardware messages, so its reset path prints e1000e_dump(): all registers
#   and the full TX/RX descriptor rings.
# - Records every packet handed to the NIC (net_dev_start_xmit: len, frags, gso_size/segs/type)
#   and its result (net_dev_xmit) in a tracing instance, plus NAPI polls and the NIC's interrupts.
# - On "Detected Hardware Unit Hang": freezes the trace, captures NIC/PCIe state, gives the driver
#   120 s to recover, captures again, and reboots only if the link stays dead. Recovered hangs are
#   recorded and watching continues, so one boot can yield several samples.
set -u
iface=${1:-eno1}
peer=${2:-10.0.255.34}
root=/var/lib/e1000e-hang-test/debug
tr=/sys/kernel/tracing/instances/e1000e
mkdir -p "$root"
mountpoint -q /sys/kernel/tracing || mount -t tracefs nodev /sys/kernel/tracing

arm_trace() {
    mkdir -p "$tr"
    echo 0 > "$tr/tracing_on"
    echo 32768 > "$tr/buffer_size_kb"          # per CPU: minutes of history at line rate
    echo > "$tr/trace"
    for e in net/net_dev_start_xmit net/net_dev_xmit; do
        echo "name == \"$iface\"" > "$tr/events/$e/filter"
        echo 1 > "$tr/events/$e/enable"
    done
    echo "dev_name == \"$iface\"" > "$tr/events/napi/napi_poll/filter" 2>/dev/null
    echo 1 > "$tr/events/napi/napi_poll/enable" 2>/dev/null
    local irqs; irqs=$(awk -v i="$iface" '$0 ~ i {sub(":","",$1); printf "%sirq == %s", (n++ ? " || " : ""), $1}' /proc/interrupts)
    if [ -n "$irqs" ]; then echo "$irqs" > "$tr/events/irq/irq_handler_entry/filter"; echo 1 > "$tr/events/irq/irq_handler_entry/enable"; fi
    echo 1 > "$tr/tracing_on"
}

snap() {   # snap <dir>
    local d=$1; mkdir -p "$d"
    ethtool -d "$iface" raw off > "$d/regs.txt" 2>&1
    ethtool -S "$iface"         > "$d/stats.txt" 2>&1
    ethtool -k "$iface"         > "$d/features.txt" 2>&1
    ethtool --show-eee "$iface" > "$d/eee.txt" 2>&1
    ethtool -a "$iface"         > "$d/pause.txt" 2>&1
    ethtool "$iface"            > "$d/link.txt" 2>&1
    lspci -vvv -s "$(basename "$(readlink -f /sys/class/net/$iface/device)")" > "$d/lspci.txt" 2>&1
    cat /proc/interrupts        > "$d/interrupts.txt"
    ip -s -s link show "$iface" > "$d/ip-link.txt" 2>&1
    journalctl -k --since "-15min" --no-pager -o short-monotonic > "$d/kernel.log"
}

ethtool -s "$iface" msglvl 0x26ff     # drv probe link timer ifdown ifup rx_err tx_err tx_done hw (tx_done: full ring dump)
arm_trace
echo "$(date -u +%FT%TZ) armed on $iface (msglvl $(ethtool $iface | sed -n "s/.*Current message level: \\(0x[0-9a-f]*\\).*/\\1/p"), e1000e module $(cat /sys/module/e1000e/srcversion), tracing instance e1000e)" >> "$root/watch.log"

journalctl -k -f -n 0 --no-pager --grep='Detected Hardware Unit Hang' | while read -r line; do
    echo 0 > "$tr/tracing_on"                    # freeze the packets that led up to it
    echo 0 > /sys/kernel/tracing/tracing_on       # and the driver's trace_printk lines (instrumented module)
    d="$root/$(date -u +%Y%m%dT%H%M%SZ)"
    mkdir -p "$d"
    printf '%s\n' "$line" > "$d/trigger.txt"
    snap "$d/at-hang"
    cp "$tr/trace" "$d/xmit-trace.txt"
    grep "e1000e-tso" /sys/kernel/tracing/trace > "$d/tso-trace.txt" 2>/dev/null
    sleep 120                                    # driver hang detection, tx timeout, reset, e1000e_dump
    snap "$d/after-120s"
    if ping -c 3 -W 2 -I "$(ip -o route get "$peer" | awk '{for(i=1;i<NF;i++) if($i=="dev") print $(i+1)}')" "$peer" >/dev/null 2>&1; then
        echo RECOVERED > "$d/verdict"
        echo "$(date -u +%FT%TZ) hang, recovered: $d" >> "$root/watch.log"
        journalctl --sync
        echo 1 > /sys/kernel/tracing/tracing_on
        arm_trace
    else
        echo DEAD > "$d/verdict"
        echo "$(date -u +%FT%TZ) hang, link dead after 120 s, rebooting: $d" >> "$root/watch.log"
        journalctl --sync; sync
        systemctl reboot
        exit 0
    fi
done
