# e1000e: transmit hang on I219 when a TSO packet's headers cross a 4 KB boundary

Investigation report for the upstream patch
`fix/0001-e1000e-segment-TSO-packets-whose-headers-cross-a-4-KB-boundary.patch`.

## Summary

On Intel I219-LM (8086:15bb) and I219-V (8086:15bc), driven by e1000e, the transmit DMA can stop
for good on a **TSO packet whose protocol headers cross a 4 KB boundary** in the buffer that holds
them. The NIC never writes back the packet's data descriptor, e1000e logs `Detected Hardware Unit
Hang` every two seconds, and the link stays dead until the adapter is reset; in practice the host
is rebooted.

Such packets come from **forwarded guest traffic**: tun/vhost-net copies a VM's small GSO packet
into one linear buffer (header and payload together) taken from a page fragment, which can start
anywhere in a page. The host's own TCP stack never produces them, because its TSO skbs keep the
headers in a small separately allocated linear head. That is why only hypervisor hosts with a busy
VM bridged onto the I219 were affected.

The fix implements `ndo_features_check` in e1000e: for TCP GSO packets whose headers cross a 4 KB
boundary, it clears the GSO features, so the stack segments those few packets in software. All
other packets keep TSO and checksum offload.

Before the queue stops, the NIC also **sends corrupted payload**. A reproducer that checks every
received byte caught it at each of 10 hangs: the TCP segments carried the stream data from
exactly 128 bytes earlier in the stream, with a valid TCP checksum (computed by the NIC over the
wrong bytes), so the receiver accepted them. The data was silently corrupted, not dropped.

## Affected systems

| | |
|---|---|
| NIC | Intel Ethernet Connection (7) I219-LM [8086:15bb] rev 10, subsystem HP 8598; also I219-V [8086:15bc] rev 10 (Lenovo M720q) |
| Firmware | NVM 0.5-4 |
| Hosts | HP ProDesk 600 G5 Desktop Mini (BIOS R22 Ver. 02.27.00, Core i5-9500T); Lenovo ThinkCentre M720q |
| Kernel | 7.0.14-24-pve (Proxmox VE 9.2, Ubuntu-based), e1000e in tree; upstream net.git e1000e has the same transmit path |
| IOMMU | Intel VT-d enabled, default domain Translated (ring at IOVA 0xfffed000) |
| Link | 1000 Mb/s full duplex, flow control Rx/Tx, EEE enabled and active |
| Virtualization | QEMU 11.0.3, guests with virtio-net on a Linux bridge (vmbr0) over the I219, vhost-net |

In production, three hangs between 2026-09-01 and 2026-10-17 on the two cluster nodes that run a
busy VM (one of them rebooted by a hang watchdog); none on the nodes that run only containers.
Until now the only known workaround was turning off the NIC's offloads entirely
(`ethtool -K <if> tso off gso off gro off tx off rx off sg off`), which is what many guides for
"e1000e Detected Hardware Unit Hang" recommend.

## Symptom

```
e1000e 0000:00:1f.6 eno1: Detected Hardware Unit Hang:
  TDH                  <1>
  TDT                  <44>
  next_to_use          <44>
  next_to_clean        <0>
buffer_info[next_to_clean]:
  time_stamp           <1000b67e5>
  next_to_watch        <1>
  jiffies              <1000b6cc0>
  next_to_watch.status <0>
