import asyncio
import json
import subprocess
import sys

from digital_dna import DigitalDNA
from network_os import NetworkNode


async def main():
    dna = DigitalDNA(seed_label="python-interop-node")
    node = NetworkNode(dna, host="127.0.0.1", port=9501)
    await node.start()
    print("[py] node listening on 127.0.0.1:9501")

    proc = await asyncio.create_subprocess_exec(
        "node", "interop_client.js", "127.0.0.1", "9501",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    print(stdout.decode())

    await asyncio.sleep(0.3)

    js_node_id = None
    for pid, blocks in node.network_ledger.items():
        for b in blocks:
            if b.get("source") == "js_interop_node":
                js_node_id = pid

    if js_node_id:
        print(f"[py] SUCCESS: received a validly-signed block from JS node (node_id={js_node_id})")
        print(json.dumps(node.network_ledger[js_node_id], indent=2))
    else:
        print("[py] FAILURE: no block from the JS node landed in the ledger")
        sys.exit(1)

    await node.stop()


if __name__ == "__main__":
    asyncio.run(main())
