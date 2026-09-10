"""
token_ledger.py
================
An internal contribution-scoring ledger — nodes earn "tokens" for
mining well-scored blocks and useful AI insights. Read the boundary
below before treating this as more than that.

WHAT THIS IS
------------
A local reputation score per node_id, computed from DAS scores
already flowing through your pipeline. Useful for: seeing which
nodes/algorithms are contributing high-quality data over time,
weighting trust in future consensus decisions, giving your AI
participant a concrete signal to optimize for.

WHAT THIS IS EXPLICITLY NOT
------------------------------
This is NOT a cryptocurrency, NOT an on-chain asset, and NOT
tradeable or transferable between people. Balances live in this
process's memory (or wherever you persist them) — they don't move
value, can't be sold, and aren't backed by anything. That's a
deliberate scope limit, not a missing feature.

If you ever want an actual on-chain token (tradeable, backed by
your pending Polygon smart-contract work), that crosses into
securities/financial-regulation territory depending on how it's
distributed and marketed — how tokens are minted, whether they're
sold or airdropped, whether they're described as having value, etc.
all matter legally. That needs a lawyer's review before code, not
after. Keep this ledger as internal reputation scoring until you've
had that conversation.

Usage
-----
    ledger = TokenLedger()
    ledger.award("node-a-id", das_score=5.7, reason="mined block")
    ledger.balance("node-a-id")   # -> running total
    ledger.leaderboard()          # -> sorted contribution ranking

    # optional: pass an AuditTrail to get a tamper-evident, code-fingerprinted
    # log of every award, in addition to the in-memory history above
    from audit_trail import AuditTrail
    ledger = TokenLedger(audit=AuditTrail("token_audit.jsonl"))

    # optional: persist balances + history to disk automatically, same
    # atomic-write pattern as digital_dna.py / network_os.py
    ledger = TokenLedger(store_path="token_ledger.json")
"""

from __future__ import annotations
import json
import os
import time
from dataclasses import dataclass, field, asdict


@dataclass
class LedgerEntry:
    node_id: str
    amount: float
    reason: str
    timestamp: float = field(default_factory=time.time)


class TokenLedger:
    def __init__(self, audit=None, store_path: str | None = None):
        self.audit = audit   # optional AuditTrail instance — see audit_trail.py
        self.store_path = store_path

        if store_path and os.path.exists(store_path):
            with open(store_path) as f:
                state = json.load(f)
            self._balances: dict[str, float] = state["balances"]
            self._history: list[LedgerEntry] = [LedgerEntry(**e) for e in state["history"]]
        else:
            self._balances: dict[str, float] = {}
            self._history: list[LedgerEntry] = []

    def _save(self):
        if not self.store_path:
            return
        state = {
            "balances": self._balances,
            "history": [asdict(e) for e in self._history],
        }
        tmp_path = self.store_path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, self.store_path)   # atomic, same pattern as the rest of the system

    def award(self, node_id: str, das_score: float, reason: str = "block mined") -> float:
        """
        DAS score (0-6) maps directly to award amount — higher-quality
        contributions earn more. This is the only formula; keep it
        simple and auditable rather than tuning a hidden multiplier.
        """
        amount = round(max(0.0, das_score), 3)
        self._balances[node_id] = self._balances.get(node_id, 0.0) + amount
        self._history.append(LedgerEntry(node_id=node_id, amount=amount, reason=reason))
        self._save()

        if self.audit is not None:
            self.audit.log(
                module="token_ledger", action="award", node_id=node_id,
                details={"amount": amount, "reason": reason, "new_balance": self._balances[node_id]},
            )

        return self._balances[node_id]

    def balance(self, node_id: str) -> float:
        return self._balances.get(node_id, 0.0)

    def leaderboard(self) -> list[tuple[str, float]]:
        return sorted(self._balances.items(), key=lambda kv: kv[1], reverse=True)

    def history_for(self, node_id: str) -> list[LedgerEntry]:
        return [e for e in self._history if e.node_id == node_id]
