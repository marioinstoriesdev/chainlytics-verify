"""Daily receipts root, to be written on chain as a memo.

  python3.12 -m verify.anchor root [--day YYYY-MM-DD]       compute and store the Merkle root of that UTC day's receipts
  python3.12 -m verify.anchor record --day D --sig SIG      store the signature of the memo transaction

This tool never holds a private key. The operator posts the printed memo text from his own wallet and
records the transaction signature here."""
import argparse
import calendar
import time

from . import settings
from .receipts import merkle_root
from .store import Store


def day_bounds(day):
    start = calendar.timegm(time.strptime(day, "%Y-%m-%d")) * 1000
    return start, start + 86400 * 1000


def main():
    cfg = settings.bootstrap(settings.load())
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("root")
    r.add_argument("--day", default=time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400)))
    c = sub.add_parser("record")
    c.add_argument("--day", required=True)
    c.add_argument("--sig", required=True)
    a = ap.parse_args()
    st = Store(cfg["paths"]["db"])
    if a.cmd == "root":
        hashes = st.receipt_hashes(*day_bounds(a.day))
        root = merkle_root(hashes)
        if not root:
            raise SystemExit("no receipts on %s" % a.day)
        st.save_root(a.day, root, len(hashes))
        print("day %s receipts %d root %s" % (a.day, len(hashes), root))
        print("memo text: %s%s:%s" % (cfg["anchor"]["memo_prefix"], a.day, root))
    else:
        st.record_memo(a.day, a.sig)
        print("recorded", a.day, a.sig)


if __name__ == "__main__":
    main()
