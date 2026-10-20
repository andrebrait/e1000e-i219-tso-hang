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

v2 (`fix/e1000e-v2.py`, applied to `drivers/net/ethernet/intel/e1000e/netdev.c`) checks the header
placement in `e1000_xmit_frame()`, where `skb->data` is final:

```c
static bool e1000_tso_hdr_crosses_4k(const struct sk_buff *skb)
{
	return (offset_in_page(skb->data) & (SZ_4K - 1)) +
	       skb_tcp_all_headers(skb) > SZ_4K;
}
```

called for GSO packets after the 82571/2/3 payload pull and after `skb_cow_head(skb, 0)`. Packets
that fail go to `e1000_xmit_gso_sw()`, which segments them with `skb_gso_segment()` (features
without `NETIF_F_GSO_MASK`), reserves ring space for every segment (returning `NETDEV_TX_BUSY` if
there is none yet, dropping the packet if the segments could never fit the ring), and sends each
segment through `e1000_xmit_frame()` as an ordinary packet with checksum offload. The descriptor
count of `e1000_xmit_frame()` moved into `e1000_tx_desc_count()` so both paths use one formula.

- The check uses the 4 KB offset of `skb->data`, which is what the NIC fetches: the DMA address of
  the linear part keeps the offset within a 4 KB page with or without an IOMMU (IOMMU granule 4 KB
  on x86), and masking with `SZ_4K - 1` keeps the check right where `PAGE_SIZE` is larger than
  4 KB.
- Cost: one branch per TSO packet, plus software segmentation of the affected packets (in the
  reproduction about one packet per second out of roughly 3,000 TSO packets per second).

v1 (posted 2026-10-18) did the same check in `ndo_features_check`, which runs in
`validate_xmit_skb()` before the skb's final layout is known. Gabriel pointed out in review that
`validate_xmit_vlan()` runs afterwards: when the NIC does not insert the VLAN tag, the stack pushes
it into the packet and moves `skb->data` back by 4 bytes. `skb_cow_head()` in `e1000_tso()` and the
82571/2/3 payload pull can also move or copy the headers later. See "VLAN tests" below.

