"""What a store is, and how its phase follows from what the cluster shows.

Nothing here talks to Kubernetes, so the rules can be tested on their own.

A store's record lives on its namespace, as labels and annotations, not in a
database of our own. The provisioner can restart at any moment: it lists the
store namespaces, reads each record, looks at the Pods and the setup Job, and
carries on from there.

    Provisioning ──▶ Ready
         │
         └──▶ Failed      (setup Job failed, or not ready before the deadline)

    any phase ──▶ Deleting ──▶ gone (the namespace and everything in it)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

Phase = Literal["Provisioning", "Ready", "Failed", "Deleting"]

LABEL_STORE = "urumi.io/store"
LABEL_SLUG = "urumi.io/store-slug"
LABEL_OWNER = "urumi.io/owner"
ANN_NAME = "urumi.io/display-name"
ANN_OWNER_EMAIL = "urumi.io/owner-email"
ANN_PHASE = "urumi.io/phase"
ANN_REASON = "urumi.io/reason"
ANN_CREATED = "urumi.io/created-at"
ANN_DEADLINE = "urumi.io/deadline"
ANN_READY_AT = "urumi.io/ready-at"
ANN_ATTEMPTS = "urumi.io/helm-attempts"

# Lowercase letters, digits and single hyphens: it becomes a namespace, a Helm
# release, a hostname and the gateway's tool prefix, so it must suit all four.
SLUG_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
MAX_SLUG = 20
RESERVED = {"app", "shop", "www", "api", "mcp", "admin", "urumi", "store"}


def check_slug(slug: str) -> str | None:
    """Return why a slug is unusable, or None if it is fine."""
    if not 2 <= len(slug) <= MAX_SLUG:
        return f"Use 2 to {MAX_SLUG} characters."
    if not SLUG_RE.match(slug):
        return "Use lowercase letters, digits and single hyphens, starting with a letter."
    if slug in RESERVED:
        return f"{slug!r} is reserved."
    return None


def namespace_for(slug: str) -> str:
    return f"store-{slug}"


def now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


@dataclass
class Observed:
    """The parts of a store the provisioner checks, as seen in the cluster right now."""

    setup_done: bool = False
    setup_failed: str | None = None  # why the setup Job failed, if it did
    database_ready: bool = False
    wordpress_ready: bool = False
    mcp_ready: bool = False
    # Plain reasons for anything not ready yet, e.g. "wordpress: Pod pending (Insufficient memory)".
    waiting: list[str] = field(default_factory=list)

    @property
    def all_ready(self) -> bool:
        return self.setup_done and self.database_ready and self.wordpress_ready and self.mcp_ready

    def missing(self) -> list[str]:
        parts = [("database", self.database_ready), ("WordPress", self.wordpress_ready),
                 ("store setup", self.setup_done), ("MCP server", self.mcp_ready)]
        return [name for name, ok in parts if not ok]


def next_phase(phase: Phase, now: datetime, deadline: datetime | None, seen: Observed) -> tuple[Phase, str]:
    """The phase and reason to record for a store, given what the cluster shows."""
    if phase == "Provisioning":
        if seen.setup_failed:
            return "Failed", f"Store setup failed: {seen.setup_failed}"
        if seen.all_ready:
            return "Ready", ""
        waiting = "; ".join(seen.waiting) or ", ".join(seen.missing())
        if deadline is not None and now >= deadline:
            return "Failed", f"Not ready before the provisioning deadline. Still waiting for: {waiting}"
        return "Provisioning", f"Waiting for: {waiting}"
    if phase == "Ready":
        if seen.database_ready and seen.wordpress_ready and seen.mcp_ready:
            return "Ready", ""
        return "Ready", "Degraded: " + ("; ".join(seen.waiting) or ", ".join(seen.missing()))
    return phase, ""
