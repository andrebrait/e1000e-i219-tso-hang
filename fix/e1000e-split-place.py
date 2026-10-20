"""TEST ONLY, never for submission: place linear TSO buffers so a 4 KB boundary falls at a chosen
offset, with no other change to the driver under test (for v3 and unpatched trees).

Apply after the fix under test (run in drivers/net/ethernet/intel/e1000e). Module parameters:
  split_before (rw): N in 1..255 moves the linear part of linear TSO packets back inside their
                     headroom so the buffer starts N bytes before a 4 KB boundary. 0 disables it.
  split_count (ro):  packets placed so far.
Unlike e1000e-split-sweep.py, unplaced packets are not diverted: the driver under test handles them.
"""
c = open("netdev.c").read()


def sub(old, new):
    global c
    assert c.count(old) == 1, f"anchor not found once: {old[:60]!r}"
    c = c.replace(old, new)


sub("static netdev_tx_t e1000_xmit_frame(struct sk_buff *skb,\n",
    "static int split_before;\n"
    "module_param(split_before, int, 0644);\n"
    "static unsigned long split_count;\n"
    "module_param(split_count, ulong, 0444);\n"
    "\n"
    "/* TEST ONLY: start the linear part split_before bytes before a 4 KB boundary. */\n"
    "static void e1000_split_place(struct sk_buff *skb)\n"
    "{\n"
    "\tunsigned int off = offset_in_page(skb->data) & (SZ_4K - 1);\n"
    "\tint n = READ_ONCE(split_before);\n"
    "\tunsigned int d;\n"
    "\n"
    "\tif (n <= 0 || n >= 256 || skb_headlen(skb) <= n || skb_shinfo(skb)->nr_frags)\n"
    "\t\treturn;\n"
    "\tif (skb_cow_head(skb, 0))\n"
    "\t\treturn;\n"
    "\n"
    "\td = (off - (SZ_4K - n)) & (SZ_4K - 1);\n"
    "\tif (d > skb_headroom(skb))\n"
    "\t\treturn;\n"
    "\n"
    "\tif (d) {\n"
    "\t\tmemmove(skb->data - d, skb->data, skb_headlen(skb));\n"
    "\t\tskb->data -= d;\n"
    "\t\tskb_set_tail_pointer(skb, skb_headlen(skb));\n"
    "\t\tskb_headers_offset_update(skb, -(int)d);\n"
    "\t}\n"
    "\tsplit_count++;\n"
    "}\n"
    "\n"
    "static netdev_tx_t e1000_xmit_frame(struct sk_buff *skb,\n")
sub("\t\t\tlen = skb_headlen(skb);\n"
    "\t\t}\n"
    "\t}\n",
    "\t\t\tlen = skb_headlen(skb);\n"
    "\t\t}\n"
    "\t\te1000_split_place(skb);\n"
    "\t}\n")
# Report the v3 cap once per reset, if the tree has it (test builds only).
cap = "\tadapter->tx_hdr_buf_max = 3 * (pcie_get_readrq(adapter->pdev) - 4);\n"
if c.count(cap) == 1:
    sub(cap, cap + "\tpr_info(\"e1000e test: readrq %d tx_hdr_buf_max %u\\n\",\n"
                   "\t\tpcie_get_readrq(adapter->pdev), adapter->tx_hdr_buf_max);\n")
if "#include <linux/sizes.h>" not in c:
    sub("#include <linux/netdevice.h>\n", "#include <linux/netdevice.h>\n#include <linux/sizes.h>\n")
open("netdev.c", "w").write(c)
print("split placement applied (TEST ONLY)")
