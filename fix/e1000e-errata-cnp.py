"""Test: apply the SPT/KBL Si errata workaround (IOSFPC RDMTS_HEX, TARC0 2 outstanding requests)
to every PCH from SPT on, instead of SPT only. Run in drivers/net/ethernet/intel/e1000e.
Also logs IOSFPC and TARC0 before and after (test-only print, no behaviour change)."""
c = open("netdev.c").read()


def sub(old, new):
    global c
    assert c.count(old) == 1, f"anchor not found once: {old[:60]!r}"
    c = c.replace(old, new)


sub("\t/* SPT and KBL Si errata workaround to avoid data corruption */\n"
    "\tif (hw->mac.type == e1000_pch_spt) {\n",
    "\tpr_info(\"e1000e test: before IOSFPC %08x TARC0 %08x\\n\", er32(IOSFPC), er32(TARC(0)));\n"
    "\t/* SPT and KBL Si errata workaround to avoid data corruption */\n"
    "\tif (hw->mac.type >= e1000_pch_spt) {\n")
sub("\t\tew32(TARC(0), reg_val);\n\t}\n}\n",
    "\t\tew32(TARC(0), reg_val);\n\t}\n"
    "\tpr_info(\"e1000e test: after IOSFPC %08x TARC0 %08x\\n\", er32(IOSFPC), er32(TARC(0)));\n}\n")
open("netdev.c", "w").write(c)
print("errata workaround extended to >= pch_spt")
