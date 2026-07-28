from gld_scalper.config import Settings, load_settings


def test_default_settings_keep_paper_data_separate():
    settings = Settings()

    assert settings.data_mode == "paper"
    assert settings.database_url == "sqlite:///data/paper/gld_scalper.db"
    assert settings.csv_export_dir == "exports/paper/hourly"
    assert settings.database_path.parts[-3:] == ("data", "paper", "gld_scalper.db")


def test_load_settings_uses_mode_specific_defaults(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "ALPACA_API_KEY=test-key",
                "ALPACA_SECRET_KEY=test-secret",
                "ALPACA_PAPER=true",
                "ALPACA_PAPER_TRADE=true",
                "ALPACA_ENDPOINT=https://paper-api.alpaca.markets/v2",
                "BOT_DATA_MODE=paper",
            ]
        ),
        encoding="utf-8",
    )
    for name in [
        "ALPACA_API_KEY",
        "ALPACA_SECRET_KEY",
        "ALPACA_PAPER",
        "ALPACA_PAPER_TRADE",
        "ALPACA_ENDPOINT",
        "BOT_DATA_MODE",
        "DATABASE_URL",
        "CSV_EXPORT_DIR",
    ]:
        monkeypatch.delenv(name, raising=False)

    settings = load_settings(env_file)

    assert settings.data_mode == "paper"
    assert settings.database_url == "sqlite:///data/paper/gld_scalper.db"
    assert settings.csv_export_dir == "exports/paper/hourly"


def test_load_settings_rejects_shared_database_path(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "ALPACA_API_KEY=test-key",
                "ALPACA_SECRET_KEY=test-secret",
                "ALPACA_PAPER=true",
                "ALPACA_PAPER_TRADE=true",
                "ALPACA_ENDPOINT=https://paper-api.alpaca.markets/v2",
                "BOT_DATA_MODE=paper",
                "DATABASE_URL=sqlite:///data/gld_scalper.db",
                "CSV_EXPORT_DIR=exports/paper/hourly",
            ]
        ),
        encoding="utf-8",
    )
    for name in [
        "ALPACA_API_KEY",
        "ALPACA_SECRET_KEY",
        "ALPACA_PAPER",
        "ALPACA_PAPER_TRADE",
        "ALPACA_ENDPOINT",
        "BOT_DATA_MODE",
        "DATABASE_URL",
        "CSV_EXPORT_DIR",
    ]:
        monkeypatch.delenv(name, raising=False)

    try:
        load_settings(env_file)
    except RuntimeError as exc:
        assert "data/paper" in str(exc)
    else:
        raise AssertionError("Expected shared database path to be rejected.")
