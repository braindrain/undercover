"""Run the UnderCover Agent.

uv run -m agent            carries on any claim in progress, then works every
                           waiting claim in the queue
uv run -m agent --cap 5    works at most five claims and leaves the rest waiting
uv run -m agent CLM-1001   works one claim
"""

import argparse
from itertools import chain

from dotenv import load_dotenv

from agent import agents, api, harness

load_dotenv(override=True)

parser = argparse.ArgumentParser(prog="agent", description="Run the UnderCover Agent.")
parser.add_argument("claim_id", nargs="?", help="work this one claim")
parser.add_argument("--cap", type=int, help="the most claims to work in the run")
args = parser.parse_args()

if args.claim_id:
    print(agents.work_claim(args.claim_id))
else:
    cap = args.cap or len(api.waiting_claims())
    lines = (
        agents.run_queue(cap)
        if cap
        else chain(agents.carry_on_stopped(), ["No claims are waiting."])
    )
    for line in lines:
        print(line, flush=True)
harness.flush_traces()