MAC Status             <80083>
PHY Status             <796d>
PHY 1000BASE-T Status  <3800>
PHY Extended Status    <3000>
PCI Status             <10>
```

repeated every two seconds; the driver does not recover the queue (no reset within 120 s in any
capture).

## Reproduction

Two kinds of traffic together; neither alone hung in 22 hours.

1. **Guest traffic through the bridge.** A VM (virtio-net, vhost-net) on the I219 host sends TCP
   bulk data and small UDP datagrams to another machine, so its GSO packets reach the host as
   linear skbs.
2. **Host TSO traffic with a different MSS.** Eight TCP streams with MSS 536 from the host to the
   peer, and back. Their role is most likely to keep the transmit ring busy alongside the guest
   packets; they do not themselves produce the bad packets.

All traffic is generated and checked by `e1000e-verify.py`: `send <peer>` in the VM,
`load <peer> 536 8` on both hosts and `sink` on both hosts. Every TCP byte at stream offset o
carries `PAT[o % 65521]` and every UDP datagram carries its sequence number and a derived
pattern, so the sink detects corrupted, missing, duplicated or misplaced data, including errors
that TCP cannot catch. The units are `e1000e-verify-{sink,load}.service` (hosts) and
`vm-verify.service` (VM). `manifest.sh` fingerprints everything in the setup except the driver
(scripts, units, VM configuration, kernels, offloads, MTU, EEE, flow control, kernel command line)
on the test host, the peer and the VM; runs are compared only when their manifests are identical.

With offloads at their defaults, the I219 hung about every 7 to 13 minutes (see "Test log"). The
peer was another host on the same switch with offloads off.

An earlier reproducer (`e1000e-sparse.py` and `iperf3`, which do not check the data) hung the
NIC every 6 to 9 minutes and was used for the investigation below.

The generators, units, debug watcher and capture scripts are in this directory; `README.md` lists
them.

## Investigation

### Data collected at each hang

`e1000e-debug-watch.sh` ran on the test host:
- `ethtool -s eno1 msglvl 0x26ff` (`NETIF_MSG_HW` and `NETIF_MSG_TX_DONE`, among others), so the
  driver's `e1000e_dump()` prints all registers and the full TX descriptor ring at a hang;
- tracepoints `net:net_dev_start_xmit`, `net:net_dev_xmit`, `napi:napi_poll` and the NIC's
  `irq:irq_handler_entry` in a tracing instance, frozen at the hang;
- `ethtool -d/-S/-k/--show-eee/-a`, `lspci -vvv`, `/proc/interrupts` at the hang and 120 s later;
- later, an instrumented e1000e logging each TSO packet's layout from `e1000_xmit_frame()`
  (`fix/e1000e-instrument.py`: ring index, length, linear length, number of fragments, header
  length, MSS, segments, page offset of `skb->data`, and whether the headers or the whole linear
  part cross a page).

Excerpts of four captures are in `evidence/`.

### What the hangs have in common

1. **Link, PHY and PCIe are fine.** STATUS 0x80083 (link up, 1000 FD, bus master enabled), PHY
   status 0x796d, 1000BASE-T status 0x3800, PCI status 0x10 (no master or target abort, no parity
   error). No IOMMU (DMAR) fault and no AER error from the NIC.
2. **The transmit DMA stopped mid-ring on one packet.** TDH points at the data descriptor of the
   packet at next_to_clean; that descriptor's status is 0 (never written back); the TX data FIFO
   is empty (TDFH == TDFT == TDFHS == TDFTS), so the NIC is not holding the packet's data.
3. **The stuck packet is always a linear TSO packet: one context descriptor plus one data
   descriptor holding header and payload.** Capture 20261018T082430Z, ring dump:

   ```
   Tc[0x000] 000032220021180E 05A84200270005AE   TSO context: MSS 0x5A8 (1448), HDRLEN 0x42 (66), PAYLEN 0x5AE (1454)
   Td[0x001] 00000000FFCCAFC0 00000300AF1005F0   data: addr 0xFFCCAFC0, len 0x5F0 (1520), DCMD 0xAF (TSE|EOP|RS|IFCS|IDE), status 0
   Tc/Td[0x002..0x009]                            66-byte ACKs (checksum offload) queued behind it
   ```

   The traced packet with the same skb address: `len=1520 data_len=0 gso_size=1448 gso_segs=2`,
   sent from softirq context, i.e. forwarded from the VM. The four ACKs after it in the trace are
   exactly descriptors 0x003-0x009.
4. **Its headers cross a 4 KB boundary.** The data buffer starts at page offset 0xFC0 (4032), so
   the 66-byte header (Ethernet 14 + IPv4 20 + TCP 32 with timestamps) spans 4032-4098.

The instrumented driver confirmed this on three further hangs (20261018T103307Z, ...T104202Z,
...T104845Z). Each stuck packet:

```
len=2962 headlen=2962 frags=0 hdr=66 mss=1448 segs=2 pgoff=4032 hdr_cross=1 head_cross=1
```

and in the same windows:

| Capture | TSO packets logged | linear TSO | headers cross 4 KB | linear part crosses, headers do not |
|---|---|---|---|---|
| 103307Z | 118,784 | 8,552 | 37 (36 passed, the 37th hung) | 7,068 |
| 104202Z | 118,699 | | 19 (18 passed, the 19th hung) | |
| 104845Z | 31,520 | | 3 (2 passed, the 3rd hung) | |

So:
- every hang with full data stopped on a TSO packet whose headers cross a 4 KB boundary, while such
  packets are about 0.03% of TSO traffic;
- linear TSO packets whose buffer crosses a page only after the headers (7,068 in one window) never
  hung;
- not every header-crossing packet hangs (1 in 3 to 1 in 37 here), so a timing condition inside
  the NIC is also involved. A plausible mechanism is the header fetch being split into two PCIe
  read requests at the 4 KB boundary (PCIe reads may not cross 4 KB) racing with the TSO engine;
  we have no Intel documentation to confirm it.

### Ruled out on the way

- **Load alone:** 30 minutes at ~920 Mb/s in both directions with all offloads on; 22 hours of
  mixed host-only traffic (sustained, small MSS, 64 streams, small UDP, bursts with idle gaps for
  EEE transitions): no hang.
- **e1000's "non-TSO linear packet after a TSO packet" erratum** (e1000 splits off the last 4
  bytes of such a packet): the stuck descriptor is the TSO packet's own, not a packet after it.
- **IOMMU faults from premature descriptor write-back:** no DMAR fault in any hang.
- **The MSS switch between host and guest TSO packets:** 12 of the 36 header-crossing packets that
  passed in one window also followed a packet with the other MSS.
- **A tiny last segment:** the first two stuck packets had 6-7 bytes in the second segment, the
  later ones exactly 2 x 1448.

### Why only forwarded guest traffic

The host's TCP stack builds TSO skbs with the headers (plus a few bytes) in a small linear head
from the slab allocator (headlen 70 in the traces) and the payload in page fragments; small slab
objects never straddle a page. tun/vhost-net builds a single linear skb for small guest GSO packets
from a page fragment, so header and payload are contiguous and the start offset depends on the
sizes of the previous allocations. At MTU 1500 (MSS 1448), the 2962-byte buffers regularly start
64 bytes before a page end. A different MTU changes which packets land there, not whether it can
happen.

## The fix

```c
static netdev_features_t e1000_features_check(struct sk_buff *skb,
					      struct net_device *netdev,
					      netdev_features_t features)
{
	if (skb_is_gso(skb) &&
	    (skb_shinfo(skb)->gso_type & (SKB_GSO_TCPV4 | SKB_GSO_TCPV6)) &&
	    (offset_in_page(skb->data) & (SZ_4K - 1)) +
	    skb_tcp_all_headers(skb) > SZ_4K)
		features &= ~NETIF_F_GSO_MASK;

	return features;
}
```

replacing `passthru_features_check` in `e1000e_netdev_ops`.

- `ndo_features_check` runs for every packet before transmission; clearing the GSO bits makes
  `validate_xmit_skb()` segment that packet in software (`skb_gso_segment()`), so the NIC receives
  ordinary MSS-sized packets with checksum offload instead of one TSO packet.
- The check uses the 4 KB offset of `skb->data`, which is what the NIC fetches: the DMA address of
  the linear part keeps the offset within a 4 KB page with or without an IOMMU (IOMMU granule 4 KB
  on x86), and masking with `SZ_4K - 1` keeps the check right where `PAGE_SIZE` is larger than
  4 KB.
- Only TCP GSO types are considered, so `skb_tcp_all_headers()` is valid; e1000e advertises TSO
  for TCP over IPv4 and IPv6 only.
- Cost: one branch per packet, plus software segmentation of the affected packets. In the
  reproduction, about one packet per second was diverted out of roughly 3,000 TSO packets per
  second.

Not chosen:
- Disabling TSO for the whole interface (today's common workaround): costs throughput and CPU
  for all traffic, while the bug needs one specific packet layout.
- Copying the headers into a driver-owned bounce buffer: more code in the hot path for a rare
  packet; software segmentation already exists and is well tested.
- Reallocating the skb head: the new offset is not controllable without extra copying.

## Results

- **Diversion verified** with the instrumented fix module: in the first minute of reproduction,
  16 header-crossing TSO packets were segmented in software, 0 reached the NIC as TSO, and
  80,453 other TSO packets were transmitted unchanged. With the clean patch, a kretprobe on
  `e1000_features_check()` (17:07:57-17:09:57 UTC) counted 8,612,900 calls: 64 returned the
  features without `NETIF_F_TSO | NETIF_F_TSO6` (0x110000, the only GSO features enabled on the
  interface), the rest the full set. Checksum offload stays on for the diverted packets.
- **A/B test with the verifying reproducer:** upstream v7.0.14 e1000e, built once without and once
  with the patch from source trees whose only difference is the patch, under identical manifests.
  Unpatched: 5 hangs in 64 minutes, each with corrupted payload. Patched: 0 hangs and 0 corrupted
  bytes in 4 hours 12 minutes, where about 20 hangs were expected at the unpatched rate.
- **No regressions seen with the patch:** 1.51 TB of TCP payload and 720,203 UDP datagrams
  verified at the peer without a single error; throughput unchanged (795-813 Mb/s against
  794-813 Mb/s unpatched, one-minute averages); UDP loss unchanged (0.022% against 0.021%, queue
  drops on the saturated 1 Gb/s link); no kernel warnings and no NIC error counters.
- **Corruption signature:** all 11 corrupted chunks (10 events, one per hang) came from the
  guest's stream, were whole segments (1448 to 5792 bytes), and held the data from 128 bytes
  earlier in the stream. Each was received at the moment the hang began (the watcher logs the
  hang about 130 s after onset, when it decides to reboot).

## Test log

All on 2026-10-18 (UTC), proxmox-6 (I219-LM), kernel 7.0.14-24-pve, offloads at defaults.

| Driver | Traffic | Window | Hangs | Corrupted payload |
|---|---|---|---|---|
| stock e1000e (Proxmox build) | earlier reproducer | 08:02-08:57 | 3 (08:02, 08:24, 08:57), each a reboot | not checked |
| upstream v7.0.14 + instrumentation only (no behaviour change) | earlier reproducer | 10:28-10:54 | 3 (10:33, 10:42, 10:48), each a reboot | not checked |
| upstream v7.0.14 + fix + instrumentation (`fix/e1000e-tso-hdr-4k.patch`) | earlier reproducer | 10:56-12:27 | 0 | not checked |
| stock e1000e (Proxmox build, `E3A63E01041D6A13D1FAAFF`) | verifying reproducer | 12:30-13:04 | 5 (onsets 12:30, 12:40, 12:47, 12:55, 13:02) | 5 events, 5 chunks, all -128 bytes |
| upstream v7.0.14, unpatched (`13BCF787596E0D3E967EF44`) | verifying reproducer | 13:13:34-14:17 | 5 (onsets 13:40, 13:48, 13:55, 14:04, 14:15) | 5 events, 6 chunks, all -128 bytes |
| upstream v7.0.14 + fix (`E7777359E1D6B11377D070E`, the submitted patch) | verifying reproducer | 14:26:32-18:38:27 | 0 | 0 |

## Open questions for Intel

- Is this a known I219 (PCH) erratum, and does it apply to other e1000e MACs (82574, 82579,
  I217/I218)? We only had I219-LM/-V; the fix is applied to all e1000e devices because the check is
  cheap and the affected packets are rare.
- Is the exact condition "headers cross a 4 KB boundary", or "the first descriptor's buffer crosses
  4 KB within the first N bytes"? The data fits the former; all stuck packets also crossed within
  the first 64 bytes.
