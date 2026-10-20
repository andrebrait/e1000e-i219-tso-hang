"""TEST ONLY: end the first data descriptor of a TSO packet at the first 4 KB boundary of the
linear part, so the NIC gets two buffers instead of one buffer crossing the boundary.

Apply after fix/e1000e-split-sweep.py (run in drivers/net/ethernet/intel/e1000e). Inspired by the
legacy e1000 erratum workaround for linear TSO packets ("premature desc write-backs in TSO mode,
append 4-byte sentinel desc", drivers/net/ethernet/intel/e1000/e1000_main.c). Only the first
descriptor is split, so at most one extra descriptor is used (covered by the spare one that
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
    "\t\t/* TEST ONLY: end the first TSO descriptor at a 4 KB boundary */\n"
    "\t\tif (!offset && skb_is_gso(skb)) {\n"
    "\t\t\tunsigned int left = SZ_4K - (offset_in_page(skb->data) & (SZ_4K - 1));\n"
    "\n"
    "\t\t\tif (size > left)\n"
    "\t\t\t\tsize = left;\n"
    "\t\t}\n\n"))
open("netdev.c", "w").write(c)
print("descriptor split at 4 KB applied (TEST ONLY)")
