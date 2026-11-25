"""What the Component CR points at: the OAS files and Swagger UI (no token), and the metrics listener."""
import httpx
import yaml

from .api import auth, running_api


async def test_oas_files_and_docs_are_served_without_a_token(postgres_url, tmp_path):
    async with running_api(postgres_url, str(tmp_path / "store")) as api:
        process = api.app.state.settings.process_base_path
        for base, title in ((api.base, "Knowledge Management"), (process, "Process")):
            r = await api.get(f"{base}/openapi.yaml")
            assert r.status_code == 200, r.text
            assert r.headers["content-type"].startswith("application/yaml")
            assert title in yaml.safe_load(r.text)["info"]["title"]
            r = await api.get(f"{base}/docs")
            assert r.status_code == 200 and f"{base}/openapi.yaml" in r.text
        # FastAPI's generated description is off: the OAS files are the API description
        for path in ("/openapi.json", "/docs", "/redoc"):
            assert (await api.get(path)).status_code == 404


async def test_metrics_count_requests_by_route_template_on_their_own_port(postgres_url, tmp_path):
    async with running_api(postgres_url, str(tmp_path / "store")) as api:
        server = api.app.state.metrics_server
        assert server.port and server.path == f"/{api.app.state.settings.component_name}/metrics"
        for kb in ("kb-a", "kb-b"):
            assert (await api.get(f"{api.base}/knowledge/{kb}", headers=auth())).status_code == 404
        assert (await api.get(f"{api.base}/knowledge", headers=auth())).status_code == 200

        async with httpx.AsyncClient() as client:
            r = await client.get(f"http://127.0.0.1:{server.port}{server.path}")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
        text = r.text
        route = f"{api.base}/knowledge/{{id}}"
        assert f'tmfc060_http_requests_total{{method="GET",route="{route}",status="404"}} 2.0' in text
        assert f'tmfc060_http_requests_total{{method="GET",route="{api.base}/knowledge",status="200"}} 1.0' in text
        assert "kb-a" not in text and "kb-b" not in text  # ids never become label values
        assert "tmfc060_http_request_duration_seconds_bucket" in text
        assert "process_cpu_seconds_total" in text or "python_info" in text

        # The main port has no metrics
        assert (await api.get(server.path)).status_code == 404
