"""Deep checks: what trading screens show as labels (dev holdings, bundles, snipers), rebuilt
from public Solana transactions with an open definition anyone can re-run. Descriptive only: none of these numbers
is claimed to predict price.

Definitions (all windows and caps come from config.yaml -> forensics)
  launch window     the creation slot and the next launch.sniper_slots slots
  bundle            buys in the creation slot whose transaction indices follow the creation contiguously
  snipers           every other buy in the launch window
                    (verify/launch.py)
  dev               both on-chain definitions: the creation signer and the creator stored in the pump.fun curve
  ledger horizon    signature histories only reach back to the oldest block the RPC node holds; a launch whose
                    creation transaction is older returns creation_not_found
  launch group      the dev wallets and the launch buyers together; "now" is their current balance as a share of
                    supply

This public build rebuilds the launch only. Wallet-link tracing (buyers tied to the dev or to each other before
launch, fresh holders) runs in the hosted service and is not included here; those fields come back as
{"status": "not_in_public_build"}.
"""
import asyncio

from .launch import classify_launch, signer0


async def _gather(coros, limit):
    sem = asyncio.Semaphore(limit)

    async def run(c):
        async with sem:
            return await c
    return await asyncio.gather(*(run(c) for c in coros), return_exceptions=True)


def _pct_now(owners, wallets, raw_supply):
    ws = {w for w in wallets if w}
    return round(100.0 * sum(owners.get(w, 0) for w in ws) / raw_supply, 4) if raw_supply else None


async def deep(chain, cfg, mint, mint_info, owners, curve_creator, sigs, create_tx):
    f = cfg["forensics"]
    raw_supply = mint_info["supply"]
    if create_tx is None:
        return {"status": "creation_not_found",
                "note": "creation transaction not reachable (longer history than the page budget, or older than the RPC node's ledger); launch not rebuilt"}
    lcfg = dict(f["launch"])
    lcfg["assumed_total_supply"] = raw_supply / 10 ** mint_info["decimals"]
    lcfg["lamports_per_sol"] = cfg["solana"]["lamports_per_sol"]
    slot0 = sigs[-1].get("slot")
    window = [s for s in reversed(sigs[:-1]) if s.get("slot") is not None and not s.get("err")
              and slot0 <= s["slot"] <= slot0 + lcfg["sniper_slots"]]
    truncated = len(window) > f["max_launch_txs"]
    window = window[:f["max_launch_txs"]]
    txs = await _gather([chain.tx(s["signature"]) for s in window], f["concurrency"])
    rec = classify_launch(mint, create_tx, [t for t in txs if isinstance(t, dict)], lcfg)
    signer = rec["creator"] or signer0(create_tx)
    buyers = rec["bundle"] + rec["snipers"]
    buyer_wallets = [b["wallet"] for b in buyers]

    initial = rec["creator_pct"]
    signer_now = _pct_now(owners, [signer], raw_supply)
    dev = {"creation_signer": signer, "curve_creator": curve_creator,
           "same_wallet": signer == curve_creator if curve_creator else None,
           "initial_buy_pct": initial, "creation_sol": rec["creator_sol"],
           "signer_now_pct": signer_now,
           "curve_creator_now_pct": _pct_now(owners, [curve_creator], raw_supply) if curve_creator else None,
           "signer_sold_pct_of_initial": round(100.0 * (1 - signer_now / initial), 1) if initial and signer_now is not None else None}
    launch = {"slot": slot0, "block_time": rec["block_time"], "n_bundle": rec["n_bundle"], "bundle_pct": rec["bundle_wallets_pct"],
              "n_snipers": rec["n_snipers"], "snipers_pct": rec["snipers_pct"], "tips_sol": rec["tips"],
              "buyers_bought_pct": round(rec["bundle_wallets_pct"] + rec["snipers_pct"], 4),
              "buyers_now_pct": _pct_now(owners, buyer_wallets, raw_supply),
              "window_txs_read": len(window), "window_truncated": truncated}

    horizon = await chain.horizon()
    skipped = {"status": "not_in_public_build"}
    group = [signer, curve_creator] + buyer_wallets
    return {"status": "ok", "ledger_horizon_time": horizon, "dev": dev, "launch": launch, "creator_linked": skipped,
            "buyer_clusters": skipped,
            "fresh_holders": skipped,
            "launch_group_now_pct": _pct_now(owners, group, raw_supply), "launch_group_wallets": len({w for w in group if w})}
