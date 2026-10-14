"""Chainlytics Verify tests. No network: RPC and HTTP client are fakes."""
import asyncio
import base64
import copy
import struct

import pytest
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from verify import chain, definitions, receipts, report, sampler, settings, verdict
from verify.service import Verifier
from verify.store import Store

CFG = settings.load()
PUMP = CFG["pump"]["program_id"]
T22 = CFG["solana"]["token2022_program"]
WSOL = CFG["solana"]["wsol_mint"]


def cfg(tmp_path):
    c = copy.deepcopy(CFG)
    c["paths"]["db"] = str(tmp_path / "v.db")
    c["paths"]["reports_dir"] = str(tmp_path / "reports")
    c["network"]["source_sleep_s"] = 0
    return c


def wallet():
    return str(Keypair().pubkey())


def pda(tag):
    return str(Pubkey.find_program_address([tag.encode()], Pubkey.from_string(PUMP))[0])


def mint_account(supply, decimals=6, mint_auth=False, freeze=False, owner=T22):
    raw = bytearray(82)
    struct.pack_into("<I", raw, 0, 1 if mint_auth else 0)
    struct.pack_into("<Q", raw, 36, supply)
    raw[44] = decimals
    struct.pack_into("<I", raw, 46, 1 if freeze else 0)
    return {"owner": owner, "data": [base64.b64encode(bytes(raw)).decode(), "base64"]}


def curve_account(vtr, vsr, rtr, complete, creator):
    raw = b"\x00" * 8 + struct.pack("<5Q", vtr, vsr, rtr, 0, 10 ** 15) + (b"\x01" if complete else b"\x00") + bytes(Pubkey.from_string(creator))
    return {"owner": PUMP, "data": [base64.b64encode(raw).decode(), "base64"]}


def token_acc(owner, amount):
    return {"account": {"data": [base64.b64encode(bytes(Pubkey.from_string(owner)) + struct.pack("<Q", amount)).decode(), "base64"]}}


