import os
import json
import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Any, Tuple
from contextlib import contextmanager
from pathlib import Path

import config

logger = logging.getLogger("omni-database")

# Database File Path
DB_PATH = config.DATABASE_PATH


def _get_sqlite_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    target = db_path if db_path else DB_PATH
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=20.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


@contextmanager
def get_db(db_path: Optional[str] = None):
    """Context manager for SQLite/PostgreSQL connection with auto-commit/rollback."""
    conn = _get_sqlite_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception as exc:
        conn.rollback()
        logger.exception("Database transaction rolled back due to error: %s", exc)
        raise
    finally:
        conn.close()


def init_db(db_path: Optional[str] = None) -> None:
    """Initializes all multi-tenant relational tables, indexes, and seeds baseline data."""
    logger.info("Initializing multi-tenant database schema...")
    with get_db(db_path) as conn:
        cursor = conn.cursor()

        # 1. Branches Table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS branches (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                city TEXT NOT NULL,
                address TEXT NOT NULL,
                phone TEXT NOT NULL,
                email TEXT NOT NULL,
                manager_name TEXT NOT NULL,
                ntfy_topic TEXT NOT NULL,
                check_in TEXT DEFAULT '14:00',
                check_out TEXT DEFAULT '11:00',
                pricing_json TEXT,
                amenities_json TEXT,
                description TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        # 2. Users & RBAC Table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('superadmin', 'branch_manager', 'agent')),
                branch_id TEXT REFERENCES branches(id) ON DELETE SET NULL,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_branch ON users(branch_id);")

        # 3. Conversations (Tenant Isolated)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                branch_id TEXT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
                channel TEXT NOT NULL CHECK (channel IN ('whatsapp', 'instagram', 'messenger', 'telegram')),
                customer_id TEXT NOT NULL,
                customer_name TEXT DEFAULT '',
                is_muted INTEGER DEFAULT 0,
                muted_until TIMESTAMP,
                mute_reason TEXT,
                is_lead INTEGER DEFAULT 0,
                lead_details TEXT,
                status TEXT DEFAULT 'active' CHECK (status IN ('active', 'pending_human', 'resolved', 'closed')),
                last_message TEXT DEFAULT '',
                last_message_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(branch_id, channel, customer_id)
            );
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conv_lookup ON conversations(branch_id, channel, customer_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conv_branch_updated ON conversations(branch_id, updated_at DESC);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conv_status ON conversations(status);")

        # 4. Messages (Tenant Isolated)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER REFERENCES conversations(id) ON DELETE CASCADE,
                branch_id TEXT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
                channel TEXT NOT NULL,
                customer_id TEXT NOT NULL,
                direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'agent')),
                content TEXT NOT NULL,
                media_json TEXT,
                metadata_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_branch ON messages(branch_id, created_at DESC);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_cust ON messages(customer_id, channel);")

        # 5. Branch Knowledge Base
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS branch_knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                branch_id TEXT UNIQUE NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
                content_markdown TEXT NOT NULL,
                structured_json TEXT,
                updated_by TEXT DEFAULT 'system',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        # 6. Channel Configs per Branch
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS channel_configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                branch_id TEXT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
                channel TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                credentials_json TEXT,
                webhook_secret TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(branch_id, channel)
            );
            """
        )

        # 7. System Settings
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

    # Seed baseline catalog and credentials
    seed_database_if_empty(db_path)
    logger.info("Database schema initialized and verified successfully.")


def seed_database_if_empty(db_path: Optional[str] = None) -> None:
    """Populates 17 Navitas Spa & Wellness luxury branches, initial users, knowledge bases, and settings."""
    import auth

    with get_db(db_path) as conn:
        cursor = conn.cursor()

        # Check for legacy camp data; if present, clean up to synchronize with Navitas Spa centers
        cursor.execute("SELECT id FROM branches WHERE id = 'olympos'")
        if cursor.fetchone():
            logger.info("Migrating database from legacy camp data to 17 Navitas Spa & Wellness centers...")
            cursor.execute("DELETE FROM branches")
            cursor.execute("DELETE FROM branch_knowledge")
            cursor.execute("DELETE FROM users WHERE role != 'superadmin'")

        # 1. Seed / Update 17 Navitas Branches
        for branch in config.BRANCHES_CATALOG:
            cursor.execute("SELECT id FROM branches WHERE id = ?", (branch["id"],))
            if not cursor.fetchone():
                cursor.execute(
                    """
                    INSERT INTO branches (
                        id, name, city, address, phone, email, manager_name,
                        ntfy_topic, check_in, check_out, pricing_json, amenities_json,
                        description, is_active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1);
                    """,
                    (
                        branch["id"],
                        branch["name"],
                        branch["city"],
                        branch["address"],
                        branch["phone"],
                        branch["email"],
                        branch["manager_name"],
                        branch["ntfy_topic"],
                        branch["check_in"],
                        branch["check_out"],
                        json.dumps(branch["pricing"], ensure_ascii=False),
                        json.dumps(branch["amenities"], ensure_ascii=False),
                        branch["description"],
                    ),
                )
                logger.info("Seeded Navitas Spa branch: %s (%s)", branch["name"], branch["id"])

        # 2. Seed Rich Knowledge Base for each branch
        for branch in config.BRANCHES_CATALOG:
            b_id = branch["id"]
            cursor.execute("SELECT id FROM branch_knowledge WHERE branch_id = ?", (b_id,))
            if not cursor.fetchone():
                amenities_str = "\n".join([f"- {a}" for a in branch["amenities"]])
                pricing_str = "\n".join([f"- **{k.replace('_', ' ').title()}**: {v}" for k, v in branch["pricing"].items()])

                special_notes = ""
                if b_id == "istanbul-airport":
                    special_notes = (
                        "### ✈️ İstanbul Havalimanı Özel Bilgilendirme:\n"
                        "- **Konum:** Hilton Istanbul Airport içinde, terminal binasına birkaç dakika yürüyüş mesafesinde, **kara tarafındadır (landside)**.\n"
                        "- **Transit Yolcular:** Dış hat aktarma yolcularımızın pasaport kontrolünden geçmesi gerekir.\n"
                        "- **Valiz & Eşya:** Resepsiyonda misafirlerimiz için kilitli dolap muhafazası mevcuttur.\n"
                        "- **Uçuş Rötarları:** Uçuş saatlerindeki değişikliklerde esnek randevu güncellemesi sağlanır.\n\n"
                    )
                elif b_id in ["sultanahmet", "bolu-abant"]:
                    special_notes = (
                        "### ⚠️ Önemli Durum Bilgilendirmesi:\n"
                        f"- **Tadilat Bildirimi:** {branch['name']} merkezimiz şu anda tadilat sürecindedir. Misafirlerimize en yakın alternatif şubelerimiz önerilmektedir.\n\n"
                    )
                elif b_id == "izmir-bomonti":
                    special_notes = (
                        "### 🌟 Açılış Bilgilendirmesi:\n"
                        "- **Çok Yakında Açılıyor:** Mahall Bomonti İzmir merkezimiz için hazırlıklarımız tamamlanmak üzeredir.\n\n"
                    )

                kb_markdown = (
                    f"# {branch['name']} — Resmi Bilgi Bankası ve Hizmet Rehberi\n\n"
                    f"## 📍 Konum & İletişim\n"
                    f"- **Şube / Merkez:** {branch['name']}\n"
                    f"- **Şehir:** {branch['city']}\n"
                    f"- **Adres:** {branch['address']}\n"
                    f"- **İletişim & WhatsApp Randevu:** {branch['phone']}\n"
                    f"- **E-posta:** {branch['email']}\n"
                    f"- **Spa Yöneticisi:** {branch['manager_name']}\n\n"
                    f"## 🕒 Çalışma Saatleri\n"
                    f"- Haftanın her günü **{branch['check_in']} – {branch['check_out']}** saatleri arasında açıktır.\n\n"
                    f"{special_notes}"
                    f"## 🌿 Şube Olanakları & Tesis Donanımı\n"
                    f"{amenities_str}\n\n"
                    f"## 💆‍♀️ Masaj Hizmetleri Koleksiyonu\n"
                    f"1. **Antistress Masajı (60 dk):** Bel, boyun ve sırttaki gerginlikleri hedefleyen ritmik rahatlama.\n"
                    f"2. **Klasik İsveç Masajı (50 dk):** Tüm vücut kan dolaşımını ve kas tonusunu canlandırıcı terapi.\n"
                    f"3. **Spor Masajı (45 dk):** Derin manipülasyon ve esnetmelerle laktik asit toparlanması.\n"
                    f"4. **Derin Doku Masajı (45 dk):** Tetik noktalar ve kronik kas sertliklerine yönelik yüksek basınçlı dokunuş.\n"
                    f"5. **Bali Masajı (50/90 dk):** Doğal bitkisel yağlar ve akupresur baskılarıyla enerji dengeleme.\n"
                    f"6. **Mum Masajı (50 dk):** Ilık soya/shea mumu ile yoğun nem ve ipeksi sıcaklık.\n"
                    f"7. **Aromaterapi Masajı (50 dk):** Saf aromatik bitki özleriyle zihinsel ve bedensel arınma.\n"
                    f"8. **Medikal Masaj (50 dk):** Özel medikal kremler eşliğinde eklem ve kas tedavisi.\n"
                    f"9. **Sıcak Taş Masajı (60 dk):** Volkanik bazalt taşlarının ısısıyla derin gevşeme.\n"
                    f"10. **Sultan Masajı (50 dk - 4 El):** İki uzman terapistin aynı anda uyguladığı senkronize lüks.\n"
                    f"11. **Refleksoloji (Ayak Masajı - 30 dk):** Ayak tabanı refleks noktalarına uygulanan akupresur.\n"
                    f"12. **Selülit Masajı (50 dk):** Dolaşımı hızlandıran ödem atıcı sıkılaştırıcı bakım.\n\n"
                    f"## 🛁 Hamam Ritüelleri & Cilt Bakımı\n"
                    f"- **Geleneksel Kese & Köpük (45 dk):** İpek kese ve organik zeytinyağlı yoğun köpük masajı.\n"
                    f"- **Navitas Signature Ritüeli (60 dk):** Kese ve köpüğe ek vücut peelingi ve nem maskesi.\n"
                    f"- **Sultan Hamamı (50 dk):** Çift görevli ile uygulanan 4 el geleneksel hamam ritüeli.\n"
                    f"- **Hydrafacial Cilt Bakımı:** Tek seansta derin gözenek temizliği, vakum, peeling ve nem infüzyonu.\n"
                    f"- **VIP Ritüeli:** Çiftlere özel süitte kese-köpük, çift masajı, sauna, jakuzi, taze meyve/detoks ikramı ve Smart TV sinema keyfi.\n"
                    f"- **Gelin Hamamı:** Kadınlar bölümü özel tahsisi, saray şerbeti, lüks çerez ve meyve ikramı (09:00 - 14:00, max 15 kişi).\n\n"
                    f"## 💰 Fiyatlandırma Politikası\n"
                    f"{pricing_str}\n"
                    f"*(Fiyatlar kişi başıdır; şubeye ve dönemsel paketlere göre değiştiği için WhatsApp veya telefonla anlık teyit edilir.)*\n\n"
                    f"## ⚠️ Rezervasyon ve Misafir Kuralları\n"
                    f"1. **İptal & Değişiklik:** Randevudan en az 4 saat öncesine kadar değişiklik ve iptal tamamen ücretsizdir.\n"
                    f"2. **Varış Saati:** Seans sürenizden kayıp yaşamamak için randevudan 10-15 dakika önce gelinmesi önerilir.\n"
                    f"3. **Sağlanan Malzemeler:** Bornoz, peştamal, havlu, şampuan, duş jeli ve tek kullanımlık terlikler temin edilir.\n"
                    f"4. **Havuz Kuralları:** Hijyen standartlarımız gereği havuz kullanımında bone takılması zorunludur. Misafir kendi mayosunu getirmelidir.\n"
                    f"5. **Yaş Sınırı:** 18 yaş altı misafirlerimiz yalnızca ebeveyn refakatiyle kabul edilir.\n"
                    f"6. **Hamilelik:** İlk 3 ay masaj önerilmez; sonraki dönemde hafif masaj yapılabilir ancak sauna, hamam ve sıcak ıslak alanlar uygun değildir.\n"
                )
                cursor.execute(
                    """
                    INSERT INTO branch_knowledge (branch_id, content_markdown, structured_json, updated_by)
                    VALUES (?, ?, ?, 'system_seed');
                    """,
                    (b_id, kb_markdown, json.dumps(branch, ensure_ascii=False)),
                )

        # 3. Seed Super Admin User
        admin_email = config.INITIAL_ADMIN_EMAIL
        cursor.execute("SELECT id FROM users WHERE email = ?", (admin_email,))
        if not cursor.fetchone():
            hashed_pw = auth.get_password_hash(config.INITIAL_ADMIN_PASSWORD)
            cursor.execute(
                """
                INSERT INTO users (email, password_hash, full_name, role, branch_id, is_active)
                VALUES (?, ?, ?, 'superadmin', NULL, 1);
                """,
                (admin_email, hashed_pw, config.INITIAL_ADMIN_NAME),
            )
            logger.info("Seeded initial Super Admin: %s", admin_email)

        # 4. Seed Branch Managers and Live Support Agents for all 17 Navitas branches
        manager_pw = auth.get_password_hash("ManagerPass2026!")
        agent_pw = auth.get_password_hash("AgentPass2026!")

        for branch in config.BRANCHES_CATALOG:
            b_id = branch["id"]
            # Manager
            m_email = f"manager.{b_id}@navitasspa.com"
            cursor.execute("SELECT id FROM users WHERE email = ?", (m_email,))
            if not cursor.fetchone():
                cursor.execute(
                    """
                    INSERT INTO users (email, password_hash, full_name, role, branch_id, is_active)
                    VALUES (?, ?, ?, 'branch_manager', ?, 1);
                    """,
                    (m_email, manager_pw, f"{branch['manager_name']} ({branch['city']} Müdürü)", b_id),
                )

            # Support Agent
            a_email = f"agent.{b_id}@navitasspa.com"
            cursor.execute("SELECT id FROM users WHERE email = ?", (a_email,))
            if not cursor.fetchone():
                cursor.execute(
                    """
                    INSERT INTO users (email, password_hash, full_name, role, branch_id, is_active)
                    VALUES (?, ?, ?, 'agent', ?, 1);
                    """,
                    (a_email, agent_pw, f"{branch['name']} Canlı Destek Temsilcisi", b_id),
                )

        # 5. Seed Default System Settings (Filters, Prefix, LLM Defaults)
        default_settings = {
            "trigger_prefix": config.TRIGGER_PREFIX,
            "only_unknown_contacts": "true" if config.ONLY_UNKNOWN_CONTACTS else "false",
            "blacklist": ",".join(config.BLACKLIST),
            "debounce_seconds": str(config.DEBOUNCE_SECONDS),
            "llm_provider": config.LLM_PROVIDER,
            "llm_model": config.LLM_MODEL,
        }
        for key, val in default_settings.items():
            cursor.execute("SELECT key FROM system_settings WHERE key = ?", (key,))
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO system_settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP);",
                    (key, val),
                )


# =====================================================================
# Conversations & Messaging Repository Methods
# =====================================================================

def get_or_create_conversation(
    branch_id: str,
    channel: str,
    customer_id: str,
    customer_name: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Finds existing conversation or creates a new one for (branch_id, channel, customer_id)."""
    b_id = branch_id.strip().lower()
    chan = channel.strip().lower()
    c_id = customer_id.strip()
    c_name = customer_name.strip() if customer_name else ""

    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM conversations
            WHERE branch_id = ? AND channel = ? AND customer_id = ?;
            """,
            (b_id, chan, c_id),
        )
        row = cursor.fetchone()
        if row:
            # Update customer name if provided and previously empty
            if c_name and not row["customer_name"]:
                cursor.execute(
                    "UPDATE conversations SET customer_name = ? WHERE id = ?",
                    (c_name, row["id"]),
                )
            return dict(row)

        # Create new
        cursor.execute(
            """
            INSERT INTO conversations (branch_id, channel, customer_id, customer_name, status)
            VALUES (?, ?, ?, ?, 'active');
            """,
            (b_id, chan, c_id, c_name),
        )
        conv_id = cursor.lastrowid
        cursor.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,))
        return dict(cursor.fetchone())


def save_message(
    branch_id: str,
    channel: str,
    customer_id: str,
    direction: str,
    role: str,
    content: str,
    customer_name: Optional[str] = None,
    media: Optional[List[Dict[str, Any]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Saves a message turn to the database and updates the parent conversation's last message time.
    """
    conv = get_or_create_conversation(
        branch_id=branch_id,
        channel=channel,
        customer_id=customer_id,
        customer_name=customer_name,
        db_path=db_path,
    )
    conv_id = conv["id"]

    media_json = json.dumps(media, ensure_ascii=False) if media else None
    meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
    content_clean = content.strip()

    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO messages (
                conversation_id, branch_id, channel, customer_id,
                direction, role, content, media_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                conv_id,
                branch_id.strip().lower(),
                channel.strip().lower(),
                customer_id.strip(),
                direction.strip(),
                role.strip(),
                content_clean,
                media_json,
                meta_json,
            ),
        )
        msg_id = cursor.lastrowid

        # Update parent conversation
        cursor.execute(
            """
            UPDATE conversations
            SET last_message = ?, last_message_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?;
            """,
            (content_clean[:120], conv_id),
        )

        cursor.execute("SELECT * FROM messages WHERE id = ?", (msg_id,))
        return dict(cursor.fetchone())


