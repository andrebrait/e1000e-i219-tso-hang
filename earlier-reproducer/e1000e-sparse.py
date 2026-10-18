#!/usr/bin/env python3
"""Sparse mixed traffic for the e1000e hang: what the NIC sees on a quiet cluster node.

  e1000e-sparse.py sink            discard TCP :5202 and UDP :5203
  e1000e-sparse.py send <peer>     send to the peer's sink until stopped

The hang on proxmox stopped the transmit DMA on the last descriptor of a two-descriptor packet,
with only 7 descriptors queued. So: low rate, random idle gaps (each packet wakes the link from
EEE low-power idle), TCP sends sized to give header + one fragment (and other segment
boundaries) with TCP_NODELAY so each write is its own skb, interleaved with small linear UDP
packets like corosync's.
"""
import os
import random
import socket
import sys
import threading
import time

MSS = 1448
SIZES = ([*range(1, 9)] + [*range(MSS - 8, MSS + 9)] + [*range(2 * MSS - 4, 2 * MSS + 5)]
         + [*range(4088, 4105)] + [MSS * k + d for k in (3, 4, 8, 16, 32) for d in (1, 2, 3, 4)])
BUF = os.urandom(70000)


def sink():
    def tcp_client(conn):
        with conn:
            while conn.recv(65536):
                pass
    t = socket.socket(); t.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    t.bind(("0.0.0.0", 5202)); t.listen(16)
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); u.bind(("0.0.0.0", 5203))
    threading.Thread(target=lambda: [u.recv(65536) for _ in iter(int, 1)], daemon=True).start()
    while True:
        conn, _ = t.accept()
        threading.Thread(target=tcp_client, args=(conn,), daemon=True).start()


def gap():
    time.sleep(min(random.expovariate(1 / 0.02), 0.1) + 0.001)   # 1-100 ms idle, mean ~20 ms


def tcp_sender(peer):
    while True:
        try:
            s = socket.create_connection((peer, 5202), timeout=10)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            while True:
                n = random.choice(SIZES) if random.random() < 0.8 else random.randint(1, 65000)
                s.sendall(BUF[:n])
                if random.random() < 0.02:                           # short back-to-back run
                    for _ in range(random.randint(5, 40)):
                        s.sendall(BUF[:random.choice(SIZES)])
                gap()
        except OSError:
            time.sleep(2)


def udp_sender(peer):
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    while True:
        try:
            u.sendto(BUF[:170 if random.random() < 0.9 else 1400], (peer, 5203))
        except OSError:
            time.sleep(1)
        gap()


if __name__ == "__main__":
    if sys.argv[1:] == ["sink"]:
        sink()
    elif len(sys.argv) == 3 and sys.argv[1] == "send":
        for f in (tcp_sender, udp_sender):
            threading.Thread(target=f, args=(sys.argv[2],), daemon=True).start()
        while True:
            time.sleep(3600)
    else:
        sys.exit(__doc__)
