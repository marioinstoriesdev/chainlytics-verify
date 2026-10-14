"""One verification: chain read, public sources, chain read again, token accounts, creation signer,
definitions, verdicts, receipts."""
import asyncio
import time

from . import definitions, factories, forensics, verdict
from .launch import signer0

from .chain import ChainReader
from .receipts import Recorder, merkle_root
from .sources import Sources
from .store import Store


def build_rpc(cfg):
    """Solana JSON-RPC client (verify/net.py). The hosted service runs the same interface on RPC Fast."""
    from .net import JsonRpc
    return JsonRpc(cfg)


def build_client(cfg):
    """HTTP client for the public sources, with no response cache: a cached source answer next to a fresh chain
    read would produce false divergences."""
    from .net import HttpClient
    return HttpClient(cfg)


class Verifier:
    def __init__(self, cfg, rpc=None, client=None, store=None):
        self.cfg = cfg
        self.rpc = rpc or build_rpc(cfg)
        self.sources = Sources(client or build_client(cfg), cfg)
        self.store = store or Store(cfg["paths"]["db"])
        self._lock = asyncio.Lock()

    async def verify(self, mint, origin="api", deep=False):
        async with self._lock:
            return await self._verify(mint, origin, deep)

    async def _verify(self, mint, origin, deep):
        rec = Recorder()
        chain = ChainReader(self.rpc, self.cfg, rec)
        t0 = time.time()
        before = await chain.snapshot(mint)
        sol_usd = await asyncio.to_thread(self.sources.sol_usd, rec)
        src = await asyncio.to_thread(self.sources.fetch_all, mint, rec)
        after = await chain.snapshot(mint)
        m = after.get("mint")
        accounts, signer, sigs, create_tx, errors = None, None, [], None, []
        if m and m["program"] in (self.cfg["solana"]["token_program"], self.cfg["solana"]["token2022_program"]):
            try:
                accounts = await chain.token_accounts(mint, m["program"])
            except RuntimeError as e:
                errors.append(str(e))
        if (self.cfg["rpc"]["signer_lookup"] or deep) and m:
            try:
                sigs, create_tx = await chain.creation(mint)
                signer = signer0(create_tx) if create_tx else None
            except RuntimeError as e:
                errors.append(str(e))
        quote = None
        qm = (after.get("curve") or {}).get("quote_mint")
        if qm:
            quote = {"mint": qm, "decimals": None, "sol_per_unit": None}
            try:
                quote["decimals"] = await chain.mint_decimals(qm)
            except RuntimeError as e:
                errors.append(str(e))
            usd = await asyncio.to_thread(self.sources.usd_price, qm, rec)
            if usd and sol_usd:
                quote["sol_per_unit"] = usd / sol_usd
        chain_values = definitions.compute(before, after, accounts, signer, self.cfg, quote)
        verdicts = verdict.evaluate(chain_values, src, sol_usd, self.cfg)
        forensic = None
        if deep and m and accounts is not None:
            owners = {}
            for o, a in accounts:
                owners[o] = owners.get(o, 0) + a
            try:
                forensic = await forensics.deep(chain, self.cfg, mint, m, owners, (after.get("curve") or {}).get("creator"),
                                                sigs, create_tx)
            except RuntimeError as e:
                forensic = {"status": "error", "note": str(e)[:200]}
        jup = (src.get("jupiter") or {}).get("values") or {}
        fresh = {}
        if jup.get("price_slot") and after.get("slot"):
            fresh["jupiter_price_slot_age"] = after["slot"] - jup["price_slot"]
        result = {
            "mint": mint, "created_ms": int(time.time() * 1000), "elapsed_s": round(time.time() - t0, 2), "deep": bool(deep),
            "found_on_chain": bool(m), "context": chain_values.pop("_context"),
            "slots": {"before": before.get("slot"), "after": after.get("slot")},
            "sol_usd": sol_usd, "quote": quote, "chain": chain_values,
            "sources": {k: {"latency_s": v["latency_s"], "error": v["error"]} for k, v in src.items()},
            "reported": {k: (v.get("values") or {}).get("reported") for k, v in src.items() if (v.get("values") or {}).get("reported")},
            "verdicts": verdicts, "forensics": forensic, "freshness": fresh, "errors": errors,
            "factory": factories.check(self.cfg, signer, (after.get("curve") or {}).get("creator"),
                                       ((forensic or {}).get("creator_linked") or {}).get("signer_funder_addresses") or (),
                                       ((forensic or {}).get("creator_linked") or {}).get("signer_recipient_addresses") or ()),
            "receipts": {"count": len(rec.items), "root": merkle_root([r["sha256"] for r, _ in rec.items])},
        }
        result["check_id"] = self.store.save(result, rec, origin)
        return result
