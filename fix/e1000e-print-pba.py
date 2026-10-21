"""TEST ONLY: print the packet buffer allocation and the derived Tx limits at each reset.
Apply after fix/e1000e-v3.py (run in drivers/net/ethernet/intel/e1000e). No behaviour change."""
c = open("netdev.c").read()
a = "\tadapter->tx_hdr_buf_max = 3 * (pcie_get_readrq(adapter->pdev) - 4);\n"
assert c.count(a) == 1, "v3 anchor not found"
c = c.replace(a, a + (
    "\tpr_info(\"e1000e test: PBA %08x (Rx %u KB, Tx %u KB) tx_fifo_limit %u tx_hdr_buf_max %u readrq %d mtu %u\\n\",\n"
    "\t\ter32(PBA), er32(PBA) & 0xffff, er32(PBA) >> 16, adapter->tx_fifo_limit,\n"
    "\t\tadapter->tx_hdr_buf_max, pcie_get_readrq(adapter->pdev), adapter->netdev->mtu);\n"))
open("netdev.c", "w").write(c)
print("PBA print applied (TEST ONLY)")
