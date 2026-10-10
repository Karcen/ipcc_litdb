"""Optional AI queries, configuration, and answer language."""
import pytest

import ai


def test_status_shape():
    st = ai.status()
    for k in ("enabled", "configured", "provider", "note"):
        assert k in st


def test_unconfigured_returns_a_clear_message(monkeypatch, db_path):
    monkeypatch.setattr(ai.config, "AI_API_KEY", "")
    out = ai.answer("How many papers are there?", "en", db_path=db_path)
    assert out["configured"] is False
    assert out["sql"] is None
    assert out["rows"] == []
    assert "AI_API_KEY" in out["message"]


def test_no_provider_without_key(monkeypatch):
    monkeypatch.setattr(ai.config, "AI_API_KEY", "")
    assert ai.get_provider() is None


def test_prompt_forbids_ungrounded_claims():
    assert "SELECT" in ai.SCHEMA_PROMPT
    assert "references_occurrence" in ai.SCHEMA_PROMPT


def test_answer_uses_english_with_a_legacy_language_argument(monkeypatch, db_path):
    class Provider:
        def __init__(self):
            self.prompts = []

        def chat(self, system, user):
            self.prompts.append((system, user))
            if len(self.prompts) == 1:
                return "SELECT COUNT(*) AS papers FROM papers LIMIT 1"
            return "The database contains the papers counted in the query result."

    provider = Provider()
    monkeypatch.setattr(ai, "is_configured", lambda: True)
    monkeypatch.setattr(ai, "get_provider", lambda: provider)
    out = ai.answer("How many papers are there?", "zh-CN", db_path=db_path)
    assert out["rows"][0]["papers"] > 0
    assert "Answer in English" in provider.prompts[1][1]
    assert "Preserve paper titles" in provider.prompts[1][1]
