import time
import re
import logging
import json
import hashlib
import os
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional, Any
from collections import defaultdict

import config
import notifier

logger = logging.getLogger("omni-security")

# Rate Limiter State: composite_key -> list of float timestamps
_request_timestamps: Dict[str, List[float]] = defaultdict(list)
_blocked_entities: Dict[str, float] = {}  # composite_key -> unblock timestamp

GENESIS_HASH = "0" * 64

REFUSAL_OFFTOPIC = (
    "Ben yalnızca Navitas Spa & Wellness merkezlerimizle ilgili konularda "
    "(masaj terapileri, hamam ritüelleri, cilt bakımı, fitness, havuz ve rezervasyon) "
    "bilgi vermek üzere görevlendirilmiş resmi asistanım. "
    "Spa dışı genel konularda, kod, şiir veya ödev yazımında destek verememekteyim. ✨"
)

REFUSAL_JAILBREAK = (
    "Üzgünüm, sistem güvenlik kuralları gereği bu tür komutları işleyemiyorum. "
    "Yalnızca Navitas Spa & Wellness hizmetleri ve randevu süreçlerinizle ilgili sorularınıza memnuniyetle yanıt verebilirim. ✨"
)

FALLBACK_COORDINATOR = (
    "Bu konuyu ilgili şube spa koordinatörümüze iletiyorum, en kısa sürede size bilgi verilecektir."
)


