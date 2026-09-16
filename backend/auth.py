import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
import jwt
import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr

import config

logger = logging.getLogger("omni-auth")

# Security Bearer scheme
security_bearer = HTTPBearer(auto_error=False)


# =====================================================================
# Pydantic Schemas
# =====================================================================

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: Dict[str, Any]


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserInfo(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    branch_id: Optional[str] = None
    is_active: bool = True


# =====================================================================
# Password Hashing & Verification
# =====================================================================

def get_password_hash(password: str) -> str:
    """Hashes plain password using bcrypt."""
    pw_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(pw_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plain password against a stored bcrypt hash."""
    try:
        pw_bytes = plain_password.encode("utf-8")
        hash_bytes = hashed_password.encode("utf-8")
        return bcrypt.checkpw(pw_bytes, hash_bytes)
    except Exception as e:
        logger.warning("Password verification error: %s", e)
        return False


# =====================================================================
# JWT Generation & Decoding
# =====================================================================

def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Encodes JWT access token with expiration."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc)})
    encoded_jwt = jwt.encode(to_encode, config.JWT_SECRET_KEY, algorithm=config.JWT_ALGORITHM)
    return encoded_jwt


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    """Decodes and validates a JWT token."""
    try:
        payload = jwt.decode(token, config.JWT_SECRET_KEY, algorithms=[config.JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        logger.debug("Token has expired.")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug("Invalid JWT token: %s", e)
        return None


# =====================================================================
# RBAC FastAPI Dependencies
# =====================================================================

def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer),
) -> Dict[str, Any]:
    """
    Extracts and validates the authenticated user from the HTTP Bearer header.
    Raises 401 Unauthorized if invalid or missing.
    """
    import database

    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Oturum doğrulaması başarısız. Lütfen giriş yapınız.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    payload = decode_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Geçersiz veya süresi dolmuş token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("user_id")
    email = payload.get("sub")

    user = None
    if user_id:
        user = database.get_user_by_id(user_id)
    if not user and email:
        user = database.get_user_by_email(email)

    if not user or not user.get("is_active"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Kullanıcı hesabı bulunamadı veya pasif durumda.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


def require_superadmin(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Ensures user has 'superadmin' role."""
    if current_user.get("role") != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bu işlem yalnızca Süper Yönetici (Super Admin) yetkisi gerektirir.",
        )
    return current_user


def check_branch_access(user: Dict[str, Any], branch_id: str) -> bool:
    """
    Super Admin has access to all branches.
    Branch Managers & Agents only have access to their assigned branch.
    """
    role = user.get("role")
    if role == "superadmin":
        return True

    user_branch = user.get("branch_id")
    if not user_branch:
        return False

    return user_branch.strip().lower() == branch_id.strip().lower()


def require_branch_access(branch_id: str, current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Dependency verifying user has permission to access specified branch."""
    if not check_branch_access(current_user, branch_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Bu şubeye ({branch_id}) erişim yetkiniz bulunmamaktadır.",
        )
    return current_user
