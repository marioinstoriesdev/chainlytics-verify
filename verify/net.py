"""Network adapters for the standalone build.

HttpClient   blocking JSON over HTTP for the public sources and the Telegram bot.
JsonRpc      async Solana JSON-RPC over HTTP (any provider; the hosted service uses RPC Fast).

Both return plain data and never raise on network errors: a failure comes back as {"error": ..., "status_code": ...}
so it is recorded as a receipt like any other answer. Timeouts, headers and retry counts come from config.yaml
(network section); the RPC URL, which usually carries an API key, comes from config.local.yaml or the
CHAINLYTICS_VERIFY_RPC_URL environment variable and is never logged or stored in a receipt.
"""
import asyncio
import os

import httpx

RPC_URL_ENV = "CHAINLYTICS_VERIFY_RPC_URL"


class HttpClient:
    def __init__(self, cfg):
        n = cfg["network"]
        self.default_timeout = n["source_timeout_s"]
        self.headers = {"User-Agent": n["user_agent"], "Accept": "application/json"}
        self.retries = n["http_retries"]

    def _make_request(self, url, method="GET", params=None, json_data=None, timeout=None):
        last = None
        for _ in range(self.retries + 1):
            try:
                r = httpx.request(method, url, params=params, json=json_data, headers=self.headers,
                                  timeout=timeout or self.default_timeout)
            except httpx.HTTPError as e:
                last = {"error": type(e).__name__, "message": str(e)[:200]}
                continue
            if r.status_code >= 400:
                last = {"error": "HTTP %d" % r.status_code, "status_code": r.status_code}
                if r.status_code in (400, 401, 403, 404):
                    return last
                continue
            try:
                return r.json()
            except ValueError:
                return {"error": "response is not JSON", "status_code": r.status_code}
        return last or {"error": "no response"}


def rpc_url(cfg):
    return os.environ.get(RPC_URL_ENV) or (cfg.get("rpc") or {}).get("url") or ""


class JsonRpc:
    def __init__(self, cfg):
        self.url = rpc_url(cfg)
        if not self.url:
            raise SystemExit("no RPC URL: set rpc.url in config.local.yaml or %s" % RPC_URL_ENV)
        r = cfg["rpc"]
        self.timeout = r["timeout_s"]
        self.retries = r["retries"]
        self.backoff = r["retry_backoff_s"]
        self._client = None
        self._loop = None
        self._id = 0

    async def _rpc_call(self, method, params):
        loop = asyncio.get_running_loop()
        if self._client is None or self._loop is not loop:
            self._client = httpx.AsyncClient(timeout=self.timeout)
            self._loop = loop
        self._id += 1
        body = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        last = None
        for attempt in range(self.retries + 1):
            try:
                r = await self._client.post(self.url, json=body)
                if r.status_code == 429 or r.status_code >= 500:
                    last = {"error": {"code": r.status_code, "message": "HTTP %d" % r.status_code}}
                else:
                    return r.json()
            except (httpx.HTTPError, ValueError) as e:
                last = {"error": {"code": -1, "message": type(e).__name__}}
            await asyncio.sleep(self.backoff * (attempt + 1))
        return last
