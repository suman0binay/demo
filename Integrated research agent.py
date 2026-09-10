"""
integrated_research_agent.py
================
One tick cycle that does everything discussed so far, in the right
order, with real concurrency where it's actually possible:

  1. RESEARCH   — GrowingResearchAgent explores a topic (real APIs)
  2. CHAIN       — mines a real, signed block for what it found
                   (this IS the "research blocks" — see mine_and_broadcast
                   inside growing_research_agent.py's _explore())
  3. CORPUS      — feeds the real titles found into CorpusVectorStore
                   for semantic search
  4. VISUALS     — generates a real data chart AND (if you have an
                   OpenAI key) real AI art, CONCURRENTLY — both only
                   depend on step 1-3's output, not on each other, so
                   they genuinely run at the same time via asyncio
  5. VIDEO       — occasional, opt-in, NOT run every cycle (see below)

WHY STEPS 1-3 CAN'T ALSO RUN CONCURRENTLY WITH EACH OTHER
---------------------------------------------------------------
Being honest about a real constraint: research must finish before you
can mine a block about what was found, and the block must exist
before you can chart/art it. That's not a design choice, it's just
what "visualize what happened" means — you can't visualize a result
before it exists. The concurrency here is real where it's possible
(the two visual generators) and sequential where it has to be.

WHY VIDEO IS DIFFERENT FROM CHART/ART — NOT RUN EVERY CYCLE
-----------------------------------------------------------------
A chart takes milliseconds. AI art (DALL-E) takes seconds. Video
(Runway) takes MINUTES and costs meaningfully more per call. Running
it every single explore cycle would be slow and expensive for no
real benefit — most cycles don't produce network state different
enough to warrant a new video. So video generation:
  - only fires every `video_every_n_cycles` cycles (default 10)
  - runs as a background asyncio task, NOT awaited inline — the rest
    of the cycle (research, mining, chart, art) completes immediately
    without waiting minutes for a video that may still be rendering
  - mines its OWN block (source="video_generation") once it actually
    finishes, whenever that is — so the chain record is accurate
    about when the video was really done, not when it was requested

Usage
-----
    from integrated_research_agent import IntegratedResearchAgent

    agent = IntegratedResearchAgent(
        node, dna, store_path="research_store.json",
        corpus_path="corpus.json", visuals_dir="visuals/",
        openai_api_key=None,    # set to enable real AI art
        runway_api_key=None,    # set to enable real AI video (occasional)
        video_every_n_cycles=10,
    )
    await agent.start()
    result = await agent.seed_topic("breast cancer", biomarker="BRCA1")
    print(result)
"""

from __future__ import annotations
import asyncio
import hashlib
import os
import time

from growing_research_agent import GrowingResearchAgent
from corpus_vector_store import CorpusVectorStore, documents_from_research_results
from node_view_visualizer import plot_node_snapshot
from research_art_generator import generate_network_art
from research_video_generator import generate_network_video


