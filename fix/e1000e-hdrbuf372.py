"""TEST ONLY: Intel's workaround for 82571/82572 erratum 76, 82574 erratum 17 and 82583V erratum 7
("Tx Data Corruption When Using TCP Segmentation Offload"): the buffer containing the headers must
not be larger than 3 * (max_read_request - 4), i.e. 372 bytes for the minimum 128-byte
max_read_request. Here: cap the first data descriptor of a TSO packet at 372 bytes.

Apply after fix/e1000e-split-sweep.py (run in drivers/net/ethernet/intel/e1000e). Only the first
descriptor is shortened, so at most one extra descriptor is used (covered by the spare one that
e1000_tx_desc_count() reserves).
"""
c = open("netdev.c").read()
a = ("\t\tbuffer_info = &tx_ring->buffer_info[i];\n"
     "\t\tsize = min(len, max_per_txd);\n"
     "\n"
     "\t\tbuffer_info->length = size;\n"
     "\t\tbuffer_info->time_stamp = jiffies;\n"
     "\t\tbuffer_info->next_to_watch = i;\n"
     "\t\tbuffer_info->dma = dma_map_single(")
assert c.count(a) == 1, "linear map anchor not found"
c = c.replace(a, a.replace(
    "\t\tsize = min(len, max_per_txd);\n\n",
    "\t\tsize = min(len, max_per_txd);\n"
    "\t\t/* TEST ONLY: header buffer <= 3 * (128 - 4) bytes (82574 erratum 17) */\n"
    "\t\tif (!offset && skb_is_gso(skb) && size > 372)\n"
    "\t\t\tsize = 372;\n\n"))
open("netdev.c", "w").write(c)
print("header buffer capped at 372 bytes (TEST ONLY)")
