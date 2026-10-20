# Intel I219 (e1000e) TSO erratum: corrupted data and transmit hang

Reproducer, debugging tools and raw evidence for an upstream Linux patch to e1000e.
v1 ("e1000e: segment TSO packets whose headers cross a 4 KB boundary") was posted to
intel-wired-lan and netdev on 2026-10-18
(https://lore.kernel.org/netdev/20261018203215.77371-1-andrebrait@gmail.com/); review led to
a deeper investigation and a different fix, "e1000e: work around TSO erratum that corrupts data and
hangs Tx".

**The bug.** When the first buffer of a TSO packet is long and a 4 KB boundary lies just after its
headers, the NIC repeats a DMA read: it transmits TCP segments carrying the stream data from 128
bytes earlier with a valid checksum, then the transmit DMA hangs (`Detected Hardware Unit Hang`).
Intel documents this for the 82571/82572 (erratum 76), 82574 (erratum 17) and 82583V (erratum 7);
we measured it on the I219-LM (CNP), where the window is 1 to 16 bytes after the TCP checksum
field. Forwarded VM traffic (tun/vhost-net, bridged onto the I219) produces such packets, so
hypervisor hosts are the ones that hit it.

**The fix.** Intel's documented workaround, applied to all e1000e MACs: keep a TSO packet's headers
in a first buffer of at most `3 * (max_read_request - 4)` bytes (372 on the I219)
([`fix/e1000e-v3.py`](fix/e1000e-v3.py)). Hardware TSO stays on.

The full investigation, including the approaches that did not work, is in [`REPORT.md`](REPORT.md).

## Results

Upstream v7.0.14 e1000e on an I219-LM, with verified guest and host traffic (the receiver checks
every byte); all runs in [`evidence/v3/results.txt`](evidence/v3/results.txt).

| Test | Unpatched | Patched |
|---|---|---|
| TSO buffers placed at hang offsets, 66-byte headers (N = 51, 56, 60, 64, 66) | hang in seconds | no hang, about 1,000 packets each |
| Placed, 54-byte headers (N = 51, 64, 66) | hang | no hang |
| Placed, 70-byte headers, VLAN (N = 55, 70) | hang | no hang |
| Natural traffic, TCP timestamps on | hang at 387 s | 60 min, no hang |
| Natural traffic, TCP timestamps off | hang at 402 s | 40 min, no hang |
| Natural traffic, VLAN tag inserted by the stack | hang at 1,176 s | 40 min, no hang |

The unpatched driver corrupted payload at every hang; the patched one corrupted none, had no
transmit timeouts or drops, and the NIC counted 26.5 million hardware TSO segments.

How the window was found and what else was tried: [`evidence/split-sweep/`](evidence/split-sweep)
(placement sweep), [`evidence/ts-off/`](evidence/ts-off) (v1 and the intermediate v2 hang with
54-byte headers), [`evidence/errata/`](evidence/errata) (SPT/KBL register workaround: no effect),
[`evidence/descsplit/`](evidence/descsplit) (splitting at 4 KB: no effect),
[`evidence/hdrbuf372/`](evidence/hdrbuf372) (Intel's workaround: fixes it), and related reports and
errata from other controllers with links in
[`evidence/related/similar-issues.md`](evidence/related/similar-issues.md).

Earlier results for v1 (A/B soak, VLAN runs) remain in [`REPORT.md`](REPORT.md),
[`evidence/ab/`](evidence/ab) and [`evidence/vlan/`](evidence/vlan); tag `v2` marks the state before
the sweep.

## Contents

| Path | What it is |
|---|---|
| [`REPORT.md`](REPORT.md) | Investigation report: symptom, captures, analysis, fix, test log |
| [`evidence/`](evidence) | Hang captures, sweep and run results, related errata and reports, raw logs |
| [`reproducer/e1000e-verify.py`](reproducer/e1000e-verify.py) | Traffic generator whose receiver checks every byte (`sink`, `send <peer>`, `load <peer> <mss> <streams>`) |
| [`reproducer/units/`](reproducer/units) | The exact systemd units used on the test host, the peer and the guest |
| [`reproducer/manifest.sh`](reproducer/manifest.sh) | Fingerprint of the setup apart from the driver, so A/B runs can be compared |
| [`reproducer/split-sweep.sh`](reproducer/split-sweep.sh), [`split-bisect.sh`](reproducer/split-bisect.sh), [`v3-test.sh`](reproducer/v3-test.sh), [`confirm-run.sh`](reproducer/confirm-run.sh) | Drivers for the placement sweep, its bisection, the final test plan and single-module runs |
| [`capture/e1000e-debug-watch.sh`](capture/e1000e-debug-watch.sh) | Hang watcher: driver ring dumps, transmit trace, NIC and PCIe state at each hang |
| [`capture/e1000e-instrument.py`](capture/e1000e-instrument.py) | Adds a `trace_printk()` of each TSO packet's layout to `e1000_xmit_frame()`, and of each v2 diversion (no behaviour change) |
| [`capture/e1000e-tap.py`](capture/e1000e-tap.py) | Packet tap that keeps transmitted skbs cloned, so `e1000_tso()` copies their headers |
| [`capture/e1000e-regs.py`](capture/e1000e-regs.py) | Reads NIC registers from BAR0 |
| [`fix/`](fix) | The v1 patch as posted, the final fix (`e1000e-v3.py`), and every test-only transform: placement (`e1000e-split-place.py`, `e1000e-split-sweep.py`), the intermediate v2, SPT/KBL errata extension, descriptor split, 372-byte cap |
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

Without a fix, our I219-LM hung about every 7 to 13 minutes with TCP timestamps on, and within
minutes with them off. MTU was 1500, and EEE and flow control were on. To hit a chosen offset
directly, build e1000e with `fix/e1000e-split-place.py` and set
`/sys/module/e1000e/parameters/split_before`.

## How this was made

The investigation and the patch were done with the help of an AI coding assistant (Claude, by
Anthropic), running as an agent with SSH access to the test hosts. The assistant wrote the tools in
this repository, ran and analysed the tests, found the matching Intel erratum, and drafted the patch
and the report. Andre Brait provided the hardware, directed the work, set the evidence standards
(byte-verified traffic, controls that must fail first, identical setups), and reviewed the results.

## License

GPL-2.0 (see [`LICENSE`](LICENSE)), the same as the Linux kernel.
