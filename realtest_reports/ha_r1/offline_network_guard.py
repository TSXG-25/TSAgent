import pytest

@pytest.fixture(autouse=True)
def deny_external_io(monkeypatch):
    import httpx
    import requests
    import socket
    def denied(*args, **kwargs):
        raise AssertionError('Offline test attempted external network access')
    async def async_denied(*args, **kwargs):
        raise AssertionError('Offline test attempted external network access')
    monkeypatch.setattr(httpx.Client, 'send', denied)
    monkeypatch.setattr(httpx.AsyncClient, 'send', async_denied)
    monkeypatch.setattr(requests.Session, 'send', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)
