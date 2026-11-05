# The claims pilot

From the head of claims operations at UnderCover Travel Insurance.

## How claims work today

UnderCover sells travel insurance on three plans: Economy, Premium and First. Our three claims handlers work through a queue of claims by hand. For each claim, a handler reads the customer's story and documents and checks them against the policy, our policy wording and our claims manual. Then they approve the claim with a payout, decline it or refer it. A claim is referred when it needs someone's judgment or more information, as the claims manual explains.

## The pilot

We want an AI agent, the UnderCover Agent, to work the queue the way a handler does. A dispatch works the queue: every claim left in progress and every waiting claim, oldest submitted first, with up to N claim agents working at once. A handler chooses N, from 1 to 25, and starts a dispatch on the queue page. Only one dispatch or evaluation runs at a time. Choosing which claim goes next is a fixed rule, so no model does it. The agent can also be run on one named claim.

Each claim is worked by a claim agent of its own, from a clean start. For each claim it should:

- read the claim, the customer's policy and claim history
- find and read the sections of the policy wording and the claims manual the claim needs, the way a handler looks things up
- approve, decline or refer the claim
- work out the payout when it approves
- explain the decision in two or three sentences, along with the relevant section of the policy

When a claim agent approves a claim, the system pays it. A payout over the approval limit waits for a handler's sign-off: the claim agent stops and keeps everything it has worked out, and as soon as a handler signs off on the dashboard, it picks the claim up and pays it. Between the sign-off and the payment the claim shows as in progress, and the payment records who signed off and when.

If the agent is stopped partway through a claim, by a crash, a restart or Ctrl-C, the next dispatch carries on with that claim from where it got to, without counting a new attempt, and pays any claim signed off while it was away. A dispatch that was stopped shows as stopped.

A claim that hits a rate limit or a timeout waits and carries on as the same attempt, and one still hitting them after its waits stays in progress for the next dispatch. A claim that fails with any other error goes back to the queue and the same dispatch tries it once more.

An evaluation measures the agent against our handlers' decisions. A handler starts one on the Scoreboard page, on the evaluation set or the held-out set, whole or just its five `tiny` claims. It works its own copies of the claims, through the same dispatcher and claim agent. The copies never show in the queue, and nobody can sign them off.

Our goal is to handle claims straight through, as accurately as our handlers do.

## What the team needs to see

- The queue, with where each claim stands: waiting, in progress, waiting for sign-off, decided or left for a handler.
- The latest dispatch: each of its claims and where it stands, its model cost and time, and when it finishes a summary built from our records: how many claims were approved, declined and referred, each payout waiting for a sign-off, and each claim left for a handler or still in progress.
- Each claim in full, with the agent's decision and its reasons.
- What the agent is doing on a claim while it works, step by step, and the sections of the wording and the manual it read.
- The claim agent's instructions, read only.
- The tokens and cost of each model call, and the time the agent spends on each claim, leaving out any wait for a sign-off, with the dispatch or evaluation they happened in.
- A scoreboard of the evaluations, newest first, with `decision_accuracy`, `accuracy`, the cost of mistakes, the straight-through rate, model cost per claim, time per claim and the time for the whole run. Each opens to its claims, with the handler's decision and payout beside the agent's and the misses marked.
- Every claim's trace in Langfuse, tagged with its dispatch or evaluation, so that a whole run can be found together.

## Rules

- A payout over $1,000 needs a handler's sign-off. The agent can approve such a claim, but the payment waits until a handler signs off. The system must block the payment until then, even if the agent tries to pay.
- Decisions follow the policy wording and the claims manual.
- A long run will meet timeouts, model errors and failed steps. One bad claim never stops the run or leaves a claim stuck in progress. A claim that fails before it is decided goes back in the queue to be tried again, and a claim that fails twice is left for a handler. No claim is ever decided or paid twice. Before it finishes, the agent checks it has not missed a waiting claim within its cap.
- Our handlers' own decisions on these claims are in `data/evals/`. We use them to check the agent's work. The agent must never see them, and its instructions are never written from them.
- Amounts are in US dollars.

## Our records

Everything is in `data/`, described in `data/README.md`: the claims queue, the policies, the customers, the plans, the handlers, the policy wording and the claims manual. The held-out claims in `data/evals/` are for checking the agent later and stay out of the queue.

## Not for now

- No sign-in. Everyone who uses the screens sees everything.
- No real payments.
- No messages to customers.
