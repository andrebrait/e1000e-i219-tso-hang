"""Instrument e1000e (v7.0.14): log every TSO packet's layout to the trace buffer. No behaviour change."""
c = open("netdev.c").read()
a = "\tfirst = tx_ring->next_to_use;\n\n\ttso = e1000_tso(tx_ring, skb, protocol);\n"
assert c.count(a) == 1, "xmit_frame anchor not found"
c = c.replace(a, a + (
    "\tif (skb_is_gso(skb))\n"
    "\t\ttrace_printk(\"e1000e-tso idx=%u len=%u headlen=%u frags=%u hdr=%u mss=%u segs=%u "
    "pgoff=%lu hdr_cross=%d head_cross=%d\\n\",\n"
    "\t\t\t     first, skb->len, skb_headlen(skb), skb_shinfo(skb)->nr_frags,\n"
    "\t\t\t     skb_tcp_all_headers(skb), skb_shinfo(skb)->gso_size, skb_shinfo(skb)->gso_segs,\n"
    "\t\t\t     offset_in_page(skb->data),\n"
    "\t\t\t     (int)(offset_in_page(skb->data) + skb_tcp_all_headers(skb) > PAGE_SIZE),\n"
    "\t\t\t     (int)(offset_in_page(skb->data) + skb_headlen(skb) > PAGE_SIZE));\n"))
open("netdev.c", "w").write(c)
print("instrumented")
