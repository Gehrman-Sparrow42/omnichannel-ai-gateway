import logging
from typing import Dict, Any, List, Optional
import httpx

import config
import database

logger = logging.getLogger("omni-llm-router")

# In-memory prompt cache to speed up generation, invalidated upon DB update
_prompt_cache: Dict[str, str] = {}


def invalidate_branch_prompt_cache(branch_id: Optional[str] = None):
    """Invalidates cached system prompt for a branch or all branches."""
    if branch_id:
        _prompt_cache.pop(branch_id.strip().lower(), None)
    else:
        _prompt_cache.clear()
    logger.info("Prompt cache invalidated for branch: %s", branch_id or "ALL")


def build_branch_system_prompt(branch_id: str) -> str:
    """
    Dynamically generates the specialized luxury system prompt for a specific Navitas Spa branch.
    Injects branch location, hotel partner, contact info, hours, and markdown knowledge base.
    """
    b_id = branch_id.strip().lower()
    if b_id in _prompt_cache:
        return _prompt_cache[b_id]

    branch_info = config.get_branch_by_id(b_id)
    b_name = branch_info["name"] if branch_info else f"Navitas Spa ({b_id.title()})"
    b_city = branch_info["city"] if branch_info else "İstanbul"
    b_phone = branch_info["phone"] if branch_info else "+90 535 814 07 44"
    b_address = branch_info["address"] if branch_info else "Otel İçi Spa Alanı"
    check_in = branch_info.get("check_in", "09:00") if branch_info else "09:00"
    check_out = branch_info.get("check_out", "22:00") if branch_info else "22:00"

    # Fetch live custom markdown knowledge from database
    kb_record = database.get_branch_knowledge(b_id)
    custom_kb_text = kb_record["content_markdown"] if kb_record else ""

    prompt = f"""Sen **Navitas Spa & Wellness — {b_name}** merkezinin resmi, son derece nazik, saygılı, dingin ve lüks ağırlama standartlarına sahip AI Asistanısın.

### ✨ KURUMSAL KİMLİK & MARKA FELSEFESİ:
- **Navitas**, Latincede **"Enerji"** demektir. Bedene ve ruha kaybettiği enerjiyi geri vermeyi amaçlarız.
- Sloganımız: *"Ultimate relaxation for your soul"*
- 1999 yılından bu yana sektördeyiz; ilk Navitas merkezimiz 2010 yılında açılmıştır.
- **Terapist Ekolümüz:** Terapistlerimiz Endonezya / Bali kökenli köklü masaj akademilerinden mezun, anatomi bilgisi güçlü, sertifikalı uzmanlardır. Kadim Türk hamamı ritüelleri ile Uzak Doğu dokunuş sanatını aynı çatı altında buluşturuyoruz.

---
### 📍 AKTİF ŞUBE BİLGİLERİ:
- **Şube / Merkez:** {b_name}
- **Şehir / Bölge:** {b_city}
- **Adres:** {b_address}
- **İletişim & WhatsApp Randevu Hattı:** {b_phone}
- **Çalışma Saatleri:** Haftanın her günü {check_in} – {check_out} arası kesintisiz hizmet vermekteyiz.

---
### 📚 ŞUBE BİLGİ BANKASI VE HİZMET DETAYLARI:
{custom_kb_text}
---

### 🛡️ KESİN DAVRANIŞ, REZERVASYON VE GÜVENLİK KURALLARI:
1. **Lüks & Saygılı İletişim:** Bir lüks otel spa resepsiyonistinin zarafetine yakışır şekilde 'Siz' diliyle konuş, nazik ve dingin bir ton kullan (✨, 🌿, 💆‍♀️, 🛁 gibi ölçülü emojiler kullanabilirsin). Robotik "Ben bir yapay zekayım" gibi soğuk kalıplar kullanma; spa ekibinin bir parçası gibi doğal ve zarif ol.
2. **KESİNLİKLE BİLGİ UYDURMA (Anti-Hallucination Kuralı):** Yukarıdaki doğrulanmış şube bilgi bankasında açıkça yazmayan fiyat, kural, medikal iddia veya hizmet uydurma. Bilgi bankasında yer almayan bir detay sorulursa asla tahmin yürütme; "Bu konuda yanlış bir bilgi vermemek adına, şube yetkilimize danışıp size en kısa sürede buradan dönüş sağlayalım ✨" diyerek konuyu şube yetkilisine devret.
3. **DİL AYNASI & ÇOK DİLLİ YANIT (Language Mirroring Rule):** Misafir mesajı hangi dilde yazdıysa (Türkçe, İngilizce, Rusça, Arapça, Almanca, Fransızca, İspanyolca vb.) KESİNLİKLE VE İSTİSNASIZ O DİLDE CEVAP VER. Karşı taraf İngilizce yazdıysa İngilizce, Rusça yazdıysa kusursuz Rusça, Arapça yazdıysa Arapça yanıt ver. Asla misafirin konuştuğu dilin dışına çıkma.
4. **Yalnızca Spa & Wellness Konuları:** Kodlama, şiir, hikaye, siyaset, ödev çözümü gibi spa dışı taleplere KESİNLİKLE yanıt verme. Kibarca yalnızca spa, hamam ve randevu konularında yardımcı olabileceğini belirt.
5. **Fiyatlandırma Soruları:** Fiyatlarımız kişi başıdır; seçilen şubeye, seans süresine ve dönemsel paketlere göre değişir. Seans süresini belirt (Örn: 50 dk / 60 dk), net fiyat ve anlık müsaitlik teyidi için misafiri şubemizin WhatsApp/telefon hattına ({b_phone}) davet et.
6. **Tadilat Bilgilendirmesi:** Sultanahmet veya Abant şubesi sorulursa, bu merkezlerin şu anda tadilatta olduğunu kibarca belirt ve en yakın aktif şubelerimizi (Sultanahmet için Laleli veya Topkapı) öner.
7. **İstanbul Havalimanı Şubesi Özel Kuralı:** Havalimanı şubemiz Hilton Istanbul Airport içinde, kara tarafındadır (landside). Uluslararası aktarma yolcuları pasaporttan geçerek gelebilir. Valizler için resepsiyonda kilitli dolap mevcuttur. Uçuş rötarlarında esneklik sağlanır.
8. **Havuz Kuralı:** Hijyen standartlarımız gereği kapalı havuzda bone takılması zorunludur. Havlu, bornoz, peştamal ve terlik temin edilir; misafir kendi mayosunu getirmelidir.
9. **İptal & Değişiklik:** Randevudan en az 4 saat öncesine kadar değişiklik ve iptal ücretsizdir.
10. **Hamilelik & Sağlık:** Hamileliğin ilk 3 ayında masaj uygulanmaz; sonraki aylarda hafif seanslar yapılabilir ancak sauna, hamam ve sıcak ıslak alanlar yasaktır.
11. **Rezervasyon Talebi:** Misafir randevu talep ettiğinde: İsim, Tarih, Tercih Ettiği Saat, Hizmet Türü ve Kişi Sayısını sor ve şube ekibimizin hemen kesinleştirmesi için WhatsApp bağlantısını sun.
"""
    _prompt_cache[b_id] = prompt.strip()
    return _prompt_cache[b_id]


