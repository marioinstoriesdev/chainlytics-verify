"""Mainnet reads through RPC Fast. Every call is recorded as a receipt."""
import base64
import struct

from solders.pubkey import Pubkey


def curve_address(mint, cfg):
    p = cfg["pump"]
    return str(Pubkey.find_program_address(
        [p["bonding_curve_seed"].encode(), bytes(Pubkey.from_string(mint))],
        Pubkey.from_string(p["program_id"]))[0])


def is_wallet(owner):
    """True for an ordinary keypair address, False for a program-derived address (pool, curve, vault)."""
    return Pubkey.from_string(owner).is_on_curve()


def born_in(tx, address):
    """True when address held zero lamports before this transaction, i.e. the transaction created it. Lets a
    signature history that starts at the RPC node's ledger horizon be told apart from a real first transaction."""
    msg = (((tx or {}).get("transaction") or {}).get("message") or {})
    keys = [k.get("pubkey") if isinstance(k, dict) else k for k in msg.get("accountKeys") or []]
    loaded = ((tx or {}).get("meta") or {}).get("loadedAddresses") or {}
    keys += list(loaded.get("writable") or []) + list(loaded.get("readonly") or [])
    pre = ((tx or {}).get("meta") or {}).get("preBalances") or []
    return address in keys and keys.index(address) < len(pre) and pre[keys.index(address)] == 0


def decode_mint(acc):
    if not acc:
        return None
    raw = base64.b64decode(acc["data"][0])
    if len(raw) < 82:
        return None
    return {"program": acc.get("owner"), "supply": struct.unpack_from("<Q", raw, 36)[0], "decimals": raw[44],
            "mint_authority_set": struct.unpack_from("<I", raw, 0)[0] == 1,
            "freeze_authority_set": struct.unpack_from("<I", raw, 46)[0] == 1}


def decode_curve(acc, cfg):
    """pump.fun bonding curve: 8-byte discriminator, 5 u64 reserves, complete flag, creator pubkey."""
    p = cfg["pump"]
    if not acc or acc.get("owner") != p["program_id"]:
        return None
    raw = base64.b64decode(acc["data"][0])
    off = p["discriminator_bytes"]
    if len(raw) < off + 41:
        return None
    vtr, vsr, rtr, rsr, tts = struct.unpack_from("<5Q", raw, off)
    complete = raw[off + 40] == 1
    co = p["creator_offset"]
    creator = str(Pubkey.from_bytes(raw[co:co + 32])) if len(raw) >= co + 32 else None
    qo = p["quote_mint_offset"]
    quote = str(Pubkey.from_bytes(raw[qo:qo + 32])) if len(raw) >= qo + 32 else None
    if quote == cfg["solana"]["system_program"]:
        quote = None
    init = p["initial_real_token_reserves"]
    # progress is only defined for the standard curve size; other variants report None
    progress = 1.0 if complete else (1 - rtr / init if rtr <= init else None)
    return {"virtual_token_reserves": vtr, "virtual_sol_reserves": vsr, "real_token_reserves": rtr,
            "real_sol_reserves": rsr, "token_total_supply": tts, "complete": complete,
            "progress": progress, "creator": creator, "quote_mint": quote}


def curve_price_sol(curve, cfg, quote=None):
    """Spot price in SOL per whole token from the virtual reserves. A curve quoted in another token holds that
    token in its "SOL" reserve fields; its price is converted with quote = {"decimals", "sol_per_unit"} and is None
    when the quote's decimals or price are unknown."""
    if not curve or not curve["virtual_token_reserves"]:
        return None
    tok = curve["virtual_token_reserves"] / 10 ** cfg["pump"]["token_decimals"]
    if curve.get("quote_mint"):
        if not quote or quote.get("decimals") is None or not quote.get("sol_per_unit"):
            return None
        return curve["virtual_sol_reserves"] / 10 ** quote["decimals"] / tok * quote["sol_per_unit"]
    sol = curve["virtual_sol_reserves"] / cfg["solana"]["lamports_per_sol"]
    return sol / tok


