"""Deep-mode tests: launch rebuild, dev holdings, creator-linked buyers and fresh holders, all from fake RPC data."""
import asyncio

import pytest
from solders.keypair import Keypair

from verify import chain, report
from verify.service import Verifier
from verify.store import Store
from test_verify import CFG, cfg, curve_account, mint_account, token_acc, FakeClient

SYS = CFG["solana"]["system_program"]
T = 1_791_000_000
SLOT0 = 400_000_000


def w():
    return str(Keypair().pubkey())


def bal(owner, mint, ui):
    return {"owner": owner, "mint": mint, "uiTokenAmount": {"uiAmount": ui}}


def buy_tx(signer, mint, ui, slot, idx, extra_keys=()):
    keys = [{"pubkey": signer, "signer": True}] + [{"pubkey": k, "signer": False} for k in extra_keys]
    pre = [10 ** 10] + [0] * len(extra_keys)  # extra keys (the mint) are created by this transaction
    return {"slot": slot, "blockTime": T, "transactionIndex": idx,
            "transaction": {"signatures": ["s%d_%d" % (slot, idx)], "message": {"accountKeys": keys, "instructions": []}},
            "meta": {"err": None, "preBalances": pre, "postBalances": [9 * 10 ** 9] * len(keys),
                     "preTokenBalances": [], "postTokenBalances": [bal(signer, mint, ui)]}}


def transfer_tx(src, dsts, bt):
    ins = [{"program": "system", "programId": SYS, "parsed": {"type": "transfer", "info": {"source": src, "destination": d, "lamports": 10 ** 9}}}
           for d in dsts]
    keys = [{"pubkey": src, "signer": True}] + [{"pubkey": d, "signer": False} for d in dsts]
    return {"slot": 1, "blockTime": bt, "transaction": {"message": {"accountKeys": keys, "instructions": ins}},
            "meta": {"err": None, "innerInstructions": [], "preBalances": [10 ** 10] + [0] * len(dsts)}}


class DeepRpc:
    def __init__(self, accounts, token_accounts, histories, txs):
        self.accounts, self.token_accounts, self.histories, self.txs = accounts, token_accounts, histories, txs

    async def _rpc_call(self, method, params):
        if method == "getSlot":
            return {"result": 100}
        if method == "getMultipleAccounts":
            return {"result": {"context": {"slot": 101}, "value": [self.accounts.get(a) for a in params[0]]}}
        if method == "getProgramAccounts":
            return {"result": self.token_accounts}
        if method == "getSignaturesForAddress":
            return {"result": [] if params[1].get("before") else self.histories.get(params[0], [])}
        if method == "getTransaction":
            return {"result": self.txs.get(params[0])}
        return {"error": "unexpected " + method}


def test_deep_rebuilds_launch(tmp_path):
    c = cfg(tmp_path)
    c["rpc"]["signer_lookup"] = True
    mint, signer, b1, b2, w3 = w(), w(), w(), w(), w()
    curve = chain.curve_address(mint, c)
    supply = 10 ** 15  # 1e9 tokens, 6 decimals
    create = buy_tx(signer, mint, 50_000_000, SLOT0, 0, extra_keys=(mint,))
    tx_b1 = buy_tx(b1, mint, 30_000_000, SLOT0, 1)
    tx_b2 = buy_tx(b2, mint, 20_000_000, SLOT0 + 2, 0)
    histories = {
        mint: [{"signature": "tb2", "slot": SLOT0 + 2, "blockTime": T}, {"signature": "tb1", "slot": SLOT0, "blockTime": T},
               {"signature": "tc", "slot": SLOT0, "blockTime": T}],
    }
    txs = {"tc": create, "tb1": tx_b1, "tb2": tx_b2}
    rpc = DeepRpc({mint: mint_account(supply), curve: curve_account(10 ** 15, 30 * 10 ** 9, 6 * 10 ** 14, False, signer)},
                  [token_acc(curve, 9 * 10 ** 14 - 7 * 10 ** 13), token_acc(b1, 3 * 10 ** 13), token_acc(w3, 4 * 10 ** 13),
                   token_acc(b2, 0), token_acc(signer, 0)],
                  histories, txs)
    v = Verifier(c, rpc=rpc, client=FakeClient({}), store=Store(c["paths"]["db"]))
    r = asyncio.run(v.verify(mint, origin="test", deep=True))
    f = r["forensics"]
    assert f["status"] == "ok"
    assert f["dev"]["creation_signer"] == signer and f["dev"]["same_wallet"] is True
    assert f["dev"]["initial_buy_pct"] == pytest.approx(5.0) and f["dev"]["signer_now_pct"] == 0
    assert f["dev"]["signer_sold_pct_of_initial"] == 100.0
    L = f["launch"]
    assert (L["n_bundle"], L["bundle_pct"], L["n_snipers"], L["snipers_pct"]) == (1, 3.0, 1, 2.0)
    assert L["buyers_now_pct"] == pytest.approx(3.0)
    for key in ("creator_linked", "buyer_clusters", "fresh_holders"):
        assert f[key] == {"status": "not_in_public_build"}
    assert f["launch_group_now_pct"] == pytest.approx(3.0)
    assert r["chain"]["dev_holdings_pct"]["creation_signer"] == 0
    assert "Launch" in report.text_summary(r)


def test_born_in_tells_creation_from_ledger_horizon():
    a, b = w(), w()
    t = transfer_tx(a, [b], T)
    assert chain.born_in(t, b) is True and chain.born_in(t, a) is False and chain.born_in(t, w()) is False


def test_factory_flag_roles():
    from verify import factories
    c = {"factories": {"deployers": ["D1", "D2"], "source": "test", "ev_status": "untested"}}
    f = factories.check(c, "D1", "X", signer_funders=["D2", "Z"])
    assert f["flagged"] and f["roles"] == ["creation_signer", "signer_funded_by_listed"] and f["funded_by"] == ["D2"]
    g = factories.check(c, "S", "C")
    assert not g["flagged"] and g["roles"] == []