def get_history(
    branch_id: str,
    channel: str,
    customer_id: str,
    limit: int = 10,
    db_path: Optional[str] = None,
) -> List[Dict[str, str]]:
    """
    Retrieves recent chat history window for prompt context in chronological order.
    """
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT role, content FROM messages
            WHERE branch_id = ? AND channel = ? AND customer_id = ?
            ORDER BY id DESC
            LIMIT ?;
            """,
            (branch_id.strip().lower(), channel.strip().lower(), customer_id.strip(), limit),
        )
        rows = cursor.fetchall()
        # Return oldest to newest for LLM chat context
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def get_conversation_messages(
    conversation_id: int,
    limit: int = 100,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Retrieves all message turns for a conversation thread."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM messages
            WHERE conversation_id = ?
            ORDER BY id ASC
            LIMIT ?;
            """,
            (conversation_id, limit),
        )
        return [dict(r) for r in cursor.fetchall()]


def clear_conversation_messages(
    conversation_id: int,
    db_path: Optional[str] = None,
) -> int:
    """Purges messages for a conversation thread and resets last_message preview."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM messages WHERE conversation_id = ?;", (conversation_id,))
        count = cursor.rowcount
        cursor.execute(
            "UPDATE conversations SET last_message = '', updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
            (conversation_id,),
        )
        return count


def list_conversations(
    branch_id: Optional[str] = None,
    channel: Optional[str] = None,
    status: Optional[str] = None,
    is_lead: Optional[int] = None,
    search_query: Optional[str] = None,
    limit: int = 100,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Lists conversations filtered by branch, channel, lead status, or keyword search.
    """
    query = "SELECT c.*, b.name as branch_name FROM conversations c JOIN branches b ON c.branch_id = b.id WHERE 1=1"
    params: List[Any] = []

    if branch_id and branch_id != "all":
        query += " AND c.branch_id = ?"
        params.append(branch_id.strip().lower())

    if channel and channel != "all":
        query += " AND c.channel = ?"
        params.append(channel.strip().lower())

    if status and status != "all":
        query += " AND c.status = ?"
        params.append(status.strip().lower())

    if is_lead is not None:
        query += " AND c.is_lead = ?"
        params.append(1 if is_lead else 0)

    if search_query and search_query.strip():
        term = f"%{search_query.strip()}%"
        query += " AND (c.customer_id LIKE ? OR c.customer_name LIKE ? OR c.last_message LIKE ?)"
        params.extend([term, term, term])

    query += " ORDER BY c.last_message_at DESC LIMIT ?"
    params.append(limit)

    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [dict(r) for r in cursor.fetchall()]


