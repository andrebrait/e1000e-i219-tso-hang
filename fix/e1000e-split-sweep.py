"""TEST ONLY, never for submission: place linear TSO buffers so a 4 KB boundary falls at a chosen
offset, and keep every other packet away from the suspected window.

Apply on top of fix/e1000e-v2.py (run in drivers/net/ethernet/intel/e1000e). Adds two module
parameters:
  split_before (rw): N in 1..255 moves the linear part of linear TSO packets back inside their
                     headroom so that the buffer starts N bytes before a 4 KB boundary (N header
                     bytes before it when N < header length); such packets go to the NIC with TSO.
                     0 (default) disables it.
  split_count (ro):  packets placed so far.
Every other TSO packet whose buffer crosses 4 KB within its first 256 bytes (or whose headers
cross, as in v2) is segmented in software, so any hang comes from a placed packet. Only linear
packets (nr_frags == 0) are placed, like every packet that hung.
"""
c = open("netdev.c").read()


def sub(old, new):
    global c
    assert c.count(old) == 1, f"anchor not found once: {old[:60]!r}"
    c = c.replace(old, new)


sub(
    "static bool e1000_tso_hdr_crosses_4k(const struct sk_buff *skb)\n",
    "static int split_before;\n"
    "module_param(split_before, int, 0644);\n"
    "static unsigned long split_count;\n"
    "module_param(split_count, ulong, 0444);\n"
    "\n"
    "/* TEST ONLY: start the linear part split_before bytes before a 4 KB boundary. */\n"
    "static bool e1000_split_test(struct sk_buff *skb)\n"
    "{\n"
    "\tunsigned int off = offset_in_page(skb->data) & (SZ_4K - 1);\n"
    "\tint n = READ_ONCE(split_before);\n"
    "\tunsigned int d;\n"
    "\n"
    "\tif (n <= 0 || n >= 256 || skb_headlen(skb) <= n || skb_shinfo(skb)->nr_frags)\n"
    "\t\treturn false;\n"
    "\n"
    "\td = (off - (SZ_4K - n)) & (SZ_4K - 1);\n"
    "\tif (d > skb_headroom(skb))\n"
    "\t\treturn false;\n"
    "\n"
    "\tif (d) {\n"
    "\t\tmemmove(skb->data - d, skb->data, skb_headlen(skb));\n"
    "\t\tskb->data -= d;\n"
    "\t\tskb_set_tail_pointer(skb, skb_headlen(skb));\n"
    "\t\tskb_headers_offset_update(skb, -(int)d);\n"
    "\t}\n"
    "\tsplit_count++;\n"
    "\treturn true;\n"
    "}\n"
    "\n"
    "/* TEST ONLY: the linear part crosses 4 KB within its first 256 bytes. */\n"
    "static bool e1000_split_zone(const struct sk_buff *skb)\n"
    "{\n"
    "\tunsigned int left = SZ_4K - (offset_in_page(skb->data) & (SZ_4K - 1));\n"
    "\n"
    "\treturn left < 256 && skb_headlen(skb) > left;\n"
    "}\n"
    "\n"
    "static bool e1000_tso_hdr_crosses_4k(const struct sk_buff *skb)\n",
)

sub(
    "\t\tif (e1000_tso_hdr_crosses_4k(skb))\n"
    "\t\t\treturn e1000_xmit_gso_sw(skb, netdev);\n",
    "\t\tif (!e1000_split_test(skb) &&\n"
    "\t\t    (e1000_tso_hdr_crosses_4k(skb) || e1000_split_zone(skb)))\n"
    "\t\t\treturn e1000_xmit_gso_sw(skb, netdev);\n",
)

# v2 bug found by this sweep: e1000_xmit_gso_sw() returns NETDEV_TX_BUSY (or drops) without writing
# the tail, so descriptors queued earlier under xmit_more are never handed to the NIC and the queue
# stalls until the transmit timeout. Flush the tail on both paths.
FLUSH = (
    "\t\tif (adapter->flags2 & FLAG2_PCIM2PCI_ARBITER_WA)\n"
    "\t\t\te1000e_update_tdt_wa(tx_ring, tx_ring->next_to_use);\n"
    "\t\telse\n"
    "\t\t\twritel(tx_ring->next_to_use, tx_ring->tail);\n"
)
sub(
    "\tif (e1000_maybe_stop_tx(tx_ring, count)) {\n"
    "\t\tkfree_skb_list(segs);\n"
    "\t\treturn NETDEV_TX_BUSY;\n",
    "\tif (e1000_maybe_stop_tx(tx_ring, count)) {\n"
    "\t\tkfree_skb_list(segs);\n" + FLUSH +
    "\t\treturn NETDEV_TX_BUSY;\n",
)
sub(
    "drop:\n"
    "\tdev_core_stats_tx_dropped_inc(netdev);\n",
    "drop:\n"
    "\tdev_core_stats_tx_dropped_inc(netdev);\n" + FLUSH.replace("\t\t", "\t"),
)

open("netdev.c", "w").write(c)
print("split sweep applied (TEST ONLY)")
