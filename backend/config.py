import os
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

# Load root .env or backend .env
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")

logger = logging.getLogger("omni-config")

# =====================================================================
# Server & Environment Settings
# =====================================================================
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", 8000))
ENVIRONMENT = os.getenv("ENVIRONMENT", "production")
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# =====================================================================
# Database Configuration
# =====================================================================
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DEFAULT_SQLITE_PATH = str(Path(__file__).resolve().parent / "omnichannel_data.db")
DATABASE_PATH = os.getenv("DATABASE_PATH", DEFAULT_SQLITE_PATH)

# =====================================================================
# Redis Configuration (Debounce Buffer, Cache, Queue)
# =====================================================================
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_ENABLED = os.getenv("REDIS_ENABLED", "true").lower() == "true"
DEBOUNCE_SECONDS = int(os.getenv("DEBOUNCE_SECONDS", "15"))

# =====================================================================
# JWT Authentication & RBAC
# =====================================================================
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "omni_enterprise_super_secret_jwt_key_2026")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 480))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", 30))

INITIAL_ADMIN_EMAIL = os.getenv("INITIAL_ADMIN_EMAIL", "admin@navitasspa.com")
INITIAL_ADMIN_PASSWORD = os.getenv("INITIAL_ADMIN_PASSWORD", "AdminSecure2026!")
INITIAL_ADMIN_NAME = os.getenv("INITIAL_ADMIN_NAME", "Navitas Spa Genel Direktörü")

# =====================================================================
# LLM Provider Configuration
# =====================================================================
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "45.0"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))
LLM_TOP_P = float(os.getenv("LLM_TOP_P", "0.9"))

# Ollama Fallback Settings
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

def update_llm_config(
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
) -> None:
    """Dynamically updates active LLM credentials and provider settings in runtime."""
    global LLM_PROVIDER, LLM_API_KEY, LLM_MODEL, LLM_BASE_URL
    if api_key is not None:
        LLM_API_KEY = api_key.strip()
    if provider is not None:
        LLM_PROVIDER = provider.strip().lower()
    if model is not None:
        LLM_MODEL = model.strip()
    if base_url is not None:
        LLM_BASE_URL = base_url.strip()

    if LLM_API_KEY and LLM_PROVIDER == "ollama":
        LLM_PROVIDER = "openai"

    if LLM_PROVIDER == "openai":
        if not LLM_BASE_URL:
            LLM_BASE_URL = "https://api.openai.com/v1"
        if not LLM_MODEL:
            LLM_MODEL = "gpt-4o-mini"
    elif LLM_PROVIDER == "groq":
        if not LLM_BASE_URL:
            LLM_BASE_URL = "https://api.groq.com/openai/v1"
        if not LLM_MODEL:
            LLM_MODEL = "llama-3.3-70b-versatile"
    elif LLM_PROVIDER == "openrouter":
        if not LLM_BASE_URL:
            LLM_BASE_URL = "https://openrouter.ai/api/v1"
        if not LLM_MODEL:
            LLM_MODEL = "meta-llama/llama-3.3-70b-instruct"
    elif LLM_PROVIDER == "gemini":
        if not LLM_BASE_URL:
            LLM_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
        if not LLM_MODEL:
            LLM_MODEL = "gemini-1.5-flash"

# =====================================================================
# Omnichannel Webhook & Provider Credentials
# =====================================================================
META_WEBHOOK_VERIFY_TOKEN = os.getenv("META_WEBHOOK_VERIFY_TOKEN", "omni_meta_secure_verify_token_2026")
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v20.0")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")

INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
INSTAGRAM_ACCOUNT_ID = os.getenv("INSTAGRAM_ACCOUNT_ID", "")

