"""THROWAWAY canary test — do NOT merge.

Exists only to prove the CI `test` job goes red and blocks the deploy gate.
Delete this file / close its PR without merging once verified.
"""


def test_ci_gate_canary_is_intentionally_failing():
    assert False, "Intentional failure to verify the CI test gate blocks deploy."
