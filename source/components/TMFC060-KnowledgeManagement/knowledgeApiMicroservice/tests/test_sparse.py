"""The sparse (BM25) encoder shared by search and the pipeline worker."""
from app.kb import sparse


def test_tokens():
    assert sparse.tokens("The PowerAlarm on cell_4711 raised by HTTPServer — Störung ÄrgerMeldung") == [
        "power", "alarm", "poweralarm", "cell", "4711", "raised", "http", "server", "httpserver", "störung",
        "ärger", "meldung", "ärgermeldung"]


def test_encoding_is_deterministic_and_bm25_shaped():
    indices, values = sparse.encode_document("alarm alarm alarm power")
    assert indices == sorted(indices) and len(indices) == 2
    weights = dict(zip(indices, values))
    assert weights[sparse.index("alarm")] > weights[sparse.index("power")]  # term frequency, saturated
    assert weights[sparse.index("alarm")] < 3 * weights[sparse.index("power")]
    assert sparse.encode_document("alarm alarm alarm power") == (indices, values)
    q_indices, q_values = sparse.encode_query("Power power ALARM the")
    assert q_indices == sorted({sparse.index("power"), sparse.index("alarm")}) and set(q_values) == {1.0}
    assert all(0 <= i < 2 ** 31 for i in indices)
