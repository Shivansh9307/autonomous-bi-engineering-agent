"""DAX assertion, RLS impersonation, and domain-agnostic ground-truth schemas."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DaxAssertion(BaseModel):
    name: str
    dax_query: str
    expected_value: float
    tolerance: float = 0.01
    scope_label: str = "overall"


class RlsImpersonationCase(BaseModel):
    role_name: str
    effective_user_name: str | None = None
    expected_row_count: int = 0
    expected_fact_counts: dict[str, int] = Field(default_factory=dict)
    expected_metric_value: float | None = None
    must_be_empty: bool = False


class PerformanceBudget(BaseModel):
    cold_ms: int = 1500
    warm_ms: int = 500


def _wrap_node(value: Any) -> Any:
    if isinstance(value, dict):
        return GroundTruthNode(value)
    if isinstance(value, list):
        return [_wrap_node(item) for item in value]
    return value


def _unwrap_node(value: Any) -> Any:
    if isinstance(value, GroundTruthNode):
        return value.to_dict()
    if isinstance(value, list):
        return [_unwrap_node(item) for item in value]
    return value


def resolve_dot_path(root: Any, dot_path: str, default: Any = None) -> Any:
    """Navigate nested mappings/lists by dot-path, matching dotted keys greedily."""
    if not dot_path:
        return root

    def _walk(curr: Any, remaining: str) -> Any:
        if not remaining:
            return curr
        if isinstance(curr, GroundTruthNode):
            curr = curr.to_dict()
        if isinstance(curr, dict):
            if remaining in curr:
                return curr[remaining]
            # Greedy longest-prefix key matching so dotted keys (e.g. email UPNs) resolve.
            dot_indices = [idx for idx, ch in enumerate(remaining) if ch == "."]
            for idx in reversed(dot_indices):
                prefix = remaining[:idx]
                if prefix in curr:
                    return _walk(curr[prefix], remaining[idx + 1 :])
            return default
        if isinstance(curr, list):
            head, _, tail = remaining.partition(".")
            if head.isdigit():
                pos = int(head)
                if 0 <= pos < len(curr):
                    return _walk(curr[pos], tail) if tail else curr[pos]
            return default
        return default

    return _walk(root, dot_path)


class GroundTruthNode:
    """Dynamic attribute- and mapping-accessible wrapper over ground-truth JSON objects."""

    def __init__(self, data: dict[str, Any]) -> None:
        object.__setattr__(self, "_data", dict(data))

    def to_dict(self) -> dict[str, Any]:
        return dict(object.__getattribute__(self, "_data"))

    def resolve(self, dot_path: str, default: Any = None) -> Any:
        val = resolve_dot_path(object.__getattribute__(self, "_data"), dot_path, default=default)
        return _wrap_node(val)

    def get(self, key: str, default: Any = None) -> Any:
        data = object.__getattribute__(self, "_data")
        if key in data:
            return _wrap_node(data[key])
        return default

    def items(self) -> list[tuple[str, Any]]:
        data = object.__getattribute__(self, "_data")
        return [(k, _wrap_node(v)) for k, v in data.items()]

    def keys(self) -> list[str]:
        return list(object.__getattribute__(self, "_data").keys())

    def values(self) -> list[Any]:
        return [_wrap_node(v) for v in object.__getattribute__(self, "_data").values()]

    def __getitem__(self, key: str) -> Any:
        data = object.__getattribute__(self, "_data")
        return _wrap_node(data[key])

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(f"GroundTruthNode has no attribute '{name}'")
        data = object.__getattribute__(self, "_data")
        if name in data:
            return _wrap_node(data[name])
        return None

    def __contains__(self, key: object) -> bool:
        return key in object.__getattribute__(self, "_data")

    def __iter__(self) -> Iterator[str]:
        return iter(object.__getattribute__(self, "_data"))

    def __len__(self) -> int:
        return len(object.__getattribute__(self, "_data"))

    def __bool__(self) -> bool:
        return bool(object.__getattribute__(self, "_data"))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, GroundTruthNode):
            return object.__getattribute__(self, "_data") == object.__getattribute__(other, "_data")
        if isinstance(other, dict):
            return object.__getattribute__(self, "_data") == other
        return False

    def __repr__(self) -> str:
        return f"GroundTruthNode({object.__getattribute__(self, '_data')!r})"


class GroundTruthDataset(BaseModel):
    """Domain-agnostic ground-truth dataset supporting dot-path and attribute lookup."""

    model_config = ConfigDict(extra="allow")

    seed: int | None = None
    dataset_sha256: dict[str, str] = Field(default_factory=dict)

    def raw_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "seed": self.seed,
            "dataset_sha256": dict(self.dataset_sha256),
        }
        if self.__pydantic_extra__:
            payload.update({k: _unwrap_node(v) for k, v in self.__pydantic_extra__.items()})
        return payload

    def resolve(self, dot_path: str, default: Any = None) -> Any:
        """Resolve a dotted key path (supporting keys that themselves contain dots)."""
        val = resolve_dot_path(self.raw_payload(), dot_path, default=default)
        return _wrap_node(val)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or name.startswith("model_"):
            raise AttributeError(f"GroundTruthDataset has no attribute '{name}'")
        extra = self.__pydantic_extra__ or {}
        if name in extra:
            return _wrap_node(extra[name])
        return None

    def reconciliation_status(
        self, actual: float, expected: float, tolerance: float = 0.01
    ) -> Literal["PASS", "FAIL"]:
        return "PASS" if abs(actual - expected) <= tolerance else "FAIL"


def load_ground_truth(path: Path) -> GroundTruthDataset:
    """Load and validate a ground-truth JSON payload."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Ground-truth JSON at {path} must deserialize to an object.")
    return GroundTruthDataset.model_validate(raw)