class SecurityGuard:
    def __init__(
        self,
        rate_limit_per_minute: int = config.RATE_LIMIT_PER_MINUTE,
        rate_limit_daily: int = config.RATE_LIMIT_DAILY,
        enable_jailbreak_filter: bool = config.ENABLE_JAILBREAK_FILTER,
        enable_offtopic_filter: bool = config.ENABLE_OFFTOPIC_FILTER,
    ):
        self.rate_limit_per_minute = rate_limit_per_minute
        self.rate_limit_daily = rate_limit_daily
        self.enable_jailbreak_filter = enable_jailbreak_filter
        self.enable_offtopic_filter = enable_offtopic_filter

        # 1. Prompt Injection & Jailbreak Patterns (TR & EN)
        self.jailbreak_patterns = [
            r"(?i)\b(ignore|bypass|override|forget)\b.*\b(instructions?|prompt|rules?|system|guidelines?)\b",
            r"(?i)\b(system\s+prompt|dan\s+mode|jailbreak|unrestricted|developer\s+mode)\b",
            r"(?i)\b(bütün|tüm|önceki)\s+(talimatları|kuralları|komutları|yönergeleri)\s+(unut|yok\s+say|sil|boşver)\b",
            r"(?i)\b(sen\s+artık|bundan\s+sonra|kendini)\b.*\b(olarak\s+davran|gibi\s+davran|rolü\s+oyna|farz\s+et)\b",
            r"(?i)\b(sistem\s+kurallarını|gizli\s+talimatları|promptunu|promptunu)\s+(yaz|göster|listele|söyle|ver)\b",
            r"(?i)\b(pretend|act\s+as\s+a?|roleplay|simulate)\b",
            r"(?i)\b(gizli\s+talimat|gizli\s+kural|sistem\s+yönergesi)\b",
            r"(?i)\b(print\s+everything\s+above|reveal\s+your\s+prompt)\b",
        ]

        # 2. Off-Topic & Misuse Patterns (Coding, Poetry, Homework, Entertainment, Romantic)
        self.offtopic_patterns = [
            # Creative / Poetry / Stories / Song lyrics
            r"(?i)\b(şiir|siir|şiiri|siiri|şiirler|şarkı\s+sözü|sarki\s+sozu|beste|mani|fıkra\s+anlat|fikra\s+anlat|masal\s+anlat|roman\s+yaz)\b",
            r"(?i)\b(poem|poetry|tell\s+a\s+joke|write\s+a\s+story|write\s+a\s+song)\b",
            
            # Programming / Coding
            r"(?i)\b(python|javascript|typescript|html|css|golang|rust|php|csharp|sql\s+query)\b",
            r"(?i)\b(kod\s+yaz|script\s+yaz|program\s+yaz|kodla|web\s+scraper|fonksiyon\s+yaz)\b",
            r"(?i)\b(write\s+code|write\s+a\s+script|write\s+a\s+function|coding)\b",
            
            # Academic / Homework
            r"(?i)\b(matematik\s+ödevi|türevi|integrali|denklemi\s+çöz|homework)\b",
            
            # Romantic / Inappropriate
            r"(?i)\b(sevgilim\s+ol|flört\s+et|bana\s+aşık\s+ol|evlenelim)\b",
            r"(?i)\b(falıma\s+bak|burcum|astroloji|rüya\s+tabiri)\b",
        ]

        # 3. Explicit Genuine Spa Whitelist (Prevents False Positives)
        self.spa_explicit_whitelist = [
            "masaj", "hamam", "kese", "köpük", "cilt bakımı", "hydrafacial", "kolajen",
            "fitness", "spor", "pilates", "havuz", "bone", "sauna", "buhar", "jakuzi",
            "bali", "isveç", "aromaterapi", "sıcak taş", "derin doku", "antistress", "sultan",
            "vip", "gelin hamamı", "peştamal", "havlu", "bornoz", "duş", "şampuan",
            "rezervasyon", "randevu", "fiyat", "ücret", "paket", "seans", "süre", "dakika",
            "şube", "otel", "hilton", "havalimanı", "airport", "transit", "layover", "uçuş",
            "hamile", "sağlık", "yaş", "çocuk", "iptal", "değişiklik", "rötar",
            "merhaba", "selam", "günaydın", "iyi günler", "iyi akşamlar", "teşekkürler", "/reset"
        ]

    def check_rate_limit(self, identifier: str) -> Tuple[bool, Optional[str]]:
        """
        Sliding-window rate limiter per (channel:customer_id).
        Returns: (is_allowed: bool, reason_message: Optional[str])
        """
        key = identifier.strip().lower()
        now = time.time()

        # Check temporary block
        if key in _blocked_entities:
            unblock_time = _blocked_entities[key]
            if now < unblock_time:
                remaining_sec = int(unblock_time - now)
                return False, f"Çok fazla istek gönderdiniz. Güvenlik nedeniyle {remaining_sec} saniye sonra tekrar deneyebilirsiniz."
            else:
                del _blocked_entities[key]

        # Clean timestamps older than 24 hours (86400 seconds)
        timestamps = [t for t in _request_timestamps[key] if now - t < 86400]

        # 1. Daily limit check
        if len(timestamps) >= self.rate_limit_daily:
            _blocked_entities[key] = now + 3600  # 1 hour cooldown
            logger.warning("Daily rate limit exceeded for entity %s (%d requests).", key, len(timestamps))
            return False, "Günlük mesaj limitine ulaştınız. Yoğunluğu önlemek için asistanımız yarın tekrar hizmetinizde olacaktır."

        # 2. Per-minute limit check (last 60 seconds)
        recent_timestamps = [t for t in timestamps if now - t < 60]
        if len(recent_timestamps) >= self.rate_limit_per_minute:
            _blocked_entities[key] = now + 60  # 1 minute temporary cooldown
            logger.warning("Per-minute rate limit exceeded for entity %s (%d reqs/min).", key, len(recent_timestamps))
            return False, "Çok hızlı mesaj gönderiyorsunuz. Lütfen 1 dakika bekleyip tekrar yazınız."

        # Record valid request
        timestamps.append(now)
        _request_timestamps[key] = timestamps
        return True, None

    def check_input_safety(self, message: str) -> Tuple[bool, Optional[str]]:
        """Validates inbound user message against jailbreaks and off-topic chatbot abuse."""
        if not message or not message.strip():
            return False, "Boş mesaj gönderilemez."

        msg_clean = message.strip()

        # Whitelist /reset command
        if msg_clean.lower() == "/reset":
            return True, None

        # 1. Jailbreak Check
        if self.enable_jailbreak_filter:
            for pattern in self.jailbreak_patterns:
                if re.search(pattern, msg_clean):
                    logger.warning("Jailbreak / Prompt Injection pattern detected: '%s' in '%s'", pattern, msg_clean)
                    return False, REFUSAL_JAILBREAK

        # 2. Off-Topic Check
        if self.enable_offtopic_filter:
            for pattern in self.offtopic_patterns:
                if re.search(pattern, msg_clean):
                    is_genuine_spa_query = any(kw in msg_clean.lower() for kw in self.spa_explicit_whitelist)
                    if not is_genuine_spa_query:
                        logger.warning("Off-topic abuse detected: pattern '%s' in '%s'", pattern, msg_clean)
                        return False, REFUSAL_OFFTOPIC

        return True, None

    def sanitize_output(self, assistant_reply: str) -> str:
        """
        Sanitizes model output to eliminate foreign tokens, raw codeblocks, and unsolicited IBAN sharing.
        """
        if not assistant_reply or not assistant_reply.strip():
            return "Size nasıl yardımcı olabilirim? Masaj, hamam veya bakım hizmetlerimizle ilgili bilgi alabilirsiniz. ✨"

        reply = assistant_reply.strip()

        # 1. Detect CJK/Chinese foreign token leakage from base model
        if re.search(r"[\u4e00-\u9fff]", reply):
            logger.warning("Sanitizer caught foreign characters in reply. Overriding with clean response.")
            return "Size nasıl yardımcı olabilirim? Masaj, hamam veya spa randevunuzla ilgili bilgi alabilirsiniz. ✨"

        # 2. Intercept code block leakage
        if re.search(r"```(python|javascript|html|css|json|sql|bash|sh)", reply):
            logger.warning("Sanitizer caught code block in output. Overriding with scope refusal.")
            return REFUSAL_OFFTOPIC

        # 3. Intercept IBAN leak or unsolicited bank sharing
        iban_pattern = r"(?i)\b(?:TR\s*(?:\d\s*){24}|[A-Z]{2}\s*(?:\d\s*){2}(?:[A-Z0-9]\s*){12,30})\b"
        if re.search(iban_pattern, reply):
            logger.warning("Sanitizer caught IBAN in output! Enforcing human handover.")
            return (
                "Ödeme, kapora ve hesap işlemlerimiz doğrudan şube yetkilimiz tarafından güvenli şekilde "
                "yürütülmektedir. Yetkilimize bilgi verdim, en kısa sürede sizinle buradan iletişime geçecektir. "
                "[YETKILI_DEVRET: Müşteri ödeme/kapora/IBAN talebi]"
            )

        return reply

    def detect_intervention_intent(self, message: str) -> bool:
        """Detects severe complaints, emergency legal threats, or consumer accusations requiring immediate human handoff."""
        msg = message.lower().strip()
        patterns = [
            r"(?i)(şikayet|dolandırıcı|dava\s*aç|savcılık|tüketici\s*hakem|avukat)",
            r"(?i)(acil\s*yetkili|derhal\s*yetkili|insanla\s*görüş|canlı\s*destek|yetkili\s*birine)",
            r"(?i)(para\s*iadesi|iade\s*yap|hırsızlık|taciz)",
        ]
        return any(bool(re.search(p, msg)) for p in patterns)

    def detect_reservation_intent(self, message: str) -> bool:
        """Detects strong reservation, booking, appointment, phone number, or deposit intent across multiple languages."""
        msg = message.lower().strip()
        patterns = [
            r"(?i)(yer\s*ayırt|rezervasyon|randevu|seans|booking|reservation|appointment).*?(yap|al|oluştur|ayarla|iste|isti|almak|tarih|saat|gün)",
            r"(?i)(kesin\s*kayıt|kapora|ödeme\s*yap|rezervasyon|randevu)",
            r"(?i)(book\s+a\s+session|make\s+a\s+reservation|book\s+now|book\s+an\s+appointment)",
            # Russian booking / appointment terms
            r"(?i)(забронировать|бронирование|запись|записаться|заказ|свободное\s*время)",
            # German booking / appointment terms
            r"(?i)(termin|buchen|reservieren|buchung|reservierung|zeitfenster)",
            # Phone number detection (hot lead signal)
            r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{2}[-.\s]?\d{2}",
        ]
        return any(bool(re.search(p, msg)) for p in patterns)

    def unblock_entity(self, identifier: str) -> bool:
        """Clears blocks and rate limit history for an entity."""
        key = identifier.strip().lower()
        if key in _blocked_entities:
            del _blocked_entities[key]
        if key in _request_timestamps:
            del _request_timestamps[key]
        return True


