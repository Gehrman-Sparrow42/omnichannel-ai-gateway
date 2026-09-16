import os
import json
import time
import asyncio
import logging
import socket
import subprocess
import atexit
from pathlib import Path
from typing import Dict, Any, List, Optional, TypedDict
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, status, Request, Response, Depends, Query, Path as FPath
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import config
import database
import auth
from auth import require_superadmin, require_branch_access, get_current_user
from security import (
    security_guard,
    login_rate_limiter,
    api_budget_guard,
    endpoint_limiter,
    append_audit_log_file,
    verify_audit_log_chain,
    resolve_audit_log_path,
    check_append_only_attribute,
    enable_append_only_attribute,
    secure_session_directories,
    ensure_gitignore_entries,
)
import notifier
from debounce import debounce_buffer
import llm_router
from adapters import get_adapter, ADAPTERS_REGISTRY
import system_monitor

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("omni-main")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


# =====================================================================
# Debounce Message Flush Processor Callback
# =====================================================================

async def process_flushed_message(
    branch_id: str,
    channel: str,
    customer_id: str,
    customer_name: Optional[str],
    aggregated_text: str,
):
    """
    Core AI processing pipeline invoked after sliding debounce aggregates
    rapid customer messages into a unified cohesive turn.
    """
    b_id = branch_id.strip().lower()
    chan = channel.strip().lower()
    c_id = customer_id.strip()
    c_name = customer_name.strip() if customer_name else c_id
    adapter = get_adapter(chan)

    logger.info(
        "[PIPELINE] Processing message from %s on branch '%s' (%s): \"%s...\"",
        c_id, b_id, chan, aggregated_text[:60]
    )

    # 1. Handle special /reset command
    if aggregated_text.strip().lower() == "/reset":
        database.save_message(b_id, chan, c_id, direction="inbound", role="user", content="/reset", customer_name=c_name)
        reset_reply = "Sohbet geçmişiniz sıfırlandı. Size nasıl yardımcı olabilirim? ✨"
        database.save_message(b_id, chan, c_id, direction="outbound", role="assistant", content=reset_reply)
        if adapter:
            await adapter.send_message(b_id, c_id, reset_reply)
        return

    # 1b. Check Trigger Prefix if configured (e.g. -test)
    settings = database.get_system_settings()
    prefix = settings.get("trigger_prefix", config.TRIGGER_PREFIX).strip()
    clean_text = aggregated_text.strip()
    if prefix:
        if clean_text.lower().startswith(prefix.lower()):
            clean_text = clean_text[len(prefix):].strip()
            if not clean_text:
                return
        else:
            # Does not start with required test prefix -> ignore without responding
            logger.info("[TRIGGER_PREFIX] Ignored message without prefix '%s' from %s", prefix, c_id)
            return

    # 2. Check if conversation is currently muted (human takeover active)
    is_muted, mute_reason = database.is_conversation_muted(b_id, chan, c_id)
    if is_muted:
        logger.info("[PIPELINE] Chat %s:%s is muted (%s). Saving user message without AI reply.", chan, c_id, mute_reason)
        database.save_message(b_id, chan, c_id, direction="inbound", role="user", content=clean_text, customer_name=c_name)
        return

    # 3. Rate limiting check per (channel:customer_id)
    rate_key = f"{chan}:{c_id}"
    allowed, rate_msg = security_guard.check_rate_limit(rate_key)
    if not allowed:
        database.save_message(b_id, chan, c_id, direction="inbound", role="user", content=clean_text, customer_name=c_name)
        database.save_message(b_id, chan, c_id, direction="outbound", role="assistant", content=rate_msg or "")
        if adapter and rate_msg:
            await adapter.send_message(b_id, c_id, rate_msg)
        return

    # 4. Input Safety Check (Jailbreak, Prompt Injection, Off-Topic Chatbot Abuse)
    is_safe, safety_refusal = security_guard.check_input_safety(clean_text)
    if not is_safe and safety_refusal:
        database.save_message(b_id, chan, c_id, direction="inbound", role="user", content=clean_text, customer_name=c_name)
        database.save_message(b_id, chan, c_id, direction="outbound", role="assistant", content=safety_refusal)
        if adapter:
            await adapter.send_message(b_id, c_id, safety_refusal)
        return

    # 5. Intent: Human Intervention / Coordinator Handover Request
    if security_guard.detect_intervention_intent(clean_text):
        logger.warning("[INTERVENTION] Human handover intent detected for %s on branch %s.", c_id, b_id)
        # Mute bot for 2 hours
        database.set_conversation_mute(b_id, chan, c_id, is_muted=True, hours=2, reason="Müşteri yetkili devralma talep etti")
        handover_reply = (
            "Talebinizi aldım! ✨ Şube yetkilimiz konuyu devraldı; "
            "en kısa sürede sizinle bu sohbet üzerinden doğrudan iletişime geçecektir."
        )
        database.save_message(b_id, chan, c_id, direction="inbound", role="user", content=clean_text, customer_name=c_name)
        database.save_message(b_id, chan, c_id, direction="outbound", role="assistant", content=handover_reply)

        if adapter:
            await adapter.send_message(b_id, c_id, handover_reply)

        # Dispatch urgent loud mobile push alarm via ntfy.sh
        branch_info = config.get_branch_by_id(b_id)
        branch_name = branch_info["name"] if branch_info else b_id
        asyncio.create_task(
            notifier.send_intervention_alert(
                branch_id=b_id,
                channel=chan,
                customer_id=c_id,
                customer_name=c_name,
                user_message=clean_text,
                branch_name=branch_name,
            )
        )
        return

    # 6. Intent: Reservation / Booking Lead Detection
    is_res_lead = security_guard.detect_reservation_intent(clean_text)
    if is_res_lead:
        logger.info("[LEAD] Booking intent detected for %s on branch %s.", c_id, b_id)
        database.set_conversation_lead(b_id, chan, c_id, is_lead=True, lead_details=f"Talep: {clean_text[:100]}")
        branch_info = config.get_branch_by_id(b_id)
        branch_name = branch_info["name"] if branch_info else b_id
        asyncio.create_task(
            notifier.send_reservation_lead_alert(
                branch_id=b_id,
                channel=chan,
                customer_id=c_id,
                customer_name=c_name,
                user_message=clean_text,
                branch_name=branch_name,
            )
        )

    # 7. Query AI LLM Engine with Branch Knowledge & History
    past_history = database.get_history(b_id, chan, c_id, limit=8)

    try:
        raw_reply = await llm_router.query_llm_for_branch(b_id, clean_text, past_history)
        sanitized_reply = security_guard.sanitize_output(raw_reply)
    except Exception as exc:
        logger.exception("Error during LLM query for branch %s: %s", b_id, exc)
        sanitized_reply = (
            "Değerli misafirimiz, şu anda mesajınızı yanıtlarken geçici bir yoğunluk oluştu. "
            "Şube yetkilimiz en kısa sürede bu sohbet üzerinden sizinle iletişime geçecektir. ✨"
        )

    # 8. Save Assistant Reply & Dispatch Outbound via Channel Adapter
    database.save_message(b_id, chan, c_id, direction="outbound", role="assistant", content=sanitized_reply)
    append_audit_log_file(recipient_jid=f"{chan}:{c_id}", message_preview=sanitized_reply, status="SENT")
    if adapter:
        await adapter.send_message(b_id, c_id, sanitized_reply)


