"""Isolated fault-injection lifecycle.

The initial provider is an in-memory, no-device-write simulator. Real device
providers must be added behind a reviewed approval boundary, not shell templates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import uuid4


class FaultError(RuntimeError):
    pass


class FaultProvider(Protocol):
    def prepare(self, parameters: dict) -> dict: ...
    def inject(self, state: dict) -> dict: ...
    def verify(self, state: dict) -> bool: ...
    def recover(self, state: dict) -> dict: ...
    def verify_recovery(self, state: dict) -> bool: ...


class MockBgpRouterIdConflict:
    """Deterministic simulation only; does not connect to network equipment."""

    def prepare(self, parameters: dict) -> dict:
        if not parameters.get("device") or not parameters.get("peer"):
            raise FaultError("device and peer are required")
        if parameters["device"] == parameters["peer"]:
            raise FaultError("device and peer must differ")
        return {"parameters": dict(parameters), "active": False}

    def inject(self, state: dict) -> dict:
        state["active"] = True
        return state

    def verify(self, state: dict) -> bool:
        return state["active"] is True

    def recover(self, state: dict) -> dict:
        state["active"] = False
        return state

    def verify_recovery(self, state: dict) -> bool:
        return state["active"] is False


@dataclass
class FaultRun:
    id: str
    fault_type: str
    state: dict
    status: str = "PREPARED"
    events: list[str] = field(default_factory=lambda: ["PREPARED"])


class FaultEngine:
    def __init__(self, providers: dict[str, FaultProvider] | None = None):
        self.providers = providers or {"mock-bgp-router-id-conflict": MockBgpRouterIdConflict()}
        self.runs: dict[str, FaultRun] = {}

    def prepare(self, fault_type: str, parameters: dict) -> FaultRun:
        provider = self.providers.get(fault_type)
        if provider is None:
            raise FaultError(f"Unsupported fault type: {fault_type}")
        run = FaultRun(id=str(uuid4()), fault_type=fault_type, state=provider.prepare(parameters))
        self.runs[run.id] = run
        return run

    def _get(self, run_id: str) -> tuple[FaultRun, FaultProvider]:
        if run_id not in self.runs:
            raise FaultError("Unknown fault run")
        run = self.runs[run_id]
        return run, self.providers[run.fault_type]

    def inject(self, run_id: str) -> FaultRun:
        run, provider = self._get(run_id)
        if run.status != "PREPARED":
            raise FaultError(f"Cannot inject from {run.status}")
        run.state = provider.inject(run.state)
        run.status = "INJECTED" if provider.verify(run.state) else "VERIFY_FAILED"
        run.events.append(run.status)
        return run

    def recover(self, run_id: str) -> FaultRun:
        run, provider = self._get(run_id)
        if run.status not in ("INJECTED", "VERIFY_FAILED", "RECOVERY_REQUIRED"):
            raise FaultError(f"Cannot recover from {run.status}")
        try:
            run.state = provider.recover(run.state)
            run.status = "RECOVERED" if provider.verify_recovery(run.state) else "RECOVERY_REQUIRED"
        except Exception:
            run.status = "RECOVERY_REQUIRED"
            run.events.append(run.status)
            raise
        run.events.append(run.status)
        return run
