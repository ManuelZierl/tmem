from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(slots=True)
class HistoryEntry:
    id: int
    command: str
    cwd: str
    exit_code: Optional[int]
    started_at_ms: Optional[int]
    finished_at_ms: int
    duration_ms: Optional[int]
    hostname: str
    session_id: str
    shell: str


@dataclass(slots=True)
class MemoryStep:
    id: int
    memory_id: int
    position: int
    command_template: str


@dataclass(slots=True)
class Memory:
    id: int
    name: str
    description: str
    stop_on_error: bool
    created_at_ms: int
    updated_at_ms: int
    last_run_at_ms: Optional[int]
    run_count: int
    shell: str = "bash"
    scope_cwd: str = ""
    steps: list[MemoryStep] = field(default_factory=list)

    @property
    def is_group(self) -> bool:
        return len(self.steps) > 1

    @property
    def is_global(self) -> bool:
        return not self.scope_cwd


@dataclass(slots=True)
class ParameterDefinition:
    memory_id: int
    name: str
    default_value: Optional[str]
    position: int
