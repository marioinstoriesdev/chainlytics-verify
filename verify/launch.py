"""Launch rebuild from the creation transaction and the transactions in the launch window.

Definitions (windows and limits from config.yaml -> forensics.launch)
  buyer of a transaction   its fee payer (first account key); the pool or curve account is never a buyer
  buy                      a successful transaction whose fee payer gained at least min_buy_tokens of the mint
  bundle                   buys in the creation slot whose transaction indices follow the creation one without a gap
                           (a block-engine bundle lands as one contiguous run)
  snipers                  every other buy in the creation slot or the next sniper_slots slots
  tips                     SOL received by the configured tip accounts in the creation and buy transactions
Shares are percentages of assumed_total_supply (whole tokens). Pure functions on already fetched transactions.
"""


def account_keys(tx):
    """Account pubkeys in the order of pre/postBalances: static keys, then loaded writable, then loaded readonly."""
    msg = ((tx or {}).get("transaction") or {}).get("message") or {}
    keys = [k.get("pubkey") if isinstance(k, dict) else k for k in msg.get("accountKeys") or []]
    loaded = ((tx or {}).get("meta") or {}).get("loadedAddresses") or {}
    return keys + list(loaded.get("writable") or []) + list(loaded.get("readonly") or [])


def signer0(tx):
    """Fee payer, the first signer of the transaction."""
    keys = account_keys(tx)
    return keys[0] if keys else None


def _balances(rows, mint):
    out = {}
    for b in rows or []:
        if b.get("mint") == mint and b.get("owner"):
            amt = float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0.0)
            out[b["owner"]] = out.get(b["owner"], 0.0) + amt
    return out


def token_change(tx, mint):
    """Net change of the mint's balance per owner in one transaction."""
    meta = (tx or {}).get("meta") or {}
    pre, post = _balances(meta.get("preTokenBalances"), mint), _balances(meta.get("postTokenBalances"), mint)
    return {o: post.get(o, 0.0) - pre.get(o, 0.0) for o in set(pre) | set(post) if post.get(o, 0.0) != pre.get(o, 0.0)}


def lamport_change(tx, pubkey, lamports_per_sol):
    keys = account_keys(tx)
    meta = (tx or {}).get("meta") or {}
    pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
    if pubkey not in keys:
        return 0.0
    i = keys.index(pubkey)
    return (post[i] - pre[i]) / lamports_per_sol if i < len(pre) and i < len(post) else 0.0


def tips(tx, tip_accounts, lamports_per_sol):
    keys = account_keys(tx)
    meta = (tx or {}).get("meta") or {}
    pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
    out = {}
    for i, k in enumerate(keys):
        if k in tip_accounts and i < len(pre) and i < len(post) and post[i] > pre[i]:
            out[tip_accounts[k]] = out.get(tip_accounts[k], 0.0) + (post[i] - pre[i]) / lamports_per_sol
    return out


def _add(acc, more):
    for k, v in more.items():
        acc[k] = round(acc.get(k, 0.0) + v, 9)


def classify_launch(mint, create_tx, window_txs, lcfg):
    supply = float(lcfg["assumed_total_supply"])
    lps = lcfg["lamports_per_sol"]
    tip_accounts = lcfg.get("tip_accounts") or {}
    slot0 = create_tx.get("slot")
    create_index = create_tx.get("transactionIndex")
    creator = signer0(create_tx)
    creator_tokens = token_change(create_tx, mint).get(creator, 0.0)

    def pct(tokens):
        return round(100.0 * tokens / supply, 4) if supply else 0.0

    paid = {}
    _add(paid, tips(create_tx, tip_accounts, lps))
    same_slot, later = [], []
    for t in sorted(window_txs, key=lambda x: (x.get("slot") or 0, x.get("transactionIndex") or 0)):
        slot = t.get("slot")
        if (t.get("meta") or {}).get("err") is not None or slot is None:
            continue
        if not slot0 <= slot <= slot0 + lcfg["sniper_slots"]:
            continue
        buyer = signer0(t)
        got = token_change(t, mint).get(buyer, 0.0) if buyer and buyer != creator else 0.0
        if got < lcfg["min_buy_tokens"]:
            continue
        _add(paid, tips(t, tip_accounts, lps))
        row = {"wallet": buyer, "tokens": got, "pct": pct(got),
               "sol_spent": round(-lamport_change(t, buyer, lps), 9),
               "position": t.get("transactionIndex"), "slot_offset": slot - slot0}
        (same_slot if slot == slot0 else later).append(row)

    bundle, snipers = [], []
    if create_index is None:
        bundle = same_slot
    else:
        nxt = create_index + 1
        for row in sorted(same_slot, key=lambda r: (r["position"] is None, r["position"] or 0)):
            if not snipers and row["position"] == nxt:
                bundle.append(row)
                nxt += 1
            else:
                snipers.append(row)
    snipers += later
    return {"status": "ok", "mint": mint, "slot0": slot0, "block_time": create_tx.get("blockTime"),
            "creator": creator, "creator_tokens": creator_tokens, "creator_pct": pct(creator_tokens),
            "creator_sol": round(-lamport_change(create_tx, creator, lps), 9) if creator else 0.0,
            "n_bundle": len(bundle), "bundle_wallets_pct": round(sum(r["pct"] for r in bundle), 4),
            "n_snipers": len(snipers), "snipers_pct": round(sum(r["pct"] for r in snipers), 4),
            "tips": paid, "bundle": bundle, "snipers": snipers}