# Global security instance
security_guard = SecurityGuard()


class LoginRateLimiter:
    """
    Brute-force protection for control panel login attempts (/api/auth/login).
    - Tracks failed attempts per IP address using a sliding window.
    - Max 5 failed attempts allowed within 10 minutes.
    - On 5th failure: Locks out the IP for 15 minutes (900 seconds).
    - An artificial 1-second delay is added on failed attempts to throttle automated tools.
    """
    def __init__(self, max_attempts: int = 5, window_seconds: int = 600, lockout_seconds: int = 900):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self._failed_attempts: Dict[str, List[float]] = defaultdict(list)
        self._lockouts: Dict[str, float] = {}

    def is_locked_out(self, ip: str) -> Tuple[bool, int]:
        """Checks if an IP is currently locked out. Returns (is_locked, remaining_seconds)."""
        now = time.time()
        ip = (ip or "127.0.0.1").strip()
        if ip in self._lockouts:
            unlock_time = self._lockouts[ip]
            if now < unlock_time:
                return True, max(1, int(unlock_time - now))
            else:
                del self._lockouts[ip]
                self._failed_attempts[ip] = []
        return False, 0

    def record_failure(self, ip: str) -> Tuple[bool, int]:
        """
        Records a failed attempt for an IP.
        Returns: (is_now_locked: bool, remaining_attempts_or_lockout_seconds: int)
        """
        now = time.time()
        ip = (ip or "127.0.0.1").strip()
        timestamps = [t for t in self._failed_attempts[ip] if now - t < self.window_seconds]
        timestamps.append(now)
        self._failed_attempts[ip] = timestamps

        if len(timestamps) >= self.max_attempts:
            unlock_time = now + self.lockout_seconds
            self._lockouts[ip] = unlock_time
            logger.warning(
                "Admin panel brute-force lockout triggered for IP %s (%d failed attempts). Locked for %ds.",
                ip, len(timestamps), self.lockout_seconds
            )
            return True, self.lockout_seconds

        remaining_attempts = max(0, self.max_attempts - len(timestamps))
        return False, remaining_attempts

    def record_success(self, ip: str) -> None:
        """Clears failure history upon successful authentication."""
        ip = (ip or "127.0.0.1").strip()
        self._failed_attempts.pop(ip, None)
        self._lockouts.pop(ip, None)

    def unblock_ip(self, ip: str) -> None:
        """Manually unblocks an IP."""
        ip = (ip or "127.0.0.1").strip()
        self._failed_attempts.pop(ip, None)
        self._lockouts.pop(ip, None)