class ChainReader:
    def __init__(self, rpc, cfg, recorder):
        self.rpc, self.cfg, self.rec = rpc, cfg, recorder
        self._horizon = None

    async def horizon(self):
        """Unix time of the oldest block the RPC node still holds, or None when the node does not say. Signature
        histories cannot reach further back than this."""
        if self._horizon is None:
            try:
                first = await self.call("getFirstAvailableBlock", [])
                self._horizon = await self.call("getBlockTime", [first]) or 0
            except RuntimeError:
                self._horizon = 0
        return self._horizon or None

    async def call(self, method, params):
        r = await self.rpc._rpc_call(method, params)
        res = (r or {}).get("result") if isinstance(r, dict) else None
        slot = res.get("context", {}).get("slot") if isinstance(res, dict) and isinstance(res.get("context"), dict) else None
        self.rec.add("rpc", method, params, r, slot=slot)
        if not isinstance(r, dict) or "result" not in r:
            raise RuntimeError("rpc %s failed: %s" % (method, str((r or {}).get("error") if isinstance(r, dict) else r)[:160]))
        return r["result"]

    async def slot(self):
        return await self.call("getSlot", [])

    async def snapshot(self, mint):
        res = await self.call("getMultipleAccounts", [[mint, curve_address(mint, self.cfg)], {"encoding": "base64"}])
        vals = (res or {}).get("value") or [None, None]
        return {"slot": (res.get("context") or {}).get("slot"), "mint": decode_mint(vals[0]),
                "curve": decode_curve(vals[1], self.cfg)}

    async def mint_decimals(self, mint):
        res = await self.call("getMultipleAccounts", [[mint], {"encoding": "base64"}])
        m = decode_mint(((res or {}).get("value") or [None])[0])
        return m["decimals"] if m else None

    async def token_accounts(self, mint, program):
        s = self.cfg["solana"]
        flt = [{"memcmp": {"offset": 0, "bytes": mint}}]
        if program == s["token_program"]:
            flt.append({"dataSize": s["legacy_account_size"]})
        res = await self.call("getProgramAccounts", [program, {"encoding": "base64", "filters": flt,
                                                               "dataSlice": {"offset": 32, "length": 40}}])
        out = []
        for it in res or []:
            raw = base64.b64decode(it["account"]["data"][0])
            out.append((str(Pubkey.from_bytes(raw[:32])), struct.unpack_from("<Q", raw, 32)[0]))
        return out

    async def history(self, address, max_pages, stop_before_time=None, page_limit=None):
        """Signatures newest to oldest and whether the history is complete: the oldest signature was reached, or
        the last page already crossed stop_before_time (a unix time)."""
        lim = page_limit or self.cfg["rpc"]["sig_page_limit"]
        sigs, before = [], None
        for _ in range(max_pages):
            p = {"limit": lim, "commitment": "confirmed"}
            if before:
                p["before"] = before
            res = await self.call("getSignaturesForAddress", [address, p]) or []
            sigs.extend(res)
            if len(res) < lim:
                return sigs, True
            before = res[-1]["signature"]
            if stop_before_time and (res[-1].get("blockTime") or 0) < stop_before_time:
                return sigs, True
        return sigs, False

    async def tx(self, sig):
        return await self.call("getTransaction", [sig, {"encoding": "jsonParsed", "commitment": "confirmed",
                                                       "maxSupportedTransactionVersion": self.cfg["rpc"]["max_supported_tx_version"]}])

    async def creation(self, mint):
        """The mint's signature history and its creation transaction (the oldest one). The transaction is None when
        the history is longer than the page budget or older than the RPC node's ledger horizon."""
        sigs, complete = await self.history(mint, self.cfg["rpc"]["max_sig_pages"])
        if not complete or not sigs:
            return sigs, None
        tx = await self.tx(sigs[-1]["signature"])
        # the oldest signature the node returns is the creation only if the mint account did not exist before it
        return sigs, tx if born_in(tx, mint) else None
