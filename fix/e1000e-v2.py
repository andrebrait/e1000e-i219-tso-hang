"""e1000e v2 fix: check TSO header placement in e1000_xmit_frame(), where skb->data is final.

Applies to drivers/net/ethernet/intel/e1000e/netdev.c (v7.0.14 and net-queue dev-queue); run in
that directory. Unlike v1 (ndo_features_check), the check runs after software VLAN insertion
(validate_xmit_vlan), the payload pull workaround and skb_cow_head(), so the address checked is the
address the NIC reads. Packets that fail it are segmented here, in software.
"""
c = open("netdev.c").read()


def sub(old, new):
    global c
    assert c.count(old) == 1, f"anchor not found once: {old[:60]!r}"
    c = c.replace(old, new)


sub("#include <linux/netdevice.h>\n", "#include <linux/netdevice.h>\n#include <linux/sizes.h>\n")
sub("#include <net/ip6_checksum.h>\n", "#include <net/ip6_checksum.h>\n#include <net/gso.h>\n")

# One descriptor count for e1000_xmit_frame() and the software segmentation path.
sub(
    "static netdev_tx_t e1000_xmit_frame(struct sk_buff *skb,\n"
    "\t\t\t\t    struct net_device *netdev)\n{\n",
    "/**\n"
    " * e1000_tx_desc_count - descriptors needed to transmit a packet\n"
    " * @adapter: board private structure\n"
    " * @skb: packet to transmit\n"
    " *\n"
    " * Returns: data descriptors for @skb, plus one for the offload context if\n"
    " * it needs one, plus one spare.\n"
    " */\n"
    "static int e1000_tx_desc_count(struct e1000_adapter *adapter,\n"
    "\t\t\t       struct sk_buff *skb)\n"
    "{\n"
    "\tunsigned int nr_frags = skb_shinfo(skb)->nr_frags;\n"
    "\tunsigned int f;\n"
    "\tint count = 1;\n"
    "\n"
    "\tif (skb_is_gso(skb) || skb->ip_summed == CHECKSUM_PARTIAL)\n"
    "\t\tcount++;\n"
    "\n"
    "\tcount += DIV_ROUND_UP(skb_headlen(skb), adapter->tx_fifo_limit);\n"
    "\tfor (f = 0; f < nr_frags; f++)\n"
    "\t\tcount += DIV_ROUND_UP(skb_frag_size(&skb_shinfo(skb)->frags[f]),\n"
    "\t\t\t\t      adapter->tx_fifo_limit);\n"
    "\n"
    "\treturn count;\n"
    "}\n"
    "\n"
    "static netdev_tx_t e1000_xmit_frame(struct sk_buff *skb,\n"
    "\t\t\t\t    struct net_device *netdev);\n"
    "\n"
    "/**\n"
    " * e1000_tso_hdr_crosses_4k - check a TSO packet's header placement\n"
    " * @skb: GSO packet, in the layout the NIC will read\n"
    " *\n"
    " * The transmit DMA can stop for good when a TSO packet's protocol headers\n"
    " * cross a 4 KB boundary in the buffer holding them: the descriptor is never\n"
    " * written back, the driver reports \"Detected Hardware Unit Hang\", and only a\n"
    " * reset brings the queue back. Seen on I219-LM/-V (PCH) with forwarded guest\n"
    " * traffic, which arrives as linear GSO skbs that can start anywhere in a\n"
    " * page.\n"
    " *\n"
    " * Returns: true if the headers cross a 4 KB boundary.\n"
    " */\n"
    "static bool e1000_tso_hdr_crosses_4k(const struct sk_buff *skb)\n"
    "{\n"
    "\treturn (offset_in_page(skb->data) & (SZ_4K - 1)) +\n"
    "\t       skb_tcp_all_headers(skb) > SZ_4K;\n"
    "}\n"
    "\n"
    "/**\n"
    " * e1000_xmit_gso_sw - segment a TSO packet in software and send the segments\n"
    " * @skb: GSO packet that must not be sent with TSO\n"
    " * @netdev: network interface device structure\n"
    " *\n"
    " * Returns: NETDEV_TX_BUSY if the ring has no room for all segments yet,\n"
    " * NETDEV_TX_OK otherwise (sent, or dropped if they can never fit).\n"
    " */\n"
    "static netdev_tx_t e1000_xmit_gso_sw(struct sk_buff *skb,\n"
    "\t\t\t\t     struct net_device *netdev)\n"
    "{\n"
    "\tstruct e1000_adapter *adapter = netdev_priv(netdev);\n"
    "\tstruct e1000_ring *tx_ring = adapter->tx_ring;\n"
    "\tstruct sk_buff *segs, *seg, *next;\n"
    "\tint count = 2;\n"
    "\n"
    "\tsegs = skb_gso_segment(skb, netdev->features & ~NETIF_F_GSO_MASK);\n"
    "\tif (IS_ERR_OR_NULL(segs))\n"
    "\t\tgoto drop;\n"
    "\n"
    "\t/* Reserve room for every segment, so none of them is refused below;\n"
    "\t * e1000_desc_unused() never exceeds the ring size minus one.\n"
    "\t */\n"
    "\tskb_list_walk_safe(segs, seg, next)\n"
    "\t\tcount += e1000_tx_desc_count(adapter, seg);\n"
    "\n"
    "\tif (count >= tx_ring->count) {\n"
    "\t\tkfree_skb_list(segs);\n"
    "\t\tgoto drop;\n"
    "\t}\n"
    "\n"
    "\tif (e1000_maybe_stop_tx(tx_ring, count)) {\n"
    "\t\tkfree_skb_list(segs);\n"
    "\t\treturn NETDEV_TX_BUSY;\n"
    "\t}\n"
    "\n"
    "\tdev_consume_skb_any(skb);\n"
    "\tskb_list_walk_safe(segs, seg, next) {\n"
    "\t\tskb_mark_not_on_list(seg);\n"
    "\t\te1000_xmit_frame(seg, netdev);\n"
    "\t}\n"
    "\n"
    "\treturn NETDEV_TX_OK;\n"
    "\n"
    "drop:\n"
    "\tdev_core_stats_tx_dropped_inc(netdev);\n"
    "\tdev_kfree_skb_any(skb);\n"
    "\treturn NETDEV_TX_OK;\n"
    "}\n"
    "\n"
    "static netdev_tx_t e1000_xmit_frame(struct sk_buff *skb,\n"
    "\t\t\t\t    struct net_device *netdev)\n{\n",
)

