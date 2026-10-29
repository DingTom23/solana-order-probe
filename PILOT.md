# Pilot — parser and statistics on live blocks

This is **not** the bounty measurement. The bounty requires evidence derived
from transactions the operator sends. The pilot only checks that the block
parser and the position/fee statistics behave sensibly on live mainnet blocks.

Date: 2026-10-29 (UTC), after the Alpenglow upgrade.
RPC: `https://api.mainnet-beta.solana.com`.
Slots: 462912668, 462912665, 462912662.

| slot | leader (prefix) | txs | vote txs | non-vote with priority fee | fee-priority score | exactly fee-descending |
|---|---|---:|---:|---:|---:|---|
| 462912668 | GEM1N1UE | 1034 | 0 | 437 | +0.34 | no |
| 462912665 | 5pPRHnie | 540 | 0 | 203 | -0.06 | no |
| 462912662 | 5EhGYUyQ | 440 | 0 | 223 | +0.19 | no |

Mean fee-priority score over the three blocks: **+0.158**.

## Observations that shaped the harness

1. **v1 transactions are not expanded by `jsonParsed`.** In these blocks a
   large share of transactions are version 1. Their instructions carry
   `programId` and base58 `data`, but no `program`/`parsed` fields. The first
   version of the parser silently counted zero priority fees. `blockparse.py`
   now decodes `SetComputeUnitPrice` directly from the instruction data for
   both legacy and v1 shapes. This is why the pilot exists.
2. **The public block feed contained no vote transactions** in these samples
   (0 of 1034, 0 of 540, 0 of 440). Either the endpoint filters them or these
   leaders produced blocks without votes. The live report will state which
   feed was used and will not claim vote-first behaviour that the feed does not
   show.
3. **Fee order is not strict even across all transactions.** The whole-block
   correlation is weakly positive and noisy, which is exactly why the live
   experiment uses our own de-correlated bursts instead of whole-block
   correlations.

Raw pilot output: `data/pilot.json`.