login_rate_limiter = LoginRateLimiter()


# =========================================================================
# Cryptographic Tamper-Evident SHA-256 Merkle Chain Audit Logger
# =========================================================================

def compute_entry_hash(
    prev_hash: str,
    timestamp: str,
    recipient_jid: str,
    message_preview: str,
    status: str,
) -> str:
    """Computes canonical SHA-256 hash for an audit log entry."""
    payload = f"{prev_hash}:{timestamp}:{recipient_jid}:{message_preview}:{status}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_audit_log_path(log_file_path: Optional[str] = None) -> Path:
    """Resolves standard audit log path respecting environment and directory structure."""
    if log_file_path:
        return Path(log_file_path)
    env_path = os.getenv("AUDIT_LOG_PATH")
    if env_path:
        return Path(env_path)
    
    root_log = Path(__file__).resolve().parent.parent / "bot_audit.log"
    return root_log


def get_last_audit_entry_hash(target_path: Path) -> str:
    """Reads the last non-empty line of the audit log to extract entry_hash."""
    if not target_path.exists() or target_path.stat().st_size == 0:
        return GENESIS_HASH

    try:
        with open(target_path, "r", encoding="utf-8", errors="replace") as f:
            lines = [line.strip() for line in f if line.strip()]
        if not lines:
            return GENESIS_HASH

        last_line = lines[-1]
        try:
            data = json.loads(last_line)
            if "entry_hash" in data and data["entry_hash"]:
                return str(data["entry_hash"]).strip()
        except Exception:
            pass

        return hashlib.sha256(last_line.encode("utf-8")).hexdigest()
    except Exception as exc:
        logger.warning("Could not read last audit entry hash from %s: %s", target_path, exc)
        return GENESIS_HASH