def test_decode_curve_reads_creator_and_progress():
    c = CFG
    cr = wallet()
    d = chain.decode_curve(curve_account(10 ** 15, 30 * 10 ** 9, c["pump"]["initial_real_token_reserves"] // 2, False, cr), c)
    assert d["creator"] == cr and not d["complete"] and abs(d["progress"] - 0.5) < 1e-9
    assert chain.curve_price_sol(d, c) == pytest.approx(30 / 10 ** 9)
    assert chain.decode_curve({"owner": "Other", "data": ["", "base64"]}, c) is None


def test_holder_definitions_differ_as_expected():
    w = [wallet() for _ in range(3)]
    pool = pda("pool")
    accs = [(pool, 800), (w[0], 100), (w[1], 50), (w[1], 50), (w[2], 0)]
    hs = definitions.holder_stats(accs, 1000, 0)
    assert hs["holders"] == {"owners_with_balance": 3, "wallets_with_balance": 2, "token_accounts_incl_empty": 5}
    assert hs["top10_pct"]["incl_pools_and_curve"] == pytest.approx(100.0)
    assert hs["top10_pct"]["wallets_only"] == pytest.approx(20.0)


def chain_values(signer, creator, price_range=None, holders=None, top10=None):
    out = {k: {d: None for d in v["defs"]} for k, v in definitions.REGISTRY.items()}
    out["supply"]["mint_supply"] = 1000.0
    out["mint_authority_disabled"]["mint_account"] = True
    out["freeze_authority_disabled"]["mint_account"] = True
    out["holders"].update(holders or {"owners_with_balance": 100, "wallets_with_balance": 95, "token_accounts_incl_empty": 700})
    out["top10_pct"].update(top10 or {"incl_pools_and_curve": 90.0, "wallets_only": 12.0})
    out["dev_wallet"].update({"creation_signer": signer, "curve_creator": creator})
    out["migrated"]["curve_complete"] = price_range is None
    out["price_sol"]["curve_spot"] = price_range
    return out


def test_verdicts_name_the_definition_each_source_follows():
    s, c = wallet(), wallet()
    ch = chain_values(s, c, price_range=(1.0e-8, 1.1e-8))
    src = {"jupiter": {"values": {"supply": 1000.0, "holders": 102, "top10_pct": 12.5, "dev_wallet": s, "migrated": False,
                                  "mint_authority_disabled": True, "freeze_authority_disabled": True, "price_usd": 1.05e-8 * 100}},
           "rugcheck": {"values": {"supply": 1000.0, "holders": 690, "top10_pct": 90.0, "dev_wallet": c, "price_usd": 2e-8 * 100,
                                   "mint_authority_disabled": True, "freeze_authority_disabled": False}}}
    v = {(r["source"], r["metric"]): r for r in verdict.evaluate(ch, src, 100.0, CFG)}
    assert v[("jupiter", "holders")]["definition"] == "owners_with_balance"
    assert v[("rugcheck", "holders")]["definition"] == "token_accounts_incl_empty"
    assert v[("jupiter", "top10_pct")]["definition"] == "wallets_only"
    assert v[("rugcheck", "top10_pct")]["definition"] == "incl_pools_and_curve"
    assert v[("jupiter", "dev_wallet")]["definition"] == "creation_signer"
    same = verdict.evaluate(chain_values(s, s), {"jupiter": {"values": {"dev_wallet": s}}}, 100.0, CFG)
    assert [x["definition"] for x in same if x["metric"] == "dev_wallet"] == ["creation_signer+curve_creator"]
    assert v[("rugcheck", "dev_wallet")]["definition"] == "curve_creator"
    assert v[("jupiter", "price_sol")]["status"] == "matches"
    assert v[("rugcheck", "price_sol")]["status"] == "diverges"
    assert v[("rugcheck", "freeze_authority_disabled")]["status"] == "diverges"


def test_price_after_migration_is_not_checked():
    ch = chain_values(wallet(), wallet(), price_range=None)
    r = [x for x in verdict.evaluate(ch, {"dexscreener": {"values": {"price_sol": 1e-7, "migrated": True}}}, 100.0, CFG)
         if x["metric"] == "price_sol"][0]
    assert r["status"] == "not_checked"


def test_merkle_root_is_order_independent():
    h = [receipts.digest({"a": i}) for i in range(5)]
    assert receipts.merkle_root(h) == receipts.merkle_root(list(reversed(h)))
    assert receipts.merkle_root(h[:1]) == h[0]
    assert receipts.merkle_root([]) is None


def test_sampler_extracts_mint_from_create_instruction():
    m = wallet()
    tx = {"transaction": {"message": {"instructions": [{"programId": PUMP, "accounts": [m, wallet()]}], "accountKeys": []}}}
    assert sampler.mint_from_tx(tx, CFG) == m
    payer = wallet()
    tx2 = {"transaction": {"message": {"instructions": [], "accountKeys": [{"pubkey": payer, "signer": True}, {"pubkey": m, "signer": True}]}}}
    assert sampler.mint_from_tx(tx2, CFG) == m


def test_wilson_lower_bound():
    assert report.wilson_lower(0, 0, 1.96) is None
    lb = report.wilson_lower(17, 17, 1.96)
    assert 0.8 < lb < 1.0


class FakeRpc:
    def __init__(self, accounts, token_accounts, sigs, tx):
        self.accounts, self.token_accounts, self.sigs, self.tx = accounts, token_accounts, sigs, tx

    async def _rpc_call(self, method, params):
        if method == "getSlot":
            return {"result": 100}
        if method == "getMultipleAccounts":
            return {"result": {"context": {"slot": 101}, "value": [self.accounts.get(a) for a in params[0]]}}
        if method == "getProgramAccounts":
            return {"result": self.token_accounts}
        if method == "getSignaturesForAddress":
            return {"result": self.sigs if not params[1].get("before") else []}
        if method == "getTransaction":
            return {"result": self.tx}
        return {"error": "unexpected " + method}


class FakeClient:
    def __init__(self, routes):
        self.routes = routes
        self.cache_ttl = 300

    def _make_request(self, url, method="GET", params=None, json_data=None, timeout=None):
        key = (url, tuple(sorted((params or {}).items())))
        return self.routes.get(key, {"error": "not found", "status_code": 404})


def test_end_to_end_with_fakes(tmp_path):
    c = cfg(tmp_path)
    mint, creator, signer, w1 = wallet(), wallet(), wallet(), wallet()
    curve_pda = chain.curve_address(mint, c)
    supply = 10 ** 15
    vtr, vsr = 10 ** 15, 30 * 10 ** 9
    rpc = FakeRpc({mint: mint_account(supply), curve_pda: curve_account(vtr, vsr, 7 * 10 ** 14, False, creator)},
                  [token_acc(curve_pda, 9 * 10 ** 14), token_acc(w1, 10 ** 14)],
                  [{"signature": "sig1"}],
                  {"transaction": {"message": {"accountKeys": [{"pubkey": signer, "signer": True}, {"pubkey": mint, "signer": True}]}},
                   "meta": {"preBalances": [10 ** 10, 0]}})
    price = (vsr / 1e9) / (vtr / 1e6)
    S = c["sources"]
    routes = {
        (S["jupiter"]["url"], (("query", WSOL),)): [{"id": WSOL, "usdPrice": 100.0}],
        (S["jupiter"]["url"], (("query", mint),)): [{"id": mint, "totalSupply": 1e9, "holderCount": 2, "dev": signer,
                                                     "launchpad": "pump.fun", "usdPrice": price * 100,
                                                     "audit": {"mintAuthorityDisabled": True, "freezeAuthorityDisabled": True,
                                                               "topHoldersPercentage": 10.0}}],
        (S["dexscreener"]["url"].format(mint=mint), ()): [{"baseToken": {"address": mint}, "quoteToken": {"address": WSOL},
                                                          "dexId": "pumpfun", "priceNative": str(price), "liquidity": {"usd": 1}}],
        (S["rugcheck"]["url"].format(mint=mint), ()): {"mint": mint, "token": {"supply": supply, "decimals": 6}, "creator": creator,
                                                      "mintAuthority": None, "freezeAuthority": None, "totalHolders": 2,
                                                      "topHolders": [{"pct": 90.0}, {"pct": 10.0}], "price": price * 100},
    }
    v = Verifier(c, rpc=rpc, client=FakeClient(routes), store=Store(c["paths"]["db"]))
    r = asyncio.run(v.verify(mint, origin="test"))
    got = {(x["source"], x["metric"]): (x["status"], x["definition"]) for x in r["verdicts"]}
    assert got[("jupiter", "dev_wallet")] == ("matches", "creation_signer")
    assert got[("rugcheck", "dev_wallet")] == ("matches", "curve_creator")
    assert got[("jupiter", "top10_pct")] == ("matches", "wallets_only")
    assert got[("rugcheck", "top10_pct")] == ("matches", "incl_pools_and_curve")
    assert got[("dexscreener", "price_sol")] == ("matches", "curve_spot")
    assert got[("dexscreener", "migrated")] == ("matches", "curve_complete")
    assert r["receipts"]["count"] >= 8 and r["check_id"] == 1
    st = v.store
    assert st.raw(1, 0) is not None and len(st.receipts(1)) == r["receipts"]["count"]
    assert "matches the chain" in report.text_summary(r)
    assert "<table>" in report.html_page(r)


def test_api_health_and_validation(tmp_path):
    from fastapi.testclient import TestClient
    from verify.api import create_app

    class V:
        store = Store(str(tmp_path / "a.db"))

        async def verify(self, mint, origin="api", deep=False):
            return {"mint": mint, "verdicts": []}

    c = cfg(tmp_path)
    app = create_app(c, verifier=V())
    tc = TestClient(app)
    assert tc.get("/health").json()["ok"] is True
    assert tc.get("/verify/notamint").status_code == 400
    assert tc.get("/verify/" + wallet()).json()["verdicts"] == []
    assert "holders" in tc.get("/definitions").json()


def test_local_config_merges_over_tracked_config(tmp_path, monkeypatch):
    base = tmp_path / "config.yaml"
    base.write_text("telegram:\n  enabled: false\n  bot_token: \"\"\n  poll_timeout_s: 30\n")
    (tmp_path / "config.local.yaml").write_text("telegram:\n  enabled: true\n  bot_token: secret\n")
    monkeypatch.delenv(settings.LOCAL_ENV, raising=False)
    c = settings.load(str(base))
    assert c["telegram"] == {"enabled": True, "bot_token": "secret", "poll_timeout_s": 30}


def test_token_quoted_curve_price():
    c = {"virtual_token_reserves": 1073000000000000, "virtual_sol_reserves": 4292000000, "quote_mint": "Q"}
    assert chain.curve_price_sol(c, CFG) is None
    p = chain.curve_price_sol(c, CFG, {"decimals": 6, "sol_per_unit": 0.01})
    assert abs(p - 4292 / 1.073e9 * 0.01) < 1e-15
    s = dict(c, quote_mint=None, virtual_sol_reserves=30000000000)
    assert abs(chain.curve_price_sol(s, CFG) - 30 / 1.073e9) < 1e-15
