"""LLM clients that turn a question into a structured SQL proposal.

Interchangeable providers, chosen with LLM_PROVIDER in .env:
  gemini    -> Google Gemini (GEMINI_API_KEY, model from GEMINI_MODEL, default gemini-3.5-flash)
  openai    -> OpenAI (OPENAI_API_KEY, model from OPENAI_MODEL, default gpt-4.1)
  anthropic -> Claude (ANTHROPIC_API_KEY)

Each provider implements one method, _complete_json(), which sends a system prompt plus
messages and returns JSON matching a schema. The shared base class builds the two calls the
app needs on top of it: propose() writes SQL, select_tables() picks relevant tables for large schemas.
"""
import json
import os
import time
from dataclasses import dataclass

import anthropic
import httpx
import openai
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

MODEL = "claude-opus-5-5"
OPENAI_DEFAULT_MODEL = "gpt-4.1"
GEMINI_DEFAULT_MODEL = "gemini-3.5-flash"
GEMINI_FALLBACK_MODEL = "gemini-3.5-flash-lite"
RETRY_DELAYS_S = (0, 1, 3)  # wait before each attempt per model

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "answerable": {"type": "boolean"},
        "sql": {"type": "string"},
        "explanation": {"type": "string"},
        "clarifying_question": {"type": "string"},
    },
    "required": ["answerable", "sql", "explanation", "clarifying_question"],
    "additionalProperties": False,
}

TABLE_SELECTION_RULES = """\
You help a text-to-SQL system decide which database tables are needed to answer a question \
from aircraft maintenance and support staff. You are given a catalog listing every table with \
its description and column names.

Return the tables needed to write a SQL query that answers the question, including tables \
needed only for joins or for readable output (for example aircraft for tail numbers, \
technicians for names). Prefer a few correct tables over many loosely related ones. \
Return an empty list if no table is relevant.
"""


def table_selection_schema(table_names: list[str]) -> dict:
    # The enum stops the model inventing table names.
    return {
        "type": "object",
        "properties": {"tables": {"type": "array", "items": {"type": "string", "enum": table_names}}},
        "required": ["tables"],
        "additionalProperties": False,
    }


class GenerationError(Exception):
    """The model could not produce a usable answer (refusal, truncation, API failure)."""


@dataclass
class SQLProposal:
    answerable: bool
    sql: str
    explanation: str
    clarifying_question: str
    assistant_content: object  # raw response content, appended back to the conversation on retry
    input_tokens: int
    output_tokens: int


@dataclass
class JSONReply:
    data: dict
    assistant_content: object
    input_tokens: int
    output_tokens: int


class BaseGenerator:
    def _complete_json(self, system_prompt: str, messages: list[dict], schema: dict, name: str) -> JSONReply:
        raise NotImplementedError

    def propose(self, system_prompt: str, messages: list[dict]) -> SQLProposal:
        reply = self._complete_json(system_prompt, messages, OUTPUT_SCHEMA, "sql_proposal")
        return SQLProposal(
            answerable=reply.data["answerable"],
            sql=reply.data["sql"].strip(),
            explanation=reply.data["explanation"].strip(),
            clarifying_question=reply.data.get("clarifying_question", "").strip(),
            assistant_content=reply.assistant_content,
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
        )

    def select_tables(self, catalog: str, question: str, table_names: list[str]) -> tuple[list[str], JSONReply]:
        system_prompt = f"{TABLE_SELECTION_RULES}\n<catalog>\n{catalog}\n</catalog>"
        reply = self._complete_json(
            system_prompt,
            [{"role": "user", "content": f"Question: {question}"}],
            table_selection_schema(table_names),
            "table_selection",
        )
        known = set(table_names)
        return [t for t in dict.fromkeys(reply.data.get("tables", [])) if t in known], reply


