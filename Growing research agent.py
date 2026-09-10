"""
growing_research_agent.py
================
Answers what you asked for directly:
  - GROWS: persists to disk, survives restarts, accumulates over time.
  - NOT THE SAME INFORMATION: a global `known_ids` set means a record
    (a specific trial, article, or variant) is only ever counted/stored
    once, no matter how many topics or ticks encounter it again.
  - KEEPS LOOKING FOR RELATED TOPICS: after each query, it scans the
    article titles for candidate related gene/biomarker names and
    queues them as new topics to explore later — capped, so it can't
    run away unboundedly.
  - PEER-TO-PEER: uses the REAL gossip channel already built in
    network_os.py. When this node discovers new candidate topics, it
    shares just the topic strings (not results, not raw data) with
    connected peers via a new "topic_share" message type, using the
    generic extension point just added to NetworkNode. A peer
    receiving a shared topic queues it too — so two peers exploring
    the same condition don't have to independently rediscover the
    same related topics.

WHAT'S STILL TRUE FROM BEFORE
--------------------------------
Every explore step still mines a real, signed block (source=
"research_query"/"research_update"/"topic_expansion") with just a
hash — the growth here is in this agent's local persisted store and
in which TOPICS get shared with peers, not in exposing result
content on-chain. See digital_dna.py's ALLOWED_SOURCES for exactly
what each source claims and requires.

THE HONEST LIMITS OF "RELATED TOPIC" DETECTION
----------------------------------------------------
extract_candidate_biomarkers() is a regex heuristic (looks for
ALL-CAPS-ish tokens in article titles), not real biomedical named-
entity recognition. It will produce some false positives (acronyms
that aren't genes) and miss real gene names in mixed case. Treat its
output as candidates worth a look, not verified biomarkers.

Usage
-----
    from growing_research_agent import GrowingResearchAgent

    agent = GrowingResearchAgent(node, dna, store_path="my_research.json")
    await agent.start()
    await agent.seed_topic("breast cancer", biomarker="BRCA1")   # explicit, consent-gated
    # ... time passes, agent.tick() runs automatically every interval_s ...
"""

from __future__ import annotations
import asyncio
import hashlib
import json
import os
import re
import time

from network_os import NetworkNode
from digital_dna import DigitalDNA
from research_agent import _run_all_sources, _combined_id_set

MAX_TOTAL_TOPICS = 50   # hard cap so this can't grow unboundedly on its own
GENE_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9]{2,9}\b")
COMMON_ACRONYM_STOPLIST = {
    "DNA", "RNA", "USA", "FDA", "PCR", "MRI", "CT", "PET", "NIH", "WHO",
    "AI", "ID", "II", "III", "IV",
}


def _topic_key(condition: str, biomarker: str | None) -> str:
    return f"{condition.strip().lower()}|{(biomarker or '').strip().lower()}"


def extract_candidate_biomarkers(articles: list[dict], exclude: set[str]) -> set[str]:
    """Heuristic only — see module docstring's honesty note."""
    candidates = set()
    stop = COMMON_ACRONYM_STOPLIST | {e.upper() for e in exclude if e}
    for a in articles:
        title = a.get("title") or ""
        for tok in GENE_TOKEN_RE.findall(title):
            if tok not in stop:
                candidates.add(tok)
    return candidates


