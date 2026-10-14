"""Batch runs and the agreement index.

  python3.12 -m verify.index run [--n N] [--seed S] [--deep]   sample new pump.fun mints from the chain and verify each
  python3.12 -m verify.index report [--days D]         per source x metric agreement over the last D days
  python3.12 -m verify.index daily                     scheduled job: index run, deep run, reports, yesterday's receipts root
"""
import argparse
import asyncio
import json
import os
import time

from . import settings


async def run(cfg, n, seed, deep=False):
    from .chain import ChainReader
    from .receipts import Recorder
    from .sampler import sample_mints
    from .service import Verifier
    v = Verifier(cfg)
    mints, pool = await sample_mints(ChainReader(v.rpc, cfg, Recorder()), cfg, n, seed)
    print("sampled %d of %d creations in the window" % (len(mints), pool), flush=True)
    done = 0
    for m in mints:
        try:
            r = await v.verify(m, origin="index_deep" if deep else "index", deep=deep)
            done += 1
            print("%s check=%s %.1fs" % (m[:8], r["check_id"], r["elapsed_s"]), flush=True)
        except Exception as e:  # noqa: BLE001 - one bad mint must not stop the batch
            print("%s failed: %s" % (m[:8], repr(e)[:160]), flush=True)
    return done


def report(cfg, days, origin):
    from .report import aggregate, forensics_summary
    from .store import Store
    since = int((time.time() - days * 86400) * 1000)
    st = Store(cfg["paths"]["db"])
    table = aggregate(st.verdict_rows(since, origin), cfg["index"]["wilson_z"])
    launches = forensics_summary(st.results(since, origin))
    path = os.path.join(cfg["paths"]["reports_dir"], "index_%s.json" % time.strftime("%Y%m%dT%H%MZ", time.gmtime()))
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"since_ms": since, "origin": origin, "rows": table, "launches": launches}, f, indent=1)
    for r in table:
        print("%-12s %-26s n=%3d agree=%s low95=%s defs=%s not_reported=%d" % (
            r["source"], r["metric"], r["compared"], r["agreement"], r["agreement_lower_95"], r["by_definition"], r["not_reported"]))
    for k, val in launches.items():
        print("launches %-46s %s" % (k, val))
    print("written", path)
    return table


def daily(cfg):
    """The scheduled daily job: random index run, deep run, a report per origin, and yesterday's receipts root."""
    from .anchor import day_bounds
    from .receipts import merkle_root
    from .store import Store
    d = cfg["daily"]
    asyncio.run(run(cfg, d["n"], None, False))
    asyncio.run(run(cfg, d["deep_n"], None, True))
    for origin in d["report_origins"]:
        report(cfg, d["report_days"], origin)
    day = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400))
    st = Store(cfg["paths"]["db"])
    hashes = st.receipt_hashes(*day_bounds(day))
    root = merkle_root(hashes)
    if root:
        st.save_root(day, root, len(hashes))
        print("day %s receipts %d root %s" % (day, len(hashes), root))
        print("memo text: %s%s:%s" % (cfg["anchor"]["memo_prefix"], day, root))
    else:
        print("no receipts on %s" % day)


def main():
    cfg = settings.bootstrap(settings.load())
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--n", type=int, default=cfg["sampler"]["default_n"])
    r.add_argument("--seed", type=int, default=None)
    r.add_argument("--deep", action="store_true")
    p = sub.add_parser("report")
    p.add_argument("--days", type=float, default=cfg["index"]["default_days"])
    p.add_argument("--origin", default="index")
    sub.add_parser("daily")
    a = ap.parse_args()
    if a.cmd == "run":
        asyncio.run(run(cfg, a.n, a.seed, a.deep))
    elif a.cmd == "daily":
        daily(cfg)
    else:
        report(cfg, a.days, a.origin)


if __name__ == "__main__":
    main()
