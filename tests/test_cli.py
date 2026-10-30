"""The command line, exercised as a caller rather than as an import.

Every command imports its dependencies inside its own body, so `--help` and a package-wide
import smoke both pass while a command's first statement is a stale import. That is how
`serve` came to raise `ImportError` on every invocation after the API was rewritten around
the model registry. These tests run the command bodies -- `serve` with uvicorn stubbed, so
nothing binds a port -- which is the cheapest thing that would have caught it.
"""

import pytest
import uvicorn
from typer.testing import CliRunner

from collection.api.app import Loaded
from collection.cli import app

runner = CliRunner()


@pytest.fixture
def served(monkeypatch):
    """Record what `serve` would have started, instead of starting it."""
    started: list[dict] = []
    monkeypatch.setattr(
        uvicorn, "run", lambda target, **kwargs: started.append({"target": target, **kwargs})
    )
    return started


def _published(monkeypatch, loaded: Loaded) -> None:
    monkeypatch.setattr("collection.api.app.load_published", lambda: loaded)


def test_serve_runs_its_body_and_starts_the_server(monkeypatch, served):
    _published(monkeypatch, Loaded(bundles={"propensity": {}, "default": {}}))

    result = runner.invoke(app, ["serve", "--port", "8099"])

    assert result.exit_code == 0, result.output + str(result.exception)
    assert "propensity" in result.output
    assert served == [
        {"target": "collection.api.app:app", "host": "127.0.0.1", "port": 8099, "reload": False}
    ]


def test_serve_reports_an_outage_as_an_outage_not_as_nothing_trained(monkeypatch, served):
    _published(monkeypatch, Loaded(problems={"propensity": "OperationalError: no server"}))

    result = runner.invoke(app, ["serve"])

    assert result.exit_code == 0, result.output + str(result.exception)
    assert "503" in result.output
    assert "no trained models" not in result.output
    assert served


def test_serve_reports_an_empty_registry_as_nothing_trained(monkeypatch, served):
    _published(monkeypatch, Loaded())

    result = runner.invoke(app, ["serve"])

    assert result.exit_code == 0, result.output + str(result.exception)
    assert "no trained models" in result.output
    assert served
