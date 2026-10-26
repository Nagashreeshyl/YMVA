from ymva.simulate import _local, _stream_url
from ymva.telephony.simulator import _PLACEHOLDER, FORMATS


def test_simulator_reads_every_providers_answer() -> None:
    for fmt, client in FORMATS.items():
        body, _ = client(_PLACEHOLDER).answer_response("wss://voice.example.com/ws/x", "https://s")
        assert _local(_stream_url(fmt, body)) == "ws://127.0.0.1:8000/ws/x", fmt


def test_public_urls_point_at_the_local_app() -> None:
    assert _local("https://voice.example.com/api/a?call=1") == "http://127.0.0.1:8000/api/a?call=1"
