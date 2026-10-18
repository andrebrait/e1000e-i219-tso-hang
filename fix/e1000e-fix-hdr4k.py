"""e1000e: do not hand the NIC a TSO packet whose protocol headers cross a 4 KB boundary.

On I219 (PCH), the transmit DMA can stop for good on such a packet (header split over two 4 KB
reads); the driver then reports "Detected Hardware Unit Hang". Clearing the GSO features for just
those packets makes the stack segment them in software, so the NIC never sees them as TSO.
"""
c = open("netdev.c").read()

func = '''
/**
 * e1000_features_check - keep TSO packets with 4 KB-crossing headers away from the NIC
 * @skb: packet about to be transmitted
 * @netdev: network interface device structure
 * @features: offload features usable for this packet
 *
 * The transmit engine can stop for good on a TSO packet whose protocol headers
 * cross a 4 KB boundary in the linear buffer ("Detected Hardware Unit Hang").
 * Such packets come from forwarded guest traffic (small linear GSO skbs); the
 * stack segments the few affected ones in software.
 */
static netdev_features_t e1000_features_check(struct sk_buff *skb,
					      struct net_device *netdev,
					      netdev_features_t features)
{
	if (skb_is_gso(skb) &&
	    (offset_in_page(skb->data) & (SZ_4K - 1)) + skb_tcp_all_headers(skb) > SZ_4K) {
		trace_printk("e1000e-tso-diverted len=%u headlen=%u hdr=%u pgoff=%lu\\n",
			     skb->len, skb_headlen(skb), skb_tcp_all_headers(skb),
			     offset_in_page(skb->data));
		features &= ~NETIF_F_GSO_MASK;
	}
	return features;
}
'''

anchor = "static const struct net_device_ops e1000e_netdev_ops = {"
assert c.count(anchor) == 1, "netdev_ops not found"
c = c.replace(anchor, func.lstrip("\n") + "\n" + anchor)
a = "\t.ndo_features_check\t= passthru_features_check,\n"
assert c.count(a) == 1, "features_check line not found"
c = c.replace(a, "\t.ndo_features_check\t= e1000_features_check,\n")
if "#include <linux/sizes.h>" not in c:
    c = c.replace("#include <linux/netdevice.h>\n", "#include <linux/netdevice.h>\n#include <linux/sizes.h>\n", 1)
open("netdev.c", "w").write(c)
print("fix applied")
