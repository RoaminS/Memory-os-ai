"""Operator-only maintenance policy, captured before the store is opened."""
from dataclasses import dataclass

READ_OPERATIONS = {"memory_status", "memory_request_status", "memory_session_brief", "memory_search",
                   "memory_get_context", "memory_timeline", "memory_block_get", "memory_list_documents",
                   "memory_search_occurrences"}
READ_OPERATIONS.update({'memory_task_get', 'memory_task_list', 'memory_task_preview'})


class MaintenanceDenied(PermissionError):
    """A policy refusal, never an uncertain mutation result."""

    code = "maintenance_denied"


class MaintenanceWindow(PermissionError):
    """Operator has frozen semantic writes; no receipt may be prepared."""

    code = "maintenance_window"


@dataclass(frozen=True)
class WritePolicy:
    mode: str = "read_write"

    def __post_init__(self):
        if self.mode not in {"read_write", "read_only"}:
            raise ValueError("write_mode must be read_write or read_only")

    def require(self):
        if self.mode == "read_only":
            raise MaintenanceWindow("Operator maintenance window: mutation not started; reads remain available")

    def status(self):
        return {"mode": self.mode, "mutations_allowed": self.mode == "read_write"}


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