class SQLGenerator(BaseGenerator):
    """Claude via the Anthropic SDK."""

    def __init__(self, client: anthropic.Anthropic | None = None, model: str = MODEL):
        self.client = client or anthropic.Anthropic()
        self.model = model

    def _complete_json(self, system_prompt, messages, schema, name):
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
                messages=messages,
                output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
                # If a safety classifier declines, the API re-runs the request on a fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as e:
            raise GenerationError("Claude API key is missing or invalid. Set ANTHROPIC_API_KEY in .env.") from e
        except anthropic.RateLimitError as e:
            raise GenerationError("Claude API rate limit reached. Wait a moment and try again.") from e
        except anthropic.APIStatusError as e:
            raise GenerationError(f"Claude API error ({e.status_code}): {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise GenerationError("Could not reach the Claude API. Check your network connection.") from e

        if response.stop_reason == "refusal":
            raise GenerationError("The model declined this request.")
        if response.stop_reason == "max_tokens":
            raise GenerationError("The model's response was cut off. Try a simpler question.")

        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise GenerationError("The model returned no answer.")
        return JSONReply(json.loads(text), response.content, response.usage.input_tokens,
                         response.usage.output_tokens)


class OpenAISQLGenerator(BaseGenerator):
    """OpenAI chat completions with strict JSON schema output."""

    def __init__(self, client: openai.OpenAI | None = None, model: str | None = None):
        self.client = client or openai.OpenAI()
        self.model = model or os.getenv("OPENAI_MODEL", OPENAI_DEFAULT_MODEL)

    def _complete_json(self, system_prompt, messages, schema, name):
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system_prompt}, *messages],
                response_format={"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}},
            )
        except openai.AuthenticationError as e:
            raise GenerationError("OpenAI API key is missing or invalid. Set OPENAI_API_KEY in .env.") from e
        except openai.RateLimitError as e:
            if e.code in ("insufficient_quota", "credit_balance_exhausted"):
                raise GenerationError(
                    "The OpenAI account has no credits left. Add credits at "
                    "https://platform.openai.com/settings/organization/billing/"
                ) from e
            raise GenerationError("OpenAI rate limit reached. Wait a moment and try again.") from e
        except openai.APIStatusError as e:
            raise GenerationError(f"OpenAI API error ({e.status_code}): {e.message}") from e
        except openai.APIConnectionError as e:
            raise GenerationError("Could not reach the OpenAI API. Check your network connection.") from e

        choice = response.choices[0]
        if choice.message.refusal:
            raise GenerationError(f"The model declined this request: {choice.message.refusal}")
        if choice.finish_reason == "length":
            raise GenerationError("The model's response was cut off. Try a simpler question.")

        text = choice.message.content or ""
        return JSONReply(json.loads(text), text, response.usage.prompt_tokens, response.usage.completion_tokens)


class GeminiSQLGenerator(BaseGenerator):
    """Gemini with JSON-schema constrained output, retries and a fallback model."""

    def __init__(self, client: genai.Client | None = None, model: str | None = None):
        self.client = client or genai.Client(
            api_key=os.getenv("GEMINI_API_KEY"),
            http_options=genai_types.HttpOptions(timeout=60_000),  # ms; fail fast instead of hanging the UI
        )
        self.model = model or os.getenv("GEMINI_MODEL", GEMINI_DEFAULT_MODEL)
        self.fallback_model = os.getenv("GEMINI_FALLBACK_MODEL", GEMINI_FALLBACK_MODEL)

    def _generate(self, system_prompt: str, contents: list, schema: dict):
        """Call Gemini, retrying transient overload errors, then falling back to a lighter model.

        503/500/504 (overloaded), 429 (per-minute limit) and dropped connections are usually
        momentary on Gemini, so a short backoff fixes most of them without the user noticing.
        """
        config = genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_json_schema=schema,
        )
        models = [self.model] + ([self.fallback_model] if self.fallback_model != self.model else [])
        last_error = None
        for model in models:
            for delay in RETRY_DELAYS_S:
                if delay:
                    time.sleep(delay)
                try:
                    return self.client.models.generate_content(model=model, contents=contents, config=config)
                except genai_errors.ServerError as e:
                    last_error = e
                except genai_errors.ClientError as e:
                    if e.code != 429:
                        raise
                    last_error = e
                except httpx.TransportError as e:
                    last_error = e
        raise last_error

    def _complete_json(self, system_prompt, messages, schema, name):
        contents = [
            genai_types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[genai_types.Part(text=m["content"])],
            )
            for m in messages
        ]
        try:
            response = self._generate(system_prompt, contents, schema)
        except genai_errors.ClientError as e:
            if e.code == 429:
                raise GenerationError("Gemini quota or rate limit reached. Wait a minute and try again.") from e
            if e.code in (400, 401, 403) and "key" in str(e).lower():
                raise GenerationError("Gemini API key is missing or invalid. Set GEMINI_API_KEY in .env.") from e
            raise GenerationError(f"Gemini API error ({e.code}): {e.message}") from e
        except genai_errors.ServerError as e:
            raise GenerationError(
                f"Gemini is overloaded right now (error {e.code}), even after retries and the backup model. "
                "Try again in a minute."
            ) from e
        except httpx.TimeoutException as e:
            raise GenerationError("Gemini took too long to respond. Try again.") from e
        except httpx.TransportError as e:
            raise GenerationError("Could not reach the Gemini API. Check your network connection.") from e

        candidate = response.candidates[0] if response.candidates else None
        if candidate is None or candidate.finish_reason in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
            raise GenerationError("The model declined this request.")
        if candidate.finish_reason == "MAX_TOKENS":
            raise GenerationError("The model's response was cut off. Try a simpler question.")

        text = response.text or ""
        usage = response.usage_metadata
        return JSONReply(
            json.loads(text),
            text,
            (usage.prompt_token_count or 0) if usage else 0,
            (usage.candidates_token_count or 0) if usage else 0,
        )


def make_generator():
    provider = os.getenv("LLM_PROVIDER", "gemini").lower()
    if provider == "gemini":
        return GeminiSQLGenerator()
    if provider == "anthropic":
        return SQLGenerator()
    if provider == "openai":
        return OpenAISQLGenerator()
    raise ValueError(f"Unknown LLM_PROVIDER '{provider}'. Use 'gemini', 'openai' or 'anthropic'.")
