import pytest
import sys
from pathlib import Path
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app
import config

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["branches_count"] == 17
    assert "whatsapp" in data["supported_channels"]
    assert "instagram" in data["supported_channels"]
    assert "telegram" in data["supported_channels"]
    assert "messenger" in data["supported_channels"]
    assert "ai_backend" in data
    assert data["ai_backend"]["hallucination_guard"] == "active"
    assert data["ai_backend"]["multilingual_mirror"] == "active"
    assert "trigger_prefix" in data
    assert "only_unknown_contacts" in data


def test_admin_login_and_branch_access():
    # Login as Super Admin
    login_resp = client.post(
        "/api/auth/login",
        json={"email": config.INITIAL_ADMIN_EMAIL, "password": config.INITIAL_ADMIN_PASSWORD},
    )
    assert login_resp.status_code == 200
    login_data = login_resp.json()
    token = login_data["access_token"]
    assert token

    headers = {"Authorization": f"Bearer {token}"}

    # Super Admin can list all 17 branches
    branches_resp = client.get("/api/branches", headers=headers)
    assert branches_resp.status_code == 200
    branches = branches_resp.json()
    assert len(branches) == 17


def test_manager_scoped_login_and_access():
    # Login as Istanbul Airport Manager
    manager_email = "manager.istanbul-airport@navitasspa.com"
    login_resp = client.post(
        "/api/auth/login",
        json={"email": manager_email, "password": "ManagerPass2026!"},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Manager should only see their assigned branch in /api/branches
    b_resp = client.get("/api/branches", headers=headers)
    assert b_resp.status_code == 200
    branches = b_resp.json()
    assert len(branches) == 1
    assert branches[0]["id"] == "istanbul-airport"

    # Manager CAN access Istanbul Airport KB
    kb_resp = client.get("/api/branches/istanbul-airport/knowledge", headers=headers)
    assert kb_resp.status_code == 200

    # Manager CANNOT access Mall of Istanbul KB (Forbidden 403)
    forbidden_resp = client.get("/api/branches/mall-of-istanbul/knowledge", headers=headers)
    assert forbidden_resp.status_code == 403


def test_webhook_inbound_and_chat_thread():
    # 1. Send simulated WhatsApp message via Webhook
    bridge_payload = {
        "phone": "905358140744",
        "message": "Havalimanı şubenizde uçuş öncesi masaj için yeriniz var mı?",
        "contact_name": "Deneme Misafir",
    }
    wh_resp = client.post("/webhook/whatsapp/istanbul-airport", json=bridge_payload)
    assert wh_resp.status_code == 200

    # 2. Login as Superadmin to inspect inbox
    admin_login = client.post(
        "/api/auth/login",
        json={"email": config.INITIAL_ADMIN_EMAIL, "password": config.INITIAL_ADMIN_PASSWORD},
    ).json()
    headers = {"Authorization": f"Bearer {admin_login['access_token']}"}

    conv_resp = client.get("/api/conversations?branch_id=istanbul-airport", headers=headers)
    assert conv_resp.status_code == 200
    conv_list = conv_resp.json()
    assert len(conv_list) >= 1

    target_conv = next((c for c in conv_list if c["customer_id"] == "905358140744"), None)
    assert target_conv is not None
    conv_id = target_conv["id"]

    # 3. Agent sends manual live reply
    reply_resp = client.post(
        f"/api/conversations/{conv_id}/reply",
        json={"message": "Merhaba! Hilton Istanbul Airport şubemizde müsaitliğimiz bulunmaktadır, seans sürenizi seçebilirsiniz."},
        headers=headers,
    )
    assert reply_resp.status_code == 200
    assert reply_resp.json()["status"] == "sent"

    # 4. Toggle bot mute
    mute_resp = client.post(
        f"/api/conversations/{conv_id}/mute",
        json={"is_muted": True, "hours": 2, "reason": "Temsilci sohbete katıldı"},
        headers=headers,
    )
    assert mute_resp.status_code == 200
    assert mute_resp.json()["is_muted"] is True

    # 5. Toggle reservation lead
    lead_resp = client.post(
        f"/api/conversations/{conv_id}/lead",
        json={"is_lead": True, "lead_details": "2 kişilik Bali masajı ve hamam talebi"},
        headers=headers,
    )
    assert lead_resp.status_code == 200
    assert lead_resp.json()["is_lead"] is True


def test_filter_settings_and_prefix_flow():
    # 1. Login as Admin
    admin_login = client.post(
        "/api/auth/login",
        json={"email": config.INITIAL_ADMIN_EMAIL, "password": config.INITIAL_ADMIN_PASSWORD},
    ).json()
    headers = {"Authorization": f"Bearer {admin_login['access_token']}"}

    # 2. Update filter settings: trigger_prefix to '-test' and only_unknown_contacts to True
    filter_update = client.post(
        "/api/settings/filters",
        json={
            "trigger_prefix": "-test",
            "only_unknown_contacts": True,
            "debounce_seconds": 20,
            "blacklist": ["905000000000"],
        },
        headers=headers,
    )
    assert filter_update.status_code == 200
    res_data = filter_update.json()
    assert res_data["status"] == "success"

    # 3. Verify settings via GET /api/settings/filters
    get_filters = client.get("/api/settings/filters", headers=headers)
    assert get_filters.status_code == 200
    f_data = get_filters.json()
    assert f_data["trigger_prefix"] == "-test"
    assert f_data["only_unknown_contacts"] is True
    assert f_data["debounce_seconds"] == 20
    assert "905000000000" in f_data["blacklist"]
    assert f_data["ai_backend"]["hallucination_guard"] == "active"
    assert f_data["ai_backend"]["multilingual_mirror"] == "active"

    # Reset trigger_prefix back to "" for normal tests
    client.post(
        "/api/settings/filters",
        json={"trigger_prefix": "", "only_unknown_contacts": False, "debounce_seconds": 15},
        headers=headers,
    )
