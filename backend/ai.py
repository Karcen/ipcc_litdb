"""Optional SQL analysis through an OpenAI-compatible endpoint.

Environment variables select the provider, model, and API key. Generated queries
pass SQL validation and execute on a read-only SQLite connection. Answers include
the query and result rows for inspection.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

import config
from ai_guard import SqlRejected, execute_readonly, validate_sql

SCHEMA_PROMPT = """You write SQLite SQL for an IPCC bibliography database.

Tables (read-only; you may only SELECT):
  assessment_reports(report_id, report_label)
  working_groups(wg_id, report_id, wg_label)
  chapters(chapter_id, report_id, wg_id, chapter_label, chapter_title, n_refs, extraction_status)
  papers(paper_id, doi_norm, title_norm, title_best, year_best, journal_best,
         authors_best, n_occurrences, n_chapters, n_reports, verification_status,
         metadata_source, wos_record_id)
  references_occurrence(occurrence_id, chapter_id, paper_id, source_page,
         raw_reference, parsed_authors, parsed_title, parsed_journal, parsed_year,
         doi_norm, doi_source, match_status, match_confidence, wos_record_id)
  authors(author_id, surname, given, full_name)
  paper_authors(paper_id, author_id, author_pos)
  data_quality_flags(flag_id, entity_type, entity_id, flag_type, severity, detail)
  wos_records(record_id, ...full Web of Science record...)

Notes:
  match_status is one of 'matched_doi', 'matched_title', 'unmatched'.
  verification_status is one of 'wos_matched', 'doi_verified', 'unresolved'.
  A paper cited by several chapters is ONE row in papers with several rows in
  references_occurrence.  Never join papers to chapters directly.

Return ONLY the SQL, no prose, no code fences. Use a LIMIT."""


def is_configured() -> bool:
    return bool(config.AI_ENABLED and config.AI_API_KEY)


def status() -> dict:
    return {
        "enabled": config.AI_ENABLED,
        "configured": is_configured(),
        "provider": config.AI_PROVIDER,
        "model": config.AI_MODEL if is_configured() else None,
        "base_url": config.AI_BASE_URL if is_configured() else None,
        "note": ("Set AI_ENABLED=true and AI_API_KEY in .env to enable AI analysis. Use AI_BASE_URL "
                 "and AI_MODEL to select a provider and model.") if not is_configured() else "",
    }


# --------------------------------------------------------------------- provider
class Provider:
    name = "base"

    def chat(self, system: str, user: str) -> str:
        raise NotImplementedError


class OpenAICompatibleProvider(Provider):
    name = "openai_compatible"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def chat(self, system: str, user: str) -> str:
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0,
        }).encode()
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read().decode())
        return data["choices"][0]["message"]["content"]


def get_provider() -> Provider | None:
    if not is_configured():
        return None
    # any OpenAI-compatible endpoint: OpenAI, Azure-style gateways, vLLM,
    # Ollama (/v1), LM Studio, together, ...
    return OpenAICompatibleProvider(config.AI_BASE_URL, config.AI_API_KEY,
                                    config.AI_MODEL, config.AI_TIMEOUT)


# ------------------------------------------------------------------- workflow
def answer(question: str, lang: str = "en", db_path=None) -> dict:
    """Question -> SQL -> validation -> read-only execution -> explanation.

    Returns the query, result rows, and an English summary.
    """
    if not is_configured():
        return {"configured": False, "question": question,
                "message": status()["note"], "sql": None, "rows": [], "answer": None}

    provider = get_provider()
    try:
        raw_sql = provider.chat(SCHEMA_PROMPT, question).strip()
    except urllib.error.URLError as e:
        return {"configured": True, "error": f"provider unreachable: {e}",
                "sql": None, "rows": [], "answer": None}

    raw_sql = raw_sql.replace("```sql", "").replace("```", "").strip()
    try:
        safe_sql = validate_sql(raw_sql, config.SQL_ROW_LIMIT)
    except SqlRejected as e:
        return {"configured": True, "error": f"generated SQL rejected by the guard: {e}",
                "sql": raw_sql, "rows": [], "answer": None}

    try:
        result = execute_readonly(db_path, safe_sql, config.SQL_ROW_LIMIT,
                                  config.SQL_TIMEOUT_SECONDS)
    except Exception as e:
        return {"configured": True, "error": f"query failed: {e}",
                "sql": safe_sql, "rows": [], "answer": None}

    summary_prompt = (
        f"The user asked: {question}\n\n"
        f"SQL executed:\n{safe_sql}\n\n"
        f"Result ({result['row_count']} rows, first rows shown):\n"
        f"{json.dumps(result['rows'][:20], ensure_ascii=False)}\n\n"
        f"Answer in English in at most 4 sentences, using the numbers in "
        f"the result above. If the result is empty, say so. "
        f"Preserve paper titles, journal names, author names, and DOIs exactly. "
        f"Use plain language and state the finding directly.")
    try:
        text = provider.chat(
            "You are a careful research-data analyst. You only state what the "
            "query result supports.", summary_prompt).strip()
    except urllib.error.URLError as e:
        text = None
        result["warning"] = f"explanation unavailable: {e}"

    return {"configured": True, "question": question, "sql": safe_sql,
            "columns": result["columns"], "rows": result["rows"][:50],
            "row_count": result["row_count"], "truncated": result["truncated"],
            "answer": text}