# Final layout check, after the pull workaround and with the header unshared.
sub(
    "\t\t\tlen = skb_headlen(skb);\n"
    "\t\t}\n"
    "\t}\n",
    "\t\t\tlen = skb_headlen(skb);\n"
    "\t\t}\n"
    "\n"
    "\t\t/* skb->data is final from here: the stack has inserted any\n"
    "\t\t * VLAN tag, and e1000_tso() unshares the header in place.\n"
    "\t\t */\n"
    "\t\tif (skb_cow_head(skb, 0)) {\n"
    "\t\t\tdev_kfree_skb_any(skb);\n"
    "\t\t\treturn NETDEV_TX_OK;\n"
    "\t\t}\n"
    "\n"
    "\t\tif (e1000_tso_hdr_crosses_4k(skb))\n"
    "\t\t\treturn e1000_xmit_gso_sw(skb, netdev);\n"
    "\t}\n",
)

sub(
    "\t/* reserve a descriptor for the offload context */\n"
    "\tif ((mss) || (skb->ip_summed == CHECKSUM_PARTIAL))\n"
    "\t\tcount++;\n"
    "\tcount++;\n"
    "\n"
    "\tcount += DIV_ROUND_UP(len, adapter->tx_fifo_limit);\n"
    "\n"
    "\tnr_frags = skb_shinfo(skb)->nr_frags;\n"
    "\tfor (f = 0; f < nr_frags; f++)\n"
    "\t\tcount += DIV_ROUND_UP(skb_frag_size(&skb_shinfo(skb)->frags[f]),\n"
    "\t\t\t\t      adapter->tx_fifo_limit);\n",
    "\tcount = e1000_tx_desc_count(adapter, skb);\n"
    "\tnr_frags = skb_shinfo(skb)->nr_frags;\n",
)

sub("\tint tso;\n\tunsigned int f;\n\t__be16 protocol", "\tint tso;\n\t__be16 protocol")

open("netdev.c", "w").write(c)
print("v2 applied")
