"""Random sample of new pump.fun mints straight from the chain.

Every pump.fun creation is signed by the program's mint authority, so its signature list is the list of
creations. We page it back over the configured window and sample uniformly, which avoids drawing from any
watch list or data source."""
import random
import time


async def recent_creations(chain, cfg):
    s = cfg["sampler"]
    now = time.time()
    lo, hi = now - s["window_max_s"], now - s["window_min_s"]
    before, out = None, []
    for _ in range(s["max_pages"]):
        p = {"limit": cfg["rpc"]["sig_page_limit"]}
        if before:
            p["before"] = before
        res = await chain.call("getSignaturesForAddress", [cfg["pump"]["mint_authority"], p])
        if not res:
            break
        before = res[-1]["signature"]
        for x in res:
            bt = x.get("blockTime")
            if not x.get("err") and bt and lo <= bt <= hi:
                out.append(x["signature"])
        if res[-1].get("blockTime") and res[-1]["blockTime"] < lo:
            break
    return out


def mint_from_tx(tx, cfg):
    """The created mint: first account of the pump.fun create instruction, else the second signer."""
    msg = ((tx or {}).get("transaction") or {}).get("message") or {}
    for ins in msg.get("instructions") or []:
        if ins.get("programId") == cfg["pump"]["program_id"] and ins.get("accounts"):
            return ins["accounts"][0]
    signers = [k["pubkey"] for k in msg.get("accountKeys") or [] if isinstance(k, dict) and k.get("signer")]
    return signers[1] if len(signers) > 1 else None


async def sample_mints(chain, cfg, n, seed=None):
    sigs = await recent_creations(chain, cfg)
    rng = random.Random(seed)
    picked = rng.sample(sigs, min(n, len(sigs)))
    mints = []
    version = cfg["rpc"]["max_supported_tx_version"]
    for sig in picked:
        try:
            tx = await chain.call("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": version}])
        except RuntimeError:
            continue
        m = mint_from_tx(tx, cfg)
        if m:
            mints.append(m)
    return mints, len(sigs)
