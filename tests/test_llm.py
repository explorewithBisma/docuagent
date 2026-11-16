"""The LLM layer without any API call: retry with exponential backoff, fallback between providers, and what each client sends."""

import base64
import json
from types import SimpleNamespace

import groq
import httpx
import pytest
from google.genai import errors as genai_errors

from backend.llm.base import LLMBadOutput, LLMClient, LLMNotConfigured, LLMProviderError, LLMRateLimitError, LLMTimeoutError, LLMTruncatedOutput
from backend.llm.gemini import GeminiClient
from backend.llm.groq_client import GroqClient
from backend.llm.runner import ExtractionFailed, ResilientExtractor
from backend.schemas import Invoice
from backend.services.files import SourceFile


class Scripted(LLMClient):
    """A provider that does what its script says: an Exception is raised, an Invoice is returned."""

    def __init__(self, name, script, configured=True):
        self.name, self.script, self.configured, self.calls = name, list(script), configured, 0

    def is_configured(self):
        return self.configured

    def extract(self, source):
        self.calls += 1
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


SOURCE = SourceFile(b"%PDF-1.4 fake", "application/pdf", 1)
IMAGE = SourceFile(bytes.fromhex("89504e470d0a1a0a") + b"fake png", "image/png")        # needs no rendering, so a fake Groq SDK can be tested with it
INVOICE = Invoice(vendor_name="Acme", total=10.0)


def runner(*clients, sleeps=None):
    log = [] if sleeps is None else sleeps
    return ResilientExtractor(list(clients), max_tries=3, backoff_seconds=1.0, sleep=log.append), log


# --- retry with exponential backoff -------------------------------------------------------------------------------------------
def test_a_rate_limit_is_retried_with_backoff_and_then_succeeds():
    gemini = Scripted("gemini", [LLMRateLimitError("429"), LLMRateLimitError("429"), INVOICE])
    extractor, sleeps = runner(gemini)
    result = extractor.extract(SOURCE)
    assert result.provider == "gemini" and result.invoice == INVOICE
    assert gemini.calls == 3 and sleeps == [1.0, 2.0]                        # 1 s, then 2 s


def test_the_providers_own_wait_hint_is_respected_but_capped():
    gemini = Scripted("gemini", [LLMRateLimitError("429", retry_after=14.3), LLMRateLimitError("429", retry_after=500.0), INVOICE])
    extractor, sleeps = runner(gemini)
    extractor.extract(SOURCE)
    assert sleeps == [14.3, 30.0]                                           # asked for 14.3 s; 500 s is capped at 30 s


def test_groq_wait_hints_are_parsed():
    from backend.llm.groq_client import retry_hint
    assert retry_hint("Please try again in 14.314285714s. Need more") == pytest.approx(14.314285714)
    assert retry_hint("Please try again in 250ms.") == 0.25 and retry_hint("try again in 1m5.5s") == 65.5 and retry_hint("try again in 2m") == 120
    assert retry_hint("no hint here") is None


def test_a_timeout_is_retried_too():
    gemini = Scripted("gemini", [LLMTimeoutError("slow"), INVOICE])
    extractor, sleeps = runner(gemini)
    assert extractor.extract(SOURCE).provider == "gemini" and sleeps == [1.0]


def test_after_three_failed_tries_the_next_provider_is_used():
    gemini = Scripted("gemini", [LLMRateLimitError("429")] * 3)
    groq_client = Scripted("groq", [INVOICE])
    extractor, sleeps = runner(gemini, groq_client)
    result = extractor.extract(SOURCE)
    assert result.provider == "groq" and gemini.calls == 3 and groq_client.calls == 1
    assert sleeps == [1.0, 2.0]                                              # no pause after the last try


def test_other_errors_are_not_retried_but_fall_back_at_once():
    for error in (LLMBadOutput("not json"), LLMProviderError("bad key")):
        gemini, groq_client = Scripted("gemini", [error]), Scripted("groq", [INVOICE])
        extractor, sleeps = runner(gemini, groq_client)
        assert extractor.extract(SOURCE).provider == "groq" and gemini.calls == 1 and sleeps == []


def test_providers_without_a_key_are_skipped():
    gemini, groq_client = Scripted("gemini", [], configured=False), Scripted("groq", [INVOICE])
    extractor, _ = runner(gemini, groq_client)
    assert extractor.extract(SOURCE).provider == "groq" and gemini.calls == 0


