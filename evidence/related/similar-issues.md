# Evidence: NICs with the same or a similar TSO/transmit bug as the e1000e I219-LM (CNP) hang

Our bug, for reference: I219-LM 8086:15bb (`e1000_pch_cnp`), TSO packet whose buffer has a 4 KB boundary within 16 bytes after the TCP checksum offset (buffer start N bytes before the boundary, hang for `tucso < N <= tucso+16`), earlier segments carry data from 128 B earlier (124 B if unaligned) with a valid checksum, then "Detected Hardware Unit Hang" every 2 s, queue never recovers. Seen with forwarded VM traffic (linear skbs).

Conventions: sources are linked inline. `[INFERENCE]` marks anything not stated by a source. I fetched the PDFs and code myself (text extracted locally); where I could not open a source, that is said in section "Not found / not accessible".

## TL;DR (read this first)

1. **Intel's own public errata for the sibling TSO engine describe our bug almost word for word.** 82571/82572 erratum 76, 82574 erratum 17 and 82583V erratum 7 (findings 1-3): *"a PCIe MRd request is repeated with the same address, resulting in data corruption. At the end of the TCP packet, the Tx DMA hangs … a TCP packet is transmitted containing the wrong data but with the correct checksum"*, triggered when the first buffer is larger than `3*(max_read_request-4)` **and** there is a **4 KB boundary within 64 bytes after the end of the headers**. Workaround: split the header buffer so it is ≤ 372 B, or keep the 4 KB boundary out of that window. The I218/I219 public spec update (Rev 1.3, 2017) does **not** list this erratum, and Intel's driver comment says it is "very unlikely" with Intel drivers. Forwarded VM traffic (tap/vhost-net builds big linear skbs at arbitrary alignment) is exactly the case Intel considered unlikely.
2. The "repeated read" mechanism also explains the "128 bytes earlier" payload: a duplicate 128-byte MRd (min MaxReadReq) returns the previous chunk. [INFERENCE]
3. **"Queue never recovers" has a second, independent cause in Linux ≥ 6.12**: the generic `netif_tx_stop_queue()` change in `95ecba62e2fd` rewrites `trans_start` on every call, and `e1000_print_hw_hang()` calls it every 2 s, so `dev_watchdog()` (5 s timeout) never fires `ndo_tx_timeout`/`e1000_reset_task` (finding 8). That is why many reports changed from "self-heals" to "needs reboot" around 6.12.
4. Several other vendors fix this class per-skb (fall back to software GSO or disable TSO in `ndo_features_check`) instead of disabling TSO globally: tg3, r8169, macb (findings 12-14). e1000e has `passthru_features_check`, i.e. no such filter.

---

## Findings

### 1. Intel 82571EB/82572EI — erratum 76 "Tx Data Corruption When Using TCP Segmentation Offload"
- **Controller/driver:** 82571/82572, Linux e1000e / Intel Windows drivers.
- **Symptom:** TCP packet transmitted with wrong data but correct checksum; then Tx DMA hangs because the length no longer matches.
- **Trigger:** TSO; first buffer > `3*(max_read_request-4)`; and a 4 KB boundary within 64 bytes following the end of the header bytes in that buffer. Cause per Intel: a PCIe MRd request is repeated with the same address.
- **Workaround:** either header buffer ≤ `3*(MRRS-4)` (372 B at MRRS=128), or no 4 KB boundary within 64 B after the header, e.g. "buffer should not start 54-118 bytes before a 4 KB boundary" for 54-byte headers; 128-byte alignment is suggested. Status: "No planned fix". Intel adds "This problem has not been reported when using an Intel Linux or Windows driver … very unlikely".
- **Links:** Intel spec update (Rev 6.5, Jan 2012): https://www.intel.com/content/dam/doc/specification-update/82571eb-82572ei-gbe-controller-spec-update.pdf (errata list, item 76; same text in Rev 6.8, Nov 2014, mirror: https://theretroweb.com/chip/documentation/intel-ethernet-controller-82571eb-82572ei-82571gb-82571gi-specification-update-rev-6-8-nov-2014-69f0e583d8c11058223657.pdf).
- **Relevance to our bug:** **same (mechanism and symptom), different silicon.** Includes the 4 KB-boundary-after-header trigger, wrong-but-valid-checksum corruption, then Tx DMA hang. Our window (`tucso < N <= tucso+16`, i.e. roughly 50 < N ≤ 66 for Eth+IPv4+TCP) is narrower and starts inside the header, so the I219 TSO engine is at best a descendant with different fetch granularity. [INFERENCE]

