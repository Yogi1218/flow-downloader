import pytest
from downloader import normalize_url
from server import app
import json
import os

def test_normalize_url_reddit():
    url = "https://www.reddit.com/r/videos/comments/12345/test_video/?utm_source=share"
    expected = "https://www.reddit.com/r/videos/comments/12345/test_video/"
    assert normalize_url(url) == expected

def test_normalize_url_twitter():
    url = "https://x.com/user/status/123456789"
    expected = "https://twitter.com/user/status/123456789"
    assert normalize_url(url) == expected

def test_normalize_url_tiktok():
    url = "https://www.tiktok.com/@user/video/123456?is_from_webapp=1"
    expected = "https://www.tiktok.com/@user/video/123456"
    assert normalize_url(url) == expected

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_api_history_empty(client, monkeypatch):
    # Mock load_history to return empty list
    monkeypatch.setattr('server.load_history', lambda: [])
    response = client.get('/api/history')
    assert response.status_code == 200
    assert response.get_json() == []

def test_api_history_with_data(client, monkeypatch):
    dummy_history = [
        {"id": "1", "title": "Test Video 1", "client_id": "test_client"},
        {"id": "2", "title": "Test Video 2", "client_id": "other_client"}
    ]
    monkeypatch.setattr('server.load_history', lambda: dummy_history)

    # Test without client_id filters
    response = client.get('/api/history')
    assert response.status_code == 200
    assert len(response.get_json()) == 2

    # Test with client_id filter
    response = client.get('/api/history?client_id=test_client')
    assert response.status_code == 200
    data = response.get_json()
    assert len(data) == 1
    assert data[0]["title"] == "Test Video 1"