def append_audit_log_file(
    recipient_jid: str,
    message_preview: str,
    status: str = "SENT",
    log_file_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Appends an outbound reply entry to bot_audit.log with SHA-256 cryptographic chain continuity.
    Failsafe: never raises unhandled exceptions.
    """
    try:
        target_path = resolve_audit_log_path(log_file_path)
        prev_hash = get_last_audit_entry_hash(target_path)
        now_iso = datetime.now(timezone.utc).isoformat()
        rec_clean = (recipient_jid or "").strip()
        msg_clean = (message_preview or "").strip()
        st_clean = (status or "SENT").strip().upper()

        entry_hash = compute_entry_hash(prev_hash, now_iso, rec_clean, msg_clean, st_clean)

        entry = {
            "timestamp": now_iso,
            "recipient_jid": rec_clean,
            "message_preview": msg_clean,
            "status": st_clean,
            "prev_hash": prev_hash,
            "entry_hash": entry_hash,
        }
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with open(target_path, "a", encoding="utf-8") as f:
            f.write(line)
        return entry
    except Exception as exc:
        logger.warning("Failed to write to bot_audit.log: %s", exc)
        return None


def verify_audit_log_chain(log_file_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Cryptographically verifies the SHA-256 hash chain in bot_audit.log.
    """
    target_path = resolve_audit_log_path(log_file_path)
    if not target_path.exists() or target_path.stat().st_size == 0:
        return {
            "is_valid": True,
            "total_records": 0,
            "latest_hash": GENESIS_HASH,
            "genesis_hash": GENESIS_HASH,
            "failed_at_line": None,
            "error": None,
            "file_path": str(target_path),
        }

    try:
        with open(target_path, "r", encoding="utf-8", errors="replace") as f:
            lines = [line.strip() for line in f if line.strip()]

        total_records = len(lines)
        expected_prev_hash = GENESIS_HASH

        for idx, line in enumerate(lines, 1):
            try:
                entry = json.loads(line)
            except Exception as json_err:
                return {
                    "is_valid": False,
                    "total_records": total_records,
                    "failed_at_line": idx,
                    "error": f"Line {idx} is invalid JSON: {json_err}",
                    "file_path": str(target_path),
                }

            prev_hash = str(entry.get("prev_hash", "")).strip()
            entry_hash = str(entry.get("entry_hash", "")).strip()
            timestamp = str(entry.get("timestamp", "")).strip()
            recipient_jid = str(entry.get("recipient_jid", "")).strip()
            message_preview = str(entry.get("message_preview", "")).strip()
            status_val = str(entry.get("status", "")).strip()

            if prev_hash != expected_prev_hash:
                return {
                    "is_valid": False,
                    "total_records": total_records,
                    "failed_at_line": idx,
                    "error": f"Line {idx} prev_hash mismatch. Expected {expected_prev_hash}, got {prev_hash}.",
                    "file_path": str(target_path),
                }

            computed_hash = compute_entry_hash(
                prev_hash, timestamp, recipient_jid, message_preview, status_val
            )
            if entry_hash != computed_hash:
                return {
                    "is_valid": False,
                    "total_records": total_records,
                    "failed_at_line": idx,
                    "error": f"Line {idx} entry_hash corrupted. Expected {computed_hash}, got {entry_hash}.",
                    "file_path": str(target_path),
                }

            expected_prev_hash = entry_hash

        return {
            "is_valid": True,
            "total_records": total_records,
            "latest_hash": expected_prev_hash,
            "genesis_hash": GENESIS_HASH,
            "failed_at_line": None,
            "error": None,
            "file_path": str(target_path),
        }
    except Exception as exc:
        return {
            "is_valid": False,
            "total_records": 0,
            "failed_at_line": None,
            "error": str(exc),
            "file_path": str(target_path),
        }


def check_append_only_attribute(log_file_path: Optional[str] = None) -> bool:
    """
    Checks whether the file has the Linux ext2/ext3/ext4 append-only (+a) attribute set.
    Uses 'lsattr' utility on Linux. Returns False on Windows or if unset.
    """
    if os.name == "nt":
        return False

    target_path = resolve_audit_log_path(log_file_path)
    if not target_path.exists():
        return False

    try:
        proc = subprocess.run(
            ["lsattr", str(target_path)],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.returncode == 0:
            # Format: '-----a-------e-- /path/to/file'
            attrs_part = proc.stdout.split()[0] if proc.stdout.split() else ""
            return "a" in attrs_part
    except Exception as exc:
        logger.debug("lsattr check failed: %s", exc)
    return False


def enable_append_only_attribute(log_file_path: Optional[str] = None) -> Tuple[bool, str]:
    """
    Applies the Linux append-only (+a) file attribute using chattr.
    Ensures root or sudo cannot overwrite or truncate the audit log.
    """
    if os.name == "nt":
        return False, "Not supported on Windows OS (requires POSIX ext2/ext3/ext4)"

    target_path = resolve_audit_log_path(log_file_path)
    try:
        if not target_path.exists():
            target_path.touch(mode=0o600, exist_ok=True)

        proc = subprocess.run(
            ["chattr", "+a", str(target_path)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            return True, f"Append-only attribute (+a) successfully applied to {target_path}"

        # Try sudo if non-root
        sudo_proc = subprocess.run(
            ["sudo", "chattr", "+a", str(target_path)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if sudo_proc.returncode == 0:
            return True, f"Append-only attribute (+a) successfully applied with sudo to {target_path}"

        err_msg = sudo_proc.stderr or proc.stderr or "Permission denied"
        return False, f"Could not apply chattr +a: {err_msg.strip()}"
    except Exception as exc:
        return False, f"chattr execution error: {exc}"


def secure_session_directories(paths: Optional[List[str]] = None) -> None:
    """
    Ensures directory permissions on session and auth folders (.wwebjs_auth, auth_info, session)
    are strictly restricted to 0700 (owner-only access) on POSIX/Linux platforms.
    """
    if os.name == "nt":
        return

    default_paths = [
        Path(__file__).resolve().parent.parent / "bridge" / ".wwebjs_auth",
        Path(__file__).resolve().parent.parent / "bridge" / "auth_info",
        Path(__file__).resolve().parent.parent / "bridge" / "session",
    ]
    target_paths = [Path(p) for p in paths] if paths else default_paths

    for target in target_paths:
        try:
            if target.exists() and target.is_dir():
                os.chmod(target, 0o700)
                for root, dirs, files in os.walk(target):
                    for d in dirs:
                        try:
                            os.chmod(os.path.join(root, d), 0o700)
                        except Exception:
                            pass
                    for f in files:
                        try:
                            os.chmod(os.path.join(root, f), 0o600)
                        except Exception:
                            pass
        except Exception as exc:
            logger.debug("Failed to secure auth directory %s: %s", target, exc)


def ensure_gitignore_entries() -> None:
    """
    Ensures that security-sensitive credentials and session artifacts are registered in .gitignore.
    """
    gitignore_candidates = [
        Path(__file__).resolve().parent.parent / ".gitignore",
        Path(__file__).resolve().parent.parent / "bridge" / ".gitignore",
    ]
    required_patterns = [
        ".env",
        "*.env",
        "bot_audit.log",
        "*/bot_audit.log",
        ".wwebjs_auth/",
        "auth_info/",
        "session/",
        "*/.wwebjs_auth/",
        "*/auth_info/",
        "*/session/",
    ]
    for gpath in gitignore_candidates:
        try:
            if gpath.exists():
                content = gpath.read_text(encoding="utf-8")
                missing = [p for p in required_patterns if p not in content]
                if missing:
                    with open(gpath, "a", encoding="utf-8") as f:
                        f.write("\n# Forensic & Session Security Protection\n")
                        for m in missing:
                            f.write(f"{m}\n")
        except Exception as exc:
            logger.debug("Could not verify .gitignore at %s: %s", gpath, exc)


# =====================================================================
# API Key & Budget Safety Guard (Anti-Drain & Anti-Exploitation Shield)
# =====================================================================

class APIBudgetGuard:
    """
    Guards the Cloud LLM API key against exploitation, exhaustion attacks,
    or accidental massive cost spikes when the demo is accessed publicly.
    Enforces sliding-window limits: per-minute, per-hour, and per-day.
    """
    def __init__(
        self,
        max_per_minute: int = 15,
        max_per_hour: int = 60,
        max_per_day: int = 250,
    ):
        self.max_per_minute = max_per_minute
        self.max_per_hour = max_per_hour
        self.max_per_day = max_per_day
        self._call_timestamps: List[float] = []

    def check_budget_allowance(self) -> Tuple[bool, Optional[str]]:
        now = time.time()
        # Clean older than 24h
        self._call_timestamps = [t for t in self._call_timestamps if now - t < 86400]

        # 1. Day check
        if len(self._call_timestamps) >= self.max_per_day:
            logger.warning("Daily AI budget cap reached (%d calls). Safety refusal active.", self.max_per_day)
            return False, "Günlük maksimum AI sorgu bütçe limitine ulaşıldı (250 istek/gün). Güvenlik ve maliyet koruma kalkanı aktif."

        # 2. Hour check
        recent_hour = [t for t in self._call_timestamps if now - t < 3600]
        if len(recent_hour) >= self.max_per_hour:
            logger.warning("Hourly AI budget cap reached (%d calls). Throttling active.", self.max_per_hour)
            return False, "Saatlik AI sorgu kotasına ulaşıldı (60 istek/saat). Lütfen bir süre sonra tekrar deneyiniz."

        # 3. Minute burst check
        recent_min = [t for t in self._call_timestamps if now - t < 60]
        if len(recent_min) >= self.max_per_minute:
            logger.warning("Per-minute AI burst cap reached (%d calls). Burst refusal active.", self.max_per_minute)
            return False, "Dakikalık AI istek sınırına ulaşıldı (15 istek/dk). Lütfen 1 dakika bekleyiniz."

        return True, None

    def record_call(self) -> None:
        self._call_timestamps.append(time.time())

    def get_budget_stats(self) -> Dict[str, Any]:
        now = time.time()
        calls_day = len([t for t in self._call_timestamps if now - t < 86400])
        calls_hour = len([t for t in self._call_timestamps if now - t < 3600])
        calls_min = len([t for t in self._call_timestamps if now - t < 60])
        return {
            "calls_today": calls_day,
            "limit_today": self.max_per_day,
            "remaining_today": max(0, self.max_per_day - calls_day),
            "calls_this_hour": calls_hour,
            "limit_hour": self.max_per_hour,
            "calls_this_minute": calls_min,
            "limit_minute": self.max_per_minute,
            "is_budget_safe": calls_day < self.max_per_day,
            "protection_status": "ACTIVE_GUARD",
        }


api_budget_guard = APIBudgetGuard()


class EndpointRateLimiter:
    """
    Per-IP sliding window rate limiter to protect endpoints like
    /api/simulator/send and /api/demo/seed from script abuse or DoS.
    """
    def __init__(self):
        self._ip_timestamps: Dict[str, List[float]] = defaultdict(list)
        self._cooldowns: Dict[str, float] = {}

    def check_limit(
        self,
        endpoint: str,
        client_ip: str,
        max_per_minute: int = 10,
        cooldown_sec: int = 0,
    ) -> Tuple[bool, Optional[str]]:
        key = f"{endpoint}:{client_ip.strip()}"
        now = time.time()

        # Cooldown check
        if key in self._cooldowns:
            if now < self._cooldowns[key]:
                remaining = int(self._cooldowns[key] - now)
                return False, f"İşlem için çok sık istek gönderildi. Lütfen {remaining} saniye bekleyin."
            else:
                del self._cooldowns[key]

        timestamps = [t for t in self._ip_timestamps[key] if now - t < 60]
        if len(timestamps) >= max_per_minute:
            self._cooldowns[key] = now + 60
            return False, "Çok hızlı istek gönderiyorsunuz. Güvenlik nedeniyle lütfen 1 dakika bekleyiniz."

        if cooldown_sec > 0:
            self._cooldowns[key] = now + cooldown_sec

        timestamps.append(now)
        self._ip_timestamps[key] = timestamps
        return True, None


endpoint_limiter = EndpointRateLimiter()


