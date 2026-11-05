"""Run the UnderCover Agent on one claim: uv run -m agent CLM-1001"""

import argparse

from dotenv import load_dotenv

from agent import agents, harness

load_dotenv(override=True)

parser = argparse.ArgumentParser(prog="agent", description="Run the UnderCover Agent.")
parser.add_argument("claim_id", help="work this one claim")
args = parser.parse_args()

print(agents.work_claim(args.claim_id))
harness.flush_traces()
