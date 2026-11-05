"""The UnderCover Agent's web service, its entry point beside the backend: the
backend asks it to work a claim, to carry one on after a sign-off, and to start a
dispatch. It runs one dispatch at a time, in a thread of its own."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from functools import partial
from threading import Lock, Thread
from time import sleep

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel

from agent import agents, api, dispatcher

BACKEND_WAIT = 60
RUN_GOING = "A dispatch or an evaluation is already running."

run_slot = Lock()


def stop_old_runs() -> None:
    """Mark any run left from before this start as stopped, waiting for the
    backend, which `npm run dev` starts at the same time."""
    for _ in range(BACKEND_WAIT):
        try:
            api.stop_running_runs()
        except httpx.TransportError:
            sleep(1)
        else:
            return
    raise RuntimeError(f"The backend didn't start within {BACKEND_WAIT} seconds.")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    stop_old_runs()
    yield


app = FastAPI(title="UnderCover Agent", lifespan=lifespan)


class Accepted(BaseModel):
    claim_id: str
    status: str


class Started(BaseModel):
    status: str


class NewDispatch(BaseModel):
    agents_at_once: int


def in_background(work: Callable[[], None]) -> None:
    """Do a run's work, then free the run slot, whatever happens."""
    try:
        work()
    finally:
        run_slot.release()


def start_run(work: Callable[[], None]) -> Started:
    """Take the run slot and do the work in a thread of its own, or refuse. The
    thread dies with the process, so that a stop holds up neither a reload nor a
    shutdown, and the next start marks the run as stopped."""
    if not run_slot.acquire(blocking=False):
        raise HTTPException(409, RUN_GOING)
    Thread(target=in_background, args=(work,), daemon=True).start()
    return Started(status="accepted")


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


@app.post("/dispatches", operation_id="start_dispatch", status_code=202)
def start_dispatch(new_dispatch: NewDispatch) -> Started:
    """Start a dispatch in the background, or refuse while a run is going."""
    return start_run(partial(dispatcher.dispatch, new_dispatch.agents_at_once))
