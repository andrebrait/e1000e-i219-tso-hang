#!/bin/bash
# Fingerprint of everything in the e1000e reproduction except the driver under test.
# Run on proxmox-6 (test host); it collects proxmox-5 (peer) and VM 900 (guest) over SSH.
# Two runs are comparable only if their manifests are identical (diff -u a b).
set -u
peer=10.0.255.34
vm=10.0.255.90
h() { sha256sum "$@" 2>/dev/null | awk '{print $1, $2}'; }

echo "## test host $(hostname)"
echo "kernel $(uname -r)"
echo "cmdline $(cat /proc/cmdline)"
h /usr/local/sbin/e1000e-verify.py /usr/local/sbin/e1000e-debug-watch.sh \
  /etc/systemd/system/e1000e-verify-sink.service /etc/systemd/system/e1000e-verify-load.service \
  /etc/systemd/system/e1000e-debug-watch.service /root/e1000e-fix/swap-module.sh \
  /etc/initramfs-tools/hooks/zz-drop-e1000e /etc/network/interfaces
echo "enabled: $(systemctl is-enabled e1000e-verify-sink e1000e-verify-load e1000e-debug-watch disable-nic-offload-e1000e e1000e-hang-watch e1000e-repro e1000e-sparse-send 2>&1 | tr '\n' ' ')"
echo "vm900: $(qm config 900 | grep -vE '^(digest|vmgenid)' | sha256sum | cut -c1-16) onboot=$(qm config 900 | sed -n 's/^onboot: //p')"
echo "mtu eno1=$(cat /sys/class/net/eno1/mtu) vmbr0=$(cat /sys/class/net/vmbr0/mtu)"
echo "eee: $(ethtool --show-eee eno1 2>/dev/null | sed -n 's/.*EEE status: //p')"
echo "pause: $(ethtool -a eno1 2>/dev/null | awk '/^(RX|TX):/{printf "%s%s ", $1, $2}')"
echo "offloads (after module load, defaults expected):"
ethtool -k eno1 | grep -vE 'fixed\]$' | sort

echo "## peer $peer"
ssh -p 2223 -o BatchMode=yes -o StrictHostKeyChecking=accept-new root@$peer '
  echo "kernel $(uname -r)"
  sha256sum /usr/local/sbin/e1000e-verify.py /etc/systemd/system/e1000e-verify-sink.service /etc/systemd/system/e1000e-verify-load.service | awk "{print \$1, \$2}"
  echo "enabled: $(systemctl is-enabled e1000e-verify-sink e1000e-verify-load disable-nic-offload-e1000e e1000e-sparse-send iperf3-e1000e-repro 2>&1 | tr "\n" " ")"
  echo "tso $(ethtool -k eno1 | sed -n "s/^tcp-segmentation-offload: //p") mtu $(cat /sys/class/net/eno1/mtu)"'

echo "## guest $vm"
ssh -p 22 -o BatchMode=yes -o StrictHostKeyChecking=accept-new root@$vm '
  echo "kernel $(uname -r)"
  sha256sum /usr/local/sbin/e1000e-verify.py /etc/systemd/system/vm-verify.service | awk "{print \$1, \$2}"
  echo "enabled: $(systemctl is-enabled vm-verify vm-sparse vm-bursts 2>&1 | tr "\n" " ")"
  echo "tso $(ethtool -k eth0 | sed -n "s/^tcp-segmentation-offload: //p") mtu $(cat /sys/class/net/eth0/mtu)"'
