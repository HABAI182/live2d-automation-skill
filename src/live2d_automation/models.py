from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Status = Literal[
    "queued",
    "running",
    "verified",
    "review_required",
    "blocked",
    "failed",
    "rolled_back",
    "cancelled",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class Operation(StrictModel):
    kind: Literal[
        "create_parameter",
        "update_parameter",
        "delete_parameter",
        "move_parameter",
        "create_group",
        "update_group",
        "delete_group",
        "create_part",
        "create_warp",
        "create_rotation",
    ]
    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    name: str | None = Field(default=None, min_length=1, max_length=256)
    min: float | None = None
    default: float | None = None
    max: float | None = None
    group_id: str | None = None
    parent_id: str | None = None
    warp_div_h: int | None = Field(default=None, ge=2, le=100)
    warp_div_v: int | None = Field(default=None, ge=2, le=100)
    bezier_div_h: int | None = Field(default=None, ge=1, le=100)
    bezier_div_v: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def validate_fields(self):
        allowed = {"kind", "id"}
        if self.kind in {"create_parameter", "update_parameter"}:
            allowed |= {"name", "min", "default", "max"}
        if self.kind in {"create_parameter", "move_parameter"}:
            allowed.add("group_id")
        if self.kind in {
            "create_group",
            "update_group",
            "create_part",
            "create_warp",
            "create_rotation",
        }:
            allowed.add("name")
        if self.kind in {"create_part", "create_warp", "create_rotation"}:
            allowed.add("parent_id")
        if self.kind == "create_warp":
            allowed |= {"warp_div_h", "warp_div_v", "bezier_div_h", "bezier_div_v"}
        extra = {k for k in self.model_fields_set if k not in allowed}
        if extra:
            raise ValueError(f"Fields not supported for {self.kind}: {sorted(extra)}")
        if self.kind == "create_parameter" and any(
            v is None for v in (self.min, self.default, self.max)
        ):
            raise ValueError("create_parameter requires min, default and max")
        if self.min is not None and self.default is not None and self.max is not None:
            if not self.min <= self.default <= self.max:
                raise ValueError("min <= default <= max required")
        if self.kind == "move_parameter" and self.group_id is None:
            raise ValueError("move_parameter requires group_id (empty string means root)")
        return self


class Evidence(StrictModel):
    kind: str
    path: str
    sha256: str


class Result(StrictModel):
    job_id: str | None = None
    status: Status
    operation: str
    backend: str = "cubism_websocket"
    editor_version: str | None = None
    model_uid: str | None = None
    tool_call_succeeded: bool = False
    postconditions_passed: bool = False
    artifacts: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_action: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None = None


class Settings(StrictModel):
    port: int = Field(default=22033, ge=1, le=65535)
    timeout: float = Field(default=8, gt=0, le=60)
    state_dir: Path
    output_dir: Path


class AutomationError(Exception):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code, self.details = code, details

    def as_dict(self):
        return {"code": self.code, "message": str(self), **self.details}