def test_when_every_provider_fails_the_message_is_clear():
    extractor, sleeps = runner(Scripted("gemini", [LLMRateLimitError("quota")] * 3), Scripted("groq", [LLMTimeoutError("slow")] * 3))
    with pytest.raises(ExtractionFailed) as failure:
        extractor.extract(SOURCE)
    assert "Gemini and Groq both failed or are busy" in failure.value.message and "try again" in failure.value.message
    assert len(failure.value.errors) == 6 and "quota" in failure.value.errors[0] and len(sleeps) == 4


def test_no_provider_configured_says_which_keys_to_set():
    extractor, _ = runner(Scripted("gemini", [], configured=False), Scripted("groq", [], configured=False))
    with pytest.raises(ExtractionFailed) as failure:
        extractor.extract(SOURCE)
    assert "GEMINI_API_KEY" in failure.value.message and "GROQ_API_KEY" in failure.value.message


def test_bad_output_is_reported_as_such():
    extractor, _ = runner(Scripted("gemini", [LLMBadOutput("nope")]))
    with pytest.raises(ExtractionFailed) as failure:
        extractor.extract(SOURCE)
    assert failure.value.bad_output is True


# --- Groq: images only -------------------------------------------------------------------------------------------------------------
class FakeGroqSDK:
    def __init__(self, answer=None, error=None, finish_reason="stop"):
        self.requests, self.answer, self.error, self.finish_reason = [], answer, error, finish_reason
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason=self.finish_reason, message=SimpleNamespace(content=self.answer))])


def test_groq_gets_png_pages_never_the_pdf(sample_files, tmp_path):
    import make_samples
    pdf = make_samples.build_pdf_with_pages(tmp_path / "three.pdf", 3)
    sdk = FakeGroqSDK(answer=json.dumps({"vendor_name": "Acme", "total": 12.5}))
    result = GroqClient("key", "some-model", sdk_client=sdk).extract(SourceFile(pdf, "application/pdf", 3))
    assert result.vendor_name == "Acme" and result.total == 12.5
    request = sdk.requests[0]
    assert request["model"] == "some-model" and request["response_format"] == {"type": "json_object"} and request["temperature"] == 0
    assert request["max_tokens"] == 1000
    content = request["messages"][0]["content"]
    images = [part for part in content if part["type"] == "image_url"]
    assert len(images) == 3 and content[0]["type"] == "text" and "NEVER invent" in content[0]["text"]
    for part in images:
        prefix, encoded = part["image_url"]["url"].split(",", 1)
        assert prefix == "data:image/png;base64" and base64.b64decode(encoded).startswith(b"\x89PNG")
    assert b"%PDF" not in json.dumps(request).encode()                       # the PDF itself is never sent


def test_groq_errors_map_to_our_errors():
    request = httpx.Request("POST", "https://api.groq.com")
    cases = [(groq.RateLimitError("slow", response=httpx.Response(429, request=request), body=None), LLMRateLimitError),
             (groq.APITimeoutError(request), LLMTimeoutError),
             (groq.APIConnectionError(request=request), LLMTimeoutError),
             (groq.AuthenticationError("bad key", response=httpx.Response(401, request=request), body=None), LLMProviderError)]
    for error, expected in cases:
        with pytest.raises(expected):
            GroqClient("key", "m", sdk_client=FakeGroqSDK(error=error)).extract(IMAGE)
    with pytest.raises(LLMBadOutput):
        GroqClient("key", "m", sdk_client=FakeGroqSDK(answer="not json at all")).extract(IMAGE)
    with pytest.raises(LLMBadOutput):
        GroqClient("key", "m", sdk_client=FakeGroqSDK(answer=json.dumps({"total": "lots"}))).extract(IMAGE)
    with pytest.raises(LLMNotConfigured):
        GroqClient("", "m").extract(IMAGE)


def test_a_pdf_that_cannot_be_rendered_is_a_provider_error_not_a_crash():
    with pytest.raises(LLMProviderError):
        GroqClient("key", "m", sdk_client=FakeGroqSDK(answer="{}")).extract(SOURCE)             # SOURCE is not a real PDF


