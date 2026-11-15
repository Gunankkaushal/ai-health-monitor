"""Roles, the local actor directory, and approval rules.

Identity is ASSERTED, not authenticated: the caller names itself with the ``X-Actor`` header and the
directory below maps that name to a role (standing in for identity-provider groups). This enforces
segregation of duties and approval rules inside the app, but it is not a security boundary. Plugging
in real SSO is a deployment task and is listed as an open item.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

ROLES: dict[str, dict[str, Any]] = {
    "viewer": {"label": "Viewer", "perms": {"view", "abort"}, "group": "health-monitor-viewers"},
    "author": {"label": "Experiment author", "perms": {"view", "author", "run", "abort"}, "group": "platform-eng"},
    "sre_approver": {"label": "SRE approver", "perms": {"view", "author", "run", "approve", "abort"}, "group": "sre-oncall"},
    "product_owner": {"label": "Product owner", "perms": {"view", "approve", "abort"}, "group": "product"},
    "tenant_admin": {"label": "Tenant admin",
                     "perms": {"view", "author", "run", "approve", "abort", "edit_policy", "admin"},
                     "group": "health-monitor-admins"},
    "auditor": {"label": "Auditor", "perms": {"view"}, "group": "qa-compliance"},
    "service": {"label": "CI service", "perms": {"view", "run", "abort"}, "group": "service-principals"},
}

PERMISSION_LABELS = {
    "view": "View", "author": "Author", "run": "Run (non-prod)", "approve": "Approve",
    "edit_policy": "Edit policy", "admin": "Administer", "abort": "Abort",
}

# Illustrative local directory. Names are fictional.
DIRECTORY: dict[str, str] = {
    "morgan.author": "author",
    "devon.sre": "sre_approver",
    "kai.owner": "product_owner",
    "jordan.admin": "tenant_admin",
    "taylor.admin": "tenant_admin",
    "casey.auditor": "auditor",
    "riley.viewer": "viewer",
    "ci-pipeline": "service",
}

APPROVAL_RULES: list[dict[str, str]] = [
    {"when": "Dev · low or medium risk · read path", "approvers": "none", "extra": "—"},
    {"when": "Dev · high risk or write path", "approvers": "1 SRE approver", "extra": "—"},
    {"when": "Staging · low or medium risk", "approvers": "none", "extra": "—"},
    {"when": "Staging · high risk", "approvers": "1 SRE approver", "extra": "—"},
    {"when": "Staging · write path", "approvers": "1 SRE + 1 product owner", "extra": "Synthetic patients only"},
    {"when": "Production · any risk", "approvers": "1 SRE + 1 product owner", "extra": "Change record required"},
    {"when": "Policy change", "approvers": "2 tenant admins", "extra": "Proposer cannot self-approve"},
    {"when": "Fault library or guardrail change", "approvers": "1 tenant admin", "extra": "Audited with reason"},
]

APPROVAL_TTL_HOURS = 24


class Actor:
    def __init__(self, name: str, role: str) -> None:
        self.name, self.role = name, role

    @property
    def perms(self) -> set[str]:
        return ROLES[self.role]["perms"]

    def can(self, perm: str) -> bool:
        return perm in self.perms

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "role": self.role, "role_label": ROLES[self.role]["label"],
                "permissions": sorted(self.perms)}


def actor_from_request(request: Request) -> Actor:
    name = (request.headers.get("X-Actor") or "").strip()
    if not name:
        return Actor("anonymous", "viewer")
    if name not in DIRECTORY:
        raise HTTPException(401, f"Unknown actor '{name}'. Known actors: {', '.join(DIRECTORY)}")
    return Actor(name, DIRECTORY[name])


def require(actor: Actor, perm: str) -> None:
    if not actor.can(perm):
        raise HTTPException(403, f"{actor.name} ({ROLES[actor.role]['label']}) is not allowed to "
                                 f"{PERMISSION_LABELS.get(perm, perm).lower()}")


def required_approvers(environment: str, risk: str, write_path: bool) -> tuple[list[str], bool]:
    """Roles whose approval a run needs, and whether a change record is required."""
    if environment == "production":
        return ["sre_approver", "product_owner"], True
    if environment == "staging":
        if write_path:
            return ["sre_approver", "product_owner"], False
        return (["sre_approver"] if risk == "high" else []), False
    return (["sre_approver"] if (risk == "high" or write_path) else []), False


def describe() -> dict[str, Any]:
    return {
        "roles": [{"key": k, "label": v["label"], "group": v["group"], "permissions": sorted(v["perms"])}
                  for k, v in ROLES.items()],
        "permission_labels": PERMISSION_LABELS,
        "directory": [{"name": n, "role": r, "role_label": ROLES[r]["label"]} for n, r in DIRECTORY.items()],
        "approval_rules": APPROVAL_RULES,
        "approval_ttl_hours": APPROVAL_TTL_HOURS,
        "identity_note": "Identity is asserted with the X-Actor header and mapped by a local directory. It enforces "
                         "segregation of duties inside the app but is not authentication.",
    }
