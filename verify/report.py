"""Plain-language output for traders (bot text, token page) and the index summary."""
import html
import math

from .definitions import REGISTRY

STATUS_TEXT = {"matches": "matches the chain", "diverges": "differs from the chain",
               "not_reported": "not reported", "not_checked": "not checked"}


def _fmt(v):
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return "%.6g" % v
    if isinstance(v, str) and len(v) > 12:
        return v[:4] + "..." + v[-4:]
    return str(v)


def _chain_text(metric, chain):
    vals = chain.get(metric) or {}
    parts = []
    for d, v in vals.items():
        if v is None:
            continue
        shown = "%s to %s" % (_fmt(v[0]), _fmt(v[1])) if isinstance(v, (list, tuple)) else _fmt(v)
        parts.append("%s: %s" % (d.replace("_", " "), shown))
    return "; ".join(parts) or "no chain value"


def text_summary(result, page_url=""):
    lines = ["Chainlytics Verify  %s" % result["mint"]]
    ctx = result.get("context") or {}
    if not result.get("found_on_chain"):
        return "\n".join(lines + ["This mint was not found on Solana mainnet."])
    if ctx.get("pump_token"):
        prog = ctx.get("progress")
        lines.append("pump.fun token, bonding curve %s" % ("complete (migrated)" if prog == 1.0 else
                                                          "progress unknown" if prog is None else "%.0f%% filled" % (100 * prog)))
    by_metric = {}
    for v in result["verdicts"]:
        by_metric.setdefault(v["metric"], []).append(v)
    for metric, rows in by_metric.items():
        label = REGISTRY.get(metric, {}).get("label", metric)
        lines.append("")
        lines.append("%s  (chain: %s)" % (label, _chain_text(metric, result["chain"])))
        for v in rows:
            s = STATUS_TEXT.get(v["status"], v["status"])
            if v["status"] == "matches" and len(REGISTRY.get(metric, {}).get("defs", {})) > 1:
                s += " as '%s'" % v["definition"].replace("_", " ").replace("+", "' = '")
            lines.append("  %s: %s  [%s]" % (v["source"], _fmt(v["source_value"]) if v["source_value"] is not None else "-", s))
    fac = result.get("factory") or {}
    if fac.get("flagged"):
        lines.append("")
        lines.append("Factory flag: %s (list of %d high-volume pump.fun deployers; source: %s; %s)." % (
            ", ".join(r.replace("_", " ") for r in fac["roles"]), fac.get("list_size", 0), fac.get("source") or "-",
            fac.get("ev_status") or "expected value not measured"))
    lines.extend(forensics_lines(result))
    lines.append("")
    lines.append("Receipts: %d calls, root %s" % (result["receipts"]["count"], (result["receipts"]["root"] or "")[:16]))
    if page_url:
        lines.append("%s/t/%s" % (page_url.rstrip("/"), result["mint"]))
    return "\n".join(lines)


def _p(x):
    return "-" if x is None else ("%.2f%%" % x)


