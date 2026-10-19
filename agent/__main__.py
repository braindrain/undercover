"""Run the UnderCover Agent on one claim, or on every waiting claim in the queue.

uv run -m agent CLM-1001
uv run -m agent
"""

import sys
from uuid import uuid4

from agent.main import run, run_queue, waiting_claims

if len(sys.argv) > 1:
    print(run(sys.argv[1], str(uuid4())))
else:
    claim_ids = waiting_claims()
    print(f"{len(claim_ids)} claims waiting")
    for claim_id, reply in run_queue(claim_ids):
        print(f"{claim_id}: {reply}")
