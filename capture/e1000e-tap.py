#!/usr/bin/env python3
"""Packet tap that keeps transmitted skbs cloned until e1000e sees them.

A plain AF_PACKET socket (no mmap ring, no snap length) queues a clone of every frame. When
dev_queue_xmit_nit() clones a TSO skb before ndo_start_xmit, the skb's header is shared, so
e1000_tso() must copy it (skb_cow_head) to a new allocation: the headers move after any check in
ndo_features_check. Usage: e1000e-tap.py <iface>
"""
import socket
import sys

s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0003))  # ETH_P_ALL
s.setsockopt(socket.SOL_SOCKET, 33, 256 << 20)  # SO_RCVBUFFORCE (not exported by Python)
s.bind((sys.argv[1], 0))
buf = bytearray(1 << 16)
while True:
    s.recv_into(buf)
