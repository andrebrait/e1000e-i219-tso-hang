# Intel I219 (e1000e) transmit hang on TSO packets whose headers cross a 4 KB boundary

Reproducer, debugging tools and raw evidence for the upstream Linux patch
"e1000e: segment TSO packets whose headers cross a 4 KB boundary" (`[PATCH iwl-net]`, sent to
intel-wired-lan and netdev).

On I219-LM (8086:15bb) and I219-V (8086:15bc), the transmit DMA stops for good on a TSO packet
whose protocol headers cross a 4 KB boundary in the buffer that holds them. e1000e then logs
`Detected Hardware Unit Hang` every two seconds and the link stays down until the adapter is reset.
Just before the queue stops, the NIC sends corrupted TCP payload (the stream data from 128 bytes
earlier) with a valid checksum. Such packets come from forwarded guest traffic (virtio-net and
vhost-net bridged onto the I219), so hypervisor hosts are the ones affected. The fix makes e1000e
segment those few packets in software.

The full investigation is in [`REPORT.md`](REPORT.md).

## Results

Upstream v7.0.14 e1000e, built from one source tree with and without the patch
([`evidence/ab/tree.diff`](evidence/ab/tree.diff)), on an I219-LM under the same verified traffic.
The rest of the setup was fingerprinted before each run and was identical
([`evidence/ab/manifest-*.txt`](evidence/ab)).

| | Duration | Hangs | Corrupted TCP chunks | TCP verified | Throughput | UDP loss |
|---|---|---|---|---|---|---|
| Without patch | 64 min | 5 | 6 | 0.24 TB | 794-813 Mb/s | 0.021% |
| With patch | 252 min | 0 | 0 | 1.51 TB | 795-813 Mb/s | 0.022% |

Every corrupted chunk ([`evidence/ab/mismatches.txt`](evidence/ab/mismatches.txt), which also
covers a run with Proxmox's own e1000e build) held the stream data from exactly 128 bytes earlier
and arrived at the moment a hang began.

## Contents

| Path | What it is |
|---|---|
| [`REPORT.md`](REPORT.md) | Investigation report: symptom, captures, analysis, fix, test log |
| [`evidence/`](evidence) | Hang captures (register and TX ring dumps, per-packet TSO layout logs) and the A/B run data |
| [`reproducer/e1000e-verify.py`](reproducer/e1000e-verify.py) | Traffic generator whose receiver checks every byte (`sink`, `send <peer>`, `load <peer> <mss> <streams>`) |
| [`reproducer/units/`](reproducer/units) | The exact systemd units used on the test host, the peer and the guest |
| [`reproducer/manifest.sh`](reproducer/manifest.sh) | Fingerprint of the setup apart from the driver, so A/B runs can be compared |
| [`capture/e1000e-debug-watch.sh`](capture/e1000e-debug-watch.sh) | Hang watcher: driver ring dumps, transmit trace, NIC and PCIe state at each hang |
| [`capture/e1000e-instrument.py`](capture/e1000e-instrument.py) | Adds a `trace_printk()` of each TSO packet's layout to `e1000_xmit_frame()` (no behaviour change) |
| [`fix/`](fix) | The patch, plus the instrumented variant used in the first soak |
| [`earlier-reproducer/`](earlier-reproducer) | The first reproducer (`iperf3` and a sparse generator, data not checked) |

`reproducer/e1000e-verify.py` and the unit files are byte-identical to what ran in the A/B test;
their SHA-256 sums match the ones in `evidence/ab/manifest-*.txt`.

## Reproducing

You need an I219 host running a Linux guest with virtio-net on a bridge over the I219, and a second
machine on the same switch as the peer (offloads off on the peer, so it cannot hang itself).

1. Copy `e1000e-verify.py` to `/usr/local/sbin/` on the test host, the peer and the guest.
2. On the test host and the peer, run `e1000e-verify.py sink` and `e1000e-verify.py load <other
   host> 536 8`.
3. In the guest, run `e1000e-verify.py send <peer>`.
4. Watch the sinks' logs for `MISMATCH` and the test host's kernel log for `Detected Hardware Unit
   Hang`. Optionally run `capture/e1000e-debug-watch.sh <iface> <peer>` on the test host to capture
   each hang.

Without the patch, our I219-LM hung about every 7 to 13 minutes. MTU was 1500, and EEE and flow
control were on.

## How this was made

The investigation and the patch were done with the help of an AI coding assistant (Claude, by
Anthropic), running as an agent with SSH access to the test hosts. The assistant wrote the tools in
this repository, analysed the captures and drafted the patch and the report. Andre Brait provided
the hardware, directed the work, required the byte-verifying reproducer and the controlled A/B
test, and reviewed the results.

## License

GPL-2.0 (see [`LICENSE`](LICENSE)), the same as the Linux kernel.