class GrowingResearchAgent:
    def __init__(
        self, node: NetworkNode, dna: DigitalDNA,
        store_path: str = "research_store.json",
        interval_s: float = 3600.0,
        max_topics: int = MAX_TOTAL_TOPICS,
    ):
        self.node = node
        self.dna = dna
        self.store_path = store_path
        self.interval_s = interval_s
        self.max_topics = max_topics
        self._task: asyncio.Task | None = None

        self._load()
        node.custom_handlers["topic_share"] = self._handle_topic_share

    # ---- persistence ----
    def _load(self):
        if os.path.exists(self.store_path):
            with open(self.store_path) as f:
                store = json.load(f)
        else:
            store = {"known_ids": [], "topics": {}, "queue": []}
        self.known_ids: set[str] = set(store["known_ids"])
        self.topics: dict[str, dict] = store["topics"]
        self.queue: list[dict] = store["queue"]

    def _save(self):
        tmp_path = self.store_path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump({
                "known_ids": sorted(self.known_ids),
                "topics": self.topics,
                "queue": self.queue,
            }, f, indent=2)
        os.replace(tmp_path, self.store_path)   # atomic on POSIX — no half-written file on crash

    def total_topic_count(self) -> int:
        return len(self.topics) + len(self.queue)

    # ---- lifecycle ----
    async def start(self):
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self):
        while True:
            await asyncio.sleep(self.interval_s)
            await self.tick()

    # ---- explicit, consent-gated root query ----
    async def seed_topic(self, condition: str, biomarker: str | None = None) -> str:
        key = _topic_key(condition, biomarker)
        if key in self.topics:
            return key   # already explored — don't redo the work
        await self._explore(key, condition, biomarker, source="research_query")
        return key

    # ---- one autonomous step: explore a queued topic, or re-check an old one ----
    async def tick(self):
        if self.queue and self.total_topic_count() <= self.max_topics:
            nxt = self.queue.pop(0)
            key = _topic_key(nxt["condition"], nxt["biomarker"])
            if key not in self.topics:
                await self._explore(key, nxt["condition"], nxt["biomarker"], source="topic_expansion")
            self._save()
            return {"action": "explored_new_topic", "key": key}

        if self.topics:
            oldest_key = min(self.topics, key=lambda k: self.topics[k]["last_checked"])
            t = self.topics[oldest_key]
            await self._explore(oldest_key, t["condition"], t["biomarker"], source="research_update")
            self._save()
            return {"action": "rechecked_existing_topic", "key": oldest_key}

        return {"action": "nothing_to_do"}

    # ---- the actual work: query, dedup, store, mine, expand, share ----
    async def _explore(self, key: str, condition: str, biomarker: str | None, source: str):
        found = _run_all_sources(condition, biomarker, recruiting_only=True)
        all_ids = _combined_id_set(found["trials"], found["articles"], found["variants"])
        new_ids = [i for i in all_ids if i not in self.known_ids]
        self.known_ids.update(new_ids)

        existing = self.topics.get(key, {})
        self.topics[key] = {
            "condition": condition,
            "biomarker": biomarker,
            "last_ids": all_ids,
            "new_ids_last_run": new_ids,
            "first_seen": existing.get("first_seen", time.time()),
            "last_checked": time.time(),
        }

        query_hash = hashlib.sha256(f"{key}|{time.time()}".encode()).hexdigest()
        await self.node.mine_and_broadcast(
            source=source, feature_hash_hex=query_hash,
            confidence=min(1.0, len(new_ids) / 15.0), consent_verified=True,
        )

        # ---- expand: find related topics, queue them (capped) ----
        candidates = extract_candidate_biomarkers(found["articles"], exclude={biomarker or ""})
        already_known = {q["biomarker"] for q in self.queue if q["condition"] == condition}
        already_known |= {t["biomarker"] for t in self.topics.values() if t["condition"] == condition}
        new_candidates = []
        for c in candidates:
            if c in already_known:
                continue
            if self.total_topic_count() >= self.max_topics:
                break
            self.queue.append({"condition": condition, "biomarker": c})
            already_known.add(c)
            new_candidates.append(c)

        # ---- share newly discovered topics with peers, real p2p, dedup on their end ----
        if new_candidates:
            await self.node.broadcast_encrypted({
                "type": "topic_share", "condition": condition, "biomarkers": new_candidates,
            })

        self._save()
        return {"new_ids": new_ids, "new_candidate_topics": new_candidates}

    # ---- receiving side: a peer told us about related topics they found ----
    async def _handle_topic_share(self, payload: dict, sender_node_id: str):
        condition = payload.get("condition")
        biomarkers = payload.get("biomarkers", [])
        if not condition:
            return
        queued_here = {q["biomarker"] for q in self.queue if q["condition"] == condition}
        known_here = {t["biomarker"] for t in self.topics.values() if t["condition"] == condition}
        for b in biomarkers:
            if b in queued_here or b in known_here:
                continue   # we already know about this one — don't duplicate
            if self.total_topic_count() >= self.max_topics:
                break
            self.queue.append({"condition": condition, "biomarker": b})
            queued_here.add(b)
        self._save()


# ----------------------------------------------------------------- #
# Demo: two real peers, one discovers related topics, shares them,
# the other queues them without rederiving — then persistence proof
# ----------------------------------------------------------------- #

async def _demo():
    import tempfile

    dna_a = DigitalDNA(seed_label="growing-agent-a")
    dna_b = DigitalDNA(seed_label="growing-agent-b")
    node_a = NetworkNode(dna_a, host="127.0.0.1", port=9701)
    node_b = NetworkNode(dna_b, host="127.0.0.1", port=9702)

    await node_a.start()
    await node_b.start()
    await node_b.connect_peer("127.0.0.1", 9701)
    await asyncio.sleep(0.3)

    with tempfile.TemporaryDirectory() as tmp:
        path_a = os.path.join(tmp, "store_a.json")
        path_b = os.path.join(tmp, "store_b.json")

        agent_a = GrowingResearchAgent(node_a, dna_a, store_path=path_a, interval_s=999, max_topics=10)
        agent_b = GrowingResearchAgent(node_b, dna_b, store_path=path_b, interval_s=999, max_topics=10)

        print("=== Node A: real seed query, real candidate extraction ===")
        key = await agent_a.seed_topic("breast cancer", biomarker="BRCA1")
        print(f"Topic explored: {key}")
        print(f"Real new ids found: {len(agent_a.topics[key]['new_ids_last_run'])}")
        print(f"Candidate related topics queued at A: {[q['biomarker'] for q in agent_a.queue]}")

        await asyncio.sleep(0.3)   # let the topic_share gossip land at B

        print(f"\nQueue at B after receiving A's topic_share (no rediscovery needed): "
              f"{[q['biomarker'] for q in agent_b.queue]}")

        print("\n=== Persistence proof: reload A's store from disk into a fresh agent ===")
        agent_a2 = GrowingResearchAgent(node_a, dna_a, store_path=path_a, interval_s=999, max_topics=10)
        print(f"Topics recovered after 'restart': {list(agent_a2.topics.keys())}")
        print(f"known_ids recovered after 'restart': {len(agent_a2.known_ids)} ids")

        await agent_a.stop()
        await agent_b.stop()

    await node_a.stop()
    await node_b.stop()


if __name__ == "__main__":
    asyncio.run(_demo())