def forensics_lines(result):
    """Plain-language deep-check block: what was rebuilt from public transactions, and what sources only report."""
    f = result.get("forensics")
    out = []
    if not f:
        return out
    out.append("")
    if f.get("status") != "ok":
        out.append("Launch rebuild: not available (%s)" % (f.get("note") or f.get("status")))
        return out
    d, L, k, fr = f["dev"], f["launch"], f["creator_linked"], f["fresh_holders"]
    out.append("Launch, rebuilt from public transactions:")
    out.append("  Dev bought %s at creation; holds %s now%s." % (
        _p(d["initial_buy_pct"]), _p(d["signer_now_pct"]),
        " (sold %s%% of it)" % d["signer_sold_pct_of_initial"] if d.get("signer_sold_pct_of_initial") is not None else ""))
    if d.get("same_wallet") is False:
        out.append("  The creator stored by pump.fun is a different wallet; it holds %s." % _p(d["curve_creator_now_pct"]))
    out.append("  Bundle: %d wallets bought %s in the creation slot. Snipers: %d wallets bought %s in the next slots." % (
        L["n_bundle"], _p(L["bundle_pct"]), L["n_snipers"], _p(L["snipers_pct"])))
    out.append("  Those launch buyers still hold %s." % _p(L["buyers_now_pct"]))
    if k.get("status") == "ok":
        out.append("  Creator-linked buyers (money moved between them and the dev in the %dh before launch): %d direct, %d via one hop; they bought %s and hold %s now%s." % (
            k.get("lookback_s", 0) // 3600, k["n_direct"], k["n_two_hop"], _p(k["linked_bought_pct"]), _p(k["linked_now_pct"]),
            "" if k["history_complete"] else " (dev history partly read: lower bound)")
            + (" Window cut at the RPC node's oldest block." if k.get("lookback_cut_by_ledger") else ""))
        if k.get("n_funded_the_dev"):
            out.append("  %d of them sent SOL to the dev wallet before launch." % k["n_funded_the_dev"])
    bc = f.get("buyer_clusters") or {}
    if bc.get("status") == "ok" and bc["n_clusters"]:
        out.append("  Buyer clusters (%s): %d groups, %d wallets, largest %d; they bought %s and hold %s now." % (
            ", ".join(r.replace("_", " ") for r in bc["reasons"]), bc["n_clusters"], bc["n_wallets"], bc["largest"],
            _p(bc["bought_pct"]), _p(bc["now_pct"])))
    if fr.get("status") == "ok":
        out.append("  Fresh wallets among the top %d holders: %d, holding %s." % (fr["top_checked"], fr["n_fresh"], _p(fr["fresh_now_pct"])))
    out.append("  Launch group (dev and launch buyers) holds %s of supply now." % _p(f["launch_group_now_pct"]))
    rep = result.get("reported") or {}
    rc, jp = rep.get("rugcheck") or {}, rep.get("jupiter") or {}
    shown = []
    if rc.get("insiders_detected") is not None:
        shown.append("RugCheck flags %s insiders" % rc["insiders_detected"])
    if rc.get("risk_score_normalised") is not None:
        shown.append("RugCheck risk score %s" % rc["risk_score_normalised"])
    if jp.get("dev_mints") is not None:
        shown.append("Jupiter says the dev created %s tokens" % jp["dev_mints"])
    if shown:
        out.append("  Reported by sources, method not public: " + "; ".join(shown) + ".")
    return out


def _first(d):
    for x in (d or {}).values():
        if x is not None:
            return x
    return None


def _pc(x):
    return "%.1f%%" % x if x is not None else "-"