Not chosen:
- Disabling TSO for the whole interface (today's common workaround): costs throughput and CPU
  for all traffic, while the bug needs one specific packet layout.
- Copying the headers into a driver-owned bounce buffer: more code in the hot path for a rare
  packet; software segmentation already exists and is well tested.
- Reallocating the skb head: the new offset is not controllable without extra copying.

## VLAN tests (review of v1)

Software VLAN insertion was forced on proxmox-6 with `e1000e-vlan-test.service`: rx/tx VLAN offload
off (e1000e ties the two together), and a `tc` egress filter that pushes VLAN 0 onto every IPv4
frame to the peer. tc runs before `validate_xmit_skb()`, so the stack inserts the tag after
`ndo_features_check`, as it does for any VLAN when the NIC does not. The switch strips VLAN 0, so
the peer receives untagged frames and the rest of the reproduction is unchanged. All other settings
were identical between the v1 and v2 runs (`evidence/vlan/manifest-v1.txt`, `manifest-v2.txt`).

| Driver (2026-10-19 UTC) | Setup | Window | Hangs | Corrupted | TSO packets reaching the NIC with headers crossing 4 KB |
|---|---|---|---|---|---|
| v1 + instrumentation (`A1981ADDE3F02CFA4BFBD2E`) | VLAN | 14:19-15:31 | 0 | 0 (0.42 TB) | about 24 a minute, all `pgoff=4092 hdr=70` |
| v1 + instrumentation | VLAN + packet tap | 1-minute sample | - | - | 0 |
| v2 + instrumentation (`0B84CD06AE663161F792A81`) | VLAN | 15:36:47-16:23:45 | 0 | 0 (0.27 TB) | 0; diverted at `pgoff=4092` and `pgoff=4028` |
| v2 + instrumentation | no VLAN | 16:26:17-17:26:42 | 0 | 0 (0.35 TB) | 0 |

- **v1 misses the VLAN case.** A guest packet whose data started at page offset 0 passed v1's check;
  the stack then pushed the tag in front of it, so the NIC read a 70-byte header at 4092-4161.
- **The NIC tolerated that split.** About 1,700 such packets (4 header bytes before the boundary,
  66 after) in 72 minutes caused no hang and no corruption. The hanging packets had 64 bytes
  before and 2 after; see "Open questions for Intel".
- **Copied headers did not cross here.** A cloning packet tap (`e1000e-tap.py`) makes
  `e1000_tso()` copy every TSO header (`skb_cow_head()`); host headers then land in
  `skbuff_small_head` objects (704 bytes, 23 per 16 KB slab) that ended at most exactly at a 4 KB
  boundary, and guest packets in page-aligned kmalloc-4k buffers. This depends on the allocator
  and its object sizes, so v2 still checks after the copy.
- **v2 diverts every crossing in the final layout,** including the original case shifted by the
  tag (4028), and no crossing TSO packet reached the NIC. Mean throughput at the peer: v1 + VLAN
  774 Mb/s, v2 + VLAN 791 Mb/s, v2 800 Mb/s (unpatched baseline 794-813 Mb/s).
- **No drops from v2.** `tx_dropped` rose by 8 and later by 5, both times while the VLAN unit
  removed its qdisc and reset the NIC; a kprobe on `netdev_core_stats_inc()` showed every one in
  `dev_qdisc_enqueue()`. During 85 minutes of steady traffic with the probe armed, `tx_dropped`
  did not change, so v2's drop path (segments that can never fit the ring) never ran.

## Results (v1, 2026-10-18)

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

2026-10-19: v1 and v2 runs with and without software VLAN insertion; see "VLAN tests".

## Split sweep (2026-10-19/20, test only)

A test-only build (`fix/e1000e-split-sweep.py` on top of v2) moves linear TSO packets back inside
their headroom so that the buffer starts exactly N bytes before a 4 KB boundary, and sends them with
TSO; every other TSO packet whose buffer crosses 4 KB within its first 256 bytes is segmented in
software, so a hang can only come from a placed packet. `split-sweep.sh` sets N on the running host
and waits for a hang (the watcher reboots the host, which also resets N) or for 1,000 placed packets
(25 minutes at most). All runs: `evidence/split-sweep/results.txt`.

| Header (bytes) | TCP checksum offset | Hangs for N | No hang at |
|---|---|---|---|
| 66 (TCP timestamps) | 50 | 51-66 | 1, 2, 4, 8, 16, 32, 48, 49, 50, 67, 69, 75, 87, 111, 159, 255 |
| 54 (no TCP options) | 50 | 51-66 (51, 64, 66 tested) | 50, 67, 69, 75, 87, 111, 159, 255 |
| 70 (VLAN tag in the data) | 54 | 55-70 (55, 70 tested) | 52, 54, 71 |

- **The NIC hangs when a 4 KB boundary falls in the 16 bytes that start at the TCP checksum field:**
  N from `tucso` + 1 to `tucso` + 16, where `tucso` is the checksum's offset in the frame. The
  window moves with the checksum (VLAN shifts it by 4) and does not depend on where the header
  ends: with a 54-byte header it extends 12 bytes into the payload.
- **Every hang corrupted payload.** The received data came from 128 bytes earlier in the stream
  when N was a multiple of 4 (the buffer starts 4-byte aligned) and from 124 bytes earlier
  otherwise; all 24 corrupted chunks fit (`evidence/split-sweep/mismatch-deltas.txt`).
- **v1 and v2 do not cover the window.** With guest TCP timestamps off (54-byte headers) a buffer
  at page offset 4032 puts the boundary 10 bytes into the payload, inside the window, while the
  headers do not cross. The posted v1 (`E7777359`) and the tagged v2 (`85CEFF7A`) both hung on
  exactly this packet, within 5 minutes and 47 seconds (`evidence/ts-off/`). Windows disables TCP
  timestamps by default, so Windows guests are likely affected too [INFERENCE: untested].
- **v2 can stall the transmit queue.** The sweep build diverts larger packets than v2 ever did,
  which exposed that `e1000_xmit_gso_sw()` returns `NETDEV_TX_BUSY` (or drops) without writing
  the tail: descriptors queued earlier under `xmit_more` never reach the NIC and the netdev
  watchdog resets the adapter. Sweep module 2 hit this; module 3 writes the tail on both paths.
  Diverting packets with more segments than fit the ring also made the drop path reachable (4,178
  drops in 10 minutes under module 3; most likely host TCP with MSS 536 [INFERENCE: not traced]).

## Existing SPT/KBL errata workaround (2026-10-20)

`e1000_configure_tx()` already carries an Intel workaround for "Tx hang" and "data corruption" on
SPT/KBL (`e1000_pch_spt` only): set `E1000_RCTL_RDMTS_HEX` in IOSFPC and cut TARC0's outstanding
requests from 3 to 2. Our I219-LM is CNP (`0x15BB`, `e1000_pch_cnp`), so it does not get it.
Extending it to `>= e1000_pch_spt` (`fix/e1000e-errata-cnp.py`, on top of the sweep build) changed
TARC0 from `0d800403` to `2d800403` in every boot; IOSFPC was already `01011108`, with RDMTS_HEX
set by default. The NIC still hung at N=51, 64 and 66, each time with corrupted payload
(`evidence/errata/`). The SPT/KBL workaround does not cover this bug.

## Looking for a controller parameter behind the window (2026-10-20)

A fix that hardcodes "16 bytes" fits the I219-LM by measurement; another MAC could have a different
span. We looked for a property of the controller that the window or the corruption shift could
correspond to, so the fix could use it instead of a constant. We found none:

| Measured | Plausible hardware meaning | Visible anywhere? |
|---|---|---|
| Window: the 4 KB boundary falls 1-16 bytes after the TCP checksum offset (`tucso` < N <= `tucso` + 16) | the TSO engine's header unit or internal datapath width (128 bits); also the TX descriptor size | no: not in the driver, the registers it uses, or the public datasheets |
| Corruption: data from 128 bytes earlier (124 when N is not a multiple of 4) | the NIC's DMA fetch or burst unit | no: the integrated I219 has no PCIe capability (no Max Read Request Size), and the on-chip fabric (IOSF) parameters are not documented |
| TXDCTL `0141001f` (PTHRESH 31, WTHRESH 1, GRAN, COUNT_DESC) | descriptor prefetch | visible, but it governs descriptor fetches, not packet data |
| IOSFPC (0x0F28, "TX corrupted data") and TARC0 request count | Intel's SPT/KBL fix for Tx corruption and hangs | visible; extending it to CNP did not help (see above) |

Intel does not publish the I219 MAC registers: the PCH and I219 datasheets describe the features
but not their configuration registers
([Intel community, 2025](https://community.intel.com/t/i219-lm-ethernet-controller-documentation/133339)).
The search did not stop there: the related-controller research below found the parameter in
Intel's own errata for the older e1000e MACs (Max Read Request size).

## The same erratum on 82571/82572/82574/82583V (2026-10-20)

Intel documents this bug for the 8257x MACs that e1000e also drives: 82571EB/82572EI erratum 76,
82574 erratum 17 and 82583V erratum 7, all "Tx Data Corruption When Using TCP Segmentation
Offload", with identical text (`evidence/related/82574-erratum-17.txt`, verbatim from the
[82574 specification update, Rev 4.1](https://cdrdv2-public.intel.com/320709/82574-specupdate-rev4-1-external-320709.pdf)):

> When using TSO, a situation can occur where a PCIe MRd request is repeated with the same address,
> resulting in data corruption. At the end of the TCP packet, the Tx DMA hangs because the length
> doesn't match. This can only occur when the following are true: the first buffer of the packet is
> larger than [3 * (max_read_request - 4)]; there is a 4 KB boundary within 64 bytes following the
> end of the header bytes in the buffer.

| | 8257x errata (Intel) | I219-LM (CNP), measured here |
|---|---|---|
| Effect | wrong data with the correct checksum, then a Tx DMA hang | the same |
| First buffer | larger than `3 * (max_read_request - 4)` (372 B at the minimum 128) | linear buffers of about 2,960 B |
| Window | 4 KB boundary within 64 B after the end of the headers | 4 KB boundary 1-16 B after the TCP checksum offset |
| Corrupted data | a repeated read request | shifted back 128 B, or 124 B = 128 - 4 (the "- 4" of Intel's formula) |
| Workaround | headers in a buffer of at most `3 * (max_read_request - 4)`, or no 4 KB boundary in the window | — |

The mainline e1000e implements neither workaround for the 8257x MACs (only erratum 23, the 4-byte
pull), and Intel's text says its own drivers were "very unlikely" to hit it. Forwarded VM traffic is
the unusual case: tun/vhost hands the driver large linear buffers at arbitrary offsets.

**Tests on the I219** (sweep build, window values that hang within seconds without a fix):

| Change (test only) | N=64 | N=51 | N=60 | N=66 |
|---|---|---|---|---|
| none | hang | hang | hang | hang |
| end the first TSO descriptor at the 4 KB boundary | hang | hang | - | no hang |
| **first TSO descriptor at most 372 B (Intel's workaround)** | **no hang** | **no hang** | **no hang** | **no hang** |

- Splitting at the boundary does not help: the stuck packets then had 64 (or 51) header bytes in
  the first descriptor and the rest in a separately mapped page, so a 4 KB crossing inside one
  buffer is not required; what matters is where the header fetch is cut.
- Intel's 372-byte cap prevented every hang and corruption (about 1,000 placed packets per value),
  although the 4 KB boundary stays inside the window: as the erratum says, breaking either
  condition is enough. `evidence/hdrbuf372/`, `evidence/descsplit/`.
- So the controller parameter we looked for is the Max Read Request size. The integrated I219 has
  no PCIe capability to read it from; the corruption shift (128) matches the 128-byte minimum that
  Intel's 372-byte figure assumes.

Related reports and other vendors' fixes for this class of bug (19 findings, with links):
`evidence/related/similar-issues.md`.

## Open questions for Intel

We cannot see inside the NIC, so we propose Intel's own documented workaround, applied to the I219
as well. The data here should let Intel confirm it:

- Is 82574 erratum 17 (82571/82572 #76, 82583V #7) present in the I217/I218/I219 MACs? The I218/I219
  specification update does not list it. Our window (1-16 B after the TCP checksum offset) differs
  from the documented one (64 B after the headers): is the TSO engine different, or is the
  documented window approximate?
- What is the effective max_read_request of the integrated I219, so the cap can be computed rather
  than assumed to be 372 B?
- Does it apply to PCH generations after CNP? Reports with the same symptoms exist on ADP and MTP
  (`evidence/related/similar-issues.md`, findings 5 and 7).
