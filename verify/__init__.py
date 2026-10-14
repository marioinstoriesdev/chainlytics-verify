"""Chainlytics Verify: checks token metrics shown by public data sources against Solana mainnet.

Modules:
  settings     config loading (config.yaml, then config.local.yaml for secrets)
  net          HTTP client for public sources and Solana JSON-RPC client
  launch       launch rebuild: creator buy, bundle, snipers, tips
  receipts     receipt recording, canonical hashing, Merkle root
  chain        mainnet reads over Solana JSON-RPC (RPC Fast in the hosted service) (mint, bonding curve, token accounts, creation signer)
  definitions  the definitions registry: every on-chain definition of each metric
  sources      public source adapters (Jupiter, DexScreener, RugCheck)
  verdict      comparison of source values against chain definitions
  store        SQLite persistence of checks, verdicts and receipts
  service      one end-to-end verification
  sampler      random sample of new pump.fun mints from the chain
  index        batch runs and the per source x metric agreement index
  api          FastAPI app (/verify, /index, receipts, token page)
  bot          Telegram bot (/verify <mint>)
  anchor       daily receipts root for an on-chain memo
"""
