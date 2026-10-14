"""Definitions registry: every on-chain definition in use for each metric, computed from one chain read.

A source value is never just right or wrong; it is compared with each definition and the verdict says
which one it follows."""
from .chain import curve_price_sol, is_wallet

REGISTRY = {
    "supply": {
        "label": "Total supply",
        "defs": {"mint_supply": "Supply recorded in the mint account, in whole tokens."},
    },
    "mint_authority_disabled": {
        "label": "Mint authority disabled",
        "defs": {"mint_account": "No account can create new tokens (mint authority is empty)."},
    },
    "freeze_authority_disabled": {
        "label": "Freeze authority disabled",
        "defs": {"mint_account": "No account can freeze holders' tokens (freeze authority is empty)."},
    },
    "holders": {
        "label": "Holder count",
        "defs": {
            "owners_with_balance": "Distinct owners holding more than zero tokens, pools and curve included.",
            "wallets_with_balance": "Distinct ordinary wallets holding more than zero tokens; pools, curve and other program accounts excluded.",
            "token_accounts_incl_empty": "Every token account of the mint still open, including empty ones.",
        },
    },
    "top10_pct": {
        "label": "Top-10 holders share (%)",
        "defs": {
            "incl_pools_and_curve": "Share of supply held by the 10 largest owners, pool and bonding curve included.",
            "wallets_only": "Share of supply held by the 10 largest ordinary wallets; pool, curve and program accounts excluded.",
        },
    },
    "dev_wallet": {
        "label": "Dev wallet",
        "defs": {
            "creation_signer": "The wallet that signed and paid for the token's creation transaction.",
            "curve_creator": "The creator stored by pump.fun in the bonding curve (receives creator fees).",
        },
    },
    "dev_holdings_pct": {
        "label": "Dev holdings (% of supply)",
        "defs": {
            "creation_signer": "Current balance of the wallet that signed the creation transaction.",
            "curve_creator": "Current balance of the creator stored by pump.fun in the bonding curve.",
        },
    },
    "migrated": {
        "label": "Migrated from the bonding curve",
        "defs": {"curve_complete": "The pump.fun bonding curve is marked complete."},
    },
    "price_sol": {
        "label": "Price (SOL per token)",
        "defs": {"curve_spot": "Spot price from the bonding curve reserves, read before and after the source call; "
                               "a curve quoted in another token is converted at that token's Jupiter price."},
    },
}


def holder_stats(accounts, supply, decimals):
    owners = {}
    for owner, amount in accounts:
        owners[owner] = owners.get(owner, 0) + amount
    pos = {o: a for o, a in owners.items() if a > 0}
    ranked = sorted(pos.items(), key=lambda x: -x[1])
    wallets = [(o, a) for o, a in ranked if is_wallet(o)]
    pct = (lambda rows: 100.0 * sum(a for _, a in rows[:10]) / supply) if supply else (lambda rows: None)
    return {
        "holders": {"owners_with_balance": len(pos), "wallets_with_balance": len(wallets),
                    "token_accounts_incl_empty": len(accounts)},
        "top10_pct": {"incl_pools_and_curve": pct(ranked), "wallets_only": pct(wallets)},
        "balances": owners,
    }


def compute(before, after, accounts, signer, cfg, quote=None):
    """Chain values per metric and definition. Missing inputs give None, which later reads as not checked."""
    m = after.get("mint") or {}
    c_after, c_before = after.get("curve"), before.get("curve")
    out = {k: {d: None for d in v["defs"]} for k, v in REGISTRY.items()}
    if m:
        out["supply"]["mint_supply"] = m["supply"] / 10 ** m["decimals"]
        out["mint_authority_disabled"]["mint_account"] = not m["mint_authority_set"]
        out["freeze_authority_disabled"]["mint_account"] = not m["freeze_authority_set"]
    if accounts is not None and m:
        hs = holder_stats(accounts, m["supply"], m["decimals"])
        out["holders"].update(hs["holders"])
        out["top10_pct"].update(hs["top10_pct"])
    out["dev_wallet"]["creation_signer"] = signer
    if accounts is not None and m and m["supply"]:
        owners = {}
        for o, a in accounts:
            owners[o] = owners.get(o, 0) + a
        out["dev_holdings_pct"]["creation_signer"] = 100.0 * owners.get(signer, 0) / m["supply"] if signer else None
        if c_after and c_after.get("creator"):
            out["dev_holdings_pct"]["curve_creator"] = 100.0 * owners.get(c_after["creator"], 0) / m["supply"]
    if c_after:
        out["dev_wallet"]["curve_creator"] = c_after["creator"]
        out["migrated"]["curve_complete"] = c_after["complete"]
        if not c_after["complete"] and c_before and not c_before["complete"]:
            p0, p1 = curve_price_sol(c_before, cfg, quote), curve_price_sol(c_after, cfg, quote)
            if p0 and p1:
                out["price_sol"]["curve_spot"] = (min(p0, p1), max(p0, p1))
    out["_context"] = {"pump_token": c_after is not None, "progress": (c_after or {}).get("progress"),
                       "quote_mint": (c_after or {}).get("quote_mint"),
                       "slot_before": before.get("slot"), "slot_after": after.get("slot")}
    return out
