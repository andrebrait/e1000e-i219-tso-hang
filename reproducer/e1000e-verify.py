#!/usr/bin/env python3
"""Verified traffic for the e1000e hang: guest-like TCP/UDP whose payload the receiver checks.

  e1000e-verify.py sink              verify TCP :5302 and UDP :5303, log totals every 60 s
  e1000e-verify.py send <peer>       guest-like traffic to the peer's sink (run inside the test VM)
  e1000e-verify.py load <peer> <mss> <streams>
                                     host TSO load: continuous TCP with TCP_MAXSEG=<mss>, verified

Sender (run inside the test VM, whose small GSO packets reach the host NIC as linear TSO skbs):
  - sparse TCP: TCP_NODELAY writes sized around segment boundaries (1..8, MSS-8..MSS+8,
    2*MSS-4..2*MSS+4, ~4 KB, k*MSS+1..4) plus random sizes, random 1-100 ms gaps;
  - bulk TCP: 5 min continuous, then 20 short bursts, then 10 min idle, repeating;
  - UDP: 170-byte (corosync-like) and 1400-byte datagrams with a sequence number.

Every TCP byte at stream offset o carries PAT[o % L], and every UDP datagram a pattern derived
from its sequence number, so the sink detects corrupted, missing, duplicated or misplaced data,
including errors TCP cannot catch (with checksum offload the NIC checksums what it fetched).
"""
import os
import random
import socket
import struct
import sys
import threading
import time

L = 65521                                    # prime period: shifts by anything else show up
_b = bytearray(L)
x = 0x9E3779B9
for i in range(L):                           # xorshift32: no short-period structure
    x ^= (x << 13) & 0xFFFFFFFF; x ^= x >> 17; x ^= (x << 5) & 0xFFFFFFFF
    _b[i] = x & 0xFF
PAT = bytes(_b) * 3                          # slices up to 2*L long at any offset
MSS = 1448
SIZES = ([*range(1, 9)] + [*range(MSS - 8, MSS + 9)] + [*range(2 * MSS - 4, 2 * MSS + 5)]
         + [*range(4088, 4105)] + [MSS * k + d for k in (3, 4, 8, 16, 32) for d in (1, 2, 3, 4)])
LOG = "/var/lib/e1000e-hang-test/verify.log"


def log(msg):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- sink

stats = {"tcp_bytes": 0, "tcp_bad_chunks": 0, "tcp_conns": 0, "udp_ok": 0, "udp_bad": 0, "udp_gaps": 0}
lock = threading.Lock()


def tcp_client(conn, peer):
    off = 0
    with conn:
        while True:
            try:
                d = conn.recv(32768)
            except OSError:
                break
            if not d:
                break
            p = off % L
            exp = PAT[p:p + len(d)]
            if d != exp:
                i = next(k for k in range(len(d)) if d[k] != exp[k])
                with lock:
                    stats["tcp_bad_chunks"] += 1
                log(f"MISMATCH tcp from {peer} at stream offset {off + i}: got {d[i:i+16].hex()} "
                    f"expected {exp[i:i+16].hex()} (chunk {len(d)} bytes)")
            off += len(d)
            with lock:
                stats["tcp_bytes"] += len(d)


def udp_sink():
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    u.bind(("0.0.0.0", 5303))
    last = {}
    while True:
        d, a = u.recvfrom(65536)
        if len(d) < 8:
            with lock:
                stats["udp_bad"] += 1
            continue
        seq = struct.unpack("!Q", d[:8])[0]
        p = (seq * 97) % L
        ok = d[8:] == PAT[p:p + len(d) - 8]
        with lock:
            if ok:
                stats["udp_ok"] += 1
            else:
                stats["udp_bad"] += 1
            if a[0] in last and seq > last[a[0]] + 1:
                stats["udp_gaps"] += seq - last[a[0]] - 1
            last[a[0]] = max(seq, last.get(a[0], -1))
        if not ok:
            log(f"MISMATCH udp from {a[0]} seq {seq} len {len(d)}")


def reporter():
    while True:
        time.sleep(60)
        with lock:
            s = dict(stats)
        log("totals " + " ".join(f"{k}={v}" for k, v in s.items()))


def sink():
    t = socket.socket()
    t.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    t.bind(("0.0.0.0", 5302))
    t.listen(32)
    threading.Thread(target=udp_sink, daemon=True).start()
    threading.Thread(target=reporter, daemon=True).start()
    log("sink listening on tcp/5302 udp/5303")
    while True:
        conn, a = t.accept()
        with lock:
            stats["tcp_conns"] += 1
        threading.Thread(target=tcp_client, args=(conn, a[0]), daemon=True).start()


# ---------------------------------------------------------------- sender

def gap():
    time.sleep(min(random.expovariate(1 / 0.02), 0.1) + 0.001)


def connect(peer):
    s = socket.create_connection((peer, 5302), timeout=30)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    s.settimeout(None)
    return s


def send_pattern(s, off, n):
    p = off % L
    s.sendall(PAT[p:p + n])
    return off + n


def tcp_sparse(peer):
    while True:
        try:
            s, off = connect(peer), 0
            while True:
                n = random.choice(SIZES) if random.random() < 0.8 else random.randint(1, 65000)
                off = send_pattern(s, off, n)
                if random.random() < 0.02:
                    for _ in range(random.randint(5, 40)):
                        off = send_pattern(s, off, random.choice(SIZES))
                gap()
        except OSError:
            time.sleep(2)


def tcp_bulk(peer):
    while True:
        try:
            s, off = connect(peer), 0
            end = time.time() + 300                          # 5 min continuous
            while time.time() < end:
                off = send_pattern(s, off, 65000)
            time.sleep(120)
            for _ in range(20):                              # bursts with idle gaps
                end = time.time() + 5
                while time.time() < end:
                    off = send_pattern(s, off, 65000)
                time.sleep(25)
            s.close()
            time.sleep(600)
        except OSError:
            time.sleep(2)


def udp_sender(peer):
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    seq = 0
    while True:
        n = 162 if random.random() < 0.9 else 1392           # 170 / 1400 bytes with the header
        p = (seq * 97) % L
        try:
            u.sendto(struct.pack("!Q", seq) + PAT[p:p + n], (peer, 5303))
        except OSError:
            time.sleep(1)
        seq += 1
        gap()


def tcp_load(peer, mss):
    while True:
        try:
            s = socket.socket()
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_MAXSEG, mss)
            s.connect((peer, 5302))
            off = 0
            while True:
                off = send_pattern(s, off, 65000)
        except OSError:
            time.sleep(2)


if __name__ == "__main__":
    if sys.argv[1:] == ["sink"]:
        sink()
    elif len(sys.argv) == 3 and sys.argv[1] == "send":
        for f in (tcp_sparse, tcp_bulk, udp_sender):
            threading.Thread(target=f, args=(sys.argv[2],), daemon=True).start()
        while True:
            time.sleep(3600)
    elif len(sys.argv) == 5 and sys.argv[1] == "load":
        for _ in range(int(sys.argv[4])):
            threading.Thread(target=tcp_load, args=(sys.argv[2], int(sys.argv[3])), daemon=True).start()
        while True:
            time.sleep(3600)
    else:
        sys.exit(__doc__)
