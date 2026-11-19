from types import SimpleNamespace
from unittest.mock import Mock
from art_pipeline.production_cli import main
from art_pipeline.images_api import api_error_message


def test_cli_exposes_api_rejection(tmp_path, monkeypatch, capsys):
    class BadRequestError(Exception):
        status_code = 400
        request_id = "req_synthetic"
        body = {"message": "Prompt is too long", "type": "invalid_request_error", "code": "string_above_max_length", "param": "prompt"}
    error = BadRequestError("SDK wrapper text")
    client = SimpleNamespace(images=SimpleNamespace(generate=Mock(side_effect=error)))
    monkeypatch.setattr("art_pipeline.images_api.create_client", lambda: client)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Synthetic prompt")
    assert main(["generate", "--prompt", str(prompt), "--asset", "test", "--revision", "r01", "-o", str(tmp_path / "result")]) == 2
    stderr = capsys.readouterr().err
    for detail in ("HTTP 400", "Prompt is too long", "invalid_request_error", "string_above_max_length", "param: prompt", "req_synthetic"):
        assert detail in stderr
    assert client.images.generate.call_count == 1
    assert not (tmp_path / "result").exists()


def test_errors_redact_credentials_and_omit_headers(monkeypatch):
    monkeypatch.setenv("EXAMPLE_API_KEY", "synthetic-private-value")
    error = SimpleNamespace(status_code=400, body={"error": {"message": "bad synthetic-private-value client-private-value sk-example123", "param": "prompt"}}, request_id="req_test", headers={"Authorization": "never-output"})
    text = api_error_message(error, "edit", SimpleNamespace(api_key="client-private-value"))
    for secret in ("synthetic-private-value", "client-private-value", "sk-example123", "never-output"):
        assert secret not in text
    assert "param: prompt" in text


def test_unstructured_exception_stays_private():
    text = api_error_message(RuntimeError("private arbitrary error"), "edit", None)
    assert "RuntimeError" in text
    assert "private arbitrary error" not in text


def test_unknown_error_preserves_server_message():
    text = api_error_message(SimpleNamespace(status_code=400, body={"message": "Unknown problem", "code": "new_code"}), "edit", None)
    assert "Unknown problem" in text
    assert "Suggestion:" not in text
