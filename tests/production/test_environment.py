import os
import pytest
from art_pipeline import images_api


def setup_files(tmp_path, monkeypatch):
    user = tmp_path / "config" / "art-pipeline"
    user.mkdir(parents=True)
    checkout = tmp_path / "checkout"
    (checkout / "src" / "art_pipeline").mkdir(parents=True)
    (checkout / "pyproject.toml").write_text('[project]\nname = "art-pipeline"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user.parent))
    monkeypatch.setattr(images_api, "__file__", str(checkout / "src" / "art_pipeline" / "images_api.py"))
    for key in ("ART_TEST_KEY", "ART_TEST_SECOND", "ART_TEST_EMPTY"):
        monkeypatch.delenv(key, raising=False)
    return user / ".env", checkout / ".env"


def test_precedence_and_checkout_fallback(tmp_path, monkeypatch):
    user, checkout = setup_files(tmp_path, monkeypatch)
    user.write_text("ART_TEST_KEY=user\n")
    checkout.write_text("ART_TEST_KEY=checkout\nART_TEST_SECOND=fallback\n")
    images_api.load_environment()
    assert os.environ["ART_TEST_KEY"] == "user"
    assert os.environ["ART_TEST_SECOND"] == "fallback"
    monkeypatch.setenv("ART_TEST_KEY", "environment")
    images_api.load_environment()
    assert os.environ["ART_TEST_KEY"] == "environment"


def test_quotes_comments_empty_and_no_shell_execution(tmp_path, monkeypatch):
    user, _ = setup_files(tmp_path, monkeypatch)
    user.write_text('# comment\nexport ART_TEST_KEY="a # b" # comment\nART_TEST_SECOND=$(false)\nART_TEST_EMPTY=\n')
    images_api.load_environment()
    assert os.environ["ART_TEST_KEY"] == "a # b"
    assert os.environ["ART_TEST_SECOND"] == "$(false)"
    assert os.environ["ART_TEST_EMPTY"] == ""


def test_bad_value_is_not_exposed(tmp_path, monkeypatch):
    user, _ = setup_files(tmp_path, monkeypatch)
    user.write_text('ART_TEST_KEY="secret-value\n')
    with pytest.raises(ValueError) as error:
        images_api.load_environment()
    assert "secret-value" not in str(error.value)


def test_client_loads_before_construction(tmp_path, monkeypatch):
    import openai
    user, _ = setup_files(tmp_path, monkeypatch)
    user.write_text("ART_TEST_KEY=loaded\n")
    def fake_client(**kwargs):
        assert os.environ["ART_TEST_KEY"] == "loaded"
        assert kwargs == {"timeout": 240, "max_retries": 0}
        return "client"
    monkeypatch.setattr(openai, "OpenAI", fake_client)
    assert images_api.create_client(timeout=240, max_retries=0) == "client"


def test_arty_config_precedes_legacy_config(tmp_path, monkeypatch):
    legacy, checkout = setup_files(tmp_path, monkeypatch)
    arty = legacy.parent.parent/'arty'
    arty.mkdir()
    (arty/'.env').write_text('ART_TEST_KEY=arty\n')
    legacy.write_text('ART_TEST_KEY=legacy\nART_TEST_SECOND=legacy-fallback\n')
    checkout.write_text('ART_TEST_KEY=checkout\n')
    images_api.load_environment()
    assert os.environ['ART_TEST_KEY'] == 'arty'
    assert os.environ['ART_TEST_SECOND'] == 'legacy-fallback'
