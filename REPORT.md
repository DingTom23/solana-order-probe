# Inside the Block — measurement report

**Bounty:** Inside the Block: How Solana Validators Order Transactions
(Superteam Ukraine, 2,500 USDC pool)
**Harness:** this repository (`solana-order-probe`)
**Measurement window:** _to be filled after the live run_ (all measurements are
after the October 21, 2026 Alpenglow upgrade)
**Operator:** _to be filled_

## 1. Summary

_To be filled from `data/analysis.json` after the live run._ The summary will
state, per validator and per client version, whether the landed order of our
transactions is explained by priority fee, by send order, by a mixture, or by
neither. Null results are reported as null results.

## 2. Targets

Selected by `select.py`: highest stake per client family, at least six
validators and at least three advertised client versions. The exact node
pubkeys, versions and stakes are in `data/dryrun.json` and repeated in
`data/run.json`.

| # | node pubkey | version | family | stake (SOL) |
|---|---|---|---|---|
| 1 | _from data/run.json_ | | | |

## 3. What was sent

Each burst contains `--burst-size` (default 8) otherwise-identical
self-transfers. The priority-fee ladder is `0, 10k, 100k, 1M, 10M`
micro-lamports per compute unit, shuffled independently of send order for every
burst. Each transaction carries a unique lamport amount and a memo
`ord:<run>:<slot>:<seq>`. Raw rows: `data/sends.jsonl`.

Budget: the run stops before exceeding `--max-lamports`; the bounty cap is
0.05 SOL. Actual spend is in `data/run.json` (`spent_lamports`).

## 4. Results

_To be filled._ Table columns:

| validator | version | scored blocks | landed txs | fee-priority score | arrival score | classification |
|---|---|---:|---:|---:|---:|---|

Scores are means over scored blocks; `fee-priority score = -tau(position, fee)`
and `arrival score = -tau(position, send_seq)`. Positive means the hypothesis
is supported. A block is scored only when at least three of our transactions
landed in it.

## 5. What went wrong

_To be filled after the run._ This section is deliberately not optional. It
will list, at minimum:

- transactions that never landed and the inferred reason (wrong leader,
  QUIC admission, deadline, dropped by RPC),
- slots that were skipped or produced no block,
- any burst where our own transactions serialised on the payer lock,
- any block excluded from scoring and why.

## 6. Limitations

- We measure the **relative order of our own transactions**, not the absolute
  position of every transaction in the block.
- The RPC path is not instrumented; we cannot distinguish "arrived late at the
  leader" from "arrived in time but was not scheduled".
- Jito bundles/tips are out of scope.
- One client version may be represented by a small number of validators; the
  report names them and does not generalise beyond the sample.

## 7. Reproduction

See `README.md`. The short version:

```bash
docker compose run --rm probe python dryrun.py
docker compose run --rm probe python measure.py --max-minutes 0.1   # no sends
docker compose run --rm probe python measure.py --live --max-lamports 50000000
docker compose run --rm probe python analyze.py
```
