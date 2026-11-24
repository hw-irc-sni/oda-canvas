"""Model calls wait for the API's model route, so they get their own, longer timeout than the other internal calls."""
import httpx

from worker.client import InternalClient


async def test_model_calls_use_the_model_timeout(tmp_path):
    token = tmp_path / "token"
    token.write_text("t")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.path] = request.extensions["timeout"]["read"]
        if request.url.path.endswith("/chat"):
            return httpx.Response(200, json={"content": "{}"})
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"embeddings": [[0.1]], "model": "e"})
        return httpx.Response(204)

    client = InternalClient("http://api", str(token), "w", timeout=30, transport=httpx.MockTransport(handler),
                            model_timeout=180)
    await client.chat([{"role": "user", "content": "x"}])
    await client.embed(["x"])
    await client.claim()
    assert seen["/internal/models/chat"] == 180 and seen["/internal/models/embeddings"] == 180
    assert seen["/internal/task/claim"] == 30
