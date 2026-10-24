# Submitting the e1000e fix upstream

| Version on the list | Status |
|---|---|
| v1, "e1000e: segment TSO packets whose headers cross a 4 KB boundary" | sent 2026-10-18: https://lore.kernel.org/netdev/20261018203215.77371-1-andrebrait@gmail.com/ ; superseded |
| v2, "e1000e: work around TSO erratum that corrupts data and hangs Tx" | sent 2026-10-20 23:02 UTC: https://lore.kernel.org/netdev/20261020230211.75665-1-andrebrait@gmail.com/ ; reviewed by David Laight; superseded |
| v3, same subject: v2 plus a comment on the spare descriptor and a changelog paragraph (David's review); generated code identical to v2 | sent 2026-10-24 00:46 UTC: https://lore.kernel.org/netdev/20261024004630.24579-1-andrebrait@gmail.com/ ; branch `e1000e-list-v3` |

An intermediate fix (tag `e1000e-v2-candidate`, "check the final layout in `e1000_xmit_frame()`")
was never sent: it had the same blind spot as v1 and a transmit-stall bug (see `../REPORT.md`).

v2 is the commit on branch `e1000e-v3` of `~/git/kernel/net-queue` (base `dev-queue`,
`0344a46e40a4`). Its changelog is `v3-commit-message.txt`; the notes below the `---` come from
`v3-reviewer-notes.txt` (list v2) and `list-v3-notes.txt` (list v3), attached as git notes. Both link the public evidence pinned at
https://github.com/andrebrait/e1000e-i219-tso-hang/tree/08c29e379bac .

## Where it goes

Intel wired Ethernet fixes go through Intel's queue, not straight to netdev:

| | |
|---|---|
| To | `intel-wired-lan@lists.osuosl.org` (moderated for non-subscribers; subscribe first or expect a delay) |
| Cc | `netdev@vger.kernel.org`, Tony Nguyen `<anthony.l.nguyen@intel.com>`, Przemek Kitszel `<przemyslaw.kitszel@intel.com>`, and Gabriel Goller `<g.goller@proxmox.com>` (reviewed v1) |
| Tree | `git://git.kernel.org/pub/scm/linux/kernel/git/tnguy/net-queue.git` (bug fixes; subject prefix `[PATCH iwl-net]`) |
| Tracking | https://patchwork.ozlabs.org/project/intel-wired-lan/list/ |

## Checks done (2026-10-20)

- Applies to `dev-queue`; all e1000e objects compile there with `W=1` and no warnings (not
  runtime-tested on a `dev-queue` kernel, only on 7.0.14).
- `checkpatch.pl --strict -g HEAD`: only the missing `Signed-off-by` (added by the submitter) and
  the `Fixes:` id, which a shallow clone cannot resolve; `bc7f75fa9788` was verified on
  git.kernel.org.
- Test plan in `../v3-test.sh`, results in `../evidence/v3/results.txt` and `../REPORT.md`
  ("Final fix").
- Classified as a regular bug, not a vulnerability (decision by the submitter, 2026-10-18).

## AI disclosure

Per `Documentation/process/coding-assistants.rst` and `generated-content.rst`: the changelog says
the problem was diagnosed and the fix written with an AI coding assistant, and carries
`Assisted-by: LLM`; the notes describe what the assistant did and what the submitter did. The AI
does not add `Signed-off-by` and does not send the patch: only the submitter can certify the DCO.

## Sending (submitter)

In `~/git/kernel/net-queue` on branch `e1000e-list-v3`:

1. Read the patch and the notes, then certify the DCO: `git commit --amend -s --no-edit`
2. Regenerate: `rm -rf outgoing && git format-patch -v3 -1 -o outgoing`
3. Send (asks for the Gmail app password, stored in the keychain after the first time):
   `git send-email --to=intel-wired-lan@lists.osuosl.org --cc=netdev@vger.kernel.org --cc=anthony.l.nguyen@intel.com --cc=przemyslaw.kitszel@intel.com --cc=g.goller@proxmox.com --cc=david.laight.linux@gmail.com outgoing/*.patch`

Post it as a new thread, not as a reply to v1: `Documentation/process/maintainer-netdev.rst`
("Resending after review") asks for a separate thread with a link to the previous posting, which
the notes carry ("v1: https://lore.kernel.org/..."). Then follow the thread on patchwork and answer
review comments yourself.
