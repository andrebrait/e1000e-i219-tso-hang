"""List v3 on top of fix/e1000e-v3.py: comment the spare descriptor that the split uses
(review by David Laight). Comment only; the generated code is unchanged.
Run in drivers/net/ethernet/intel/e1000e."""
c = open("netdev.c").read()
a = ("\t/* reserve a descriptor for the offload context */\n"
     "\tif ((mss) || (skb->ip_summed == CHECKSUM_PARTIAL))\n"
     "\t\tcount++;\n"
     "\tcount++;\n")
assert c.count(a) == 1, "count anchor not found"
c = c.replace(a, a.replace(
    "\tcount++;\n\tcount++;\n",
    "\tcount++;\n"
    "\t/* spare descriptor: e1000_tx_map() splits the linear part of a long\n"
    "\t * TSO packet to keep its headers within tx_hdr_buf_max (TSO erratum)\n"
    "\t */\n"
    "\tcount++;\n"))
open("netdev.c", "w").write(c)
print("v3 comment applied")
