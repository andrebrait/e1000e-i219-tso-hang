"""e1000e v3: Intel's TSO erratum workaround (82571/82572 #76, 82574 #17, 82583V #7), also needed on
I219: keep a TSO packet's headers in a first buffer of at most 3 * (max_read_request - 4) bytes.

Run in drivers/net/ethernet/intel/e1000e (v7.0.14 and net-queue dev-queue). e1000_xmit_frame()
already reserves one descriptor more than the linear part needs (the second count++), which covers
the extra descriptor the split can add, so the descriptor count is unchanged.
"""


def sub(text, old, new):
    assert text.count(old) == 1, f"anchor not found once: {old[:60]!r}"
    return text.replace(old, new)


h = open("e1000.h").read()
h = sub(h, "\tu32 tx_fifo_limit;\n", "\tu32 tx_fifo_limit;\n\tu32 tx_hdr_buf_max;\n")
open("e1000.h", "w").write(h)

c = open("netdev.c").read()
c = sub(c,
        "\tadapter->tx_fifo_limit = min_t(u32, ((er32(PBA) >> 16) << 10) - 96,\n"
        "\t\t\t\t       24 << 10);\n",
        "\tadapter->tx_fifo_limit = min_t(u32, ((er32(PBA) >> 16) << 10) - 96,\n"
        "\t\t\t\t       24 << 10);\n"
        "\n"
        "\t/* TSO erratum (82571/82572 #76, 82574 #17, 82583V #7, also seen on\n"
        "\t * I219): when the first buffer of a TSO packet is larger than\n"
        "\t * 3 * (max_read_request - 4) bytes and a 4 KB boundary lies just\n"
        "\t * after its headers, the NIC repeats a read, sends wrong data with a\n"
        "\t * correct checksum and then hangs. Keep the headers in a first buffer\n"
        "\t * no larger than that.\n"
        "\t */\n"
        "\tadapter->tx_hdr_buf_max = 3 * (pcie_get_readrq(adapter->pdev) - 4);\n")
c = sub(c,
        "\t\tbuffer_info = &tx_ring->buffer_info[i];\n"
        "\t\tsize = min(len, max_per_txd);\n"
        "\n"
        "\t\tbuffer_info->length = size;\n"
        "\t\tbuffer_info->time_stamp = jiffies;\n"
        "\t\tbuffer_info->next_to_watch = i;\n"
        "\t\tbuffer_info->dma = dma_map_single(",
        "\t\tbuffer_info = &tx_ring->buffer_info[i];\n"
        "\t\tsize = min(len, max_per_txd);\n"
        "\t\tif (!offset && skb_is_gso(skb))\n"
        "\t\t\tsize = min(size, adapter->tx_hdr_buf_max);\n"
        "\n"
        "\t\tbuffer_info->length = size;\n"
        "\t\tbuffer_info->time_stamp = jiffies;\n"
        "\t\tbuffer_info->next_to_watch = i;\n"
        "\t\tbuffer_info->dma = dma_map_single(")
open("netdev.c", "w").write(c)
print("v3 applied")
