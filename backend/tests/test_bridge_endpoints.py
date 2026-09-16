import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app
import config
import auth

client = TestClient(app)


def get_auth_header():
    import database
    database.seed_database_if_empty()
    resp = client.post(
        "/api/auth/login",
        json={"email": config.INITIAL_ADMIN_EMAIL, "password": config.INITIAL_ADMIN_PASSWORD},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_bridge_status_offline_graceful():
    headers = get_auth_header()
    # When bridge is not running, status should return offline safely without crashing
    response = client.get("/api/bridge/status", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert "connected" in data
    assert data["connected"] is False
    assert data["state"] == "OFFLINE"


@patch("httpx.AsyncClient.get")
def test_bridge_status_online_mock(mock_get):
    headers = get_auth_header()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "state": "CONNECTED",
        "status": "CONNECTED",
        "connectedUser": {"phone": "905379737160", "name": "Navitas Yetkilisi"},
        "hasQR": False,
        "pairingCode": None,
    }
    mock_get.return_value = mock_resp

    response = client.get("/api/bridge/status", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["connected"] is True
    assert data["state"] == "CONNECTED"
    assert data["connectedUser"]["phone"] == "905379737160"


@patch("httpx.AsyncClient.post")
def test_bridge_pair_success_mock(mock_post):
    headers = get_auth_header()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.is_success = True
    mock_resp.json.return_value = {
        "success": True,
        "code": "WXYZ-1234",
        "phoneNumber": "905379737160",
    }
    mock_post.return_value = mock_resp

    response = client.post(
        "/api/bridge/pair",
        json={"phone_number": "+90 537 973 71 60"},
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["code"] == "WXYZ-1234"


@patch("httpx.AsyncClient.post")
def test_bridge_switch_mode_mock(mock_post):
    headers = get_auth_header()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"success": True, "mode": "code"}
    mock_post.return_value = mock_resp

    response = client.post(
        "/api/bridge/switch-mode",
        json={"mode": "code"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["mode"] == "code"


@patch("httpx.AsyncClient.post")
def test_bridge_disconnect_mock(mock_post):
    headers = get_auth_header()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"success": True, "status": "DISCONNECTED"}
    mock_post.return_value = mock_resp

    response = client.post(
        "/api/bridge/disconnect",
        json={"clearSession": True},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "DISCONNECTED"