BRIDGE_DIR = Path(__file__).resolve().parent.parent / "bridge"
bridge_process: Optional[subprocess.Popen] = None


def is_bridge_port_open(port: int = 3001) -> bool:
    """Checks if the bridge management port 3001 is listening."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.4)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False


def ensure_bridge_service_running() -> bool:
    """Automatically launches the Node.js WhatsApp Bridge if not already running (local mode)."""
    global bridge_process
    if os.getenv("RUNNING_IN_DOCKER", "false").lower() == "true":
        logger.info("Running in Docker container. WhatsApp Bridge is orchestrated separately.")
        return True

    if is_bridge_port_open(3001):
        return True

    if not BRIDGE_DIR.exists():
        return False

    logger.info("WhatsApp Bridge (port 3001) is offline. Auto-launching Node.js bridge...")
    try:
        bridge_process = subprocess.Popen(
            ["node", "index.js"],
            cwd=str(BRIDGE_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        # Wait up to 4 seconds for port 3001 to bind
        for _ in range(16):
            time.sleep(0.25)
            if is_bridge_port_open(3001):
                logger.info("WhatsApp Bridge process successfully started and bound to port 3001.")
                return True
    except Exception as exc:
        logger.error("Failed to auto-spawn Node.js bridge: %s", exc)

    return is_bridge_port_open(3001)


def cleanup_bridge_service():
    """Cleanly terminates child bridge process on backend shutdown."""
    global bridge_process
    if bridge_process and bridge_process.poll() is None:
        logger.info("Stopping child WhatsApp bridge process...")
        bridge_process.terminate()
        try:
            bridge_process.wait(timeout=2.0)
        except Exception:
            bridge_process.kill()


atexit.register(cleanup_bridge_service)


# =====================================================================
# Application Lifespan Context
# =====================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initializes multi-tenant database, debounce buffer, and security parameters."""
    logger.info("Starting Navitas Spa & Wellness Omnichannel Branch Assistant...")
    database.init_db()
    debounce_buffer.set_flush_callback(process_flushed_message)

    # Forensic & Isolation Sweeps
    ensure_gitignore_entries()
    secure_session_directories()

    # KVKK Data minimization: automatically prune messages older than 30 days
    try:
        deleted = database.cleanup_old_messages(days=30)
        if deleted > 0:
            logger.info("Startup KVKK cleanup: pruned %d messages older than 30 days.", deleted)
    except Exception as e:
        logger.warning("Failed to run message retention cleanup: %s", e)

    # Auto-ensure WhatsApp bridge is online
    ensure_bridge_service_running()

    logger.info("System fully online with %d active branches and 4 channel adapters.", len(config.BRANCHES_CATALOG))
    yield
    logger.info("Shutting down Omnichannel Assistant...")
    debounce_buffer.clear_all()
    cleanup_bridge_service()


app = FastAPI(
    title="Navitas Spa & Wellness Omnichannel AI Assistant & Unified Control Center",
    description="Multi-Tenant Control Hub supporting 17 Luxury Hotel Spas across WhatsApp, Instagram, Messenger, and Telegram.",
    version="2.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =====================================================================
# 1. Omnichannel Inbound Webhooks
# =====================================================================

@app.get("/webhook/{channel}/{branch_id}")
async def verify_channel_webhook(
    channel: str,
    branch_id: str,
    request: Request,
):
    """
    Handles challenge verification for Meta (WhatsApp, Instagram, Messenger) and Telegram.
    """
    adapter = get_adapter(channel)
    if not adapter:
        raise HTTPException(status_code=404, detail=f"Desteklenmeyen kanal: {channel}")

    query_params = dict(request.query_params)
    headers = dict(request.headers)
    is_valid, response_data = adapter.verify_webhook(query_params, headers)

    if is_valid:
        return Response(content=str(response_data), media_type="text/plain")
    raise HTTPException(status_code=403, detail="Webhook verification failed")


@app.post("/webhook/{channel}/{branch_id}")
async def receive_channel_webhook(
    channel: str,
    branch_id: str,
    request: Request,
):
    """
    Universal Inbound Webhook Receiver. Normalizes payload and feeds to Sliding Debounce Buffer.
    """
    adapter = get_adapter(channel)
    if not adapter:
        raise HTTPException(status_code=404, detail=f"Desteklenmeyen kanal: {channel}")

    b_info = config.get_branch_by_id(branch_id)
    if not b_info:
        raise HTTPException(status_code=404, detail=f"Geçersiz şube kodu: {branch_id}")

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    messages = await adapter.parse_inbound_webhook(payload, branch_id)
    logger.info("[WEBHOOK] Received %d normalized messages on %s for branch %s.", len(messages), channel, branch_id)

    for msg in messages:
        # Persist inbound message immediately to conversation thread
        database.save_message(
            branch_id=msg.branch_id,
            channel=msg.channel,
            customer_id=msg.customer_id,
            direction="inbound",
            role="user",
            content=msg.text,
            customer_name=msg.customer_name,
            media=msg.media,
            metadata=msg.metadata,
        )

        await debounce_buffer.add_message(
            branch_id=msg.branch_id,
            channel=msg.channel,
            customer_id=msg.customer_id,
            message=msg.text,
            customer_name=msg.customer_name,
        )

    # Immediately acknowledge webhook to avoid platform timeout/retries
    return JSONResponse(status_code=200, content={"status": "received", "count": len(messages)})


# Legacy / Bridge compatibility webhook
@app.post("/webhook")
async def receive_legacy_bridge_webhook(request: Request):
    """
    Direct bridge endpoint mapping to default or specified branch.
    """
    try:
        data = await request.json()
    except Exception:
        data = {}

    branch_id = data.get("branch_id", "olympos")
    return await receive_channel_webhook("whatsapp", branch_id, request)


# =====================================================================
# 2. Authentication & User Management Endpoints
# =====================================================================

def get_client_ip(request: Request) -> str:
    """Extracts client IP address respecting reverse proxies and headers."""
    if not request:
        return "127.0.0.1"
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


class LoginPayload(BaseModel):
    email: str
    password: str


@app.post("/api/auth/login")
async def api_login(request: Request, payload: LoginPayload):
    """Authenticates staff and generates JWT session token with brute-force protection."""
    client_ip = get_client_ip(request)

    # Check brute force lockout
    is_locked, remaining_sec = login_rate_limiter.is_locked_out(client_ip)
    if is_locked:
        logger.warning("Blocked login attempt from locked IP %s (%d sec left)", client_ip, remaining_sec)
        raise HTTPException(
            status_code=429,
            detail=f"Çok fazla hatalı şifre denemesi yapıldı. Güvenliğiniz için erişiminiz {remaining_sec} saniye engellenmiştir."
        )

    user = database.get_user_by_email(payload.email)
    if not user or not auth.verify_password(payload.password, user["password_hash"]):
        await asyncio.sleep(0.5)
        is_now_locked, remaining = login_rate_limiter.record_failure(client_ip)
        if is_now_locked:
            asyncio.create_task(
                notifier.send_ntfy_push(
                    topic=config.NTFY_GLOBAL_TOPIC,
                    title="🚨 Panel Güvenlik Alarmı",
                    message=f"Panel girişine brute-force saldırısı tespit edildi! IP ({client_ip}) 15 dakika engellendi.",
                    priority=5,
                    tags=["rotating_light", "warning"],
                )
            )
            raise HTTPException(
                status_code=429,
                detail="Çok fazla hatalı deneme yapıldı! Güvenlik nedeniyle IP adresiniz 15 dakika engellendi."
            )
        raise HTTPException(status_code=401, detail=f"Hatalı e-posta veya şifre. Kalan deneme hakkınız: {remaining}")

    login_rate_limiter.record_success(client_ip)
    token = auth.create_access_token({
        "sub": user["email"],
        "user_id": user["id"],
        "role": user["role"],
        "branch_id": user.get("branch_id"),
    })

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "email": user["email"],
            "full_name": user["full_name"],
            "role": user["role"],
            "branch_id": user.get("branch_id"),
        }
    }


