"""Opt-in dispatch for the new record kind, preserving the original checker bytes.

Old setup receipts identify gate.py by its content hash and print that hash during
verification. Keep that implementation byte-identical; this adapter delegates old
claims to the original function and routes only intervention claims to new code.
"""

from functools import wraps


def install():
    """Activate dispatch explicitly at each public entrypoint and return its hook.

    Entry modules call this before loading a campaign. Repeated calls preserve
    the installed function, while the captured original handles every old kind.
    No intervention controller import is needed until a new claim is reviewed.
    """
    from translucent import gate

    if getattr(gate.review, "_intervention_dispatch", False):
        return gate.review
    original = gate.review

    @wraps(original)
    def review(ledger, claim_id):
        if ledger.kind_of(claim_id) == "intervention":
            from translucent.intervention_bench import review_claim

            return review_claim(ledger, claim_id)
        return original(ledger, claim_id)

    review._intervention_dispatch = True
    gate.review = review
    return review