### 2. Intel 82574L — erratum 17 (same text) and erratum 23 (TSO + MULR hang)
- **Controller/driver:** 82574, e1000e.
- **Symptom / trigger / workaround:** erratum 17 is word-for-word the same as finding 1. Erratum 23: "Device Transmit Operation Might Halt in TSO Mode when Multiple Requests (MULR) Are Enabled"; workaround "the driver must ensure that the first descriptor points to the (L2+L3+L4) Header and at least two bytes of the data … Linux (e1000e) has had this workaround implemented since the 82574 has been supported". Also Spec Change 3: `TARC[0]` bit 26 must be 1; Spec Change 4: IPv6 TSO needs IPCSO = TUCSO and IXSM.
- **Links:** https://cdrdv2-public.intel.com/320709/82574-specupdate-rev4-1-external-320709.pdf (Rev 4.1, Feb 2020; errata 17 and 23, Spec Changes 3/4).
- **Relevance:** **same** for erratum 17 (see finding 1). Erratum 23 is the origin of the `__pskb_pull_tail(skb, 4)` code in `e1000_xmit_frame()` (comment "TSO Workaround for 82571/2/3 Controllers"), which only guarantees ≥ 4 payload bytes in the header descriptor; it does nothing about a 4 KB boundary.

### 3. Intel 82583V — erratum 7 (same as 82574 #17) and erratum 11 (MULR hang)
- **Links:** https://www.intel.com/content/dam/doc/specification-update/82583v-gbe-controller-spec-update.pdf (Rev 2.9, June 2012; errata 7 and 11, text identical to the 82574).
- **Relevance:** **same** — confirms the erratum is shared by the whole 8257x TSO engine (82571/72/74/83), not a one-off. Together with findings 1-2 this is the strongest "what might we be missing" lead: the I219 MAC is the same lineage (e1000e handles it as ICH/PCH `mac.type`). [INFERENCE: that the I219 TSO engine inherits the 8257x one]

