"""Tests for the independent, mock-only fault engine."""
import pytest

from app.fault_injection import FaultEngine, FaultError


def test_mock_fault_lifecycle():
    engine = FaultEngine()
    run = engine.prepare("mock-bgp-router-id-conflict", {"device": "node-a", "peer": "node-b"})
    assert run.status == "PREPARED"
    assert engine.inject(run.id).status == "INJECTED"
    assert engine.recover(run.id).status == "RECOVERED"
    assert run.events == ["PREPARED", "INJECTED", "RECOVERED"]


def test_invalid_transitions():
    engine = FaultEngine()
    with pytest.raises(FaultError):
        engine.prepare("missing", {})
    with pytest.raises(FaultError):
        engine.prepare("mock-bgp-router-id-conflict", {"device": "node-a", "peer": "node-a"})
    run = engine.prepare("mock-bgp-router-id-conflict", {"device": "node-a", "peer": "node-b"})
    with pytest.raises(FaultError):
        engine.recover(run.id)
    engine.inject(run.id)
    with pytest.raises(FaultError):
        engine.inject(run.id)
