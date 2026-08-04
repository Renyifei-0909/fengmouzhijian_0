#!/usr/bin/env python3
"""Trusted POSIX resource-limit shim for the development predictor process."""

from __future__ import annotations

import argparse
import os
import resource
from typing import TypeAlias


ResourceLimit: TypeAlias = tuple[int, int]


def _minimum_limit(*values: int) -> int:
    """Return the tightest limit while treating RLIM_INFINITY as unbounded."""

    finite_values = [value for value in values if value != resource.RLIM_INFINITY]
    return min(finite_values) if finite_values else resource.RLIM_INFINITY


def tightened_resource_limit(inherited: ResourceLimit, requested: ResourceLimit) -> ResourceLimit:
    """Clamp a requested limit without raising either inherited component.

    A supervisor may inherit a hard limit below its normal target (for example,
    a container with fewer than 256 file descriptors).  Passing the target
    directly to ``setrlimit`` would then attempt to raise the hard limit and
    fail before the predictor starts.  Keeping the inherited soft limit when it
    is already tighter also makes this helper strictly monotonic.
    """

    requested_soft, requested_hard = requested
    for value in requested:
        if value < 0 and value != resource.RLIM_INFINITY:
            raise ValueError("requested resource limits must be non-negative or RLIM_INFINITY")
    if requested_hard != resource.RLIM_INFINITY and (
        requested_soft == resource.RLIM_INFINITY or requested_soft > requested_hard
    ):
        raise ValueError("requested soft resource limit cannot exceed the hard limit")

    inherited_soft, inherited_hard = inherited
    target_hard = _minimum_limit(inherited_hard, requested_hard)
    target_soft = _minimum_limit(inherited_soft, requested_soft, target_hard)
    return target_soft, target_hard


def _tighten_resource_limit(resource_kind: int, requested: ResourceLimit) -> None:
    inherited = resource.getrlimit(resource_kind)
    target = tightened_resource_limit(inherited, requested)
    if target != inherited:
        resource.setrlimit(resource_kind, target)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-file-bytes", type=int, required=True)
    parser.add_argument("--cpu-seconds", type=int, required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--entrypoint", required=True)
    parser.add_argument("predictor_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    predictor_args = list(args.predictor_args)
    if predictor_args and predictor_args[0] == "--":
        predictor_args.pop(0)
    os.umask(0o077)
    _tighten_resource_limit(resource.RLIMIT_FSIZE, (args.max_file_bytes, args.max_file_bytes))
    _tighten_resource_limit(resource.RLIMIT_NOFILE, (256, 256))
    cpu_soft = max(1, args.cpu_seconds)
    _tighten_resource_limit(resource.RLIMIT_CPU, (cpu_soft, cpu_soft + 1))
    argv = [args.python, "-I", "-B", "-X", "utf8", args.entrypoint, *predictor_args]
    os.execve(args.python, argv, dict(os.environ))
    return 127  # pragma: no cover - execve either replaces the process or raises


if __name__ == "__main__":
    raise SystemExit(main())