MESSENGER_ACCESS_TOKEN = os.getenv("MESSENGER_ACCESS_TOKEN", "")
MESSENGER_PAGE_ID = os.getenv("MESSENGER_PAGE_ID", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")

BRIDGE_API_URL = os.getenv("BRIDGE_API_URL", "http://127.0.0.1:3001")
TRIGGER_PREFIX = os.getenv("TRIGGER_PREFIX", "")
ONLY_UNKNOWN_CONTACTS = os.getenv("ONLY_UNKNOWN_CONTACTS", "false").lower() == "true"
BLACKLIST = [x.strip() for x in os.getenv("BLACKLIST", "").split(",") if x.strip()]

# =====================================================================
# Security & Notification Settings
# =====================================================================
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "6"))
RATE_LIMIT_DAILY = int(os.getenv("RATE_LIMIT_DAILY", "40"))
ENABLE_JAILBREAK_FILTER = os.getenv("ENABLE_JAILBREAK_FILTER", "true").lower() == "true"
ENABLE_OFFTOPIC_FILTER = os.getenv("ENABLE_OFFTOPIC_FILTER", "true").lower() == "true"

NTFY_SERVER_URL = os.getenv("NTFY_SERVER_URL", "https://ntfy.sh")
NTFY_GLOBAL_TOPIC = os.getenv("NTFY_GLOBAL_TOPIC", "navitas-spa-global-alerts")

