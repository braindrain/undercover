"""Run the UnderCover Agent.

uv run -m agent            works every waiting claim in the queue
uv run -m agent --cap 5    works at most five claims and leaves the rest waiting
uv run -m agent CLM-1001   works one claim
"""

import argparse
from uuid import uuid4

from agent import main, queue, tools

parser = argparse.ArgumentParser(prog="agent", description="Run the UnderCover Agent.")
parser.add_argument("claim_id", nargs="?", help="work this one claim")
parser.add_argument("--cap", type=int, help="the most claims to work in the run")
args = parser.parse_args()

if args.claim_id:
    print(main.run(args.claim_id, str(uuid4())))
else:
    cap = args.cap or len(tools.waiting_claims())
    lines = queue.run_queue(cap, str(uuid4())) if cap else ["No claims are waiting."]
    for line in lines:
        print(line, flush=True)