def creator_panel(result, p):
    """'Before you buy' panel: a red-flag checklist of facts about this launch, and the historical outcome for an
    outside buyer from the pre-registered study. The flags are descriptive; only the listed-deployer difference
    was tested."""
    e = html.escape
    th = p.get("thresholds") or {}
    f = result.get("forensics") or {}
    rebuilt = f.get("status") == "ok"
    dev, la, li = f.get("dev") or {}, f.get("launch") or {}, f.get("creator_linked") or {}
    fac = result.get("factory") or {}
    jp = (result.get("reported") or {}).get("jupiter") or {}
    ch = result.get("chain") or {}
    items = []

    n = fac.get("list_size")
    if n:
        roles = fac.get("roles") or []
        if not fac.get("flagged"):
            items.append(("ok", "Listed top deployer", "No. The creator is not on our list of %d high-volume deployers." % n))
        else:
            why = []
            if "creation_signer" in roles or "curve_creator" in roles:
                why.append("the creator wallet is on our list of %d high-volume deployers" % n)
            if "signer_funded_by_listed" in roles:
                why.append("the creator wallet was funded by a listed deployer")
            if "signer_sent_to_listed" in roles:
                why.append("the creator wallet sent SOL to a listed deployer")
            items.append(("flag", "Listed top deployer", "Yes: " + "; ".join(why) + "."))

    dm = jp.get("dev_mints")
    try:
        dm = int(dm)
    except (TypeError, ValueError):
        dm = None
    if dm is None:
        items.append(("unknown", "Dev launch history", "Not reported for this dev."))
    else:
        items.append(("flag" if dm >= th["dev_mints"] else "ok", "Dev launch history",
                      "Jupiter says this dev created %s tokens." % format(dm, ",")))

    if not rebuilt:
        note = "Launch not rebuilt for this token."
        for lbl in ("Dev selling", "Snipers", "Bundle at creation"):
            items.append(("unknown", lbl, note))
    else:
        ib, sold = dev.get("initial_buy_pct"), dev.get("signer_sold_pct_of_initial")
        if ib is None:
            items.append(("unknown", "Dev selling", "Dev buy not read."))
        elif ib <= 0:
            items.append(("ok", "Dev selling", "The dev did not buy at creation."))
        else:
            bad = sold is not None and sold >= th["dev_sold_pct_of_initial"]
            txt = "The dev bought %s at creation" % _pc(ib)
            txt += " and has sold %s of it." % _pc(sold) if sold is not None else "."
            items.append(("flag" if bad else "ok", "Dev selling", txt))
        ns, sp = la.get("n_snipers") or 0, la.get("snipers_pct") or 0
        items.append(("flag" if sp >= th["snipers_pct"] else "ok", "Snipers",
                      "%d wallets bought %s in the slots right after creation." % (ns, _pc(sp)) if ns else
                      "No wallet bought in the slots right after creation."))
        nb, bp = la.get("n_bundle") or 0, la.get("bundle_pct") or 0
        items.append(("flag" if nb >= th["bundle_wallets"] else "ok", "Bundle at creation",
                      "%d wallets bought %s in the creation slot." % (nb, _pc(bp)) if nb else
                      "No wallet bought in the creation slot."))

    if li.get("status") == "ok":
        nl = (li.get("n_direct") or 0) + (li.get("n_two_hop") or 0)
        nf = li.get("n_funded_the_dev") or 0
        lower = "" if li.get("history_complete") else "At least "
        if nl or nf:
            k = max(nl, nf)
            txt = "%s%d %s moved money with the dev before launch and bought %s." % (
                lower, k, "buyer" if k == 1 else "buyers", _pc(li.get("linked_bought_pct")))
            if nf:
                txt += (" That buyer sent SOL to the dev." if k == 1 and nf == 1 else
                        " %d of them sent SOL to the dev." % nf)
            items.append(("flag", "Dev-linked buyers", txt))
        else:
            items.append(("ok", "Dev-linked buyers", "No buyer had moved money with the dev before launch" +
                          ("." if not lower else ", in the history we could read.")))
    else:
        items.append(("unknown", "Dev-linked buyers", "Not traced for this token."))

    ma, fa = _first(ch.get("mint_authority_disabled")), _first(ch.get("freeze_authority_disabled"))
    if ma is None and fa is None:
        items.append(("unknown", "Mint and freeze authority", "Not read."))
    else:
        open_ = [x for x, v in (("mint", ma), ("freeze", fa)) if v is False]
        items.append(("flag" if open_ else "ok", "Mint and freeze authority",
                      ("Still enabled: " + " and ".join(open_) + ".") if open_ else
                      "Disabled. Nobody can mint new tokens or freeze holders."))

    checked = [s for s, _, _ in items if s != "unknown"]
    nflag = sum(1 for s in checked if s == "flag")
    b = p.get("bands") or {}
    band = "low" if nflag <= b["low_max"] else ("mid" if nflag <= b["mid_max"] else "high")
    words = {"low": "few red flags", "mid": "some red flags", "high": "many red flags"}[band]

    h = p.get("history") or {}
    g = h.get("groups") or {}
    this = "listed" if fac.get("flagged") else "random"
    top = max([(x.get("win_rate") or 0) for x in g.values()] + [0]) or 1
    hrows = []
    for key in ("random", "listed"):
        x = g.get(key)
        if not x:
            continue
        wr, mean = x["win_rate"], x["mean"]
        hrows.append(
            "<div class='cv-row%s'><div class='cv-rl'>%s%s</div><div class='cv-meter'><i style='width:%d%%'></i></div>"
            "<div class='cv-rv'><b>About %d%% of entries</b> ended in profit (about 1 in %d). Average result %+.1f%% per trade.</div></div>" % (
                " cv-this" if key == this else "", e(x["label"]), " <em>closest to this token</em>" if key == this else "",
                round(100 * wr / top), round(100 * wr), round(1 / wr), 100 * mean))
    fine = ("Same fixed entry and exit rule for every trade, set before looking at the data, costs included; %s. "
            "History, not a prediction for this token. Only the listed-deployer difference was tested; the other "
            "flags are facts about this launch." % h.get("sample_note", ""))
    flags = "".join("<li class='cv-f cv-%s'><span class='cv-dot'></span><div><b>%s</b><span>%s</span></div></li>" % (
        s, e(lbl), e(d)) for s, lbl, d in items)
    return ("<section class='cv-panel'><div class='cv-head'><div><div class='cv-kicker'>Before you buy</div>"
            "<div class='cv-score cv-%s'>%d of %d red flags <small>%s</small></div></div>"
            "<p class='cv-sub'>Facts read from this launch's public transactions, and what happened to outside buyers "
            "in our sample.</p></div><div class='cv-grid'><ul class='cv-flags'>%s</ul><div class='cv-hist'>"
            "<div class='cv-kicker'>What happened to outside buyers</div>%s<p class='cv-fine'>%s</p></div></div></section>") % (
        band, nflag, len(checked), e(words), flags, "".join(hrows), e(fine))


