# Engineering campaigns

A campaign takes a team's records, a failing baseline, an objective and its limits, and works toward a better
configuration. An agent proposes each experiment with its reasons and a prediction. A person approves the exact
version they read. Computational experiments run on the server; physical ones wait for a person to upload the
result. Every result is compared with the baseline by a fixed rule, and the campaign ends with the baseline against
the best candidate, or as unresolved with the reasons. Everything is kept in an insert-only ledger that
`translucent verify` re-checks offline.

The routes are listed in [API.md](API.md#loop-campaigns-apiv1ledgersid); the same actions are MCP tools for an
agent ([AGENTS.md](AGENTS.md#loop-campaigns)). This page explains how a campaign behaves.

## Start one

```sh
uv sync --group dev --group solver                   # PyBaMM runs the experiments
uv run python scripts/fetch_lgm50_data.py            # the Chen 2020 LG M50 records, 1.8 MB, checked by sha256
uv run translucent start fastcharge --data-root ./lab --person Ana --serve
```

`start` creates the ledger `lab`, imports the records with their failing 1.5C four-step protocol, and opens the
campaign: "Find the fastest protocol meeting these constraints.", for a 10 to 80 % charge of the LG M50 with the anode
potential at or above +10 mV and the cell at or below 45 degC, in at most 8 computational experiments. Running it
again finds the same ledger, dataset and campaign and writes nothing. `--serve` then serves the data root; without it, run `translucent serve --data-root
./lab` yourself. `start` refuses to begin without the solver, since the baseline is re-run on it at registration.

The first `--serve` on a data root with no keys makes an engineer key for the `--person`, prints it once and keeps it
in `lab/team-key`. Everything else needs its own key, made with `translucent keys add` (roles and commands in
[API.md](API.md#keys-and-roles)): a viewer key for someone who only reads, a planner key for an agent over HTTP, an
admin key for paid runs. `TRANSLUCENT_TEAM_TOKEN` (at least 32 characters) also works as an engineer key. The web app
at `http://localhost:3000` may call the server; set `TRANSLUCENT_CORS_ORIGINS` for another origin. Start the web app
with `NEXT_PUBLIC_API_BASE=http://localhost:8000 bun run dev` in `web/` (`localhost`, not `127.0.0.1`: the sign-in
cookie is sent only to the same site as the page).

The planner is chosen with `--planner`:

| Planner | Who proposes | Cost |
|---|---|---|
| `bo` (default) | the adapter's Bayesian optimisation | none |
| `external` | your own agent, over MCP or over HTTP with a planner key; `start` prints the MCP command (with `--adopt`) | yours |
| `muse` | Muse Wonder on the meta profile, $1.50 model budget | paid: needs `--live` and an admin key (stored, or `TRANSLUCENT_ADMIN_TOKEN`) |

Each request waits for a person's approval in the web app; `--auto-approve` lets the campaign's computational
requests run without one (0 dollars and 300 s each, up to the experiment budget).

## In the browser

The web app asks for a key once ("Sign in to the team server") and sends it once to `POST /api/v1/session`. The
server answers with a session cookie that lasts 12 hours and survives a server restart; the page keeps only the
name and role it returned. An engineer or admin can approve, amend and conclude; a viewer reads. A planner key
cannot sign in to the browser. Revoking the key, or replacing it, ends its sessions. When a session ends, the page
asks again and keeps what was on it.

**New campaign** (`/new/`) opens a campaign without the CLI, in six parts, each filled from the server:

1. the ledger and the system;
2. the dataset and its baseline record, and whether the baseline is re-run on the solver at registration;
3. the objective, from the system's objective template: the fastest protocol meeting the constraints, or a hard
   target, and the constraints with their units;
4. the planner, from `GET .../planners`, Bayesian optimisation by default. A paid planner shows whether it is
   ready and why not; one that is not ready cannot be launched, and the server never puts another in its place;
5. the budgets and the approval policy;
6. a review, then launch, which opens the campaign.

## What happens

1. **Registration.** The objective freezes the system: its version, the digest of the adapter's code and the solver
   it names. The failing baseline is re-run on the solver at once (`baseline_simulation`), so the candidate and the
   baseline are compared on the same basis. For a fastest-feasible objective, if that run fails the constraints, the
   reference search (below) starts.
2. **Proposal.** A request names an action, its parameters, the reasons, the expected effect and a prediction for
   each quantity. The adapter checks the parameters against its bounds before the request is written. A pending
   request with the same configuration is returned instead of a second one.
3. **Approval.** A person approves a content hash: the exact version they read. If the request was revised since,
   the approval is refused (409) and names the version to review. An agent can never approve, reject, revise, amend
   or submit a result. The approver is the person whose key or browser session made the call.
4. **Execution.** Computational requests run in a child process that receives no keys or tokens, once: a claim lock
   and the ledger's own checks keep a request to one execution, also across a server restart. A request whose
   system code or solver has changed since registration is not run. A run past its wall-time limit, or whose output
   is in the wrong unit, becomes a recorded failure.
5. **Physical results.** A person uploads the table and scalars of a physical test, in their own units. The upload
   is refused, and the refusal recorded, when the executed configuration is outside the approved one's tolerance,
   a unit cannot be converted, the specimen is not the requested one or was destroyed by an earlier test, or the
   version is not the approved one. Results days after approval, after a restart, are accepted.
6. **Comparison.** A new campaign's objective has one of two kinds:
   - `fastest_feasible`: "Find the fastest protocol meeting these constraints." It has no target limit.
   - `hard_target`: "Meet <target> while meeting these constraints." The target's limit is the hard target. Neither
     the system nor the planner relaxes it; only a person's amendment changes it, and an amendment keeps the kind.

   Campaigns under `loop-compare/1` or `/2` have kind `legacy`. Each result is compared by `loop-compare/3`, which
   answers three questions separately:
   - **Feasibility:** does the result, and does the baseline, meet every constraint of the current objective
     version, within the values' uncertainties? Undecided is "unknown", never "meets". A result that never reaches a
     minimised target within its window fails.
   - **Speed:** compared only for a result that meets the constraints, and only against a reference that meets the
     same constraints. That is the baseline when it meets them, and then no reference search runs. Otherwise, for a
     fastest-feasible objective with a computational baseline, it is the best single-step constant-current protocol
     found by the system's reference search. For the fast-charge system that is a bisection on the C-rate, on the
     solver, on the 0.05C lattice, with at most 8 runs. It starts once the baseline run has finished and failed,
     runs as system requests by `policy:reference`, and does not count against the experiment budget. Its answer is
     a protocol that was run and met the constraints, called the best found by the reference search.
     If the run cap is reached first with no run meeting them, there is no reference. A failing baseline is never a
     speed reference. With no reference, speed is "not compared", with the reason. "Faster" means faster than the
     reference by more than the target's margin, which cannot be negative.
   - **Objective:** a hard target is met when every constraint holds and the target's limit holds within its
     uncertainty. A fastest-feasible objective stays open: no search proves a protocol the fastest possible.
7. **Conclusion.** The outcome is computed from the comparisons, not written by the agent: objective met, faster
   than the reference, feasible only (it meets the constraints and is not shown faster), no feasible result, or
   unresolved; each "in simulation" when the evidence is computational. The conclusion records the feasibility,
   speed and objective findings it rests on. A campaign is not concluded while a computation is queued or running,
   reference-search runs included.

Amendments (a new constraint or target) apply to later requests and re-compare earlier results under the new
objective; the record keeps both. When an amendment changes the target or the constraints, proposals still waiting
for approval that were made under an older version are withdrawn by the system ("objective amended to vN", a
recorded cancellation), so they free their share of the experiment budget and the planner proposes again under the
new version. Approved, queued and running requests are left alone, and an amendment that changes only a budget or a
label withdraws nothing. An amendment that changes the constraints starts a new reference search for that version,
and speed is never compared across constraint versions; one that changes only the target keeps the reference. An
amendment may also change the budget: an engineer may lower it, and raising it needs an admin key. The objective's
one-line summary follows the current version; its statement stays as registered.

Campaigns registered before `loop-compare/3` keep the rules they ran under (`loop-compare/1` or `/2`) and the words
those rules recorded: under `/2`, a result that meets the limits a failing baseline breaks is "better", even when it
is slower. Their view adds an assessment in the `/3` terms; for such a result it says "feasibility recovered, speed
not compared", because a `/2` campaign ran no reference search. Old exports still verify.

Uncertainty is stated in two parts. Solver tolerance is the solver's numerical error: the tolerances it ran with and
the bounds the system declares on its outputs. Model uncertainty is the model's error against a real cell, as the
system states it; the fast-charge system states it is not quantified (its voltage runs 70-160 mV above the Chen 2020
records), so every result in simulation needs confirming on a physical cell.

## Restarts and several writers

The server, any number of MCP sessions and the CLI can write one ledger at once. Every write takes an
`Idempotency-Key`; a retry, also after a restart, finds the original records. A request that was running when a
process died is recorded as abandoned and run again if its action is deterministic.

## Checking a campaign

```sh
uv run translucent verify ./lab/ledgers/lab                      # the whole ledger
curl -o c.loop.json localhost:8000/api/v1/ledgers/lab/campaigns/<cid>/export
uv run translucent verify c.loop.json                            # one campaign, without the ledger
```

Verify replays each record's admission against the ledger as it stood when it was written, recomputes every
measurement from the stored files and every comparison from its rule, and checks the hash chain. When the adapter
installed is not the code the campaign registered, the domain checks are skipped and the report says so.

## Learning from campaigns

`translucent learning-export out.jsonl --ledger ./lab/ledgers/lab` writes each campaign as an episode: the objective,
each proposal with its prediction, the result, and the comparison. Free text is scrubbed of secrets when it enters
the ledger and again on export. A model planner reads the lessons of earlier campaigns on the same system. This has
not been tested for any effect on engineering campaigns, and the one memory test (microscopy,
[evals/MEMORY.md](../evals/MEMORY.md)) found no gain.
