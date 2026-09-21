"""Read-only Rescue Plane contracts and maintenance gates.

The Rescue Plane is the small, boring path that should remain useful when the
normal Guardian control plane is degraded.  This module deliberately contains
no subprocess, Docker, systemd, cgroup, filesystem, or network access.  It
only validates injected evidence and returns a fail-closed decision for a
caller that may later perform a separately-authorized operation.

The default is observation-only.  ``check_rollback_preconditions`` is a
preflight check, not a rollback executor; even a successful result never
changes host or service state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


SCHEMA = "guardian.rescue-plane.v1"

_DEPENDENCY_STATUSES = {"ready", "degraded", "failed", "missing", "stopped", "unknown"}
_HEALTHY_STATUSES = {"ready"}
_RUNTIME_STATUSES = {"ready", "maintenance", "degraded", "failed", "unknown"}


class RescuePlaneError(ValueError):
    """Raised when a Rescue Plane contract cannot be safely interpreted."""


def _non_empty(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RescuePlaneError(f"{field_name}:non_empty_string_required")
    return value.strip()


def _unique_strings(values: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise RescuePlaneError(f"{field_name}:string_sequence_required")
    result = tuple(_non_empty(value, field_name) for value in values)
    if len(set(result)) != len(result):
        raise RescuePlaneError(f"{field_name}:duplicates_not_allowed")
    return result


@dataclass(frozen=True)
class DependencySpec:
    """One node in the minimum dependency graph."""

    name: str
    kind: str = "service"
    required: bool = True

    def __post_init__(self) -> None:
        _non_empty(self.name, "dependency.name")
        _non_empty(self.kind, "dependency.kind")
        if type(self.required) is not bool:
            raise RescuePlaneError("dependency.required:boolean_required")


@dataclass(frozen=True)
class DependencyEdge:
    """A dependency relation from ``dependent`` to its prerequisite."""

    dependent: str
    prerequisite: str
    required: bool = True

    def __post_init__(self) -> None:
        _non_empty(self.dependent, "dependency_edge.dependent")
        _non_empty(self.prerequisite, "dependency_edge.prerequisite")
        if self.dependent == self.prerequisite:
            raise RescuePlaneError("dependency_edge:self_reference")
        if type(self.required) is not bool:
            raise RescuePlaneError("dependency_edge.required:boolean_required")


@dataclass(frozen=True)
class DependencyState:
    """Read-only observation for one dependency node."""

    present: bool
    status: str
    detail: str = ""

    def __post_init__(self) -> None:
        if type(self.present) is not bool:
            raise RescuePlaneError("dependency_state.present:boolean_required")
        normalized = _non_empty(self.status, "dependency_state.status").lower()
        if normalized not in _DEPENDENCY_STATUSES:
            raise RescuePlaneError(f"dependency_state.status:unsupported:{normalized}")
        object.__setattr__(self, "status", normalized)
        if not isinstance(self.detail, str):
            raise RescuePlaneError("dependency_state.detail:string_required")


@dataclass(frozen=True)
class DependencyGraph:
    """Validated minimum graph used by maintenance and rollback gates."""

    nodes: tuple[DependencySpec, ...]
    edges: tuple[DependencyEdge, ...] = ()

    def __post_init__(self) -> None:
        nodes = tuple(self.nodes)
        edges = tuple(self.edges)
        if any(not isinstance(node, DependencySpec) for node in nodes):
            raise RescuePlaneError("dependency_graph:DependencySpec_required")
        if any(not isinstance(edge, DependencyEdge) for edge in edges):
            raise RescuePlaneError("dependency_graph:DependencyEdge_required")
        names = [node.name for node in nodes]
        if len(set(names)) != len(names):
            raise RescuePlaneError("dependency_graph:duplicate_node")
        known = set(names)
        for edge in edges:
            if edge.dependent not in known or edge.prerequisite not in known:
                raise RescuePlaneError("dependency_graph:edge_references_unknown_node")
        self._assert_acyclic(names, edges)
        object.__setattr__(self, "nodes", nodes)
        object.__setattr__(self, "edges", edges)

    @staticmethod
    def _assert_acyclic(names: list[str], edges: tuple[DependencyEdge, ...]) -> None:
        adjacency: dict[str, list[str]] = {name: [] for name in names}
        for edge in edges:
            adjacency[edge.dependent].append(edge.prerequisite)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visiting:
                raise RescuePlaneError("dependency_graph:cycle_detected")
            if name in visited:
                return
            visiting.add(name)
            for prerequisite in adjacency[name]:
                visit(prerequisite)
            visiting.remove(name)
            visited.add(name)

        for name in names:
            visit(name)

    @classmethod
    def minimum(cls, *, include_optional_observer: bool = False) -> "DependencyGraph":
        """Build the smallest useful graph without probing the host.

        The graph is intentionally descriptive rather than environment
        specific.  Callers can use the default nodes as labels and inject
        platform-specific evidence separately.
        """

        nodes = [
            DependencySpec("guardian-runtime", kind="process"),
            DependencySpec("local-state", kind="state"),
            DependencySpec("diagnostic-observer", kind="observer"),
        ]
        edges = (
            DependencyEdge("diagnostic-observer", "guardian-runtime"),
            DependencyEdge("diagnostic-observer", "local-state"),
        )
        if include_optional_observer:
            nodes.append(DependencySpec("external-monitor", kind="optional-monitor", required=False))
            edges = (*edges, DependencyEdge("diagnostic-observer", "external-monitor", required=False))
        return cls(tuple(nodes), edges)

    def evaluate(self, states: Mapping[str, DependencyState]) -> "DependencyEvaluation":
        """Evaluate injected dependency states; never infer health from absence."""

        if not isinstance(states, Mapping):
            raise RescuePlaneError("dependency_states:mapping_required")
        reasons: list[str] = []
        blocking: list[str] = []
        normalized: dict[str, DependencyState] = {}
        known = {node.name: node for node in self.nodes}
        for name, raw_state in states.items():
            if name not in known:
                reasons.append(f"unknown_dependency:{name}")
                continue
            if not isinstance(raw_state, DependencyState):
                raise RescuePlaneError(f"dependency_state:{name}:DependencyState_required")
            normalized[name] = raw_state

        for node in self.nodes:
            state = normalized.get(node.name)
            if state is None:
                if node.required:
                    blocking.append(node.name)
                    reasons.append(f"dependency_missing:{node.name}")
                else:
                    reasons.append(f"optional_dependency_missing:{node.name}")
                continue
            if not state.present:
                reasons.append(f"dependency_missing:{node.name}")
                if node.required:
                    blocking.append(node.name)
                continue
            if state.status not in _HEALTHY_STATUSES:
                reasons.append(f"dependency_status_abnormal:{node.name}:{state.status}")
                if node.required:
                    blocking.append(node.name)

        for edge in self.edges:
            if not edge.required:
                continue
            prerequisite = normalized.get(edge.prerequisite)
            if prerequisite is None or not prerequisite.present or prerequisite.status not in _HEALTHY_STATUSES:
                reason = f"dependency_prerequisite_unhealthy:{edge.dependent}:{edge.prerequisite}"
                reasons.append(reason)
                if edge.dependent not in blocking:
                    blocking.append(edge.dependent)

        unique_reasons = tuple(dict.fromkeys(reasons))
        return DependencyEvaluation(
            status="blocked" if blocking or unique_reasons else "ready",
            reason_codes=unique_reasons,
            blocking_dependencies=tuple(dict.fromkeys(blocking)),
            observed=normalized,
        )


@dataclass(frozen=True)
class DependencyEvaluation:
    status: str
    reason_codes: tuple[str, ...]
    blocking_dependencies: tuple[str, ...]
    observed: Mapping[str, DependencyState] = field(default_factory=dict)


@dataclass(frozen=True)
class ResourceProtectionConfig:
    """Declarative protection settings; values are never applied here.

    ``mode`` defaults to ``observe`` and all resource limits are optional so a
    missing deployment value cannot silently become a new threshold.  The
    ordering checks only validate explicitly supplied values.
    """

    mode: str = "observe"
    protected_units: tuple[str, ...] = ()
    protected_paths: tuple[str, ...] = ()
    memory_min_bytes: int | None = None
    memory_low_bytes: int | None = None
    memory_high_bytes: int | None = None
    tasks_max: int | None = None

    def __post_init__(self) -> None:
        mode = _non_empty(self.mode, "resource_protection.mode").lower()
        if mode not in {"observe", "diagnose"}:
            raise RescuePlaneError("resource_protection.mode:read_only_mode_required")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(
            self,
            "protected_units",
            _unique_strings(self.protected_units, "resource_protection.protected_units"),
        )
        object.__setattr__(
            self,
            "protected_paths",
            _unique_strings(self.protected_paths, "resource_protection.protected_paths"),
        )
        values = {
            "memory_min_bytes": self.memory_min_bytes,
            "memory_low_bytes": self.memory_low_bytes,
            "memory_high_bytes": self.memory_high_bytes,
            "tasks_max": self.tasks_max,
        }
        for name, value in values.items():
            if value is not None and (type(value) is not int or value < 0):
                raise RescuePlaneError(f"resource_protection.{name}:non_negative_integer_or_null_required")
        if self.memory_min_bytes is not None and self.memory_low_bytes is not None:
            if self.memory_min_bytes > self.memory_low_bytes:
                raise RescuePlaneError("resource_protection:memory_min_above_low")
        if self.memory_low_bytes is not None and self.memory_high_bytes is not None:
            if self.memory_low_bytes > self.memory_high_bytes:
                raise RescuePlaneError("resource_protection:memory_low_above_high")


@dataclass(frozen=True)
class ResourceProtectionObservation:
    """Injected read-only evidence about protection state."""

    status: str
    config_matches: bool | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        status = _non_empty(self.status, "resource_protection_observation.status").lower()
        if status not in {"ready", "degraded", "missing", "failed", "unknown"}:
            raise RescuePlaneError(f"resource_protection_observation.status:unsupported:{status}")
        object.__setattr__(self, "status", status)
        if self.config_matches is not None and type(self.config_matches) is not bool:
            raise RescuePlaneError("resource_protection_observation.config_matches:boolean_or_null_required")


@dataclass(frozen=True)
class MaintenanceDiagnostic:
    """Structured result suitable for an operator-facing rescue check."""

    status: str
    read_only: bool
    reason_codes: tuple[str, ...]
    dependency: DependencyEvaluation
    resource_protection: ResourceProtectionObservation
    runtime_status: str


def diagnose_maintenance(
    graph: DependencyGraph,
    dependency_states: Mapping[str, DependencyState],
    resource_protection: ResourceProtectionObservation,
    *,
    runtime_status: str = "ready",
) -> MaintenanceDiagnostic:
    """Combine dependency, runtime, and protection evidence into one gate."""

    runtime = _non_empty(runtime_status, "runtime_status").lower()
    if runtime not in _RUNTIME_STATUSES:
        raise RescuePlaneError(f"runtime_status:unsupported:{runtime}")
    dependency = graph.evaluate(dependency_states)
    reasons = list(dependency.reason_codes)
    if runtime != "ready":
        reasons.append(f"runtime_status_abnormal:{runtime}")
    if resource_protection.status != "ready":
        reasons.append(f"resource_protection_status_abnormal:{resource_protection.status}")
    if resource_protection.config_matches is not True:
        reasons.append("resource_protection_config_unverified")
    unique_reasons = tuple(dict.fromkeys(reasons))
    return MaintenanceDiagnostic(
        status="ready" if not unique_reasons else "blocked",
        read_only=True,
        reason_codes=unique_reasons,
        dependency=dependency,
        resource_protection=resource_protection,
        runtime_status=runtime,
    )


@dataclass(frozen=True)
class RollbackPreconditions:
    """Evidence required before a separate caller may request a rollback."""

    target_id: str
    snapshot_present: bool | None
    snapshot_integrity_ok: bool | None
    target_identity_matches: bool | None
    explicit_authorization: bool = False
    request_mutation: bool = False
    maintenance: MaintenanceDiagnostic | None = None


@dataclass(frozen=True)
class RollbackPrecheckResult:
    ready: bool
    status: str
    read_only: bool
    reason_codes: tuple[str, ...]


def check_rollback_preconditions(request: RollbackPreconditions) -> RollbackPrecheckResult:
    """Return a fail-closed rollback gate without performing a rollback.

    ``request_mutation`` is opt-in and still does not authorize this module to
    mutate anything.  It only makes the explicit-authorization requirement
    visible to the caller before it hands the request to a separate adapter.
    """

    reasons: list[str] = []
    if not isinstance(request, RollbackPreconditions):
        raise RescuePlaneError("rollback_request:RollbackPreconditions_required")
    if not isinstance(request.target_id, str) or not request.target_id.strip():
        reasons.append("rollback_target_missing")
    if request.snapshot_present is not True:
        reasons.append("rollback_snapshot_missing")
    if request.snapshot_integrity_ok is not True:
        reasons.append("rollback_snapshot_integrity_unverified")
    if request.target_identity_matches is not True:
        reasons.append("rollback_target_identity_unverified")
    if request.maintenance is None:
        reasons.append("maintenance_diagnostic_missing")
    elif not isinstance(request.maintenance, MaintenanceDiagnostic):
        reasons.append("maintenance_diagnostic_invalid")
    elif request.maintenance.status != "ready":
        reasons.append("maintenance_not_ready")
    if request.request_mutation and request.explicit_authorization is not True:
        reasons.append("rollback_explicit_authorization_required")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return RollbackPrecheckResult(
        ready=not unique_reasons,
        status="ready" if not unique_reasons else "blocked",
        read_only=not request.request_mutation,
        reason_codes=unique_reasons,
    )


__all__ = [
    "DependencyEdge",
    "DependencyEvaluation",
    "DependencyGraph",
    "DependencySpec",
    "DependencyState",
    "MaintenanceDiagnostic",
    "ResourceProtectionConfig",
    "ResourceProtectionObservation",
    "RescuePlaneError",
    "RollbackPrecheckResult",
    "RollbackPreconditions",
    "SCHEMA",
    "check_rollback_preconditions",
    "diagnose_maintenance",
]