def html_page(result, defs_help=True, panel=None):
    e = html.escape
    pan = creator_panel(result, panel) if (panel or {}).get("enabled") else ""
    rows = []
    for v in result.get("verdicts", []):
        label = REGISTRY.get(v["metric"], {}).get("label", v["metric"])
        rows.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td class='%s'>%s%s</td></tr>" % (
            e(label), e(v["source"]), e(_fmt(v["source_value"])) if v["source_value"] is not None else "-",
            e(_chain_text(v["metric"], result["chain"])), e(v["status"]), e(STATUS_TEXT.get(v["status"], v["status"])),
            (" (" + e(v["definition"].replace("_", " ")) + ")") if v.get("definition") else ""))
    helps = []
    if defs_help:
        for m, spec in REGISTRY.items():
            for d, txt in spec["defs"].items():
                helps.append("<li><b>%s, %s</b>: %s</li>" % (e(spec["label"]), e(d.replace("_", " ")), e(txt)))
    return ("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Chainlytics Verify</title><style>body{font-family:system-ui,sans-serif;max-width:960px;margin:24px auto;padding:0 16px}"
            "table{border-collapse:collapse;width:100%%}td,th{border-bottom:1px solid #ddd;padding:6px;text-align:left;font-size:14px}"
            ".matches{color:#11743a}.diverges{color:#b3261e}.not_reported,.not_checked{color:#666}code{font-size:12px}</style></head><body>"
            "<h1>Chainlytics Verify</h1><p><code>%s</code></p><p>Check %s, slots %s to %s, %d receipts, root <code>%s</code></p>"
            "%s<table><tr><th>Metric</th><th>Source</th><th>Source value</th><th>Chain</th><th>Verdict</th></tr>%s</table>"
            "%s<h2>Definitions</h2><ul>%s</ul></body></html>") % (
        e(result["mint"]), e(str(result.get("check_id"))), e(str(result["slots"]["before"])), e(str(result["slots"]["after"])),
        result["receipts"]["count"], e(result["receipts"]["root"] or ""), pan, "".join(rows),
        (("<p class='diverges'><b>Factory flag:</b> %s (source: %s; %s)</p>" % (
            e(", ".join(r.replace("_", " ") for r in result["factory"]["roles"])), e(result["factory"].get("source") or "-"),
            e(result["factory"].get("ev_status") or "expected value not measured")))
         if (result.get("factory") or {}).get("flagged") and not pan else "")
        + (("<h2>Launch, rebuilt from public transactions</h2><pre>%s</pre>" % e("\n".join(forensics_lines(result)).strip())) if result.get("forensics") else ""),
        "".join(helps))


