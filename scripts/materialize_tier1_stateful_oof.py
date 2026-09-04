"""Materialize one pre-registered ETF-local stateful Tier 1 OOF ledger.

The implementation remains in the original Momentum slice script while this
generic entrypoint makes the ETF-local contract explicit for every lineage.
"""

from materialize_tier1_stateful_momentum import main


if __name__ == "__main__":
    raise SystemExit(main())