# --- Gemini: the PDF as it is ----------------------------------------------------------------------------------------------------------
class FakeGeminiSDK:
    def __init__(self, response=None, error=None):
        self.calls, self.response, self.error = [], response, error
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def test_gemini_sends_the_pdf_with_the_schema_and_uses_the_parsed_answer():
    sdk = FakeGeminiSDK(response=SimpleNamespace(parsed=INVOICE, text=None))
    result = GeminiClient("key", "gemini-test", sdk_client=sdk).extract(SOURCE)
    assert result == INVOICE
    call = sdk.calls[0]
    assert call["model"] == "gemini-test" and call["config"].response_mime_type == "application/json" and call["config"].response_schema is Invoice
    part = call["contents"][0]
    assert part.inline_data.mime_type == "application/pdf" and part.inline_data.data == SOURCE.data


def test_gemini_falls_back_to_the_json_text_and_rejects_nonsense():
    good = SimpleNamespace(parsed=None, text=json.dumps({"vendor_name": "Acme", "line_items": []}))
    assert GeminiClient("key", "m", sdk_client=FakeGeminiSDK(response=good)).extract(SOURCE).vendor_name == "Acme"
    for text in ("not json", json.dumps({"total": "lots"})):
        with pytest.raises(LLMBadOutput):
            GeminiClient("key", "m", sdk_client=FakeGeminiSDK(response=SimpleNamespace(parsed=None, text=text))).extract(SOURCE)


def test_gemini_errors_map_to_our_errors():
    for code, expected in ((429, LLMRateLimitError), (504, LLMTimeoutError), (408, LLMTimeoutError), (400, LLMProviderError), (500, LLMProviderError)):
        error = genai_errors.APIError(code, {"error": {"message": "x"}})
        with pytest.raises(expected):
            GeminiClient("key", "m", sdk_client=FakeGeminiSDK(error=error)).extract(SOURCE)
    with pytest.raises(LLMNotConfigured):
        GeminiClient("", "m").extract(SOURCE)


# --- truncated output: never use partial data ----------------------------------------------------------------------------------------------
def test_groq_finish_reason_length_is_a_truncated_output_even_if_the_json_looks_complete():
    complete_looking = json.dumps({"vendor_name": "Acme", "total": 12.5})
    with pytest.raises(LLMTruncatedOutput):
        GroqClient("key", "m", sdk_client=FakeGroqSDK(answer=complete_looking, finish_reason="length")).extract(IMAGE)


def test_groq_invalid_json_is_treated_as_truncated():
    with pytest.raises(LLMTruncatedOutput):
        GroqClient("key", "m", sdk_client=FakeGroqSDK(answer='{"vendor_name": "Acme", "line_items": [{"descr')).extract(IMAGE)


def test_gemini_max_tokens_is_a_truncated_output():
    cut = SimpleNamespace(parsed=None, text='{"vendor_name": "Acme"', candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="MAX_TOKENS"))])
    with pytest.raises(LLMTruncatedOutput):
        GeminiClient("key", "m", sdk_client=FakeGeminiSDK(response=cut)).extract(SOURCE)


def test_a_truncated_answer_stops_everything_with_a_clear_message_and_is_not_retried():
    gemini, groq_client = Scripted("gemini", [LLMTruncatedOutput("cut off")]), Scripted("groq", [INVOICE])
    extractor, sleeps = runner(gemini, groq_client)
    with pytest.raises(ExtractionFailed) as failure:
        extractor.extract(SOURCE)
    assert failure.value.truncated is True and failure.value.message == "Document too long for the free tier, try fewer pages."
    assert gemini.calls == 1 and groq_client.calls == 0 and sleeps == []


# --- provider order ---------------------------------------------------------------------------------------------------------------------
def test_default_provider_order_is_groq_then_gemini():
    from backend.config import Settings
    from backend.llm.runner import build_extractor
    settings = Settings(_env_file=None)
    assert settings.llm_provider_order == "groq,gemini"
    assert [c.name for c in build_extractor(settings).clients] == ["groq", "gemini"]


def test_provider_order_can_be_changed_and_bad_names_are_refused():
    from backend.config import Settings
    from backend.llm.runner import build_extractor
    assert [c.name for c in build_extractor(Settings(_env_file=None, llm_provider_order="Gemini, groq")).clients] == ["gemini", "groq"]
    assert [c.name for c in build_extractor(Settings(_env_file=None, llm_provider_order="gemini")).clients] == ["gemini"]
    with pytest.raises(ValueError):
        build_extractor(Settings(_env_file=None, llm_provider_order="openai"))
