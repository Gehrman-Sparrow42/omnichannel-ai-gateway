import pytest
import sys
from pathlib import Path

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auth
import config
import database


def test_password_hashing_and_verification():
    raw_pass = "SecurePass2026!"
    hashed = auth.get_password_hash(raw_pass)
    assert hashed != raw_pass
    assert auth.verify_password(raw_pass, hashed) is True
    assert auth.verify_password("WrongPassword!", hashed) is False


def test_jwt_token_generation_and_decoding():
    payload = {
        "sub": "test.manager@omnicamp.com",
        "user_id": 999,
        "role": "branch_manager",
        "branch_id": "olympos",
    }
    token = auth.create_access_token(payload)
    assert isinstance(token, str)
    assert len(token) > 20

    decoded = auth.decode_token(token)
    assert decoded is not None
    assert decoded["sub"] == "test.manager@omnicamp.com"
    assert decoded["role"] == "branch_manager"
    assert decoded["branch_id"] == "olympos"


def test_branch_access_control():
    super_admin = {"role": "superadmin", "branch_id": None}
    olympos_manager = {"role": "branch_manager", "branch_id": "olympos"}
    fethiye_agent = {"role": "agent", "branch_id": "fethiye"}

    # Super Admin has universal access
    assert auth.check_branch_access(super_admin, "olympos") is True
    assert auth.check_branch_access(super_admin, "fethiye") is True
    assert auth.check_branch_access(super_admin, "datca") is True

    # Branch Manager is strictly scoped
    assert auth.check_branch_access(olympos_manager, "olympos") is True
    assert auth.check_branch_access(olympos_manager, "fethiye") is False

    # Agent is strictly scoped
    assert auth.check_branch_access(fethiye_agent, "fethiye") is True
    assert auth.check_branch_access(fethiye_agent, "olympos") is False
