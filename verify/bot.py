"""Telegram bot: send a mint (or /verify <mint>) and get the per-metric verdicts back.

Requests go through the HTTP client in verify/net.py. The bot token is read from config.local.yaml (telegram.bot_token, untracked) and never
logged. Run: python3.12 -m verify.bot
"""
import asyncio
import logging
import re

from . import settings
from .api import valid_mint
from .report import text_summary

log = logging.getLogger("chainlytics_verify.bot")

HELP = ("Send a Solana token address (or /verify <address>).\n"
        "For each metric the trading screens show (supply, authorities, holders, top-10 share, dev wallet, "
        "migration, price on the bonding curve) I read Solana mainnet, compare what public sources report, and tell you "
        "whether each number matches the chain, which definition it follows, or that it cannot be checked.")

MINT_RE = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")


class Bot:
    def __init__(self, cfg, client, verifier):
        self.cfg, self.client, self.verifier = cfg, client, verifier
        t = cfg["telegram"]
        self.base = "%s/bot%s" % (t["api_base"].rstrip("/"), t["bot_token"])
        self.offset = None

    def _call(self, method, params=None, json_data=None, timeout=None):
        url = "%s/%s" % (self.base, method)
        if json_data is not None:
            return self.client._make_request(url, method="POST", json_data=json_data, timeout=timeout)
        return self.client._make_request(url, method="GET", params=params, timeout=timeout)

    def send(self, chat_id, text):
        return self._call("sendMessage", json_data={"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True})

    async def handle(self, msg):
        chat = (msg.get("chat") or {}).get("id")
        text = (msg.get("text") or "").strip()
        if not chat or not text:
            return
        if text.startswith("/start") or text.startswith("/help"):
            await asyncio.to_thread(self.send, chat, HELP)
            return
        m = MINT_RE.search(text)
        if not m or not valid_mint(m.group(0), self.cfg):
            await asyncio.to_thread(self.send, chat, "That does not look like a Solana token address. " + HELP)
            return
        mint = m.group(0)
        await asyncio.to_thread(self.send, chat, "Checking %s against Solana mainnet..." % mint)
        try:
            r = await self.verifier.verify(mint, origin="bot", deep=self.cfg["telegram"]["deep"])
            await asyncio.to_thread(self.send, chat, text_summary(r, self.cfg["telegram"].get("page_url", "")))
        except Exception as e:  # noqa: BLE001 - report failure to the user, keep polling
            log.warning("verify failed for %s: %s", mint, repr(e)[:200])
            await asyncio.to_thread(self.send, chat, "The check failed this time; please try again in a minute.")

    async def run(self):
        t = self.cfg["telegram"]
        while True:
            params = {"timeout": t["poll_timeout_s"]}
            if self.offset is not None:
                params["offset"] = self.offset
            r = await asyncio.to_thread(self._call, "getUpdates", params, None, t["poll_timeout_s"] + 10)
            for u in (r or {}).get("result") or [] if isinstance(r, dict) else []:
                self.offset = u["update_id"] + 1
                if u.get("message"):
                    await self.handle(u["message"])
            if isinstance(r, dict) and r.get("error"):
                await asyncio.sleep(5)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = settings.bootstrap(settings.load())
    if not cfg["telegram"]["enabled"] or not cfg["telegram"]["bot_token"]:
        raise SystemExit("telegram.enabled is false or telegram.bot_token is empty (set both in config.local.yaml)")
    from .service import Verifier, build_client
    asyncio.run(Bot(cfg, build_client(cfg), Verifier(cfg)).run())  # separate client: Telegram errors must not trip the sources' circuit breaker


if __name__ == "__main__":
    main()
