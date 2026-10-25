"""Gemini retry / fallback behaviour with a fake client - no network needed."""
import json
from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors

from nl2sql import llm
from nl2sql.llm import GeminiSQLGenerator, GenerationError


def overloaded():
    return genai_errors.ServerError(503, {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}})


def ok_response():
    body = json.dumps({"answerable": True, "sql": "SELECT 1", "explanation": "x"})
    return SimpleNamespace(
        candidates=[SimpleNamespace(finish_reason="STOP")],
        text=body,
        usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5),
    )


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make(outcomes):
    fake = FakeModels(outcomes)
    gen = GeminiSQLGenerator(client=SimpleNamespace(models=fake), model="main-model")
    gen.fallback_model = "lite-model"
    return gen, fake


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)


def ask(gen):
    return gen.propose("sys", [{"role": "user", "content": "q"}])


def test_retries_503_then_succeeds():
    gen, fake = make([overloaded(), overloaded(), ok_response()])
    assert ask(gen).sql == "SELECT 1"
    assert fake.calls == ["main-model"] * 3


def test_falls_back_to_lite_model():
    gen, fake = make([overloaded()] * 3 + [ok_response()])
    assert ask(gen).sql == "SELECT 1"
    assert fake.calls == ["main-model"] * 3 + ["lite-model"]


def test_gives_clear_error_when_everything_is_overloaded():
    gen, fake = make([overloaded()] * 6)
    with pytest.raises(GenerationError, match="overloaded"):
        ask(gen)
    assert len(fake.calls) == 6


def test_bad_request_is_not_retried():
    bad = genai_errors.ClientError(400, {"error": {"code": 400, "message": "bad", "status": "INVALID_ARGUMENT"}})
    gen, fake = make([bad])
    with pytest.raises(GenerationError):
        ask(gen)
    assert len(fake.calls) == 1
