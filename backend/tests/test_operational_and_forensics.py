import os
import pytest
from httpx import AsyncClient, ASGITransport
from datetime import datetime, timezone, timedelta

import config
import database
from main import app, is_bridge_port_open
from security import (
    check_append_only_attribute,
    enable_append_only_attribute,
    secure_session_directories,
    ensure_gitignore_entries,
    verify_audit_log_chain,
)


@pytest.fixture(autouse=True)
def setup_test_env():
    database.init_db()
    database.seed_database_if_empty()


@pytest.mark.asyncio
async def test_kvkk_data_minimization_cleanup():
    """Verifies that database.cleanup_old_messages properly prunes messages older than cutoff."""
    # Seed an old message
    b_id = "istanbul-airport"
    chan = "whatsapp"
    cust_id = "905559876543"

    database.save_message(
        b_id, chan, cust_id,
        direction="inbound", role="user",
        content="Eski KVKK test mesajı",
        customer_name="Test User",
    )

    # Manually backdate the message to 45 days ago in SQLite
    with database.get_db() as conn:
        conn.execute(
            "UPDATE messages SET created_at = datetime('now', '-45 days') WHERE customer_id = ?;",
            (cust_id,),
        )

    # Prune messages older than 30 days
    deleted = database.cleanup_old_messages(days=30)
    assert deleted >= 1

    # Verify message no longer exists
    history = database.get_history(b_id, chan, cust_id)
    assert len(history) == 0


@pytest.mark.asyncio
async def test_api_kvkk_cleanup_endpoint():
    """Verifies the superadmin POST /api/system/cleanup-messages endpoint."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Login as superadmin
        login_res = await ac.post("/api/auth/login", json={
            "email": config.INITIAL_ADMIN_EMAIL,
            "password": config.INITIAL_ADMIN_PASSWORD,
        })
        assert login_res.status_code == 200
        token = login_res.json()["access_token"]

        # 2. Trigger cleanup
        res = await ac.post(
            "/api/system/cleanup-messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"days": 14},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["retention_days"] == 14
        assert "deleted_count" in data


@pytest.mark.asyncio
async def test_forensic_security_helpers():
    """Verifies gitignore integrity, session permissions, and audit log verification."""
    # Run forensic sweeps
    ensure_gitignore_entries()
    secure_session_directories()

    # Append-only check should return a boolean (False on Windows without error)
    is_append_only = check_append_only_attribute()
    assert isinstance(is_append_only, bool)

    # Merkle audit chain verification
    chain_info = verify_audit_log_chain()
    assert "is_valid" in chain_info
    assert "total_records" in chain_info


@pytest.mark.asyncio
async def test_api_forensics_endpoint():
    """Verifies GET /api/system/forensics endpoint."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        login_res = await ac.post("/api/auth/login", json={
            "email": config.INITIAL_ADMIN_EMAIL,
            "password": config.INITIAL_ADMIN_PASSWORD,
        })
        token = login_res.json()["access_token"]

        res = await ac.get(
            "/api/system/forensics",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert "audit_chain" in data
        assert "is_append_only_protected" in data
        assert "platform" in data


@pytest.mark.asyncio
async def test_contract_pdf_generation_and_download():
    """Verifies that the legal SLA contract PDF is generated and served."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        login_res = await ac.post("/api/auth/login", json={
            "email": config.INITIAL_ADMIN_EMAIL,
            "password": config.INITIAL_ADMIN_PASSWORD,
        })
        token = login_res.json()["access_token"]

        res = await ac.get(
            "/api/system/contract-pdf",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        assert res.headers.get("content-type") == "application/pdf"
        assert len(res.content) > 10000  # Non-trivial PDF size


def test_is_bridge_port_open_helper():
    """Verifies that socket port check handles closed ports gracefully."""
    # Port 59999 should normally be closed
    assert is_bridge_port_open(59999) is False
