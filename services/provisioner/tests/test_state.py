from datetime import timedelta

from provisioner import state
from provisioner.state import Observed, next_phase


def ready() -> Observed:
    return Observed(setup_done=True, database_ready=True, wordpress_ready=True, mcp_ready=True)


def test_slug_rules():
    assert state.check_slug("north") is None
    assert state.check_slug("north-2") is None
    assert state.check_slug("North") is not None
    assert state.check_slug("2north") is not None
    assert state.check_slug("a--b") is not None
    assert state.check_slug("shop") is not None  # reserved: the first store's hostname
    assert state.check_slug("x" * 21) is not None


def test_provisioning_becomes_ready_when_everything_is():
    now = state.now_utc()
    assert next_phase("Provisioning", now, now + timedelta(minutes=5), ready()) == ("Ready", "")


def test_provisioning_says_what_it_waits_for():
    now = state.now_utc()
    seen = Observed(database_ready=True, waiting=["wordpress (not scheduled (Insufficient memory))", "store-setup"])
    phase, reason = next_phase("Provisioning", now, now + timedelta(minutes=5), seen)
    assert phase == "Provisioning"
    assert "Insufficient memory" in reason


def test_deadline_fails_with_the_reason():
    now = state.now_utc()
    seen = Observed(database_ready=True, wordpress_ready=True, waiting=["store-setup"])
    phase, reason = next_phase("Provisioning", now, now - timedelta(seconds=1), seen)
    assert phase == "Failed"
    assert "deadline" in reason and "store-setup" in reason


def test_failed_setup_job_fails_the_store():
    now = state.now_utc()
    seen = Observed(database_ready=True, setup_failed="BackoffLimitExceeded: Job has reached the specified backoff limit")
    phase, reason = next_phase("Provisioning", now, now + timedelta(minutes=5), seen)
    assert phase == "Failed" and "BackoffLimitExceeded" in reason


def test_ready_store_reports_degraded_but_stays_ready():
    now = state.now_utc()
    seen = ready()
    seen.mcp_ready = False
    seen.waiting = ["woo-mcp (CrashLoopBackOff)"]
    assert next_phase("Ready", now, None, seen) == ("Ready", "Degraded: woo-mcp (CrashLoopBackOff)")


def test_iso_round_trip():
    now = state.now_utc()
    assert state.parse_iso(state.iso(now)) == now
    assert state.parse_iso("garbage") is None
