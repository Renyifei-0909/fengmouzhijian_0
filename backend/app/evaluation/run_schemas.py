from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, field_validator

from .schemas import ID_PATTERN, SHA256_PATTERN, StrictModel


class RunArtifact(StrictModel):
    path: str = Field(min_length=1, max_length=500)
    sha256: str = Field(pattern=SHA256_PATTERN)
    size_bytes: int = Field(gt=0, le=2 * 1024 * 1024 * 1024)

    @field_validator("path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        pure = PurePosixPath(value)
        if (
            "\x00" in value
            or "\\" in value
            or pure.is_absolute()
            or pure.as_posix() != value
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise ValueError("run artifact paths must use normalized relative POSIX syntax")
        return value


class DevelopmentRunPlan(StrictModel):
    schema_version: Literal["evaluation.run-plan.v0"]
    run_id: str = Field(pattern=ID_PATTERN)
    mode: Literal["development"]
    runner: Literal["local_process"]
    formal_requested: Literal[False]
    split: Literal["train", "validation"]
    dataset_manifest: RunArtifact
    model_statement: RunArtifact
    model_artifact: RunArtifact
    entrypoint: RunArtifact
    training_data_manifest: RunArtifact
    evaluator_source_sha256: str = Field(pattern=SHA256_PATTERN)
    random_seed: int = Field(ge=0, le=2**63 - 1)
    timeout_seconds: int = Field(ge=1, le=300)
    max_predictions_bytes: int = Field(ge=1024, le=64 * 1024 * 1024)
    max_log_bytes: int = Field(ge=1024, le=16 * 1024 * 1024)
    network_policy: Literal["uncontrolled_development"]
    environment_policy: Literal["minimal_allowlist"]


class TrainingDataManifest(StrictModel):
    schema_version: Literal["evaluation.training-manifest.v0"]
    model_artifact_sha256: str = Field(pattern=SHA256_PATTERN)


__all__ = ["DevelopmentRunPlan", "RunArtifact", "TrainingDataManifest"]
