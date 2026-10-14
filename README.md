# Chainlytics Verify

Chainlytics Verify checks what public data sources say about new Solana tokens against Solana mainnet, and returns a receipt anyone can replay. It starts with pump.fun tokens.

For every metric it reads the real state from mainnet, compares it with what Jupiter, DexScreener and RugCheck report in the same minute, and says whether each value matches the chain, which on-chain definition it follows and how fresh it is. A deep check also rebuilds the launch from public transactions: dev buy and sell, the bundle in the creation slot, snipers, and what the launch group holds now. Launches by a configured list of deployers are flagged.

It is the relaunch of Chainlytics (Colosseum Frontier, April 2026) and our entry to the Colosseum x RPC Fast hackathon (October 2026).

## Quick start

```
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp config.local.example.yaml config.local.yaml      # put your Solana RPC URL in rpc.url
python3.12 -m pytest -q                              # offline tests, no network
python3.12 -m verify.api                             # then open http://127.0.0.1:8710/t/<mint>
```

## What it measures

| Metric | On-chain definitions compared |
| --- | --- |
| Mint and freeze authority | mint account flags |
| Supply | mint account supply |
| Holder count | owners holding a balance; wallets holding a balance; every token account including empty ones |
| Top-10 share | including pools and the bonding curve; wallets only |
| Dev wallet | creation signer; creator stored in the pump.fun curve |
| Dev holdings | share of supply held by each dev wallet definition |
| Migrated flag | curve complete flag |
| Price | bonding-curve spot before and after the source reads; judged only while the token is on the curve |

Tolerances are fixed in config.yaml before any comparison is made.

## Layout

| Path | Role |
| --- | --- |
| verify/chain.py | mainnet reads: mint, curve, token accounts, creation transaction, ledger horizon |
| verify/net.py | HTTP client for public sources and Solana JSON-RPC client |
| verify/launch.py | launch rebuild: creator buy, bundle, snipers, tips |
| verify/sources.py | public source reads (Jupiter, DexScreener, RugCheck) |
| verify/definitions.py, verify/verdict.py | definitions registry and per-metric verdicts |
| verify/forensics.py | deep check: launch rebuild from public transactions |
| verify/factories.py | factory flag for listed deployers |
| verify/receipts.py, verify/store.py | receipts for every RPC and HTTP call (raw answer stored, SHA-256), SQLite store |
| verify/service.py | one check end to end |
| verify/api.py | HTTP API and token page |
| verify/bot.py | Telegram bot |
| verify/sampler.py, verify/index.py | random sampling of new pump.fun mints from the chain, batch runs, agreement index |
| verify/anchor.py | daily Merkle root of receipts, to be posted as an SPL Memo by the operator |
| tests/ | unit and end-to-end tests with fake RPC and sources (no network) |

## Commands

```
python3.12 -m verify.api                                  # HTTP API on api.host:api.port
python3.12 -m verify.index run [--n N] [--seed S] [--deep]  # sample new mints from the chain and verify each
python3.12 -m verify.index report [--days D] [--origin O]   # agreement per source and metric, written to reports_dir
python3.12 -m verify.index daily                           # scheduled job (once a day): index run, deep run, reports, yesterday root
python3.12 -m verify.anchor root [--day YYYY-MM-DD]        # Merkle root of that UTC day's receipts and the memo text
python3.12 -m verify.anchor record --day D --sig SIG       # store the signature of the posted memo
python3.12 -m verify.bot                                   # Telegram bot (needs telegram.enabled and a token)
python3.12 -m pytest -q                                    # tests
```

## API

| Endpoint | Returns |
| --- | --- |
| GET /health | service status |
| GET /definitions | the definitions registry |
| GET /verify/{mint}?deep= | a fresh check, or the cached one if younger than api.cache_s |
| GET /checks/{id} | a stored result |
| GET /checks/{id}/receipts | receipt list: method, params, slot, time, hash |
| GET /checks/{id}/raw/{idx} | the raw answer behind one receipt |
| GET /index | agreement per source and metric |
| GET /roots/{day} | the day's receipts root, its receipt count and the memo signature once recorded |
| GET /t/{mint} | token page |

## Configuration

Every tunable value lives in config.yaml: RPC settings, source URLs, tolerances, forensics limits, sampler window, API, Telegram, anchor and the factory list. Secrets never go in config.yaml. Put them in config.local.yaml next to it (not tracked by git), which is merged over config.yaml at load, for example:

```
rpc:
  url: "https://<your-solana-rpc-endpoint>"
telegram:
  enabled: true
  bot_token: "<token from BotFather>"
```

The RPC URL can also come from the CHAINLYTICS_VERIFY_RPC_URL environment variable.

CHAINLYTICS_VERIFY_CONFIG and CHAINLYTICS_VERIFY_LOCAL_CONFIG override the two paths.

## Requirements and current limits

- Python 3.12 or newer; dependencies in requirements.txt. Any Solana JSON-RPC endpoint works; the hosted service at chainlytics.dev reads mainnet through RPC Fast and uses the operator's own hardened HTTP client in place of verify/net.py.
- Deep checks need the creation transaction inside the RPC node's ledger window (about 12 hours on the node the hosted service uses). Older launches return creation_not_found, and creator-linked lookbacks are cut at the window and reported as lower bounds.
- Bonding-curve price comparison is under review and hidden on the hosted token page; price after migration is not judged yet.
- The factory flag reads deployers from the operator's list, which is not published here (factories.deployers is empty in this repository). Their own profits are unverified.
- This build rebuilds the launch only; wallet-link tracing runs in the hosted service and is not included.

## Findings so far

The first public report, State of Solana Token Data (October 2026), covers about 300 random pump.fun tokens. In a pre-registered study, a blind outside buyer lost 14.1% per trade on launches by the listed deployers, against 8.2% on random launches. Live demo: https://chainlytics.dev

## License

No open-source license has been chosen yet; until one is, all rights are reserved. Reading the code, running it to check our results and opening issues are welcome.
