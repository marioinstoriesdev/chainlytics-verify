"""Compare each source value with every chain definition of the same metric.

Statuses: matches (with the definition it follows), diverges, not_reported (the source has no value),
not_checked (the chain side has no value, or the method is not ready for this case)."""


def _close(value, target, rel=None, abs_=None):
    if value is None or target is None:
        return False
    if abs_ is not None:
        return abs(value - target) <= abs_
    if target == 0:
        return value == 0
    return abs(value - target) / abs(target) <= rel


def evaluate(chain, sources, sol_usd, cfg):
    t = cfg["tolerance"]
    out = []
    for name, s in sources.items():
        v = s.get("values") or {}
        for metric, defs in chain.items():
            if metric.startswith("_"):
                continue
            if metric == "price_sol":
                out.append(_price(name, v, defs, sol_usd, t, bool((chain.get("_context") or {}).get("quote_mint"))))
                continue
            sv = v.get(metric)
            row = {"source": name, "metric": metric, "source_value": sv, "chain": defs,
                   "status": None, "definition": None}
            if metric not in v or metric == "reported":
                continue
            if sv is None:
                row["status"] = "not_reported"
            elif all(x is None for x in defs.values()):
                row["status"] = "not_checked"
            else:
                hits = []
                for d, cv in defs.items():
                    if cv is None:
                        continue
                    if ((metric == "supply" and _close(sv, cv, rel=t["supply_rel"]))
                            or (metric == "holders" and _close(sv, cv, rel=t["holders_rel"]))
                            or (metric == "top10_pct" and _close(sv, cv, abs_=t["top10_abs_pp"]))
                            or (metric == "dev_holdings_pct" and _close(sv, cv, abs_=t["dev_share_abs_pp"]))
                            or (metric in ("mint_authority_disabled", "freeze_authority_disabled", "migrated") and bool(sv) == bool(cv))
                            or (metric == "dev_wallet" and sv == cv)):
                        hits.append(d)
                # when several definitions give the same chain value, the verdict names all of them
                row["status"], row["definition"] = ("matches", "+".join(hits)) if hits else ("diverges", None)
            out.append(row)
    return out


def _price(name, v, defs, sol_usd, t, quoted=False):
    rng = defs.get("curve_spot")
    sv = v.get("price_sol")
    if sv is None and v.get("price_usd") is not None and sol_usd:
        sv = v["price_usd"] / sol_usd
    row = {"source": name, "metric": "price_sol", "source_value": sv,
           "chain": {"curve_spot": list(rng) if rng else None}, "status": None, "definition": None}
    if sv is None:
        row["status"] = "not_reported"
    elif not rng:
        row["status"] = "not_checked"
        row["note"] = "price is only judged while the token is on the bonding curve"
    else:
        lo, hi = rng
        rel = t["price_quoted_rel"] if quoted else t["price_curve_rel"]
        ok = lo * (1 - rel) <= sv <= hi * (1 + rel)
        row["status"], row["definition"] = ("matches", "curve_spot") if ok else ("diverges", None)
        row["deviation"] = round(sv / ((lo + hi) / 2) - 1, 4)
    return row