def wilson_lower(k, n, z):
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - m) / d


def aggregate(rows, z):
    out = {}
    for source, metric, status, definition in rows:
        a = out.setdefault((source, metric), {"matches": 0, "diverges": 0, "not_reported": 0, "not_checked": 0, "by_definition": {}})
        a[status] = a.get(status, 0) + 1
        if status == "matches" and definition:
            a["by_definition"][definition] = a["by_definition"].get(definition, 0) + 1
    table = []
    for (source, metric), a in sorted(out.items()):
        n = a["matches"] + a["diverges"]
        table.append({"source": source, "metric": metric, "compared": n, "matches": a["matches"], "diverges": a["diverges"],
                      "not_reported": a["not_reported"], "not_checked": a["not_checked"],
                      "agreement": round(a["matches"] / n, 3) if n else None,
                      "agreement_lower_95": round(wilson_lower(a["matches"], n, z), 3) if n else None,
                      "by_definition": a["by_definition"]})
    return table


def forensics_summary(results):
    """Launch statistics over deep checks: shares of tokens and medians, each with its n."""
    rows = [r["forensics"] for r in results if (r.get("forensics") or {}).get("status") == "ok"]

    def med(vals):
        v = sorted(x for x in vals if x is not None)
        return round(v[len(v) // 2], 3) if v else None

    def share(flags):
        f = [x for x in flags if x is not None]
        return {"n": len(f), "share": round(sum(f) / len(f), 3) if f else None}
    linked = [x["creator_linked"] for x in rows if x["creator_linked"].get("status") == "ok"]
    return {
        "deep_checks": len(rows),
        "dev_initial_buy_pct_median": med(x["dev"]["initial_buy_pct"] for x in rows),
        "dev_sold_all": share((x["dev"]["signer_now_pct"] or 0) == 0 and (x["dev"]["initial_buy_pct"] or 0) > 0 for x in rows),
        "signer_differs_from_curve_creator": share(x["dev"]["same_wallet"] is False if x["dev"]["same_wallet"] is not None else None for x in rows),
        "has_bundle": share(x["launch"]["n_bundle"] > 0 for x in rows),
        "bundle_pct_median_when_present": med(x["launch"]["bundle_pct"] for x in rows if x["launch"]["n_bundle"] > 0),
        "has_snipers": share(x["launch"]["n_snipers"] > 0 for x in rows),
        "launch_buyers_now_pct_median": med(x["launch"]["buyers_now_pct"] for x in rows),
        "has_creator_linked_buyers": share((x["n_direct"] + x["n_two_hop"]) > 0 for x in linked),
        "creator_linked_bought_pct_median_when_present": med(x["linked_bought_pct"] for x in linked if x["n_direct"] + x["n_two_hop"] > 0),
        "has_buyer_that_funded_the_dev": share(x.get("n_funded_the_dev", 0) > 0 for x in linked),
        "has_buyer_cluster": share((x.get("buyer_clusters") or {}).get("n_clusters", 0) > 0 for x in rows
                                   if (x.get("buyer_clusters") or {}).get("status") == "ok"),
        "insiders_took_10pct_or_more": share((x["dev"]["initial_buy_pct"] or 0) + x["launch"]["buyers_bought_pct"] >= 10 for x in rows),
        "has_fresh_top_holder": share(x["fresh_holders"]["n_fresh"] > 0 for x in rows if x["fresh_holders"].get("status") == "ok"),
        "launch_group_now_pct_median": med(x["launch_group_now_pct"] for x in rows),
    }