def get_conversation_by_id(conv_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieves full conversation record by ID."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT c.*, b.name as branch_name FROM conversations c JOIN branches b ON c.branch_id = b.id WHERE c.id = ?", (conv_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def set_conversation_mute(
    branch_id: str,
    channel: str,
    customer_id: str,
    is_muted: bool,
    hours: int = 2,
    reason: Optional[str] = None,
    db_path: Optional[str] = None,
) -> bool:
    """Mutes or unmutes AI replies for a customer conversation."""
    muted_until = None
    if is_muted:
        muted_until = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()

    status_val = "pending_human" if is_muted else "active"

    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE conversations
            SET is_muted = ?, muted_until = ?, mute_reason = ?, status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE branch_id = ? AND channel = ? AND customer_id = ?;
            """,
            (
                1 if is_muted else 0,
                muted_until,
                reason,
                status_val,
                branch_id.strip().lower(),
                channel.strip().lower(),
                customer_id.strip(),
            ),
        )
        return cursor.rowcount > 0


def set_conversation_lead(
    branch_id: str,
    channel: str,
    customer_id: str,
    is_lead: bool = True,
    lead_details: Optional[str] = None,
    db_path: Optional[str] = None,
) -> bool:
    """Tags a conversation as a reservation lead."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE conversations
            SET is_lead = ?, lead_details = ?, updated_at = CURRENT_TIMESTAMP
            WHERE branch_id = ? AND channel = ? AND customer_id = ?;
            """,
            (1 if is_lead else 0, lead_details, branch_id.strip().lower(), channel.strip().lower(), customer_id.strip()),
        )
        return cursor.rowcount > 0


def is_conversation_muted(
    branch_id: str,
    channel: str,
    customer_id: str,
    db_path: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Checks if conversation is currently muted. Handles auto-expiration of mute window.
    Returns (is_muted: bool, reason: Optional[str]).
    """
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT is_muted, muted_until, mute_reason FROM conversations
            WHERE branch_id = ? AND channel = ? AND customer_id = ?;
            """,
            (branch_id.strip().lower(), channel.strip().lower(), customer_id.strip()),
        )
        row = cursor.fetchone()
        if not row or not row["is_muted"]:
            return False, None

        # Check expiration
        muted_until = row["muted_until"]
        if muted_until:
            try:
                dt_until = datetime.fromisoformat(muted_until)
                if datetime.now(timezone.utc) > dt_until:
                    # Mute has expired -> auto-unmute
                    cursor.execute(
                        """
                        UPDATE conversations
                        SET is_muted = 0, muted_until = NULL, mute_reason = NULL, status = 'active', updated_at = CURRENT_TIMESTAMP
                        WHERE branch_id = ? AND channel = ? AND customer_id = ?;
                        """,
                        (branch_id.strip().lower(), channel.strip().lower(), customer_id.strip()),
                    )
                    return False, None
            except Exception:
                pass

        return True, row["mute_reason"]


# =====================================================================
# Branch Knowledge Base Methods
# =====================================================================

def get_branch_knowledge(branch_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieves branch markdown knowledge base and structured metadata."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT bk.*, b.name as branch_name, b.city, b.phone, b.email, b.address, b.manager_name
            FROM branch_knowledge bk
            JOIN branches b ON bk.branch_id = b.id
            WHERE bk.branch_id = ?;
            """,
            (branch_id.strip().lower(),),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def update_branch_knowledge(
    branch_id: str,
    content_markdown: str,
    updated_by: str = "admin",
    db_path: Optional[str] = None,
) -> bool:
    """Updates the knowledge base for a branch with zero downtime."""
    b_id = branch_id.strip().lower()
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO branch_knowledge (branch_id, content_markdown, updated_by, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(branch_id) DO UPDATE SET
                content_markdown = excluded.content_markdown,
                updated_by = excluded.updated_by,
                updated_at = CURRENT_TIMESTAMP;
            """,
            (b_id, content_markdown.strip(), updated_by),
        )
        return cursor.rowcount > 0


# =====================================================================
# User & Authentication Repository Methods
# =====================================================================

def get_user_by_email(email: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Looks up user record by email."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE email = ? AND is_active = 1;", (email.strip().lower(),))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Looks up user record by ID."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ? AND is_active = 1;", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def list_users(branch_id: Optional[str] = None, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Lists users, optionally filtered by branch."""
    query = "SELECT id, email, full_name, role, branch_id, is_active, created_at FROM users WHERE 1=1"
    params: List[Any] = []
    if branch_id and branch_id != "all":
        query += " AND branch_id = ?"
        params.append(branch_id.strip().lower())
    query += " ORDER BY role DESC, id ASC"

    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [dict(r) for r in cursor.fetchall()]


# =====================================================================
# Analytics & Metrics Methods
# =====================================================================

def get_global_analytics(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Aggregates high-level metrics across all 19 branches."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) as total_branches FROM branches WHERE is_active = 1;")
        total_branches = cursor.fetchone()["total_branches"]

        cursor.execute("SELECT COUNT(*) as total_conversations FROM conversations;")
        total_conversations = cursor.fetchone()["total_conversations"]

        cursor.execute("SELECT COUNT(*) as total_messages FROM messages;")
        total_messages = cursor.fetchone()["total_messages"]

        cursor.execute("SELECT COUNT(*) as total_leads FROM conversations WHERE is_lead = 1;")
        total_leads = cursor.fetchone()["total_leads"]

        cursor.execute("SELECT COUNT(*) as pending_human FROM conversations WHERE status = 'pending_human';")
        pending_human = cursor.fetchone()["pending_human"]

        # Counts by Channel
        cursor.execute("SELECT channel, COUNT(*) as count FROM conversations GROUP BY channel;")
        channel_breakdown = {r["channel"]: r["count"] for r in cursor.fetchall()}

        # Active Branches with most recent messages
        cursor.execute(
            """
            SELECT b.id, b.name, b.city, COUNT(c.id) as chat_count,
                   SUM(CASE WHEN c.is_lead = 1 THEN 1 ELSE 0 END) as lead_count
            FROM branches b
            LEFT JOIN conversations c ON b.id = c.branch_id
            GROUP BY b.id
            ORDER BY chat_count DESC;
            """
        )
        branches_stats = [dict(r) for r in cursor.fetchall()]

        return {
            "total_branches": total_branches,
            "total_conversations": total_conversations,
            "total_messages": total_messages,
            "total_leads": total_leads,
            "pending_human": pending_human,
            "channel_breakdown": channel_breakdown,
            "branches_stats": branches_stats,
        }


def get_branch_analytics(branch_id: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Calculates detailed metrics for a single branch."""
    b_id = branch_id.strip().lower()
    with get_db(db_path) as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) as total_conversations FROM conversations WHERE branch_id = ?;", (b_id,))
        total_conv = cursor.fetchone()["total_conversations"]

        cursor.execute("SELECT COUNT(*) as total_messages FROM messages WHERE branch_id = ?;", (b_id,))
        total_msg = cursor.fetchone()["total_messages"]

        cursor.execute("SELECT COUNT(*) as total_leads FROM conversations WHERE branch_id = ? AND is_lead = 1;", (b_id,))
        total_leads = cursor.fetchone()["total_leads"]

        cursor.execute("SELECT COUNT(*) as pending_human FROM conversations WHERE branch_id = ? AND status = 'pending_human';", (b_id,))
        pending_human = cursor.fetchone()["pending_human"]

        cursor.execute("SELECT channel, COUNT(*) as count FROM conversations WHERE branch_id = ? GROUP BY channel;", (b_id,))
        channel_breakdown = {r["channel"]: r["count"] for r in cursor.fetchall()}

        return {
            "branch_id": b_id,
            "total_conversations": total_conv,
            "total_messages": total_msg,
            "total_leads": total_leads,
            "pending_human": pending_human,
            "channel_breakdown": channel_breakdown,
        }


# =====================================================================
# System Settings Methods
# =====================================================================

def get_system_settings(db_path: Optional[str] = None) -> Dict[str, str]:
    """Retrieves key-value system settings."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM system_settings;")
        return {row["key"]: row["value"] for row in cursor.fetchall()}


def set_system_setting(key: str, value: str, db_path: Optional[str] = None) -> None:
    """Sets or updates a system setting."""
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO system_settings (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP;
            """,
            (key.strip(), value.strip()),
        )


def cleanup_old_messages(days: int = 30, db_path: Optional[str] = None) -> int:
    """
    KVKK / GDPR Data Minimization (Veri Minimizasyonu):
    Prunes messages older than the given number of days to prevent indefinite retention of customer PII.
    Returns the number of deleted records.
    """
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with get_db(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            DELETE FROM messages
            WHERE created_at < ?
               OR created_at < datetime('now', '-' || ? || ' days');
            """,
            (cutoff_iso, str(days)),
        )
        deleted_count = cursor.rowcount if cursor.rowcount is not None else 0
        if deleted_count > 0:
            logger.info("KVKK Data Minimization: Pruned %d messages older than %d days.", deleted_count, days)
        return deleted_count


# Ensure tables and seed data are created upon initial import
try:
    init_db()
except Exception as _e:
    logger.debug("Database auto-init deferred: %s", _e)


