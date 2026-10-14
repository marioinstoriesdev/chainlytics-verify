"""Public source adapters. Every request goes through the configured HTTP client (verify/net.py);
each raw response is recorded as a receipt and then normalized to the registry's metric names."""
import time


def _num(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


class Sources:
    def __init__(self, client, cfg):
        self.client, self.cfg = client, cfg
        self._sol = (None, 0.0)

    def _get(self, name, mint, rec):
        s = self.cfg["sources"][name]
        timeout = self.cfg["network"]["source_timeout_s"]
        if "query_param" in s:
            url, params = s["url"], {s["query_param"]: mint}
        else:
            url, params = s["url"].format(mint=mint), None
        t = time.time()
        raw = self.client._make_request(url, method="GET", params=params, timeout=timeout)
        lat = round(time.time() - t, 3)
        rec.add("source", name, {"url": url, "params": params}, raw)
        err = raw.get("error") if isinstance(raw, dict) else None
        return raw, lat, err

    def sol_usd(self, rec):
        val, ts = self._sol
        if val and time.time() - ts < self.cfg["network"]["sol_usd_cache_s"]:
            return val
        raw, _, err = self._get("jupiter", self.cfg["solana"]["wsol_mint"], rec)
        row = _pick_jupiter(raw, self.cfg["solana"]["wsol_mint"]) if not err else None
        val = _num((row or {}).get("usdPrice"))
        if val:
            self._sol = (val, time.time())
        return val

    def fetch_all(self, mint, rec):
        out = {}
        for name, s in self.cfg["sources"].items():
            if not s.get("enabled"):
                continue
            raw, lat, err = self._get(name, mint, rec)
            norm = None
            if not err:
                norm = PARSERS[name](raw, mint, self.cfg)
            out[name] = {"latency_s": lat, "error": str(err)[:160] if err else (None if norm else "token not found"),
                         "values": norm or {}}
            time.sleep(self.cfg["network"]["source_sleep_s"])
        return out


def _pick_jupiter(raw, mint):
    rows = raw if isinstance(raw, list) else (raw.get("data") if isinstance(raw, dict) else None)
    for r in rows or []:
        if isinstance(r, dict) and r.get("id") == mint:
            return r
    return None


def parse_jupiter(raw, mint, cfg):
    r = _pick_jupiter(raw, mint)
    if not r:
        return None
    a = r.get("audit") or {}
    return {"supply": _num(r.get("totalSupply")),
            "mint_authority_disabled": a.get("mintAuthorityDisabled"),
            "freeze_authority_disabled": a.get("freezeAuthorityDisabled"),
            "holders": _num(r.get("holderCount")),
            "top10_pct": _num(a.get("topHoldersPercentage")),
            "dev_wallet": r.get("dev"),
            "migrated": bool(r.get("graduatedPool")) if "graduatedPool" in r or r.get("launchpad") else None,
            "price_usd": _num(r.get("usdPrice")),
            "price_slot": r.get("priceBlockId"),
            "dev_holdings_pct": _num(a.get("devBalancePercentage")),
            "reported": {"dev_mints": a.get("devMints"), "dev_migrations": a.get("devMigrations"),
                         "organic_score": r.get("organicScore")}}


def parse_dexscreener(raw, mint, cfg):
    rows = raw if isinstance(raw, list) else None
    if not rows:
        return None
    rows = [x for x in rows if (x.get("baseToken") or {}).get("address") == mint] or rows
    best = max(rows, key=lambda x: _num((x.get("liquidity") or {}).get("usd")) or 0)
    quote = (best.get("quoteToken") or {}).get("address")
    return {"migrated": best.get("dexId") != "pumpfun" if best.get("dexId") else None,
            "price_sol": _num(best.get("priceNative")) if quote == cfg["solana"]["wsol_mint"] else None,
            "price_usd": _num(best.get("priceUsd")),
            "pair": best.get("pairAddress"), "dex": best.get("dexId")}


def parse_rugcheck(raw, mint, cfg):
    if not isinstance(raw, dict) or "mint" not in raw:
        return None
    tk = raw.get("token") or {}
    top = raw.get("topHolders") or []
    dec = tk.get("decimals")
    return {"supply": tk["supply"] / 10 ** dec if tk.get("supply") is not None and dec is not None else None,
            "mint_authority_disabled": raw.get("mintAuthority") is None,
            "freeze_authority_disabled": raw.get("freezeAuthority") is None,
            "holders": _num(raw.get("totalHolders")),
            "top10_pct": sum(_num(h.get("pct")) or 0 for h in top[:10]) if top else None,
            "dev_wallet": raw.get("creator"),
            "price_usd": _num(raw.get("price")),
            "dev_holdings_pct": (100.0 * raw["creatorBalance"] / tk["supply"]) if raw.get("creatorBalance") is not None and tk.get("supply") else None,
            "reported": {"insiders_detected": raw.get("graphInsidersDetected"), "risk_score_normalised": raw.get("score_normalised"),
                         "rugged": raw.get("rugged")}}


PARSERS = {"jupiter": parse_jupiter, "dexscreener": parse_dexscreener, "rugcheck": parse_rugcheck}
