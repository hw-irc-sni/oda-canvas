"""The portal chart's Knowledge settings render into values the backend's Settings accept (skipped without helm)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from config import Settings

CHART = Path(__file__).resolve().parents[2] / "helm" / "canvas-portal"
pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def render(*args):
    out = subprocess.run(["helm", "template", "t", str(CHART), *args], check=True, capture_output=True, text=True).stdout
    docs = [d for d in yaml.safe_load_all(out) if d]
    config = next(d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"].endswith("backend"))["data"]
    frontend = next(d for d in docs if d["kind"] == "Deployment" and "frontend" in d["metadata"]["name"])
    env = {e["name"]: e.get("value") for e in frontend["spec"]["template"]["spec"]["containers"][0]["env"]}
    return config, env, docs


def settings_from(config, monkeypatch):
    for key, value in config.items():
        if key.startswith(("KNOWLEDGE_", "RESOURCE_INVENTORY_")):
            monkeypatch.setenv(key, value)
    return Settings()


def test_defaults_render_the_backend_defaults(monkeypatch):
    config, env, _ = render()
    settings = settings_from(config, monkeypatch)

    assert settings.KNOWLEDGE_DISCOVERY == "ri"
    assert settings.KNOWLEDGE_URL_MODE == "service"
    assert settings.RESOURCE_INVENTORY_URL == "http://resource-inventory.canvas.svc.cluster.local/tmf-api/resourceInventoryManagement/v5"
    assert json.loads(settings.KNOWLEDGE_INSTANCES_JSON) == []
    assert (settings.KNOWLEDGE_CACHE_TTL_SECONDS, settings.KNOWLEDGE_STALE_TTL_SECONDS) == (30, 600)
    assert settings.KNOWLEDGE_CANVAS_SYSTEM_ROLE == "canvasRole"
    assert settings.KNOWLEDGE_MAX_BODY_BYTES == 10 * 1024 * 1024
    assert settings.KNOWLEDGE_MAX_UPLOAD_BYTES == 51 * 1024 * 1024
    assert settings.KNOWLEDGE_TIMEOUT_SECONDS == 60
    # Same values as the backend would use without the chart
    monkeypatch.undo()
    bare = Settings()
    for key in ("KNOWLEDGE_DISCOVERY", "KNOWLEDGE_URL_MODE", "RESOURCE_INVENTORY_URL", "KNOWLEDGE_CACHE_TTL_SECONDS",
                "KNOWLEDGE_STALE_TTL_SECONDS", "KNOWLEDGE_CANVAS_SYSTEM_ROLE", "KNOWLEDGE_MAX_BODY_BYTES",
                "KNOWLEDGE_MAX_UPLOAD_BYTES", "KNOWLEDGE_TIMEOUT_SECONDS"):
        assert getattr(settings, key) == getattr(bare, key), key
    assert env["KNOWLEDGE_MAX_UPLOAD_SIZE"] == str(51 * 1024 * 1024)


def test_overrides_render(monkeypatch, tmp_path):
    instances = [{"name": "kmk-knowledgemanagement", "label": "Knowledge (lab)", "namespace": "components",
                  "apiUrl": "http://kmk-kmapi.components.svc.cluster.local:8080/kmk-knowledgemanagement/tmf-api/knowledgeManagement/v5"}]
    values = tmp_path / "values.yaml"
    values.write_text(yaml.safe_dump({"backend": {"knowledge": {
        "discovery": "static", "resourceInventoryUrl": "http://ri.example:8639/tmf-api/resourceInventoryManagement/v5",
        "urlMode": "gateway", "instances": instances, "cacheTtlSeconds": 0, "staleTtlSeconds": 0,
        "canvasSystemRole": "system", "maxBodyBytes": 2000000, "maxUploadBytes": 104857600, "timeoutSeconds": 120}}}))

    config, env, _ = render("-f", str(values))
    settings = settings_from(config, monkeypatch)

    assert settings.KNOWLEDGE_DISCOVERY == "static" and settings.KNOWLEDGE_URL_MODE == "gateway"
    assert settings.RESOURCE_INVENTORY_URL == "http://ri.example:8639/tmf-api/resourceInventoryManagement/v5"
    assert json.loads(settings.KNOWLEDGE_INSTANCES_JSON) == instances
    assert (settings.KNOWLEDGE_CACHE_TTL_SECONDS, settings.KNOWLEDGE_STALE_TTL_SECONDS) == (0, 0)
    assert settings.KNOWLEDGE_CANVAS_SYSTEM_ROLE == "system"
    assert settings.KNOWLEDGE_MAX_BODY_BYTES == 2000000
    assert settings.KNOWLEDGE_MAX_UPLOAD_BYTES == 104857600
    assert settings.KNOWLEDGE_TIMEOUT_SECONDS == 120
    assert env["KNOWLEDGE_MAX_UPLOAD_SIZE"] == "104857600"


def test_a_values_file_without_the_block_still_renders(monkeypatch):
    config, env, _ = render("--set", "backend.knowledge=null")

    settings = settings_from(config, monkeypatch)
    assert settings.KNOWLEDGE_DISCOVERY == "ri"
    assert settings.KNOWLEDGE_MAX_UPLOAD_BYTES == 51 * 1024 * 1024
    assert env["KNOWLEDGE_MAX_UPLOAD_SIZE"] == str(51 * 1024 * 1024)


def test_the_backend_needs_no_access_to_dataresourceconfigs():
    _, _, docs = render()

    for doc in docs:
        if doc["kind"] in ("ClusterRole", "Role"):
            for rule in doc.get("rules") or []:
                assert "dataresourceconfigs" not in (rule.get("resources") or []), doc["metadata"]["name"]
