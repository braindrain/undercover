"""The UnderCover Agent's web service, its entry point beside the backend: the
backend asks it to work a claim, and to carry one on after a sign-off."""

from fastapi import BackgroundTasks, FastAPI
from pydantic import BaseModel

from agent import agents

app = FastAPI(title="UnderCover Agent")


class Accepted(BaseModel):
    claim_id: str
    status: str


@app.get("/health", operation_id="health")
def health() -> dict[str, str]:
    """Check that the agent's service is running."""
    return {"status": "ok"}


@app.post("/claims/{claim_id}/work", operation_id="work_claim", status_code=202)
def work_claim(claim_id: str, background: BackgroundTasks) -> Accepted:
    """Work a waiting claim from a clean start, in the background."""
    background.add_task(agents.work_claim, claim_id)
    return Accepted(claim_id=claim_id, status="accepted")


@app.post("/claims/{claim_id}/carry-on", operation_id="carry_on_claim", status_code=202)
def carry_on_claim(claim_id: str, background: BackgroundTasks) -> Accepted:
    """Carry on a claim in progress from where it got to, in the background."""
    background.add_task(agents.carry_on, claim_id)
    return Accepted(claim_id=claim_id, status="accepted")