# =====================================================================
# Navitas Spa & Wellness Catalog: 17 Distinct Luxury Hotel Spas
# =====================================================================
BRANCHES_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "istanbul-airport",
        "name": "İstanbul Havalimanı (Hilton Istanbul Airport)",
        "city": "İstanbul",
        "address": "İmrahor Mahallesi, Terminal Caddesi No: 13, 34283 Arnavutköy / İstanbul",
        "phone": "+90 535 814 07 44",
        "email": "hiltonistairport@navitasspa.com",
        "manager_name": "Seda Yılmaz",
        "ntfy_topic": "navitas-istanbul-airport",
        "check_in": "07:00",
        "check_out": "22:00",
        "pricing": {
            "antistress_masaji": "Fiyat bilgisi WhatsApp/telefon üzerinden paylaşılmaktadır (60 dk)",
            "klasik_isvec_masaji": "Fiyat bilgisi WhatsApp/telefon üzerinden paylaşılmaktadır (50 dk)",
            "bali_masaji": "Fiyat bilgisi WhatsApp/telefon üzerinden paylaşılmaktadır (50/90 dk)",
            "geleneksel_kese_kopuk": "Fiyat bilgisi WhatsApp/telefon üzerinden paylaşılmaktadır (45 dk)",
            "sultan_hamami_4_el": "Fiyat bilgisi WhatsApp/telefon üzerinden paylaşılmaktadır (50 dk)",
            "focused_care_transit": "Fiyat bilgisi WhatsApp/telefon üzerinden paylaşılmaktadır (20-25 dk)",
            "vip_ritueli": "Özel süit tahsisi, çift masajı, sauna, jakuzi ve ikramlar dahil",
        },
        "amenities": [
            "Terminalden 5 dk yürüyüş (Kara tarafı / Landside)",
            "Geleneksel Türk hamamı & ısıtmalı göbek taşı",
            "Yıl boyu ısıtmalı kapalı yüzme havuzu",
            "Fin saunası & mozaik buhar odası",
            "Isıtmalı dinlenme şezlongları",
            "3 bakım odası (2 tekli, 1 çift kişilik VIP süit)",
            "24/7 açık fitness (otel misafirleri için)",
            "Valiz kilitli dolap muhafazası",
            "Apron kartlı çalışanlara özel üyelik indirimi"
        ],
        "description": "Hilton Istanbul Airport içinde, terminale birkaç dakika mesafede. Transit yolcular için hızlı seanslar, uçuş öncesi/sonrası derin arınma ve lüks dinlenme alanı."
    },
    {
        "id": "mall-of-istanbul",
        "name": "Mall of İstanbul (Hilton Mall of İstanbul)",
        "city": "İstanbul",
        "address": "Ziya Gökalp Mah. Süleyman Demirel Blv. No:7, 34494 Başakşehir / İstanbul",
        "phone": "+90 212 292 09 44",
        "email": "mallofistanbul@navitasspa.com",
        "manager_name": "Banu Aksoy",
        "ntfy_topic": "navitas-mall-of-istanbul",
        "check_in": "09:00",
        "check_out": "22:00",
        "pricing": {
            "klasik_masaj": "Kişi başı seans fiyatı WhatsApp/resepsiyondan iletilir (50 dk)",
            "hydrafacial_cilt_bakimi": "Derin gözenek temizliği, peeling ve nemlendirme",
            "gelin_hamami_paketi": "Özel alan tahsisi, ikramlar ve kese-köpük (Max 15 kişi)",
            "fitness_uyelik": "3 aylık, 6 aylık ve yıllık üyelik paketleri"
        },
        "amenities": [
            "Geleneksel Türk hamamı",
            "Aynalı tavanlı kapalı yüzme havuzu",
            "Hydrafacial cilt bakımı odaları",
            "VIP ritüel süiti (sauna, jakuzi, sinema konforu)",
            "Tam donanımlı fitness merkezi & Personal Training",
            "Gelin hamamı organizasyon alanı"
        ],
        "description": "Hilton Mall of İstanbul bünyesinde, alışveriş ve iş temposunun ortasında tam donanımlı lüks spa ve fitness merkezi."
    },
    {
        "id": "topkapi",
        "name": "Topkapı (DoubleTree by Hilton Topkapı)",
        "city": "İstanbul",
        "address": "Orta Mah. Anıt Sokağı No:1, 34040 Bayrampaşa / İstanbul",
        "phone": "+90 533 071 33 73",
        "email": "topkapi@navitasspa.com",
        "manager_name": "Murat Çelik",
        "ntfy_topic": "navitas-topkapi",
        "check_in": "09:00",
        "check_out": "22:00",
        "pricing": {
            "masaj_ritueli": "Seçilen masaj ve süreye göre belirlenir",
            "kese_kopuk": "Geleneksel ipek kese ve zeytinyağlı köpük seansı"
        },
        "amenities": ["Türk hamamı", "Kapalı yüzme havuzu", "Sauna & buhar odası", "Tekli & çift masaj odaları", "Fitness salonu"],
        "description": "DoubleTree by Hilton Topkapı içinde, tarihi yarımadaya komşu ferah ve konforlu spa merkezi."
    },
    {
        "id": "laleli",
        "name": "Laleli (Crowne Plaza İstanbul Old City)",
        "city": "İstanbul",
        "address": "Balabanağa Mah. Fethibey Cd. Laleli, 34134 Fatih / İstanbul",
        "phone": "+90 530 053 35 24",
        "email": "laleli@navitasspa.com",
        "manager_name": "Aylin Şahin",
        "ntfy_topic": "navitas-laleli",
        "check_in": "09:00",
        "check_out": "22:00",
        "pricing": {
            "masaj_ve_hamam": "Dinamik fiyatlandırma WhatsApp üzerinden iletilir"
        },
        "amenities": ["Tarihi dokuda Türk hamamı", "Uzak Doğu masaj odaları", "Sauna", "Buhar odası", "Dinlenme salonu"],
        "description": "Tarihi yarımadanın merkezinde, Crowne Plaza İstanbul Old City içinde kadim hamam ve masaj deneyimi."
    },
    {
        "id": "sultanahmet",
        "name": "Sultanahmet (Hagia Sofia Mansions, Curio Collection)",
        "city": "İstanbul",
        "address": "Sultan Ahmet Mah. Soğuk Çeşme Sk. No:3, 34122 Fatih / İstanbul",
        "phone": "+90 530 047 52 44",
        "email": "sultanahmet@navitasspa.com",
        "manager_name": "Kemal Demir",
        "ntfy_topic": "navitas-sultanahmet",
        "check_in": "09:00",
        "check_out": "22:00",
        "pricing": {
            "durum": "Tadilat sürecinde (Laleli veya Topkapı şubelerimiz hizmet vermektedir)"
        },
        "amenities": ["Tarihi sarnıç mimarisi", "Özel masaj süitleri", "Geleneksel Türk hamamı"],
        "description": "Ayasofya'nın gölgesinde tarihi konakların içinde yer alan merkezimiz şu anda tadilattadır; misafirlerimize Laleli veya Topkapı şubelerimizi öneriyoruz."
    },
    {
        "id": "5-levent",
        "name": "5. Levent (Levent Çarşı)",
        "city": "İstanbul",
        "address": "5. Levent, Levent Çarşı, Güzeltepe Mah. 15 Temmuz Şehitler Cad. No:10/1D, Eyüpsultan / İstanbul",
        "phone": "+90 212 445 19 44",
        "email": "5levent@navitasspa.com",
        "manager_name": "Ezgi Vural",
        "ntfy_topic": "navitas-5levent",
        "check_in": "09:00",
        "check_out": "22:00",
        "pricing": {
            "masaj_hizmetleri": "Geniş masaj koleksiyonu ve hamam ritüelleri"
        },
        "amenities": ["Masaj odaları", "Hamam", "Sauna & buhar", "Fitness alanı"],
        "description": "5. Levent yerleşkesinde, modern yaşamın stresinden arınmak için tasarlanmış seçkin wellness merkezi."
    },
    {
        "id": "mahmutbey",
        "name": "Mahmutbey (Tryp by Wyndham İstanbul Bağcılar)",
        "city": "İstanbul",
        "address": "Mahmutbey Mah. Ordu Cd. No:13, 34218 Bağcılar / İstanbul",
        "phone": "+90 212 445 01 44",
        "email": "mahmutbey@navitasspa.com",
        "manager_name": "Serkan Öz",
        "ntfy_topic": "navitas-mahmutbey",
        "check_in": "09:00",
        "check_out": "22:00",
        "pricing": {
            "masaj_ve_hamam": "Rezervasyon hattımızdan anlık fiyat bilgisi sunulur"
        },
        "amenities": ["Kapalı havuz", "Geleneksel hamam", "Sauna", "Buhar odası", "Fitness"],
        "description": "Basın Ekspres aksında Tryp by Wyndham içinde iş ve şehir konaklamaları için eksiksiz spa çözümü."
    },
    {
        "id": "halkali",
        "name": "Halkalı (Delta Hotels by Marriott İstanbul Halkalı)",
        "city": "İstanbul",
        "address": "Basın Ekspres Cd., Halkalı Caddesi No:2, 34303 Halkalı / İstanbul",
        "phone": "+90 212 692 00 43",
        "email": "halkali@navitasspa.com",
        "manager_name": "Can Yıldız",
        "ntfy_topic": "navitas-halkali",
        "check_in": "09:00",
        "check_out": "22:00",
        "pricing": {
            "masaj_ve_spa": "Kurumsal ve bireysel paket seçenekleri"
        },
        "amenities": ["Yüzme havuzu", "Hamam", "Sauna", "Buhar odası", "Spor salonu"],
        "description": "Marriott standartlarında, Basın Ekspres üzerinde ferah ve modern spa olanakları."
    },
    {
        "id": "ankara-hilton",
        "name": "Ankara Hilton (Ankara HiltonSA)",
        "city": "Ankara",
        "address": "Kavaklıdere Mah. Tahran Cd. No:12, 06700 Çankaya / Ankara",
        "phone": "+90 312 455 00 00",
        "email": "ankarahilton@navitasspa.com",
        "manager_name": "Derya Doğan",
        "ntfy_topic": "navitas-ankara-hilton",
        "check_in": "09:00",
        "check_out": "22:00",
        "pricing": {
            "masaj_ritueli": "Kişi başı seans fiyatları telefon ve resepsiyondan iletilir",
            "hamam_ritueli": "Kese ve köpük uygulaması"
        },
        "amenities": [
            "Cam kubbeli doğal ışık alan kapalı yüzme havuzu",
            "Beyaz mermer göbek taşlı Türk hamamı",
            "Finlandiya saunası & buhar odası",
            "Buhar odası bitişiğinde özel masaj süiti",
            "Tekli ve çift kişilik masaj odaları",
            "Tam donanımlı fitness merkezi",
            "Health Club açık hava terası"
        ],
        "description": "Ankara'nın kalbinde, Kavaklıdere'de Ankara HiltonSA içinde cam kubbeli havuzu ve mermer hamamıyla prestijli spa durağı."
    },
    {
        "id": "ankara-sheraton",
        "name": "Ankara Sheraton (Sheraton Ankara Hotel & Convention Center)",
        "city": "Ankara",
        "address": "Gaziosmanpaşa Mah. Şht. Ömer Haluk Sipahioğlu Sk., 06700 Çankaya / Ankara",
        "phone": "+90 312 457 60 00",
        "email": "ankarasheraton@navitasspa.com",
        "manager_name": "Burak Kurt",
        "ntfy_topic": "navitas-ankara-sheraton",
        "check_in": "09:00",
        "check_out": "22:00",
        "pricing": {
            "spa_masaj": "Rezervasyon üzerinden anlık fiyat paylaşılır"
        },
        "amenities": ["Geniş kapalı havuz", "Geleneksel Türk hamamı", "Fin saunası", "Buhar odası", "Club fitness"],
        "description": "Sheraton Ankara'nın ikonik mimarisinde, diplomatik ve kurumsal misafirlere özel üst segment wellness alanı."
    },
    {
        "id": "cerkezkoy",
        "name": "Çerkezköy (Hawthorn Suites by Wyndham Çerkezköy)",
        "city": "Tekirdağ",
        "address": "Hawthorn Suites by Wyndham Çerkezköy, Tekirdağ",
        "phone": "+90 530 053 35 28",
        "email": "cerkezkoy@navitasspa.com",
        "manager_name": "Tolga Erdem",
        "ntfy_topic": "navitas-cerkezkoy",
        "check_in": "09:00",
        "check_out": "22:00",
        "pricing": {
            "masaj_ve_hamam": "WhatsApp üzerinden hızlı fiyat teklifi"
        },
        "amenities": ["Kapalı havuz", "Hamam", "Sauna", "Masaj odaları", "Fitness"],
        "description": "Trakya sanayi ve ticaret bölgesinde iş stresini geride bırakmak için birinci sınıf spa merkezi."
    },
    {
        "id": "topuk-yaylasi",
        "name": "Topuk Yaylası (Fenerbahçe Topuk Yaylası Resort)",
        "city": "Düzce",
        "address": "Orta Mah. 81900 Hacıazizler / Kaynaşlı / Düzce",
        "phone": "+90 530 607 52 44",
        "email": "topuk@navitasspa.com",
        "manager_name": "Selim Arslan",
        "ntfy_topic": "navitas-topuk-yaylasi",
        "check_in": "09:00",
        "check_out": "22:00",
        "pricing": {
            "yayla_spa_paketi": "Göl ve orman manzaralı dinlenme seansları"
        },
        "amenities": ["Göl manzaralı kapalı havuz", "Geleneksel hamam", "Buhar odası", "Sporcu masajı odaları", "Sauna"],
        "description": "Göknar ormanları ve göl kıyısında, temiz yayla havası eşliğinde sporcu toparlanması ve doğa içinde spa deneyimi."
    },
    {
        "id": "marmaris-lumira",
        "name": "Marmaris Lumira (Sinpaş Kızılbük Thermal Resort)",
        "city": "Muğla",
        "address": "Sinpaş Kızılbük Thermal Wellness Resort, İçmeler, 48740 Marmaris / Muğla",
        "phone": "+90 530 743 64 97",
        "email": "marmaris@navitasspa.com",
        "manager_name": "Melis Tan",
        "ntfy_topic": "navitas-marmaris-lumira",
        "check_in": "09:00",
        "check_out": "22:00",
        "pricing": {
            "termal_wellness_paketi": "Şifalı termal sular ve masaj kombinasyonları"
        },
        "amenities": ["Şifalı termal havuzlar", "Kızılbük koy manzarası", "Geleneksel hamam", "Doğal peeling ve sargılama", "Buhar odası"],
        "description": "Ege'nin turkuaz koyunda, termal şifalı suları lüks Navitas dokunuşuyla buluşturan ayrıcalıklı wellness merkezi."
    },
    {
        "id": "adana-hilton",
        "name": "Adana Hilton (Adana HiltonSA)",
        "city": "Adana",
        "address": "Sinanpaşa Mah. Hacı Sabancı Blv. No:1, 01220 Yüreğir / Adana",
        "phone": "+90 322 355 50 00",
        "email": "adanahilton@navitasspa.com",
        "manager_name": "Oğuz Karahan",
        "ntfy_topic": "navitas-adana-hilton",
        "check_in": "09:00",
        "check_out": "22:00",
        "pricing": {
            "masaj_ve_hamam": "Rezervasyon üzerinden teyit edilir"
        },
        "amenities": ["Seyhan Nehri manzaralı kapalı havuz", "Türk hamamı", "Sauna", "Buhar odası", "Fitness"],
        "description": "Seyhan Nehri kıyısında Adana HiltonSA içinde, Akdeniz sıcaklığında profesyonel arınma durağı."
    },
    {
        "id": "malatya-movenpick",
        "name": "Malatya Mövenpick (Mövenpick Hotel Malatya)",
        "city": "Malatya",
        "address": "İnönü Mah. İnönü Cd. No:174, 44090 Yeşilyurt / Malatya",
        "phone": "+90 422 377 70 00",
        "email": "malatya@navitasspa.com",
        "manager_name": "Fırat Şen",
        "ntfy_topic": "navitas-malatya-movenpick",
        "check_in": "09:00",
        "check_out": "22:00",
        "pricing": {
            "masaj_hizmetleri": "Günün yorgunluğunu alan medikal ve rahatlatıcı masajlar"
        },
        "amenities": ["Kapalı havuz", "Geleneksel Türk hamamı", "Fin saunası", "Buhar odası", "Fitness"],
        "description": "Mövenpick kalitesiyle Doğu Anadolu'nun merkezinde konforlu ve steril spa işletmeciliği."
    },
    {
        "id": "bolu-abant",
        "name": "Abant (Abant)",
        "city": "Bolu",
        "address": "Yanık, Abant Yolu, 14030 Dereceören / Bolu Merkez / Bolu",
        "phone": "+90 535 814 07 44",
        "email": "abant@navitasspa.com",
        "manager_name": "Rezervasyon Koordinatörlüğü",
        "ntfy_topic": "navitas-abant",
        "check_in": "09:00",
        "check_out": "22:00",
        "pricing": {
            "durum": "Tadilatta - Bilgi için merkezi rezervasyon hattımıza danışabilirsiniz"
        },
        "amenities": ["Doğa içi göl konsepti", "Hamam", "Sauna"],
        "description": "Abant Tabiat Parkı yolu üzerinde doğayla baş başa dinlenme merkezi (Şu an tadilat dolayısıyla kapalıdır)."
    },
    {
        "id": "izmir-bomonti",
        "name": "İzmir Bomonti (Mahall Bomonti İzmir)",
        "city": "İzmir",
        "address": "Halkapınar Mah. Şehitler Cd., 1558. Sk. No:2, 35170 Konak / İzmir",
        "phone": "+90 535 814 07 44",
        "email": "izmir@navitasspa.com",
        "manager_name": "Açılış Koordinasyon Ekibi",
        "ntfy_topic": "navitas-izmir-bomonti",
        "check_in": "09:00",
        "check_out": "22:00",
        "pricing": {
            "durum": "Çok Yakında Açılıyor - Ön kayıt ve bilgi hattı aktiftir"
        },
        "amenities": ["En yeni teknoloji spa donanımı", "Termal alanlar", "VIP süitler", "Kapalı havuz"],
        "description": "Tarihi Bomonti fabrikasının modern yorumu Mahall Bomonti İzmir'de çok yakında kapılarını açacak en yeni Navitas merkezi."
    }
]

# Quick lookup map for branches
BRANCHES_MAP: Dict[str, Dict[str, Any]] = {b["id"]: b for b in BRANCHES_CATALOG}

def get_branch_by_id(branch_id: str) -> Optional[Dict[str, Any]]:
    """Returns branch definition dict or None."""
    return BRANCHES_MAP.get(branch_id.strip().lower())

def list_all_branches() -> List[Dict[str, Any]]:
    """Returns list of all active branches."""
    return BRANCHES_CATALOG
