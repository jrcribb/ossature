from unittest.mock import MagicMock

from conftest import make_task
from pydantic_ai.exceptions import AgentRunError, ModelHTTPError, UsageLimitExceeded

from ntt.build.builder import _describe_llm_error, _format_llm_error_body, _print_llm_error


class TestDescribeLlmError:
    def test_402_insufficient_credits(self):
        e = ModelHTTPError(status_code=402, model_name="claude")
        summary, suggestion = _describe_llm_error(e)
        assert "402" in summary
        assert "credits" in summary.lower()
        assert "retry" in suggestion.lower()

    def test_429_rate_limited(self):
        e = ModelHTTPError(status_code=429, model_name="claude")
        summary, suggestion = _describe_llm_error(e)
        assert "429" in summary
        assert "rate" in summary.lower()

    def test_500_server_error(self):
        e = ModelHTTPError(status_code=500, model_name="claude")
        summary, suggestion = _describe_llm_error(e)
        assert "500" in summary
        assert "server" in summary.lower()

    def test_502_server_error(self):
        e = ModelHTTPError(status_code=502, model_name="claude")
        summary, _ = _describe_llm_error(e)
        assert "502" in summary
        assert "server" in summary.lower()

    def test_503_server_error(self):
        e = ModelHTTPError(status_code=503, model_name="claude")
        summary, _ = _describe_llm_error(e)
        assert "503" in summary

    def test_other_4xx(self):
        e = ModelHTTPError(status_code=400, model_name="claude")
        summary, suggestion = _describe_llm_error(e)
        assert "400" in summary
        assert "configuration" in suggestion.lower()

    def test_usage_limit_exceeded(self):
        e = UsageLimitExceeded("too many requests")
        summary, _ = _describe_llm_error(e)
        assert "limit" in summary.lower()

    def test_generic_agent_run_error(self):
        e = AgentRunError("something went wrong")
        summary, suggestion = _describe_llm_error(e)
        assert "something went wrong" in summary
        assert "retry" in suggestion.lower()


class TestFormatLlmErrorBody:
    def test_extracts_error_message_from_dict(self):
        e = ModelHTTPError(
            status_code=400,
            model_name="claude",
            body={
                "error": {
                    "message": "max tokens must be less than 8192",
                    "type": "invalid_request_error",
                }
            },
        )
        assert _format_llm_error_body(e) == "max tokens must be less than 8192"

    def test_falls_back_to_str_for_flat_dict(self):
        e = ModelHTTPError(status_code=400, model_name="claude", body={"detail": "bad request"})
        result = _format_llm_error_body(e)
        assert "bad request" in result

    def test_returns_str_body_directly(self):
        e = ModelHTTPError(status_code=500, model_name="claude", body="Internal Server Error")
        assert _format_llm_error_body(e) == "Internal Server Error"

    def test_returns_none_when_no_body(self):
        e = ModelHTTPError(status_code=429, model_name="claude")
        assert _format_llm_error_body(e) is None

    def test_returns_none_for_non_http_error(self):
        e = AgentRunError("something")
        assert _format_llm_error_body(e) is None


class TestPrintLlmError:
    def test_prints_panel_with_error_info(self):
        console = MagicMock()
        task = make_task("003", "AUTH")
        e = ModelHTTPError(status_code=402, model_name="claude")

        _print_llm_error(console, task, 34, e)

        console.log.assert_called_once()
        log_args = console.log.call_args[0][0]
        assert "003" in log_args
        assert "AUTH task 003" in log_args

        console.print.assert_called()
