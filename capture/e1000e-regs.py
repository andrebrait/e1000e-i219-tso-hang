#!/usr/bin/env python3
"""Read e1000e registers from BAR0 (read-only mmap of the PCI resource).
Usage: e1000e-regs.py <pci addr> <offset>...   e.g. e1000e-regs.py 0000:00:1f.6 0x3840 0x0f28"""
import mmap
import struct
import sys

with open(f"/sys/bus/pci/devices/{sys.argv[1]}/resource0", "r+b", buffering=0) as f:
    m = mmap.mmap(f.fileno(), 0x20000, mmap.MAP_SHARED, mmap.PROT_READ)  # sysfs needs an O_RDWR fd
    for off in sys.argv[2:]:
        o = int(off, 16)
        print(f"{o:#06x} {struct.unpack_from('<I', m, o)[0]:#010x}")