### 4. Intel I218/I219 Specification Update Rev 1.3 (July 2017) and the existing e1000e TSO workarounds (b10effb92e27, f29801030ac6, 67d47b95119a)
- **Controller/driver:** I219-LM/V on 100/200-series PCH (SPT, KBL), e1000e.
- **Symptom:** "unrecovered Tx hang under very stressful UDP traffic and multiple reconnection of Ethernet cable … only recovered if the system is rebooted".
- **Trigger/cause:** erratum 5 "Buffer Overrun While the I219 is Processing DMA Transactions": 100/200-series chipsets reduced DMA round-trip latency. Status: **100/200-series NoFix; 300-series (CNP) Fixed**. Intel workaround: fewer outstanding requests (`TARC0` 3→2 plus `IOSFPC`), costing up to 5-15 %; "Disabling TSO eliminates performance loss for TCP traffic".
- **Fix in kernel:** `b10effb92e27` (2017, TARC0/IOSFPC for SPT/KBL only), `f29801030ac6` (2020, TSO off on `e1000_pch_spt` in watchdog), `67d47b95119a` (2023, moved to probe; Intel-wired-lan thread: https://lists.osuosl.org/pipermail/intel-wired-lan/Week-of-Mon-20230220/032396.html). All three are gated on `mac.type == e1000_pch_spt`; **CNP and later keep TSO enabled** (current `netdev.c` probe code).
- **Links:** spec update mirror https://felloff.net/doc/comp/spec/igbe/i218-i219-ethernet-connection-spec-update.pdf (errata list 1-5; the commit message cites Intel's own URL); https://github.com/torvalds/linux/commit/b10effb92e27 ; https://github.com/torvalds/linux/commit/f29801030ac6 ; https://github.com/torvalds/linux/commit/67d47b95119a ; https://lkml.iu.edu/hypermail/linux/kernel/2005.0/07614.html
- **Relevance:** **different cause, same end symptom.** It is a DMA-latency/outstanding-request erratum triggered by UDP and cable resets, explicitly fixed on 300-series, so it cannot explain a CNP hang. Useful for the upstream report as the precedent Intel already accepted: "TSO engine misbehaves on I219 DMA; disable TSO". No public I217/I218/I219 erratum mentions the TSO 4 KB case (I217 spec update Rev 1.0 and the I219 datasheet were also checked; no TSO errata).

### 5. Kernel bugzilla 118721 "e1000e hardware unit hangs when TSO is on" (2016-2026)
- **Controllers:** 82579LM/V (PCH2), I218-LM (`MAC: 11`, LPT), I219-V (8086:1a1f, ADP), I219-LM (8086:1a1c, ADP), plus I219 (Fedora/Ubuntu, 6.12-6.15). Kernels from 4.x to 7.0.
- **Symptom:** "Detected Hardware Unit Hang" repeating every 2 s; `TDH != TDT`, `next_to_watch.status <0>`.
- **Trigger context:** bridge + VLANs (original report), VLAN routing, bridge with VLANs on I218/82579, Docker and libvirt VMs (comments 16, 19, 21, 22), "TSO off fixes it" in most comments; comment 20 says it still happened with TSO off but GSO on, comment 28 retracts that, saying TSO off alone is sufficient.
- **Workaround:** `ethtool -K <if> tso off [gso off]`.
- **Links:** https://bugzilla.kernel.org/show_bug.cgi?id=118721 (comments readable via https://bugzilla.kernel.org/rest/bug/118721/comment ; the HTML page is behind an Anubis challenge).
- **Relevance:** **same symptom, many generations (PCH2, LPT, SPT, CNP via other threads, ADP)**, so the TSO sensitivity is not specific to SPT/KBL. Forwarding/bridging/VM traffic recurs. No comment identifies a buffer-alignment trigger.

### 6. Kernel bugzilla 219489 (NAT/forwarding router) and the legacy e1000 comment in it
- **Controller:** I219 (HP 840 G6, 6.11.2), DNAT/SNAT forwarding with `ip_forward=1`; a second reporter on legacy `e1000` (PCI device 0000:07:08.0, "Detected Tx Unit Hang", 6.12.12) says "this is also a VPN box that does prerouting and postrouting" and (comment 3) that disabling TSO on that NIC solved it, citing bz 118721 as a pre-existing issue.
- **Links:** https://bugzilla.kernel.org/show_bug.cgi?id=219489 (REST: https://bugzilla.kernel.org/rest/bug/219489/comment).
- **Relevance:** **similar.** Forwarded (non-locally-generated) traffic again. The e1000 comment is interesting because both drivers share the "Tx Unit Hang" detector; the 6.12.x timing matches finding 8. [INFERENCE]

### 7. Launchpad #2121008 (Ubuntu): I219-LM with libvirt VMs, TLS "Tag mismatch" (data corruption) and hangs
- **Controller:** I219-LM (rev 11, "Connection (17)", ADP, `eno1`), HP Elite Mini 800 G9, kernel 5.15.0-152, firmware 2.5-4; second reporter 8086:550a (MTP/ARL-era I219-V rev 20), kernel 7.0.0-28, NIC stays dead after the hang until a full reboot (driver reload does not clear it).
- **Symptom:** the Jenkins agent VMs (virt-manager, `virbr0`/tap) fail with `javax.crypto.AEADBadTagException: Tag mismatch!` and `ClosedChannelException`, i.e. payload corrupted in flight with an intact TCP checksum, together with "Detected Hardware Unit Hang" + "Reset adapter unexpectedly". The second reporter saw tx_packets stuck at 0, tx_dropped climbing.
- **Workaround:** `tso off gso off gro off` (+ `SmartPowerDownEnable=1` for the second reporter).
- **Links:** https://bugs.launchpad.net/ubuntu/+source/linux/+bug/2121008 ; comments: https://www.mail-archive.com/ubuntu-bugs@lists.ubuntu.com/msg6295178.html ; API: https://api.launchpad.net/devel/bugs/2121008/messages
- **Relevance:** **same.** It is the only public report I found that pairs VM-origin traffic, hang, **and** silent payload corruption (TLS tag mismatch). Same symptom as our measured corruption-before-hang. On newer PCH (ADP/MTP) rather than CNP.

### 8. Recovery regression: `netif_tx_stop_queue()` refreshes `trans_start` (95ecba62e2fd, v6.12-rc5) — hang never self-resets
- **Controller/driver:** every driver that calls `netif_stop_queue()` repeatedly while hung; analysed for e1000e on mainline (7.x) and stable 6.1.y/6.6.y/6.12.y per the reporter.
- **Symptom:** hang message every 2 s forever, no "NETDEV WATCHDOG", no "Reset adapter unexpectedly" (8 minutes, 240 messages), host unreachable until power-cut; at 100 Mbit/s it recovered because `tx_timeout_factor` is 10 there.
- **Cause (reporter's analysis; I re-read current mainline and the code matches):** `e1000_print_hw_hang()` calls `netif_stop_queue()`; since `95ecba62e2fd` `netif_tx_stop_queue()` does `WRITE_ONCE(dev_queue->trans_start, jiffies)` every time, and the e1000e watchdog re-forces detection every 2 s (`detect_tx_hung`), while `netdev->watchdog_timeo = 5 * HZ`, so `dev_watchdog()` never sees a 5 s old `trans_start`. Proposed fix: `if (!netif_queue_stopped(netdev)) netif_stop_queue(netdev);` — **not runtime-tested** by the author.
- **Links:** https://bugzilla.kernel.org/show_bug.cgi?id=118721 (comments 17-18, 29-30; REST above); https://github.com/torvalds/linux/commit/95ecba62e2fd ; lore thread cited by the reporter (could not be opened from here, HTTP 403): https://lore.kernel.org/all/ai8hmGQmXQu64Ld3@carbonx1/
- **Relevance:** **different (recovery path), but directly explains "TX queue never recovers"** in our report. Timing matches comment 18: "in 6.12 the NIC is (apparently) no longer able to recover on its own". The Proxmox log (hang at :03/:05/:07 then "NETDEV WATCHDOG … 5251 ms" at :09, kernel 6.8.12-4) shows the pre-regression behaviour. [INFERENCE: that Proxmox kernel predates the change] Even a successful reset may not clear the root cause (finding 7: needs reboot).

### 9. Proxmox / ServeTheHome / OMV forum reports (bridged VMs, VLANs; includes our exact device ID 8086:15bb)
- **Controllers:** I219-LM 8086:15bb on ASUS, `MAC: 13` = `e1000_pch_cnp`, Proxmox kernel 6.8.12-4 (Jan 2025, Proxmox thread page 12); NUC8i3BEH (`MAC: 13`, CNP, VirtIO VMs + LXC on `vmbr0`, 2019); NUC10i7FN I219-V(10) (CMP, kernels 5.13-6.5, 2022-2024); Lenovo M720q I219-V with VLANs + bridges (2022).
- **Symptom/workaround:** "Detected Hardware Unit Hang", bridge goes down, VMs lose network; `ethtool -K <if> tso off gso off` (often `gro off tx off rx off` too, and applied to the bridge too). The 2019 NUC8 thread notes the hang still occurred with `tso off gso off` already applied; reporters there succeeded with `tx off rx off` (checksum offload off, which also removes TSO/SG), then some saw it return after a 10 GB multi-stream upload.
- **Links:** https://forum.proxmox.com/threads/e1000-driver-hang.58284/page-12 ; https://forum.proxmox.com/threads/e1000e-eno1-detected-hardware-unit-hang.59928 ; https://forum.proxmox.com/threads/intel-nic-e1000e-hardware-unit-hang.106001 ; https://forums.servethehome.com/index.php?threads%2Ffix-intel-i219-v-detected-hardware-unit-hang.36700%2F ; gist with the usual oneshot workaround: https://gist.github.com/brunneis/0c27411a8028610117fefbe5fb669d10
- **Relevance:** **same device/PCH generation, same VM + bridge environment.** The "TSO off but still hangs; checksum offload off works" detail suggests the failing condition may involve the checksum-offload context path in addition to TSO, or other offload settings not actually applied (one poster found offload re-enabled after boot). [INFERENCE] Worth testing in the repro: single-segment CHECKSUM_PARTIAL linear skbs with the same 4 KB placement.

### 10. Power-management related corruption/hangs on newer I219 (K1, DPG_EN): `efaaf344bc29`, `3c7bf5af2196`, `578294b8b60d`, `0942fc6d324e`, `1d6500523b8d`
- **Controllers:** I219 on Meteor Lake and later (K1/Kumeran frequency, PLL), Panther Lake (autonomous power gating `DPG_EN`).
- **Symptom:** "sporadic MDI errors … and in rare circumstances packet corruption", packet loss, "Tx/Rx hangs, or packet loss/corruption".
- **Fix:** K1 parameter changes (`efaaf344bc29`, Mar 2025), `disable-k1` private flag + MTL+ default off (`3c7bf5af2196`, Oct 2025), PLL clock-gate reconfiguration with DMI quirk list (`578294b8b60d`), DPG_EN clear (`0942fc6d324e`), another quirked system (`1d6500523b8d`, Oct 2026; this one explicitly has no "Hardware Unit Hang").
- **Links:** https://github.com/torvalds/linux/commit/efaaf344bc29 ; https://github.com/torvalds/linux/commit/3c7bf5af2196 ; https://github.com/torvalds/linux/commit/578294b8b60d ; https://github.com/torvalds/linux/commit/0942fc6d324e ; https://github.com/torvalds/linux/commit/1d6500523b8d
- **Relevance:** **different.** Applies to MTL+/PTL, not CNP. Included because Intel attributes I219 payload corruption to these on newer parts; if reports from ADP/MTP machines (finding 7) are mixed with ours, K1/DPG are the confounders to rule out (`ethtool --set-priv-flags … disable-k1 on` where available). Our deterministic 4 KB-boundary trigger on CNP does not look like this.

### 11. e1000e hangs that are *not* TSO-related (to avoid mixing them in)
- 7f0a3a436e88 "e1000e: fix cyclic resets at link up with active tx" caused hangs right after boot on 5.0.7 on I219-V (TDH 0, TDT 1), fixed by revert: https://bugzilla.kernel.org/show_bug.cgi?id=203175 .
- kdump over NFS on I219-LM (RHEL 9.6 certification, kernel 5.14 and 6.15): hang at TDH 0/TDT 1 even with TSO/GSO/GRO/csum/SG/VLAN offloads off and EEE off; i226 works: https://bugzilla.kernel.org/show_bug.cgi?id=220155 .
- Hang immediately after iPXE `ipxe.efi` handoff on I219 8086:57a0 (PTL); not recoverable even by PCI remove/rescan or offloads off; `snponly.efi` avoids it: https://github.com/ipxe/ipxe/issues/1731 .
- Old 82567LM "Tx Unit Hang" with DMAR faults ("PTE Read access is not set") on 2.6.28; Intel suspected FIFO overrun from large TSO bursts; reporters later tied it to DMAR/`skb_dma_map`; unresolved for some: https://bugzilla.kernel.org/show_bug.cgi?id=12303
- I218-V hangs in an active-backup bond, no resolution in thread: https://community.intel.com/t5/Ethernet-Products/Frequent-Hardware-Unit-Hangs-with-I218-V-Linux-e1000e-Module/m-p/1618398
- **Relevance:** **different.** Same log message, unrelated triggers. The generic message makes forum counts of "the I219 hang" unreliable evidence for any single root cause.

### 12. Broadcom tg3 — TSO + 4 GB DMA boundary corruption, and the software-GSO fallback pattern
- **Controllers/driver:** tg3, 5725/5762 (TSO corrupted within MSS bytes of a 4G boundary); all tg3 devices have a 4 GB boundary DMA bug (`2ffcc981d823`, 2011); 5906 may hang on TSO if CLKREQ is enabled (`c7835a77c864`, 2006).
- **Symptom:** "corrupts TSO packets where the buffer is within MSS bytes of a 4G boundary (4G, 8G etc.)"; data corruption.
- **Fix:** detect the condition in `tg3_start_xmit` and reroute the packet through `tg3_tso_bug()`, which transmits via software GSO (replaces an earlier `skb_copy()` that failed with page allocation errors on 64 KB TSO skbs: `d3f6f3a1d818`).
- **Links:** https://github.com/torvalds/linux/commit/0f0d15100a8a ; https://github.com/torvalds/linux/commit/2ffcc981d823 ; https://github.com/torvalds/linux/commit/d3f6f3a1d818 ; https://github.com/torvalds/linux/commit/c7835a77c864 ; current code `tg3_4g_tso_overflow_test()` / `tg3_tso_bug()`: https://github.com/torvalds/linux/blob/master/drivers/net/ethernet/broadcom/tg3.c
- **Relevance:** **similar (address-boundary-dependent TSO corruption) and a ready template for the fix**: test the skb's buffer address against the boundary rule and fall back to software GSO for that skb only.

### 13. Realtek r8169 — per-chip TSO hw issues, `ndo_features_check` workaround, SG/TSO off by default
- **Controllers:** RTL8168evl (`RTL_GIGA_MAC_VER_34`) and others; r8169 documents "a number of reports that using SG/TSO results in tx timeouts … It's not fully clear which chip versions are affected".
- **Fix:** `rtl8168evl_fix_tso()` in `ndo_features_check` drops TSO for packets with IPv4 options, TCP options (!) or a last fragment ≤ 6 bytes; also drops TSO/csum when `skb_transport_offset > GTTCPHO_MAX/TCPHO_MAX` on csum v2 chips; SG/TSO off by default except for newer versions (`95099c569a9f`, `b8bf38440ba9`).
- **Links:** https://github.com/torvalds/linux/commit/773235f4e1cc ; https://github.com/torvalds/linux/commit/95099c569a9f ; https://github.com/torvalds/linux/blob/master/drivers/net/ethernet/realtek/r8169_main.c (`rtl8168evl_fix_tso`, `rtl8169_features_check`)
- **Relevance:** **similar in pattern** (header/option/last-fragment layout dependent TSO failures handled per skb) and shows the accepted mainline mechanism (`ndo_features_check`). e1000e currently uses `passthru_features_check`.

### 14. Cadence macb/GEM, Cavium ThunderX, IBM ibmvnic — TSO errata worked around per packet
- **macb:** "IP errata causes false amba_error … when length in payload descriptors is specified above 16387" → TX length capped at 0x3FC0 (`f822e9c4ffa5`); `macb_features_check()` drops LSO when payload buffers are not 8-byte multiples (UFO case).
- **thunderx:** 88xx pass 2.x posts a CQE per TSO segment, mishandled for small segments (`7ceb8a1319ec`).
- **ibmvnic:** TSO packets with one segment or MSS < 224 break some backing devices, GSO disabled for them (`f10b09ef687f`).
- **Links:** https://github.com/torvalds/linux/commit/f822e9c4ffa5 ; https://github.com/torvalds/linux/commit/7ceb8a1319ec ; https://github.com/torvalds/linux/commit/f10b09ef687f
- **Relevance:** **different mechanisms**, same engineering response (per-skb software fallback / cap sizes).

### 15. Descriptor-placement rules in other NICs (4 KB and buffer-count limits)
- **Solarflare sfc (Falcon/Siena):** `efx_farch_tx_limit_len()`: "Don't cross 4K boundaries with descriptors"; EF10 splits at page boundaries (`efx_ef10_tx_limit_len`), `EFX_PAGE_SIZE` = 4096 "Alignment of PCIe DMA boundaries (4KB)". Links: https://github.com/torvalds/linux/blob/master/drivers/net/ethernet/sfc/siena/farch.c ; https://github.com/torvalds/linux/blob/master/drivers/net/ethernet/sfc/ef10.c
- **Intel i40e:** "Our HW can't DMA more than 8 buffers to build a packet … For TSO we need to count the TSO header and segment payload separately" → `__i40e_chk_linearize()`. Link: https://github.com/torvalds/linux/blob/master/drivers/net/ethernet/intel/i40e/i40e_txrx.c
- **Relevance:** **similar in principle** (descriptor/DMA-segment layout limits that the driver must split around); sfc is the precedent for splitting at 4 KB.

### 16. Legacy Intel e1000 (8254x/82571 era): TSO sentinel descriptor, linear-skb erratum, errata 10/23
- **Driver:** `e1000_main.c`: "Workaround for premature desc write-backs in TSO mode. Append 4-byte sentinel desc"; "Workaround for Controller erratum -- descriptor for non-TSO packet in a linear SKB that follows a TSO gets written back prematurely before the data is fully DMA'd" (`last_tx_tso`); errata 10 (PCI-X, first descriptor < 2016 B) and errata 23 (descriptor ring and Rx buffers must not cross 64 KB). Origins: https://github.com/torvalds/linux/commit/2701234fc037 ("Delay clean-up of last Tx packet to fix pre-mature writeback issue of Tx descriptors only when TSO is enabled", 2005) and https://github.com/torvalds/linux/commit/9a3056da0d7f ("a non-tso packet in a linear SKB which followed a TSO packet would get written back prematurely", 2006). e1000e dropped the sentinel and `last_tx_tso`.
- **Link:** https://github.com/torvalds/linux/blob/master/drivers/net/ethernet/intel/e1000/e1000_main.c ; FreeBSD `em_txrx.c` keeps a 4-byte TSO sentinel only for pre-82571 MACs (`txr->tx_tso = sc->hw.mac.type < e1000_82571`): https://cgit.freebsd.org/src/plain/sys/dev/e1000/em_txrx.c
- **Relevance:** **similar (TSO + premature descriptor write-back/DMA ordering; "linear SKB" appears in the Intel erratum text itself).** Not the same trigger, but it shows the same engine family is sensitive to linear-skb layout and that Intel accepted descriptor-trick workarounds (extra sentinel descriptor).

### 17. FreeBSD em(4)/igb(4)/igc(4) and other OSes
- **FreeBSD:** after the `iflib` conversion TSO is **off on all em(4)** ("Disable TSO on all em(4) until ring stalls can be debugged", `Reported by: grog`; Aug 2023 MFC; still unconditional in current `if_em.c`) and, separately, on SPT because of the I218/I219 spec-update erratum ("1.5.4.5"). The 2023 commit text also says "i219 SPT devices have an errata that downclocks the DMA engine"; the comment it removed said "MACs like 82579 still can hang at Gigabit even with all publicly documented TSO workarounds". A 2026-07-25 commit fixes link-up publication after FreeBSD's automatic 10/100 TSO masking reset.
- **Links:** https://lists.freebsd.org/archives/dev-commits-src-branches/2023-August/011553.html ; https://cgit.freebsd.org/src/commit/?id=0bd6a167c1561f01c227b1c428a3d8adf0e38833 ; current `if_em.c` (line "Disable TSO on all em(4) until ring stalls are debugged"): https://cgit.freebsd.org/src/plain/sys/dev/e1000/if_em.c
- **Relevance:** **similar (independent confirmation).** A second OS stack with a different transmit path also reports TSO "ring stalls" on em(4) (the disable is unconditional, so it covers I219 on every PCH generation FreeBSD supports, CNP and later included) and turned TSO off. No public root cause. [INFERENCE: the stalls are the same hang]
- **Not found:** OpenBSD em(4) TSO or Windows e1kexpress I219 LSO hang/corruption release notes (see below). igc/i225/i226 hang reports I checked (FreeBSD PR 279245, Intel community) tie to NVM/ASPM, not TSO: https://lists.freebsd.org/archives/freebsd-net/2024-May/004996.html

### 18. PCIe duplicate-completion corruption on 82571/82572 (erratum 68) — alternative root cause for "repeated read data"
- **Symptom:** "TX data or Tx descriptor without offload — 82571/82572 will transmit a packet on the network with invalid data but a valid CRC"; with TSO descriptors the offload machine may hang.
- **Cause:** a completion that should be dropped (corrupted tag, L0s/NAK replay, ACK timer edge case) collides with a new request using the same TAG. Workaround: disable L0s in the upstream switch port; keep EEPROM ACK/NACK scheme bit 13 at 0.
- **Link:** https://www.intel.com/content/dam/doc/specification-update/82571eb-82572ei-gbe-controller-spec-update.pdf (erratum 68, Rev 6.5).
- **Relevance:** **similar symptom, different trigger.** Our corruption is deterministic by address, which fits erratum 76 much better; but it is a reminder that PCIe replay/ASPM-L0s noise can also produce valid-CRC wrong-data packets. [INFERENCE] Rule out via PCIe AER counters (`Bad DLLP`, `Replay`) and by checking whether corruption is reproducible without link-state changes.

### 19. e1000e's own TSO-related history (smaller items)
- `eca90f550494` (2012): 82571 Tx data corruption when `TCTL` was cleared (clearing `MULR` mid-fetch) while a TSO packet was being fetched. https://github.com/torvalds/linux/commit/eca90f550494
- `d821a4c4d11a` (2012): e1000e "DoS while TSO enabled caused by link partner with small MSS": driver-side descriptor count made the queue stick until the stack timeout. https://github.com/torvalds/linux/commit/d821a4c4d11a
- **Relevance:** **different**, but both show Intel previously fixed TSO transmit stalls by changing descriptor generation/queue setup, not by disabling TSO.

---

## Patterns across findings

1. **TSO engine + PCIe read granularity + buffer address = corruption/hang is a documented class.** Intel: 4 KB boundary within 64 B after the header (82571/72/74/83V, findings 1-3). Broadcom: within MSS of a 4 GB boundary (tg3). Solarflare: descriptors must not cross 4 KB (sfc). Cadence/Intel i40e: buffer size/count limits. The recurrent resolution is "split the descriptor or fall back to software GSO for this skb only".
2. **Where the layout is controlled by the host, not the NIC, the bug shows up only for unusual skbs.** Intel's own wording is that Intel Linux/Windows drivers are "very unlikely" to hit it. `tap_alloc_skb()` makes a fully linear skb whenever `prepad + len < PAGE_SIZE` (and `tun_get_user()` computes `linear` from the guest-supplied `hdr_len`), with the head from `alloc_skb_with_frags`/slab at an arbitrary offset; locally generated TCP normally has a small linear header and payload in page frags. This fits "forwarded VM traffic" and the router/bridge/VLAN/VM prevalence in findings 5-9. [INFERENCE from reading `drivers/net/tap.c`/`tun.c` on master; not tested]
3. **Reports are dominated by bridge/VLAN/forwarding/VM setups** (findings 5-9), consistent with linear skbs from tap/virtio or re-segmented GRO packets; plain local TCP rarely triggers.
4. **Same log line, several different bugs** (findings 4, 8, 10, 11): SPT/KBL DMA-latency erratum (fixed on CNP), recovery regression since v6.12, K1/DPG power issues on MTL+/PTL, early-init/UEFI-handoff hangs. Forum "TSO off fixes it" statistics cannot be taken as one root cause.
5. **Linux has a standard hook for per-skb TSO fallback** (`ndo_features_check`: r8169, macb, igb, igc, ixgbe, be2net, stmmac) and a standard fallback in tg3 (`tg3_tso_bug`); e1000e uses `passthru_features_check`.

## What we might be missing / suggested checks before sending

- **Check the 82574 erratum 17 conditions against our data:** (a) first (linear) buffer larger than `3*(MRRS-4)`; with MRRS=128 that is > 372 B. Our linear skbs have header + payload in one descriptor, so likely yes. (b) 4 KB boundary within 64 B after header end; ours is `tucso < N <= tucso+16`. For Eth+IPv4+TCP, `tucso = 50`, so the I219 window is (50, 66]. If the test traffic had TCP timestamps, header end = 66 = `tucso+16`; run with timestamps off (header end 54) and with SACK options to see whether the window tracks `tucso` or the header end. [INFERENCE/suggested experiment]
- **Read the PCIe MaxReadReq of the I219** (`lspci -vv -s 00:1f.6`, `DevCtl: MaxReadReq`). "128 bytes earlier" equals a repeated 128 B read; if MRRS is 128 this fits the erratum text exactly. [INFERENCE]
- **Candidate fix shapes:** (1) in `e1000_tx_map()` split the first descriptor so the header buffer is ≤ 372 B (or at least avoid a 4 KB crossing within 64 B after the headers) for TSO skbs on all `mac.type`s, as the 8257x errata workaround prescribes; (2) `ndo_features_check` that clears TSO for skbs whose linear head violates the rule (tg3/r8169 pattern); (3) at minimum, gate TSO off for the CNP+ range as already done for `e1000_pch_spt` (but note FreeBSD also sees em stalls beyond SPT).
- **Hang recovery:** include finding 8; the "never recovers" part is independently reproducible on 6.12+ and fixable with a one-line `netif_queue_stopped()` guard in `e1000_print_hw_hang()` (author reports it builds, **untested on a real hang**). Check `/sys/class/net/<if>/queues/tx-0/tx_timeout` stays 0 during a hang, as the reporter predicts.
- **Rule out confounders:** K1/PLL (finding 10; N/A on CNP), PCIe ASPM/L0s noise (finding 18), offload settings actually applied on both the NIC and the bridge (Proxmox thread: offloads were re-enabled after boot in one case).
- **TSO off sometimes isn't enough** in a minority of reports (finding 9; bz 118721 comment 20, later retracted in comment 28; bz 220155 kdump case in finding 11). If a single-segment CHECKSUM_PARTIAL packet with the same placement also fails, the trigger is the checksum-offload context path, not TSE alone. [INFERENCE]

## Not found / not accessible

- **lore.kernel.org** (the June 2026 intel-wired-lan thread "e1000e: Report link down after "Detected Hardware Unit Hang" ?") returned HTTP 403; it is cited only through bugzilla comment 30 of bz 118721. Not verified directly.
- **Red Hat KB 4297061** ("log message 'e1000e Detected Hardware Unit Hang'") is behind a login; not read. Red Hat bugzilla 785806 (2012, Fedora 16, `tso off` workaround) was not opened beyond the search snippet.
- **Windows / OpenBSD:** no public report or release note found saying I217/I218/I219 LSO hangs or corrupts data (searched Intel PROSet/driver-pack release notes, Intel community, Microsoft Q&A). Intel's Windows release notes mention an LSO v2 > MTU problem (https://drivers.softpedia.com/get/NETWORK-CARD/INTEL/Intel-PROSet-Network-Adapter-Driver-30-1-for-Server-2025.shtml), which is unrelated. OpenBSD em(4) man page and changelogs: nothing on TSO hangs.
- **Intel spec updates checked for TSO errata:** I218 (2013), I217 (2013), I218/I219 Rev 1.3, 82599 Rev 4.3 and I350 (only a "buffer limited to 64 KB" clarification), 82574, 82571/2, 82583V. I225 spec update Rev 1.2 text extraction was empty, so unverified. 82577/8/9, ICH8/9/10, X540, 82576/82580 spec updates were not located.
- **Windows guests / TCP timestamps correlation:** no source found reporting the hang only with Windows guests or with timestamps off/on; reports mention VirtIO, libvirt, Docker, VirtualBox guests (bz 118721 comment 19), none specify the guest OS or TCP options. The Launchpad "KVM with e1000e and WinGuest" bug (https://bugs.launchpad.net/bugs/1849720) concerns an emulated e1000e guest NIC and was not read in detail.
