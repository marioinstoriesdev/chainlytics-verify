"""HTTP API and token page.

  GET /health
  GET /verify/{mint}?deep=      fresh check, or the cached one if younger than api.cache_s; deep=true adds the
                                launch rebuild (dev buy and sell, bundle, snipers, launch group)
  GET /checks/{id}              stored result
  GET /checks/{id}/receipts     receipt list (hashes, methods, params, slots)
  GET /checks/{id}/raw/{idx}    raw response behind one receipt, to recompute the verdict
  GET /index?days=D             agreement per source and metric
  GET /roots/{day}              daily receipts root and its memo signature, if anchored
  GET /definitions              the definitions registry
  GET /t/{mint}                 token page (HTML)
Run: python3.12 -m verify.api
"""
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from solders.pubkey import Pubkey

from . import settings
from .definitions import REGISTRY
from .report import aggregate, html_page


def valid_mint(mint, cfg):
    a = cfg["api"]
    if not (a["mint_min_len"] <= len(mint) <= a["mint_max_len"]):
        return False
    try:
        Pubkey.from_string(mint)
        return True
    except ValueError:
        return False


def create_app(cfg, verifier=None):
    app = FastAPI(title="Chainlytics Verify", version=cfg["service"]["version"])
    state = {"v": verifier}

    def v():
        if state["v"] is None:
            from .service import Verifier
            state["v"] = Verifier(cfg)
        return state["v"]

    async def get_or_run(mint, deep):
        if not valid_mint(mint, cfg):
            raise HTTPException(400, "not a Solana address")
        cid, cached = v().store.latest(mint, cfg["api"]["cache_s"] * 1000)
        if cached and (cached.get("deep") or not deep):
            cached["cached"] = True
            return cached
        return await v().verify(mint, origin="api", deep=deep)

    @app.get("/health")
    def health():
        return {"ok": True, "service": cfg["service"]["name"], "version": cfg["service"]["version"], "time_ms": int(time.time() * 1000)}

    @app.get("/definitions")
    def definitions():
        return REGISTRY

    @app.get("/verify/{mint}")
    async def verify(mint: str, deep: bool = False):
        return await get_or_run(mint, deep)

    @app.get("/checks/{cid}")
    def check(cid: int):
        r = v().store.check(cid)
        if not r:
            raise HTTPException(404, "no such check")
        return r

    @app.get("/checks/{cid}/receipts")
    def receipts(cid: int):
        return v().store.receipts(cid)

    @app.get("/checks/{cid}/raw/{idx}")
    def raw(cid: int, idx: int):
        r = v().store.raw(cid, idx)
        if r is None:
            raise HTTPException(404, "no such receipt")
        return r

    @app.get("/index")
    def index(days: float = None, origin: str = None):
        d = days or cfg["index"]["default_days"]
        rows = v().store.verdict_rows(int((time.time() - d * 86400) * 1000), origin)
        return {"days": d, "origin": origin, "rows": aggregate(rows, cfg["index"]["wilson_z"])}

    @app.get("/roots/{day}")
    def root(day: str):
        r = v().store.root(day)
        if not r:
            raise HTTPException(404, "no root for that day")
        return r

    @app.get("/t/{mint}", response_class=HTMLResponse)
    async def page(mint: str):
        return html_page(await get_or_run(mint, cfg["api"]["page_deep"]), panel=cfg.get("panel"))

    return app


def main():
    import uvicorn
    cfg = settings.bootstrap(settings.load())
    uvicorn.run(create_app(cfg), host=cfg["api"]["host"], port=cfg["api"]["port"])


if __name__ == "__main__":
    main()
