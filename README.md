# Solana block-order probe

A reproducible measurement harness for one question:

> When a Solana leader builds a block, what decides which of **our** transactions
> lands earlier and which later — priority fee, arrival order, something else, or
> nothing at all?

This repository is the submission for the Superteam Ukraine bounty
**"Inside the Block: How Solana Validators Order Transactions"**.
It is designed to be run by a judge with one command in dry-run mode and by the
operator with an explicit `--live` flag and a hard lamport budget.

## What is measured

For each target validator we send a burst of otherwise-identical transfer
transactions into its leader slot. The burst varies exactly two things:

1. **Priority fee** (`SetComputeUnitPrice`), from 0 to 10,000,000
   micro-lamports per compute unit.
2. **Send order**, recorded with a monotonic timestamp.

The fee ladder is **shuffled independently of the send order** in every burst.
That makes "ordered by fee" and "ordered by arrival" competing hypotheses
instead of the same signal. Every transaction is a self-transfer of a unique
lamport amount tagged with a memo `ord:<run>:<slot>:<seq>`, so a block can be
matched back to the send log without ambiguity.

A block is scored only if at least three of our transactions landed in it.
For each scored block we compute:

- Kendall tau between block position and priority fee
  (`fee_priority_score = -tau`, positive means higher fee earlier),
- Kendall tau between block position and send sequence
  (`arrival_score = -tau`, positive means earlier send earlier),
- whether the landed sequence is exactly fee-descending or send-ascending.

Per validator we aggregate the scores across blocks and classify the behaviour
as `fee-priority`, `arrival-order`, `mixed`, `no-clear-rule`, or
`insufficient-data`. Null results are reported as null results.

## Targets

The harness selects at least six validators across at least three distinct
advertised client versions. The default selection is stake-weighted but
round-robins across client families so the sample is not all Agave.
The table is produced by `dryrun.py` and stored in `data/dryrun.json`.

At the time of writing the cluster advertises (stake-weighted):

| version | family | stake (SOL) | validators |
|---|---|---:|---:|
| 4.3.2 | agave-4.x | 311,112,812 | 389 |
| 26.10.4 | non-agave-26.x | 53,403,841 | 56 |
| 4.4.0-rc.1 | agave-4.x | 50,612,212 | 171 |
| 26.10.5 | non-agave-26.x | 15,272,941 | 25 |
| 0.1.1 | non-agave-0.x | 371,156 | 3 |

The experiment covers `4.3.2`, `26.10.x` and `0.1.1`. The version string is the
identifier accepted by the bounty; this repository does **not** claim to know
which binary a node runs beyond what it advertises.

## Design: the things that can ruin the measurement

**Leader schedule and leader types.** Slots are taken from
`getLeaderSchedule` for the current epoch and filtered to the target node
pubkeys. A target's slot is only used once we have polled `getSlot` to the start
of that slot. Rare leaders (the `0.1.x` nodes) are selected per validator, not
by taking the first N slots globally, so low-stake clients are not silently
dropped.

**The path from our backend to the executing node.** The harness sends through
one RPC endpoint (`sendTransaction`). The RPC forwards to the leader it believes
is current. This is a real confounder: a transaction can be forwarded to the
wrong leader, dropped by QUIC, or arrive after the leader's deadline. We record
the send timestamp, the RPC endpoint and the target slot, and we only analyse
transactions that actually appear in the target block. The report states the
send-to-land rate per validator rather than hiding it.

**Our own transactions interacting.** All transactions share the same payer,
the same compute limit (200 CU) and the same instruction shape. The only
differences are the fee instruction and the unique transfer amount/memo.
Transactions are sent 12 ms apart inside the first part of the slot, so the
payer's account lock does not serialise them into a single order at the bank
stage.

**Competition inside the block.** Other users' transactions are not under our
control. We therefore compare the **relative order of our own transactions**,
not their absolute position. Block-level statistics (transaction count, vote
count, fee distribution) are stored alongside the result so a reader can see
whether a block was unusually empty or crowded.

**Send order.** Send order is recorded before the RPC call
(`sent_ns`, monotonic) and the sequence number is embedded in the memo. The
fee ladder is shuffled per burst, so a correlation with send order cannot be
explained by the fee ladder.

**Noise versus rule.** A single block is not evidence. Each validator is
targeted for multiple slots (`--slots-per-validator`, default 6). The report
gives the number of scored blocks, the mean scores and the exact-match ratios,
and classifies only when the signal is consistent across blocks. "No rule" is a
valid outcome.

## Safety

- **Dry run is the default.** `measure.py` without `--live` builds, signs and
  verifies transactions but never calls `sendTransaction`.
- **Hard budget.** `--max-lamports` is enforced before every send; the run stops
  when the next transaction would exceed it. The default is 5,000,000 lamports
  (0.005 SOL), and the bounty cap is 0.05 SOL.
- **Keys never in the repo.** The payer key is loaded from `PAYER_KEYPAIR` and
  mounted as a Docker volume. `wallet/` is git-ignored.
- **No mainnet writes without the flag.** `--live` is required for any
  `sendTransaction` call.

## Reproduce

```bash
# 1. Dry run: no keys needed, no transactions sent, no SOL spent.
docker compose run --rm probe python dryrun.py

# 2. Inspect the plan (still no sending).
docker compose run --rm probe python measure.py --max-minutes 0.1

# 3. Live run, only after funding the payer with <= 0.05 SOL.
cp .env.example .env
# edit SOLANA_RPC and PAYER_KEYPAIR, mount the key at ./wallet
docker compose run --rm probe python measure.py --live --max-lamports 50000000

# 4. Score the collected blocks.
docker compose run --rm probe python analyze.py
```

Local (no Docker):

```bash
python -m pip install cryptography
python dryrun.py
python measure.py --max-minutes 0.1
python measure.py --live --max-lamports 50000000
python analyze.py
```

`RPC_PROXY` is optional and only used when the host needs an HTTP proxy to
reach the RPC endpoint.

## Data layout

```
data/
  run.json              run metadata, targets, budget, spent, sent
  dryrun.json           validator table, leader windows, sample block summary
  sends.jsonl           one row per built transaction (dry and live)
  blocks/<slot>.json    raw getBlock response for every measured slot
  analysis.json         per-validator scores and classification
REPORT.md               human-readable report with dates and what went wrong
```

Each `sends.jsonl` row carries `run_id`, `slot`, `validator`, `send_seq`,
`fee_micro_lamports`, `fee_lamports`, `transfer_lamports`, `signature`,
`sent_at`, `sent_ns`, `live` and, for live rows, `sent`/`error`.

## What this harness does not do

- It does not separate QUIC admission from bank-stage ordering. A transaction
  that never reaches the leader cannot tell us anything about the scheduler.
- It does not measure Jito bundles or tips; Jito validators are in scope only
  for their normal scheduler behaviour.
- It does not claim a rule for a validator with fewer than three scored blocks.
- It does not measure before/after Alpenglow; all measurements are after the
  October 21, 2026 upgrade and are reported with their dates.
