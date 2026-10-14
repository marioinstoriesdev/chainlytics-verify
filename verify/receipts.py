"""Receipts: what we asked, when, and a hash of exactly what came back.

The raw response is kept with the receipt so anyone can recompute the verdict from it. The daily
Merkle root of all receipt hashes is what gets written on chain (see anchor.py)."""
import hashlib
import json
import time


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def digest(obj):
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


class Recorder:
    def __init__(self):
        self.items = []

    def add(self, kind, target, params, response, slot=None):
        rec = {"kind": kind, "target": target, "params": params, "slot": slot,
               "ts_ms": int(time.time() * 1000), "sha256": digest(response)}
        self.items.append((rec, response))
        return rec


def merkle_root(hashes):
    """Merkle root over hex digests, sorted for order independence; odd levels duplicate the last node."""
    level = sorted(hashes)
    if not level:
        return None
    level = [bytes.fromhex(h) for h in level]
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [hashlib.sha256(level[i] + level[i + 1]).digest() for i in range(0, len(level), 2)]
    return level[0].hex()