@app.get("/api/auth/me")
async def api_get_current_user(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Returns authenticated user profile."""
    return {
        "id": current_user["id"],
        "email": current_user["email"],
        "full_name": current_user["full_name"],
        "role": current_user["role"],
        "branch_id": current_user.get("branch_id"),
    }


@app.get("/api/users")
async def api_list_users(
    branch_id: Optional[str] = None,
    current_user: Dict[str, Any] = Depends(require_superadmin),
):
    """Lists staff users across branches."""
    return database.list_users(branch_id)


# =====================================================================
# 3. Branch Management & Knowledge Base Endpoints
# =====================================================================

@app.get("/api/branches")
async def api_list_branches(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Lists branches accessible to current user."""
    all_branches = config.list_all_branches()
    if current_user["role"] == "superadmin":
        return all_branches
    # Filter only assigned branch for managers / agents
    user_branch = current_user.get("branch_id")
    return [b for b in all_branches if b["id"] == user_branch]


@app.get("/api/branches/{branch_id}")
async def api_get_branch(
    branch_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Retrieves specific branch details."""
    if not auth.check_branch_access(current_user, branch_id):
        raise HTTPException(status_code=403, detail="Bu şubeye erişim yetkiniz bulunmamaktadır.")
    branch = config.get_branch_by_id(branch_id)
    if not branch:
        raise HTTPException(status_code=404, detail="Şube bulunamadı.")
    return branch


@app.get("/api/branches/{branch_id}/knowledge")
async def api_get_branch_knowledge(
    branch_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Retrieves branch markdown knowledge base."""
    if not auth.check_branch_access(current_user, branch_id):
        raise HTTPException(status_code=403, detail="Bu şubeye erişim yetkiniz bulunmamaktadır.")
    kb = database.get_branch_knowledge(branch_id)
    if not kb:
        raise HTTPException(status_code=404, detail="Şube bilgi bankası bulunamadı.")
    return kb


class UpdateKnowledgePayload(BaseModel):
    content_markdown: str


@app.put("/api/branches/{branch_id}/knowledge")
async def api_update_branch_knowledge(
    branch_id: str,
    payload: UpdateKnowledgePayload,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Updates branch knowledge base with instant zero-downtime cache invalidation."""
    if not auth.check_branch_access(current_user, branch_id):
        raise HTTPException(status_code=403, detail="Bu şubenin bilgi bankasını güncelleme yetkiniz yok.")

    success = database.update_branch_knowledge(
        branch_id=branch_id,
        content_markdown=payload.content_markdown,
        updated_by=current_user["email"],
    )
    if success:
        llm_router.invalidate_branch_prompt_cache(branch_id)
        return {"status": "success", "message": "Bilgi bankası güncellendi ve AI prompt önbelleği yenilendi."}
    raise HTTPException(status_code=500, detail="Bilgi bankası güncellenirken hata oluştu.")


# =====================================================================
# 4. Unified Inbox & Live Chat Endpoints
# =====================================================================

@app.get("/api/conversations")
async def api_list_conversations(
    branch_id: Optional[str] = Query(default=None),
    channel: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    is_lead: Optional[int] = Query(default=None),
    search: Optional[str] = Query(default=None),
    limit: int = Query(default=100, le=200),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Unified Inbox conversation list. Scoped by branch permissions.
    """
    target_branch = branch_id
    if current_user["role"] != "superadmin":
        target_branch = current_user.get("branch_id")

    return database.list_conversations(
        branch_id=target_branch,
        channel=channel,
        status=status,
        is_lead=is_lead,
        search_query=search,
        limit=limit,
    )


@app.get("/api/conversations/{conv_id}")
async def api_get_conversation(
    conv_id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Gets single conversation thread details."""
    conv = database.get_conversation_by_id(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Sohbet bulunamadı.")
    if not auth.check_branch_access(current_user, conv["branch_id"]):
        raise HTTPException(status_code=403, detail="Bu sohbete erişim yetkiniz yok.")
    return conv


@app.get("/api/conversations/{conv_id}/messages")
async def api_get_conversation_messages(
    conv_id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Returns full message history thread for conversation."""
    conv = database.get_conversation_by_id(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Sohbet bulunamadı.")
    if not auth.check_branch_access(current_user, conv["branch_id"]):
        raise HTTPException(status_code=403, detail="Bu sohbete erişim yetkiniz yok.")
    return database.get_conversation_messages(conv_id, limit=100)


class AgentReplyPayload(BaseModel):
    message: str


@app.post("/api/conversations/{conv_id}/reply")
async def api_agent_reply(
    conv_id: int,
    payload: AgentReplyPayload,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Live human agent takeover: sends manual reply to customer across
    WhatsApp, Instagram, Telegram, or Messenger.
    """
    conv = database.get_conversation_by_id(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Sohbet bulunamadı.")
    if not auth.check_branch_access(current_user, conv["branch_id"]):
        raise HTTPException(status_code=403, detail="Bu sohbete mesaj gönderme yetkiniz yok.")

    text = payload.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Mesaj metni boş olamaz.")

    b_id = conv["branch_id"]
    chan = conv["channel"]
    c_id = conv["customer_id"]

    # 1. Save agent message to database
    saved_msg = database.save_message(
        branch_id=b_id,
        channel=chan,
        customer_id=c_id,
        direction="outbound",
        role="agent",
        content=text,
        metadata={"agent_id": current_user["id"], "agent_name": current_user["full_name"]},
    )
    append_audit_log_file(recipient_jid=f"{chan}:{c_id}", message_preview=text, status="SENT")

    # 2. Dispatch outbound message via channel adapter
    adapter = get_adapter(chan)
    dispatch_success = False
    if adapter:
        dispatch_success = await adapter.send_message(b_id, c_id, text)

    return {
        "status": "sent",
        "message": saved_msg,
        "dispatched_to_channel": dispatch_success,
    }


class MuteTogglePayload(BaseModel):
    is_muted: bool
    hours: int = 2
    reason: Optional[str] = "Manuel temsilci müdahalesi"


@app.post("/api/conversations/{conv_id}/mute")
async def api_toggle_conversation_mute(
    conv_id: int,
    payload: MuteTogglePayload,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Mutes or unmutes AI bot for a specific conversation."""
    conv = database.get_conversation_by_id(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Sohbet bulunamadı.")
    if not auth.check_branch_access(current_user, conv["branch_id"]):
        raise HTTPException(status_code=403, detail="Yetkisiz işlem.")

    database.set_conversation_mute(
        branch_id=conv["branch_id"],
        channel=conv["channel"],
        customer_id=conv["customer_id"],
        is_muted=payload.is_muted,
        hours=payload.hours,
        reason=payload.reason,
    )
    return {"status": "success", "is_muted": payload.is_muted}


class LeadTogglePayload(BaseModel):
    is_lead: bool
    lead_details: Optional[str] = None


@app.post("/api/conversations/{conv_id}/lead")
async def api_toggle_conversation_lead(
    conv_id: int,
    payload: LeadTogglePayload,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Tags or untags conversation as a booking lead."""
    conv = database.get_conversation_by_id(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Sohbet bulunamadı.")
    if not auth.check_branch_access(current_user, conv["branch_id"]):
        raise HTTPException(status_code=403, detail="Yetkisiz işlem.")

    database.set_conversation_lead(
        branch_id=conv["branch_id"],
        channel=conv["channel"],
        customer_id=conv["customer_id"],
        is_lead=payload.is_lead,
        lead_details=payload.lead_details,
    )
    return {"status": "success", "is_lead": payload.is_lead}


@app.post("/api/conversations/{conv_id}/clear")
async def api_clear_conversation(
    conv_id: int,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Purges chat history for a specific conversation."""
    conv = database.get_conversation_by_id(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Sohbet bulunamadı.")
    if not auth.check_branch_access(current_user, conv["branch_id"]):
        raise HTTPException(status_code=403, detail="Yetkisiz işlem.")

    deleted = database.clear_conversation_messages(conv_id)
    system_monitor.add_system_log("info", "Sohbet Temizlendi", f"Sohbet #{conv_id} geçmişi temizlendi ({deleted} mesaj).", conv["branch_id"], conv["channel"])
    return {"status": "success", "deleted_count": deleted}


class SimulatorPayload(BaseModel):
    branch_id: str
    channel: str = "whatsapp"
    customer_id: str = "905551234567"
    customer_name: Optional[str] = "Simülasyon Kullanıcısı"
    message: str


@app.post("/api/simulator/send")
async def api_simulator_send(
    request: Request,
    payload: SimulatorPayload,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Live Chat Simulator endpoint. Protected by client IP rate limiting to prevent API key drain.
    """
    client_ip = get_client_ip(request)
    allowed, limit_msg = endpoint_limiter.check_limit("simulator", client_ip, max_per_minute=10)
    if not allowed:
        raise HTTPException(status_code=429, detail=limit_msg)

    b_id = payload.branch_id.strip().lower()
    chan = payload.channel.strip().lower()
    text = payload.message.strip()

    if not text:
        raise HTTPException(status_code=400, detail="Mesaj metni boş olamaz.")

    branch_info = config.get_branch_by_id(b_id)
    if not branch_info:
        raise HTTPException(status_code=404, detail=f"Geçersiz şube: {b_id}")

    if not auth.check_branch_access(current_user, b_id):
        raise HTTPException(status_code=403, detail="Bu şube için simülasyon yetkiniz yok.")

    system_monitor.add_system_log("info", "Simülasyon Mesajı Alındı", f"'{text[:40]}...' ({chan.upper()})", b_id, chan)

    # Persist immediately to conversation
    database.save_message(
        branch_id=b_id,
        channel=chan,
        customer_id=payload.customer_id,
        direction="inbound",
        role="user",
        content=text,
        customer_name=payload.customer_name,
        metadata={"source": "simulator", "simulated_by": current_user["email"]},
    )

    # Queue in sliding debounce buffer
    await debounce_buffer.add_message(
        branch_id=b_id,
        channel=chan,
        customer_id=payload.customer_id,
        message=text,
        customer_name=payload.customer_name,
    )

    return {
        "status": "queued",
        "branch_id": b_id,
        "channel": chan,
        "customer_id": payload.customer_id,
        "message": text,
        "debounce_seconds": config.DEBOUNCE_SECONDS,
    }


class DemoPresetItem(TypedDict):
    branch_id: str
    channel: str
    customer_id: str
    customer_name: str
    message: str
    is_lead: bool
    lead_details: Optional[str]


DEMO_PRESETS_DATA: List[DemoPresetItem] = [
    {
        "branch_id": "istanbul-airport",
        "channel": "whatsapp",
        "customer_id": "+447911123456",
        "customer_name": "Sarah Jenkins",
        "message": "Hello! I have a 3-hour layover at Istanbul Airport on my way to London. Do you have an express anti-jetlag back massage and shower facilities available right now? Where are you located inside the airport?",
        "is_lead": False,
        "lead_details": None,
    },
    {
        "branch_id": "marmaris-lumira",
        "channel": "whatsapp",
        "customer_id": "+79261234567",
        "customer_name": "Elena Rostova",
        "message": "Здравствуйте! Мы отдыхаем в отеле в Мармарисе, сильно обгорели на солнце. Есть ли у вас охлаждающий уход с алоэ вера или мягкий успокаивающий массаж для двоих? Можно ли записаться на вечер?",
        "is_lead": True,
        "lead_details": "Güneş yanığı sonrası çift masajı ve aloe vera bakımı rezervasyonu",
    },
    {
        "branch_id": "topuk-yaylasi",
        "channel": "whatsapp",
        "customer_id": "+491701234567",
        "customer_name": "Hans Schmidt",
        "message": "Guten Tag! Wir machen Urlaub im Topuk Yaylası Resort und haben eine lange Bergwanderung im Wald hinter uns. Bieten Sie Sportmassagen oder Hot-Stone-Therapien gegen Muskelkater an? Welche Öffnungszeiten haben Sie?",
        "is_lead": False,
        "lead_details": None,
    },
    {
        "branch_id": "mall-of-istanbul",
        "channel": "whatsapp",
        "customer_id": "+905329998877",
        "customer_name": "Murat & Deniz Yılmaz",
        "message": "Merhaba, evlilik yıldönümümüz için VIP süitinizde çift masajı ve geleneksel Türk hamamı paketi için randevu almak istiyoruz. Cumartesi 16:00 müsait mi? Telefon numaram: 0532 999 88 77, acil dönüş bekliyorum.",
        "is_lead": True,
        "lead_details": "VIP Süit Çift Masajı & Hamam Paketi (Tel: 0532 999 88 77)",
    },
    {
        "branch_id": "laleli",
        "channel": "instagram",
        "customer_id": "@can_adventures",
        "customer_name": "Can Demir",
        "message": "Selamlar! Tarihi yarımada ve Kapalıçarşı turundan geldik, ayaklarımız çok yoruldu. Geleneksel kese-köpük Türk hamamı ritüeli ve Bali masajı yaptırmak istiyoruz, yanımızda havlu veya peştamal getirmemiz gerekiyor mu?",
        "is_lead": False,
        "lead_details": None,
    },
]


@app.post("/api/demo/seed")
async def api_demo_seed(
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Seeds 5 rich, realistic, multilingual demo conversations across 5 different branches.
    Protected by 30-second IP cooldown and rate limits to safeguard the LLM API quota.
    """
    client_ip = get_client_ip(request)
    allowed, limit_msg = endpoint_limiter.check_limit("demo_seed", client_ip, max_per_minute=2, cooldown_sec=30)
    if not allowed:
        raise HTTPException(status_code=429, detail=limit_msg)

    results = []
    for sc in DEMO_PRESETS_DATA:
        b_id: str = str(sc["branch_id"])
        chan: str = str(sc["channel"])
        c_id: str = str(sc["customer_id"])
        c_name: str = str(sc["customer_name"])
        text: str = str(sc["message"])

        # 1. Save user inbound message
        database.save_message(
            branch_id=b_id,
            channel=chan,
            customer_id=c_id,
            direction="inbound",
            role="user",
            content=text,
            customer_name=c_name,
            metadata={"source": "demo_seed", "seeded_by": current_user["email"]},
        )

        # 2. Check reservation lead intent
        is_res_lead = security_guard.detect_reservation_intent(text) or bool(sc.get("is_lead", False))
        if is_res_lead:
            lead_details_val: Optional[str] = sc.get("lead_details")
            lead_info_str: str = lead_details_val if lead_details_val else f"Talep: {text[:100]}"
            database.set_conversation_lead(
                b_id,
                chan,
                c_id,
                is_lead=True,
                lead_details=lead_info_str,
            )

        # 3. Query LLM
        try:
            raw_reply = await llm_router.query_llm_for_branch(b_id, text, [])
            sanitized_reply = security_guard.sanitize_output(raw_reply)
        except Exception as exc:
            logger.exception("LLM generation error during demo seed for %s: %s", b_id, exc)
            sanitized_reply = "Değerli misafirimiz, Navitas Spa olarak size yardımcı olmaktan mutluluk duyarız. Randevu ve detaylı bilgi için bize her zaman ulaşabilirsiniz. ✨"

        # 4. Save AI assistant reply
        database.save_message(
            branch_id=b_id,
            channel=chan,
            customer_id=c_id,
            direction="outbound",
            role="assistant",
            content=sanitized_reply,
        )

        system_monitor.add_system_log(
            "info",
            "Demo Senaryosu Yüklendi",
            f"{b_id.upper()}: {c_name} ({chan.upper()})",
            b_id,
            chan,
        )

        results.append({
            "branch_id": b_id,
            "channel": chan,
            "customer_id": c_id,
            "customer_name": c_name,
            "is_lead": is_res_lead,
            "reply_preview": sanitized_reply[:120],
        })

    return {
        "status": "success",
        "message": "5 farklı lokasyonda gerçekçi demo sohbetleri başarıyla oluşturuldu!",
        "count": len(results),
        "conversations": results,
    }



class TestAlertPayload(BaseModel):
    branch_id: Optional[str] = "olympos"
    priority: int = 5


@app.post("/api/notifier/test")
async def api_test_notifier(
    payload: TestAlertPayload,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Sends a live test alert to ntfy.sh to verify mobile push sound."""
    b_id = payload.branch_id or "olympos"
    target_topic = f"omni-camp-{b_id.strip().lower()}"
    b_info = config.get_branch_by_id(b_id)
    b_name = b_info["name"] if b_info else b_id.title()

    sent = await notifier.send_ntfy_push(
        topic=target_topic,
        title=f"🔔 [{b_name.upper()}] TEST BİLDİRİMİ",
        message=f"Omnichannel Kontrol Masası test alarmıdır. Mobil push ses ve butonları başarıyla çalışıyor! (Gönderen: {current_user['email']})",
        priority=payload.priority,
        tags=["test_tube", "bell"],
        actions=[{"action": "view", "label": "🖥️ Paneli Aç", "url": f"http://127.0.0.1:{config.PORT}"}],
    )
    system_monitor.add_system_log("info", "Test Bildirimi Gönderildi", f"Topic: {target_topic}, Öncelik: {payload.priority}", b_id, "ntfy")
    return {"status": "sent" if sent else "failed", "topic": target_topic}


# =====================================================================
# 5. Analytics, System Metrics & Logs Endpoints
# =====================================================================

@app.get("/api/system/metrics")
async def api_system_metrics(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Returns hardware resource usage and uptime."""
    metrics = system_monitor.get_system_metrics()
    metrics["active_debounce_queues"] = debounce_buffer.get_pending_count()
    return metrics


@app.get("/api/system/logs")
async def api_system_logs(
    level: Optional[str] = Query(default=None),
    branch_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, le=150),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Returns recent system events from in-memory ring buffer."""
    return system_monitor.get_recent_system_logs(level=level, branch_id=branch_id, limit=limit)


@app.post("/api/system/logs/clear")
async def api_clear_system_logs(current_user: Dict[str, Any] = Depends(require_superadmin)):
    """Clears system logs ring buffer."""
    system_monitor.clear_system_logs()
    return {"status": "cleared"}


@app.get("/api/analytics/global")
async def api_global_analytics(current_user: Dict[str, Any] = Depends(require_superadmin)):
    """Global multi-branch operational analytics."""
    return database.get_global_analytics()


@app.get("/api/analytics/branch/{branch_id}")
async def api_branch_analytics(
    branch_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Single branch metrics."""
    if not auth.check_branch_access(current_user, branch_id):
        raise HTTPException(status_code=403, detail="Bu şubeye erişim yetkiniz yok.")
    return database.get_branch_analytics(branch_id)


@app.get("/api/system/settings")
async def api_get_settings(current_user: Dict[str, Any] = Depends(require_superadmin)):
    """Retrieves system settings including filters and LLM configuration with masked secrets."""
    raw_settings = database.get_system_settings()
    settings: Dict[str, Any] = dict(raw_settings)
    # Mask API key for security - never return raw key to client
    active_key = settings.get("llm_api_key", config.LLM_API_KEY)
    if active_key:
        key_str = str(active_key)
        settings["llm_api_key_masked"] = f"{key_str[:7]}...{key_str[-4:]}" if len(key_str) > 12 else "***"
    else:
        settings["llm_api_key_masked"] = ""
    
    # Strictly remove plaintext key from response payload
    settings.pop("llm_api_key", None)
    settings["ai_backend"] = llm_router.get_llm_backend_info()
    settings["budget_stats"] = api_budget_guard.get_budget_stats()
    return settings


@app.get("/api/settings/filters", tags=["Settings"])
async def api_get_filter_settings(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Retrieves current contact filter, blacklist, debounce duration, trigger prefix, and LLM configuration."""
    settings = database.get_system_settings()
    masked_key = ""
    active_key = settings.get("llm_api_key", config.LLM_API_KEY)
    if active_key:
        masked_key = f"{active_key[:6]}...{active_key[-4:]}" if len(active_key) > 10 else "***"

    return {
        "only_unknown_contacts": settings.get("only_unknown_contacts", "false").lower() == "true",
        "blacklist": [x.strip() for x in settings.get("blacklist", "").split(",") if x.strip()],
        "debounce_seconds": int(settings.get("debounce_seconds", str(config.DEBOUNCE_SECONDS))),
        "trigger_prefix": settings.get("trigger_prefix", config.TRIGGER_PREFIX),
        "ntfy_topic": settings.get("ntfy_topic", config.NTFY_GLOBAL_TOPIC),
        "llm_provider": settings.get("llm_provider", config.LLM_PROVIDER),
        "llm_model": settings.get("llm_model", config.LLM_MODEL),
        "has_llm_api_key": bool(active_key),
        "masked_api_key": masked_key,
        "ai_backend": llm_router.get_llm_backend_info(),
        "budget_stats": api_budget_guard.get_budget_stats(),
    }


class FilterSettingsPayload(BaseModel):
    only_unknown_contacts: Optional[bool] = None
    blacklist: Optional[List[str]] = None
    debounce_seconds: Optional[int] = None
    trigger_prefix: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    llm_base_url: Optional[str] = None


@app.post("/api/settings/filters", tags=["Settings"])
async def api_update_filter_settings(
    payload: FilterSettingsPayload,
    current_user: Dict[str, Any] = Depends(require_superadmin),
):
    """Updates contact filter, blacklist, debounce, trigger prefix, and LLM configuration, syncing to WhatsApp Bridge."""
    current = database.get_system_settings()
    updates = {}
    if payload.only_unknown_contacts is not None:
        updates["only_unknown_contacts"] = "true" if payload.only_unknown_contacts else "false"
        database.set_system_setting("only_unknown_contacts", updates["only_unknown_contacts"])
    if payload.blacklist is not None:
        updates["blacklist"] = ",".join([b.strip() for b in payload.blacklist if b.strip()])
        database.set_system_setting("blacklist", updates["blacklist"])
    if payload.debounce_seconds is not None:
        val = max(1, min(120, payload.debounce_seconds))
        updates["debounce_seconds"] = str(val)
        database.set_system_setting("debounce_seconds", str(val))
        debounce_buffer.window_seconds = val
    if payload.trigger_prefix is not None:
        updates["trigger_prefix"] = payload.trigger_prefix.strip()
        database.set_system_setting("trigger_prefix", updates["trigger_prefix"])

    if payload.llm_api_key is not None and payload.llm_api_key.strip():
        clean_key = payload.llm_api_key.strip()
        # Protect against saving masked/placeholder strings back to the DB
        if not ("..." in clean_key or "***" in clean_key):
            updates["llm_api_key"] = clean_key
            database.set_system_setting("llm_api_key", updates["llm_api_key"])
    if payload.llm_provider is not None:
        updates["llm_provider"] = payload.llm_provider.strip().lower()
        database.set_system_setting("llm_provider", updates["llm_provider"])
    if payload.llm_model is not None:
        updates["llm_model"] = payload.llm_model.strip()
        database.set_system_setting("llm_model", updates["llm_model"])
    if payload.llm_base_url is not None:
        updates["llm_base_url"] = payload.llm_base_url.strip()
        database.set_system_setting("llm_base_url", updates["llm_base_url"])

    if any(k in updates for k in ["llm_api_key", "llm_provider", "llm_model", "llm_base_url"]):
        config.update_llm_config(
            provider=updates.get("llm_provider", current.get("llm_provider")),
            api_key=updates.get("llm_api_key", current.get("llm_api_key")),
            model=updates.get("llm_model", current.get("llm_model")),
            base_url=updates.get("llm_base_url", current.get("llm_base_url")),
        )
        llm_router.invalidate_branch_prompt_cache()

    # Sync filter parameters to WhatsApp Bridge process
    bridge_payload = {
        "only_unknown_contacts": updates.get("only_unknown_contacts", current.get("only_unknown_contacts", "false")) == "true",
        "blacklist": updates.get("blacklist", current.get("blacklist", "")).split(","),
        "debounce_seconds": int(updates.get("debounce_seconds", current.get("debounce_seconds", "15"))),
        "trigger_prefix": updates.get("trigger_prefix", current.get("trigger_prefix", "")),
    }
    try:
        async with httpx.AsyncClient(timeout=4.0) as http_c:
            await http_c.post(f"{BRIDGE_API_URL}/api/config", json=bridge_payload)
    except Exception as exc:
        logger.warning("[BRIDGE] Could not sync filter settings to WhatsApp Bridge: %s", exc)

    system_monitor.add_system_log("info", "Filtre Ayarları Güncellendi", f"Önek: '{updates.get('trigger_prefix', current.get('trigger_prefix', ''))}', Rehber Koruması: {bridge_payload['only_unknown_contacts']}")
    return {"status": "success", "settings": updates}


class UpdateSettingsPayload(BaseModel):
    llm_provider: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    llm_base_url: Optional[str] = None
    trigger_prefix: Optional[str] = None
    only_unknown_contacts: Optional[bool] = None
    debounce_seconds: Optional[int] = None


@app.post("/api/system/settings")
async def api_update_settings(
    payload: UpdateSettingsPayload,
    current_user: Dict[str, Any] = Depends(require_superadmin),
):
    """Updates runtime system settings."""
    if payload.llm_provider:
        database.set_system_setting("llm_provider", payload.llm_provider.strip().lower())
    
    clean_key = None
    if payload.llm_api_key and payload.llm_api_key.strip():
        k = payload.llm_api_key.strip()
        if not ("..." in k or "***" in k):
            clean_key = k
            database.set_system_setting("llm_api_key", clean_key)

    if payload.llm_model:
        database.set_system_setting("llm_model", payload.llm_model.strip())
    if payload.llm_base_url:
        database.set_system_setting("llm_base_url", payload.llm_base_url.strip())
    if payload.trigger_prefix is not None:
        database.set_system_setting("trigger_prefix", payload.trigger_prefix.strip())
    if payload.only_unknown_contacts is not None:
        database.set_system_setting("only_unknown_contacts", "true" if payload.only_unknown_contacts else "false")
    if payload.debounce_seconds is not None:
        database.set_system_setting("debounce_seconds", str(payload.debounce_seconds))
        debounce_buffer.window_seconds = payload.debounce_seconds

    current_db = database.get_system_settings()
    config.update_llm_config(
        provider=payload.llm_provider,
        api_key=clean_key or current_db.get("llm_api_key"),
        model=payload.llm_model,
        base_url=payload.llm_base_url,
    )

    llm_router.invalidate_branch_prompt_cache()
    system_monitor.add_system_log("info", "Sistem Ayarları Güncellendi", f"Sağlayıcı: {payload.llm_provider or 'değişmedi'}")
    return {"status": "success", "message": "Sistem ayarları güncellendi."}


@app.get("/api/dashboard/audit-logs", tags=["Audit"])
async def get_dashboard_audit_logs(
    limit: int = 100,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Retrieves recent tamper-evident SHA-256 Merkle chain audit records."""
    target_path = resolve_audit_log_path()
    if not target_path.exists():
        return {"logs": [], "total": 0}
    try:
        with open(target_path, "r", encoding="utf-8", errors="replace") as f:
            lines = [line.strip() for line in f if line.strip()]
        recent = lines[-limit:]
        parsed = []
        for l in reversed(recent):
            try:
                parsed.append(json.loads(l))
            except Exception:
                parsed.append({"raw": l})
        return {"logs": parsed, "total": len(lines)}
    except Exception as e:
        return {"logs": [], "total": 0, "error": str(e)}


@app.get("/api/dashboard/audit-logs/verify", tags=["Audit"])
async def verify_dashboard_audit_logs(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Cryptographically verifies SHA-256 hash continuity of all outbound messages."""
    return verify_audit_log_chain()


# =====================================================================
# WhatsApp Bridge Session & Pairing Management
# =====================================================================

BRIDGE_API_URL = os.getenv("BRIDGE_API_URL", "http://127.0.0.1:3001")


class BridgePairPayload(BaseModel):
    phone_number: str = Field(..., description="Phone number to generate 8-digit pairing code for")


class BridgeSwitchModePayload(BaseModel):
    mode: str = Field("qr", description="Pairing mode: 'qr' or 'code'")


@app.get("/api/bridge/status", tags=["WhatsApp Bridge"])
async def get_bridge_status(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Retrieves real-time WhatsApp Web session status, QR code, and connected user info."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            res = await client.get(f"{BRIDGE_API_URL}/status")
            if res.status_code == 200:
                data = res.json()
                return {
                    "connected": data.get("state") == "CONNECTED" or data.get("status") == "CONNECTED",
                    "state": data.get("state") or data.get("status", "OFFLINE"),
                    "status": data.get("status") or data.get("state", "OFFLINE"),
                    "qr": data.get("qr") or data.get("qrDataURL"),
                    "pairingCode": data.get("pairingCode"),
                    "pairingPhone": data.get("pairingPhone"),
                    "connectedUser": data.get("connectedUser"),
                    "hasQR": data.get("hasQR", False),
                    "logs": data.get("logs", []),
                }
            return {"connected": False, "state": "OFFLINE", "status": "OFFLINE", "error": f"HTTP {res.status_code}"}
    except Exception as e:
        return {"connected": False, "state": "OFFLINE", "status": "OFFLINE", "error": str(e), "hasQR": False}


@app.post("/api/bridge/connect", tags=["WhatsApp Bridge"])
async def trigger_bridge_connect(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Triggers WhatsApp client initialization and generates QR code."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(f"{BRIDGE_API_URL}/reconnect")
            return res.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"WhatsApp Bridge servisine erişilemiyor: {str(e)}")


@app.post("/api/bridge/switch-mode", tags=["WhatsApp Bridge"])
async def trigger_bridge_switch_mode(
    payload: BridgeSwitchModePayload,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Switches WhatsApp Web view between QR mode and 8-digit Pairing Code mode."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(f"{BRIDGE_API_URL}/api/switch-mode", json=payload.model_dump())
            return res.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"WhatsApp Bridge servisine erişilemiyor: {str(e)}")


@app.post("/api/bridge/pair", tags=["WhatsApp Bridge"])
async def trigger_bridge_pair(
    payload: BridgePairPayload,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Generates an 8-character pairing code for phone-number linking without scanning QR."""
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            res = await client.post(f"{BRIDGE_API_URL}/api/pair", json={"phoneNumber": payload.phone_number})
            data = res.json()
            if not res.is_success or not data.get("success", False):
                err_msg = data.get("error") or "Eşleştirme kodu oluşturulamadı"
                raise HTTPException(status_code=res.status_code if res.status_code != 200 else 400, detail=err_msg)
            return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Eşleştirme hatası: {str(e)}")


@app.post("/api/bridge/disconnect", tags=["WhatsApp Bridge"])
async def trigger_bridge_disconnect(
    body: Optional[Dict[str, Any]] = None,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Logs out / disconnects active WhatsApp Web session and optionally clears session cache."""
    clear_session = bool(body.get("clearSession", False)) if body else False
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(f"{BRIDGE_API_URL}/api/disconnect", json={"clearSession": clear_session})
            return res.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"WhatsApp Bridge servisine erişilemiyor: {str(e)}")


@app.get("/api/bridge/screenshot", tags=["WhatsApp Bridge"])
async def get_bridge_screenshot(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Captures and returns real-time Chromium WhatsApp screen for debugging."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(f"{BRIDGE_API_URL}/api/debug/screenshot")
            return Response(content=res.content, media_type="image/png")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ekran görüntüsü alınamadı: {str(e)}")


@app.get("/api/health")
async def api_health():
    """System health check endpoint with live AI backend and filter status."""
    metrics = system_monitor.get_system_metrics()
    settings = database.get_system_settings()
    return {
        "status": "healthy",
        "timestamp": time.time(),
        "branches_count": len(config.BRANCHES_CATALOG),
        "supported_channels": list(ADAPTERS_REGISTRY.keys()),
        "active_debounce_queues": debounce_buffer.get_pending_count(),
        "uptime": metrics.get("uptime"),
        "cpu_percent": metrics.get("cpu_percent"),
        "ram_display": metrics.get("ram", {}).get("display"),
        "ai_backend": llm_router.get_llm_backend_info(),
        "trigger_prefix": settings.get("trigger_prefix", config.TRIGGER_PREFIX),
        "only_unknown_contacts": settings.get("only_unknown_contacts", "false").lower() == "true",
    }


class CleanupMessagesPayload(BaseModel):
    days: int = Field(default=30, ge=1, le=365)


@app.post("/api/system/cleanup-messages", tags=["System"])
async def api_cleanup_old_messages(
    payload: CleanupMessagesPayload,
    current_user: Dict[str, Any] = Depends(require_superadmin),
):
    """KVKK / GDPR: Prunes messages older than specified days to minimize customer personal data retention."""
    deleted = database.cleanup_old_messages(days=payload.days)
    system_monitor.add_system_log(
        "warning" if deleted > 0 else "info",
        "KVKK Veri Minimizasyonu",
        f"{payload.days} günden eski {deleted} adet mesaj temizlendi (Yönetici: {current_user.get('email')})"
    )
    return {
        "success": True,
        "deleted_count": deleted,
        "retention_days": payload.days,
        "message": f"{payload.days} günden eski {deleted} adet mesaj veritabanından güvenle silindi.",
    }


@app.post("/api/system/shutdown", tags=["System"])
async def api_system_shutdown(
    current_user: Dict[str, Any] = Depends(require_superadmin),
):
    """Gracefully terminates WhatsApp bridge child process and stops the assistant service."""
    logger.warning("Shutdown initiated by admin: %s", current_user.get("email"))
    cleanup_bridge_service()
    system_monitor.add_system_log(
        "error",
        "Sistem Kapatma",
        f"Sistem kapatma sinyali verildi (Yönetici: {current_user.get('email')})"
    )

    async def _delayed_exit():
        await asyncio.sleep(1.0)
        os._exit(0)

    asyncio.create_task(_delayed_exit())
    return {"success": True, "message": "Sistem ve WhatsApp köprüsü kapatılıyor..."}


@app.get("/api/system/forensics", tags=["System"])
async def api_get_forensics(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Verifies Merkle audit log cryptographic chain and checks Linux immutable append-only (+a) attribute."""
    audit_result = verify_audit_log_chain()
    is_append_only = check_append_only_attribute()
    return {
        "audit_chain": audit_result,
        "is_append_only_protected": is_append_only,
        "platform": os.name,
    }


@app.get("/api/system/contract-pdf", tags=["System"])
async def api_download_contract_pdf(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Generates and serves the official Navitas Spa & Wellness AI Assistant Legal SLA & KVKK Contract PDF."""
    pdf_script = Path(__file__).resolve().parent.parent / "scripts" / "generate_contract_pdf.py"
    out_pdf = Path(__file__).resolve().parent.parent / "scripts" / "Navitas_Spa_AI_Asistan_Hizmet_Sozlesmesi.pdf"
    if not out_pdf.exists() and pdf_script.exists():
        subprocess.run(["python", str(pdf_script)], capture_output=True, text=True, timeout=15)

    if not out_pdf.exists():
        raise HTTPException(status_code=500, detail="Sözleşme PDF'i oluşturulamadı.")

    return FileResponse(
        str(out_pdf),
        media_type="application/pdf",
        filename="Navitas_Spa_AI_Asistan_Hizmet_Sozlesmesi.pdf",
    )


# =====================================================================
# 6. Static Frontend Mount
# =====================================================================

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/style.css")
    async def serve_css():
        return FileResponse(FRONTEND_DIR / "style.css", media_type="text/css")

    @app.get("/app.js")
    async def serve_js():
        return FileResponse(FRONTEND_DIR / "app.js", media_type="application/javascript")

    @app.get("/")
    async def serve_index():
        return FileResponse(FRONTEND_DIR / "index.html")
