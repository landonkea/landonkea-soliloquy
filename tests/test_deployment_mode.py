import pytest

from soliloquy.deployment_mode import describe_deployment_mode, get_deployment_mode


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("DATABASE_URL", "S3_ENDPOINT_URL", "MQTT_HOST"):
        monkeypatch.delenv(var, raising=False)


def test_defaults_with_nothing_configured_are_local(monkeypatch):
    assert get_deployment_mode() == "local"


def test_all_localhost_is_local(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5433/db")
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://localhost:9000")
    monkeypatch.setenv("MQTT_HOST", "localhost")
    assert get_deployment_mode() == "local"


def test_private_lan_addresses_are_local(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@192.168.0.130:5433/db")
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://10.0.0.5:9000")
    monkeypatch.setenv("MQTT_HOST", "192.168.0.130")
    assert get_deployment_mode() == "local"


def test_a_public_database_host_is_cloud(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.example-project.supabase.co:5432/db")
    assert get_deployment_mode() == "cloud"


def test_a_public_s3_endpoint_is_cloud(monkeypatch):
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://abc123.r2.cloudflarestorage.com")
    assert get_deployment_mode() == "cloud"


def test_a_public_mqtt_host_is_cloud(monkeypatch):
    monkeypatch.setenv("MQTT_HOST", "mqtt.example-broker.com")
    assert get_deployment_mode() == "cloud"


def test_one_public_host_among_otherwise_local_ones_is_still_cloud(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5433/db")
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://abc123.r2.cloudflarestorage.com")
    monkeypatch.setenv("MQTT_HOST", "localhost")
    assert get_deployment_mode() == "cloud"


def test_a_public_literal_ip_is_cloud(monkeypatch):
    monkeypatch.setenv("MQTT_HOST", "8.8.8.8")
    assert get_deployment_mode() == "cloud"


def test_describe_deployment_mode_mentions_local_when_local(monkeypatch):
    assert "LOCAL" in describe_deployment_mode()


def test_describe_deployment_mode_mentions_cloud_when_cloud(monkeypatch):
    monkeypatch.setenv("MQTT_HOST", "mqtt.example-broker.com")
    assert "CLOUD" in describe_deployment_mode()
