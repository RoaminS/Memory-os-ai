"""Operator-only maintenance policy, captured before the store is opened."""
from dataclasses import dataclass


class MaintenanceDenied(PermissionError):
    """A policy refusal, never an uncertain mutation result."""

    code = "maintenance_denied"


@dataclass(frozen=True)
class MaintenancePolicy:
    mode: str = "disabled"

    def __post_init__(self):
        if self.mode not in {"disabled", "preview", "apply"}:
            raise ValueError("maintenance_mode must be disabled, preview or apply")

    def require(self, dry_run: bool) -> None:
        if type(dry_run) is not bool:
            raise ValueError("dry_run must be a boolean")
        if self.mode == "disabled" or (not dry_run and self.mode != "apply"):
            raise MaintenanceDenied(
                "Maintenance refused by operator policy; no operation was performed. "
                "An operator must authorize maintenance outside the memory tools."
            )

    def status(self) -> dict:
        return {"mode": self.mode, "preview_allowed": self.mode != "disabled",
                "apply_allowed": self.mode == "apply", "default_dry_run": True}
