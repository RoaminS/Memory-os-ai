"""Operator-approved project IDs. Hints never authorize a merge or a read scope."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class ProjectError(ValueError):
    pass


class ProjectScope(str):
    """Canonical string with an explicit, immutable read set; ordinary equality stays ordinary."""
    def __new__(cls, canonical: str, labels: tuple[str, ...]):
        value = super().__new__(cls, canonical)
        value.labels = labels
        return value


def matches(label: str | None, scope: str | None) -> bool:
    return scope is None or label in getattr(scope, "labels", (scope,))


class Context(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,79}$")
    root: str = Field(min_length=1, max_length=4096)
    git_root_hint: str | None = Field(None, max_length=4096)
    remote_hint: str | None = Field(None, max_length=4096)

    @model_validator(mode="after")
    def absolute_root(self):
        if not Path(self.root).is_absolute() or str(Path(self.root)) != self.root or ".." in Path(self.root).parts:
            raise ValueError("context root must be an absolute normalized path")
        return self


class Project(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^prj_[a-z0-9][a-z0-9._-]{0,75}$")
    aliases: tuple[str, ...] = ()
    contexts: tuple[Context, ...] = ()

    @model_validator(mode="after")
    def labels(self):
        for alias in self.aliases:
            if not alias or alias != alias.strip() or len(alias) > 200 or alias == "*" or re.search(r"[\x00-\x1f\x7f]", alias):
                raise ValueError("invalid project alias")
        return self


class RegistryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: StrictInt = 1
    projects: tuple[Project, ...]

    @model_validator(mode="after")
    def unique(self):
        if self.schema_version != 1:
            raise ValueError("unsupported project registry schema")
        labels, contexts, roots = set(), set(), set()
        for project in self.projects:
            for label in (project.id, *project.aliases):
                if label in labels:
                    raise ValueError("project registry label collision")
                labels.add(label)
            for context in project.contexts:
                if context.id in contexts or context.root in roots:
                    raise ValueError("project registry context collision")
                contexts.add(context.id)
                roots.add(context.root)
        return self


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key in project registry")
        result[key] = value
    return result


class ProjectRegistry:
    def __init__(self, document: RegistryDocument | None = None, digest: str | None = None):
        self.document, self.sha256 = document, digest
        self._labels = {label: project for project in document.projects for label in (project.id, *project.aliases)} if document else {}

    @classmethod
    def load(cls, path: str | None):
        if path is None:
            return cls()
        # Fixed snapshot per engine lifetime: no mutable identity during a receipt.
        fd = os.open(Path(path).expanduser(), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise ProjectError("project_registry_invalid: owned regular file, no group/other write required")
            raw = source.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ProjectError("project_registry_invalid: exceeds 1 MiB")
        doc = RegistryDocument.model_validate(json.loads(raw, object_pairs_hook=_unique_pairs))
        return cls(doc, hashlib.sha256(raw).hexdigest())

    def resolve(self, label: str, *, read: bool = False) -> str:
        if self.document is None:
            return label
        project = self._labels.get(label)
        if project is None:
            raise ProjectError("project_unknown: approved project ID or alias required; nothing sent across projects")
        return ProjectScope(project.id, (project.id, *project.aliases)) if read else project.id

    def select_context(self, cwd: str) -> dict:
        """Exact approved root only; clients may walk parents locally, never infer from a remote."""
        if self.document:
            for project in self.document.projects:
                for context in project.contexts:
                    if cwd == context.root:
                        return {"project_id": project.id, "context_id": context.id,
                                "registry_sha256": self.sha256}
        raise ProjectError("project_unknown: context not approved")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Validate a registry without opening any memory store")
    parser.add_argument("--registry", required=True)
    parser.add_argument("--root", help="Exact approved workspace root (no Git inference)")
    args = parser.parse_args()
    registry = ProjectRegistry.load(args.registry)
    result = registry.select_context(args.root) if args.root else {"valid": True, "sha256": registry.sha256, "projects": len(registry.document.projects)}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