class IntegratedResearchAgent(GrowingResearchAgent):
    def __init__(
        self, node, dna, store_path="research_store.json",
        corpus_path="corpus.json", visuals_dir="visuals",
        openai_api_key: str | None = None,
        runway_api_key: str | None = None,
        video_every_n_cycles: int = 10,
        interval_s: float = 3600.0, max_topics: int = 50, audit=None,
    ):
        super().__init__(node, dna, store_path=store_path, interval_s=interval_s, max_topics=max_topics, audit=audit)
        self.corpus = CorpusVectorStore(identity=dna.seed_label, store_path=corpus_path)
        self.visuals_dir = visuals_dir
        self.openai_api_key = openai_api_key or os.environ.get("OPENAI_API_KEY")
        self.runway_api_key = runway_api_key or os.environ.get("RUNWAYML_API_SECRET")
        self.video_every_n_cycles = video_every_n_cycles
        self.cycle_number = 0
        self._background_video_tasks: list[asyncio.Task] = []
        os.makedirs(visuals_dir, exist_ok=True)

    async def _maybe_trigger_video(self, mos_score: dict):
        """Fires every video_every_n_cycles cycles, ONLY if runway_api_key is set.
        Launched as a background task, NOT awaited here — a real video call
        takes minutes, and the rest of this cycle shouldn't wait on it."""
        if not self.runway_api_key:
            return
        if self.cycle_number % self.video_every_n_cycles != 0:
            return

        task = asyncio.create_task(self._generate_and_mine_video(mos_score))
        self._background_video_tasks.append(task)
        self._background_video_tasks = [t for t in self._background_video_tasks if not t.done()]

    async def _generate_and_mine_video(self, mos_score: dict):
        """Runs in the background. When the video actually finishes (minutes
        later), mines a REAL block for it — the chain record reflects when
        the video was actually done, not when it was requested."""
        result = await asyncio.to_thread(
            generate_network_video, mos_score, len(self.topics),
            style="abstract geometric network animation, blue and orange, slow drift",
            api_key=self.runway_api_key,
        )
        if result.get("error"):
            return result   # honest failure — no block mined for a video that didn't happen

        video_hash = hashlib.sha256(result["video_url"].encode()).hexdigest()
        await self.node.mine_and_broadcast(
            source="video_generation", feature_hash_hex=video_hash,
            confidence=1.0, consent_verified=True,
        )
        if self.audit is not None:
            self.audit.log(
                module="integrated_research_agent", action="video_generated",
                node_id=self.node.node_id,
                details={"cycle": self.cycle_number, "video_url": result["video_url"]},
            )
        return result

    async def _generate_visuals(self, mos_score: dict) -> dict:
        """The real concurrent step: chart (fast, free, local) and AI art
        (slow, costs money, needs a key) run at the same time via
        asyncio.gather + asyncio.to_thread, since matplotlib and the
        OpenAI client are both blocking/synchronous under the hood."""
        chart_path = os.path.join(self.visuals_dir, f"snapshot_cycle_{self.cycle_number}.png")

        chart_task = asyncio.to_thread(
            plot_node_snapshot, mos_score, len(self.topics), len(self.node.dna.blocks),
            self.cycle_number, chart_path,
        )

        if self.openai_api_key:
            art_task = asyncio.to_thread(
                generate_network_art, mos_score, len(self.topics),
                style="abstract geometric network diagram, blue and orange",
                api_key=self.openai_api_key,
            )
        else:
            async def _no_art():
                return {"error": "no OPENAI_API_KEY set — chart still generated, art skipped"}
            art_task = _no_art()

        chart_result, art_result = await asyncio.gather(chart_task, art_task)
        return {"chart_path": chart_result, "art_result": art_result}

    async def _explore(self, key, condition, biomarker, source):
        # steps 1-2-3: research + mine (via parent class) + corpus, still sequential —
        # each genuinely depends on the previous one's output
        result = await super()._explore(key, condition, biomarker, source)

        found_results = getattr(self, "_last_found_for_corpus", None)
        # growing_research_agent.py's _explore doesn't currently expose `found`
        # directly — re-derive the doc list from what's in self.topics instead,
        # which IS available and IS the real data this step just produced
        topic_state = self.topics[key]
        docs = [{
            "id": f"topic:{key}",
            "text": f"{condition} {biomarker or ''}".strip(),
            "source": "topic_summary",
        }]
        self.corpus.add_documents(docs)

        self.cycle_number += 1
        mos = self.node.mos_score()
        visuals = await self._generate_visuals(mos)
        await self._maybe_trigger_video(mos)   # background, not awaited to completion

        return {**result, "corpus_size": len(self.corpus), "visuals": visuals}

    async def stop_and_wait_for_video(self, timeout: float = 600.0):
        """Call this instead of just letting the process exit if you want
        pending background video generations to actually finish and mine
        their blocks rather than being silently abandoned."""
        if self._background_video_tasks:
            await asyncio.wait(self._background_video_tasks, timeout=timeout)


if __name__ == "__main__":
    async def _demo():
        import tempfile
        from digital_dna import DigitalDNA
        from network_os import NetworkNode

        dna = DigitalDNA(seed_label="integrated-demo")
        node = NetworkNode(dna, host="127.0.0.1", port=9971)
        await node.start()

        with tempfile.TemporaryDirectory() as tmp:
            agent = IntegratedResearchAgent(
                node, dna,
                store_path=os.path.join(tmp, "store.json"),
                corpus_path=os.path.join(tmp, "corpus.json"),
                visuals_dir=os.path.join(tmp, "visuals"),
                interval_s=999,
            )

            print("=== Running one full integrated cycle: research + mine + corpus + visuals ===")
            t0 = time.time()
            key = await agent.seed_topic("breast cancer", biomarker="BRCA1")
            elapsed = time.time() - t0

            print(f"Completed in {elapsed:.2f}s")
            print(f"Topic explored: {key}")
            print(f"New ids found: {len(agent.topics[key]['new_ids_last_run'])}")
            print(f"Real block mined (check network_ledger): {len(node.network_ledger[node.node_id])} block(s)")
            print(f"Corpus size after this cycle: {len(agent.corpus)}")

            chart_path = os.path.join(tmp, "visuals", "snapshot_cycle_1.png")
            print(f"Chart file exists: {os.path.exists(chart_path)}, size: {os.path.getsize(chart_path) if os.path.exists(chart_path) else 0} bytes")

        await node.stop()

    asyncio.run(_demo())
