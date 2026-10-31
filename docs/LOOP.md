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
uv run python scripts/fetch_lgm50_data.py            # the Chen 2020 LG M50 records, 1.8 MB, checked by sha256
uv run translucent start fastcharge --data-root ./lab --person Ana --serve
```

`start` creates the ledger `lab`, imports the records with their failing 1.5C four-step protocol, and opens the
campaign: charge the LG M50 from 10 to 80 % faster while the anode potential stays at or above +10 mV and the cell at
or below 45 degC, in at most 8 computational experiments. Running it again finds the same ledger, dataset and
campaign and writes nothing. `--serve` then serves the data root; without it, run `translucent serve --data-root
./lab` yourself.

The planner is chosen with `--planner`:

| Planner | Who proposes | Cost |
|---|---|---|
| `bo` (default) | the adapter's Bayesian optimisation | none |
| `external` | your own agent, over MCP or HTTP | yours |
| `muse` | Muse Wonder on the meta profile, $1.50 model budget | paid: needs `--live` and `TRANSLUCENT_ADMIN_TOKEN` |

Each request waits for a person's approval in the web app; `--auto-approve` lets the campaign's computational
requests run without one (0 dollars and 300 s each, up to the experiment budget).

## What happens

1. **Registration.** The objective freezes the system: its version, the digest of the adapter's code and the solver
   it names. The failing baseline is re-run on the solver at once (`baseline_simulation`), so the candidate and the
   baseline are compared on the same basis.
2. **Proposal.** A request names an action, its parameters, the reasons, the expected effect and a prediction for
   each quantity. The adapter checks the parameters against its bounds before the request is written. A pending
   request with the same configuration is returned instead of a second one.
3. **Approval.** A person approves a content hash: the exact version they read. If the request was revised since,
   the approval is refused (409) and names the version to review. An agent can never approve, reject, revise, amend
   or submit a result.
4. **Execution.** Computational requests run in a child process that receives no keys or tokens, once: a claim lock
   and the ledger's own checks keep a request to one execution, also across a server restart. A request whose
   system code or solver has changed since registration is not run. A run past its wall-time limit, or whose output
   is in the wrong unit, becomes a recorded failure.
5. **Physical results.** A person uploads the table and scalars of a physical test, in their own units. The upload
   is refused, and the refusal recorded, when the executed configuration is outside the approved one's tolerance,
   a unit cannot be converted, the specimen is not the requested one or was destroyed by an earlier test, or the
   version is not the approved one. Results days after approval, after a restart, are accepted.
6. **Comparison.** Each result is compared with the baseline by `loop-compare/2`:
   - the target must improve by more than its margin, with both values' uncertainties counted;
   - every constraint must hold, also within its uncertainty;
   - a candidate that meets the limits a failing baseline breaks is better even when it is slower, and the time it
     costs is shown; the target's own absolute limit, if it has one, must still hold;
   - anything undecided by the uncertainties is inconclusive, never better.
7. **Conclusion.** The outcome is computed from the comparisons, not written by the agent: improved in simulation,
   not improved, or unresolved. A campaign is not concluded while a computation is queued or running.

Amendments (a new constraint or target) apply to later requests and re-compare earlier results under the new
objective; the record keeps both.

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
the ledger and again on export. A model planner reads the lessons of earlier campaigns on the same system.