def get_llm_backend_info() -> Dict[str, Any]:
    """Returns active LLM backend runtime metadata and safety budget statistics."""
    from security import api_budget_guard
    saved = database.get_system_settings()
    provider = saved.get("llm_provider", config.LLM_PROVIDER)
    model = saved.get("llm_model", config.LLM_MODEL)
    base_url = saved.get("llm_base_url", config.LLM_BASE_URL)
    has_key = bool(saved.get("llm_api_key", config.LLM_API_KEY))
    return {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "has_api_key": has_key,
        "temperature": config.LLM_TEMPERATURE,
        "top_p": config.LLM_TOP_P,
        "timeout": config.LLM_TIMEOUT,
        "max_tokens": 350,
        "hallucination_guard": "active",
        "multilingual_mirror": "active",
        "budget_stats": api_budget_guard.get_budget_stats(),
    }


async def query_llm_for_branch(
    branch_id: str,
    user_message: str,
    history: List[Dict[str, str]],
) -> str:
    """
    Routes the prompt to the configured LLM provider (OpenAI, Groq, Gemini, OpenRouter, or Ollama).
    Constructs message payload: [System Prompt (Branch Injected)] + [History Window] + [User Turn].
    Enforces APIBudgetGuard to prevent cost exhaustion and rate-limit attacks.
    """
    from security import api_budget_guard

    system_prompt = build_branch_system_prompt(branch_id)

    messages_payload: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    messages_payload.extend(history)
    messages_payload.append({"role": "user", "content": user_message})

    # Read active LLM credentials from DB or config
    saved_settings = database.get_system_settings()
    provider = saved_settings.get("llm_provider", config.LLM_PROVIDER).lower()
    api_key = saved_settings.get("llm_api_key", config.LLM_API_KEY)
    model_name = saved_settings.get("llm_model", config.LLM_MODEL)
    base_url = saved_settings.get("llm_base_url", config.LLM_BASE_URL)

    is_cloud_api = provider in ["openai", "groq", "openrouter", "gemini", "custom"] or bool(api_key)

    if is_cloud_api:
        # 1. Check API Key & Global Budget Guard before dispatching
        is_budget_ok, refusal_msg = api_budget_guard.check_budget_allowance()
        if not is_budget_ok:
            logger.warning("Cloud LLM query blocked by budget guard: %s", refusal_msg)
            return (
                "Değerli misafirimiz, merkezimiz şu anda yoğunluk ve sistem güvenlik koruması altındadır. "
                "Sorularınız ve anlık randevu teyidi için lütfen doğrudan şubemizin WhatsApp hattı üzerinden bize ulaşınız. ✨"
            )

        if provider == "groq" and not saved_settings.get("llm_base_url"):
            target_url = "https://api.groq.com/openai/v1/chat/completions"
            if not model_name or model_name == "gpt-4o-mini":
                model_name = "llama-3.3-70b-versatile"
        elif provider == "openrouter" and not saved_settings.get("llm_base_url"):
            target_url = "https://openrouter.ai/api/v1/chat/completions"
        else:
            target_url = f"{base_url.rstrip('/')}/chat/completions"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model_name,
            "messages": messages_payload,
            "temperature": config.LLM_TEMPERATURE,
            "top_p": config.LLM_TOP_P,
            "max_tokens": 350,  # Strict cap to prevent massive essay/token drainage
        }

        try:
            async with httpx.AsyncClient(timeout=config.LLM_TIMEOUT) as client:
                resp = await client.post(target_url, json=body, headers=headers)
                if resp.status_code == 200:
                    api_budget_guard.record_call()
                    data = resp.json()
                    return data["choices"][0]["message"]["content"].strip()
                else:
                    logger.error("Cloud LLM API error (%d): %s", resp.status_code, resp.text)
                    return "Değerli misafirimiz, şu anda yoğunluk nedeniyle sistemimiz yanıt veremiyor. Rezervasyon ve anlık bilgi için lütfen şubemizi doğrudan arayınız veya WhatsApp üzerinden yazınız. ✨"
        except Exception as exc:
            logger.error("Failed connecting to Cloud LLM provider: %s", exc)
            return "Değerli misafirimiz, şu anda teknik bir aksaklık yaşanmaktadır. Lütfen doğrudan WhatsApp randevu hattımız üzerinden bizimle iletişime geçiniz. ✨"

    # Fallback to Local Ollama
    ollama_url = f"{config.OLLAMA_BASE_URL.rstrip('/')}/api/chat"
    body = {
        "model": config.OLLAMA_MODEL,
        "messages": messages_payload,
        "stream": False,
        "options": {
            "temperature": config.LLM_TEMPERATURE,
            "top_p": config.LLM_TOP_P,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=config.LLM_TIMEOUT) as client:
            resp = await client.post(ollama_url, json=body)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("message", {}).get("content", "").strip()
            else:
                logger.error("Ollama error (%d): %s", resp.status_code, resp.text)
                return "Değerli misafirimiz, asistanımız şu an hizmet verememektedir. Şubemizi doğrudan arayarak anında randevu oluşturabilirsiniz. ✨"
    except Exception as exc:
        logger.error("Ollama connection exception: %s", exc)
        return "Değerli misafirimiz, randevu ve bilgi talepleriniz için lütfen WhatsApp veya telefon numaramız üzerinden bize ulaşınız. ✨"
