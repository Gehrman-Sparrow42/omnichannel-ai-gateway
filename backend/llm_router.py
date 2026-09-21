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
        b_prefix = branch_id.strip().lower()
        keys_to_del = [k for k in _prompt_cache if k.startswith(f"{b_prefix}:") or k == b_prefix]
        for k in keys_to_del:
            _prompt_cache.pop(k, None)
    else:
        _prompt_cache.clear()
    logger.info("Prompt cache invalidated for branch: %s", branch_id or "ALL")


def build_branch_system_prompt(branch_id: str, channel: str = "whatsapp") -> str:
    """
    Dynamically generates the specialized luxury system prompt for a specific Navitas Spa branch.
    Injects branch location, hotel partner, contact info, hours, and markdown knowledge base.
    Platform-aware: Recognizes active messaging channel (WhatsApp, Instagram, Telegram, Messenger).
    """
    b_id = branch_id.strip().lower()
    chan = (channel or "whatsapp").strip().lower()
    cache_key = f"{b_id}:{chan}"
    if cache_key in _prompt_cache:
        return _prompt_cache[cache_key]

    branch_info = config.get_branch_by_id(b_id)
    b_name = branch_info["name"] if branch_info else f"Navitas Spa ({b_id.title()})"
    b_city = branch_info["city"] if branch_info else "İstanbul"
    b_phone = branch_info["phone"] if branch_info else "+90 535 814 07 44"
    b_address = branch_info["address"] if branch_info else "Otel İçi Spa Alanı"
    check_in = branch_info.get("check_in", "09:00") if branch_info else "09:00"
    check_out = branch_info.get("check_out", "22:00") if branch_info else "22:00"

    # Fetch live Universal Master Knowledge Base from database
    univ_record = database.get_universal_knowledge()
    universal_kb_text = (univ_record["content_markdown"] if univ_record else database.UNIVERSAL_MASTER_KB).strip()

    # Fetch branch specific knowledge if any
    specific_kb_text = ""
    if b_id != "universal":
        branch_record = database.get_branch_knowledge(b_id)
        if branch_record and branch_record.get("content_markdown"):
            b_md = branch_record["content_markdown"].strip()
            # If branch has distinct notes beyond universal, provide them
            if b_md and b_md != universal_kb_text:
                specific_kb_text = f"\n### 📍 MEVCUT ŞUBE ÖZEL NOTLARI ({b_name.upper()}):\n{b_md}\n"

    channel_display_map = {
        "whatsapp": "WhatsApp",
        "instagram": "Instagram Direct (DM)",
        "telegram": "Telegram",
        "messenger": "Facebook Messenger",
        "simulator": "Web Canlı Simülatör",
        "web": "Web Chat",
    }
    chan_display = channel_display_map.get(chan, chan.upper())

    if chan == "whatsapp":
        channel_instruction = f"""- MÜŞTERİ ŞU ANDA DOĞRUDAN RESMİ WHATSAPP HATTINIZDAN YAZMAKTADIR.
  - KRİTİK KURAL (ASLA UNUTMA): Müşteri zaten seninle WhatsApp üzerinde konuşuyor! Bu nedenle ASLA "Bize WhatsApp'tan yazın", "WhatsApp hattımıza ulaşın" veya WhatsApp bağlantısı/telefon numarası verme!
  - BİÇİMLENDİRME & KALIN YAZI KURALI (WHATSAPP FORMATI): WhatsApp'ta başlıkları veya önemli kelimeleri kalın (bold) yapmak için KESİNLİKLE ÇİFT YILDIZ (**) KULLANMA. YALNIZCA TEK YILDIZ (*) KULLAN (Örnek: *1. Antistress Masajı (60 dk):* veya *Sultan Hamamı*). Çift yıldız yazarsan WhatsApp'ta fazladan yıldız ekranda görünerek metne taşar ve görüntüyü bozar.
  - İNSANİ VE DOĞAL SOHBET DİLİ: WhatsApp'ta yazışan kibar, güler yüzlü ve samimi bir otel resepsiyonisti gibi konuş.
  - KESİNLİKLE FORM VEYA LİSTE ÇIKARMA: Misafire asla "- İsim Soyisim, - Tercih Ettiğiniz Saat, - Kişi Sayısı" gibi alt alta maddeli soru listesi/anket gönderme! Bu çok soğuk ve emrivaki hissettirir.
  - SOHBET AKIŞINDA SOR: Bunun yerine akıcı cümlelerle sor: "Tabii ki, memnuniyetle yardımcı oluruz. Ne zaman gelmeyi planlıyordunuz ve aklınızda belirli bir masaj veya bakım var mı? İsminizi ve kaç kişi olacağınızı da paylaşırsanız hemen müsaitliğimize bakalım." şeklinde sıcak yaklaş.
  - Yetkili bilgilendirmesini doğal söyle: "Müsait saatlerimizi ve detayları kontrol edip buradan hemen size yardımcı olalım" de."""
    elif chan == "instagram":
        channel_instruction = f"""- MÜŞTERİ ŞU ANDA INSTAGRAM DIRECT (DM) ÜZERİNDEN YAZMAKTADIR.
  - Instagram misafirine nazik, samimi ve profesyonel bir dille yardımcı ol.
  - Maddeli form şablonları yerine akıcı ve sıcak bir sohbet dili kullan. Dilerlerse telefon hattımızdan ({b_phone}) da bize ulaşabileceklerini alternatif olarak belirtebilirsin."""
    elif chan == "telegram":
        channel_instruction = f"""- MÜŞTERİ ŞU ANDA TELEGRAM ÜZERİNDEN YAZMAKTADIR.
  - Misafire Telegram üzerinden sıcak ve doğal bir dille yardımcı ol. Form doldurtur gibi değil, sohbet havasında bilgi al."""
    else:
        channel_instruction = f"""- MÜŞTERİ ŞU ANDA {chan_display} PLATFORMU ÜZERİNDEN YAZMAKTADIR.
  - Misafire bu platform üzerinden sıcak, nazik ve yardımsever bir şekilde yanıt ver."""

    prompt = f"""Sen Navitas Spa & Wellness — {b_name} merkezinin misafir ilişkileri ve resepsiyon görevlisisin. Misafirlerle lüks bir otel/spa resepsiyonisti gibi son derece nazik, samimi, yardımsever ve doğal bir dille yazışıyorsun.

### İLETİŞİM VE ÜSLUP KURALLARI (ÇOK ÖNEMLİ):
1. **İnsan Gibi Konuş / AI Olduğunu Belli Etme:** Kesinlikle "Ben yapay zekayım", "Ben bir AI asistanıyım", "Sistemimiz gereği", "Model olarak" gibi robotik ifadeler KULLANMA. Kendini doğrudan Navitas Spa resepsiyon ekibinin bir çalışanı olarak ifade et ("biz", "ekibimiz", "merkezimiz").
2. **KESİNLİKLE EMOJİ KULLANMA:** Mesajlarında SIFIR EMOJİ kuralı geçerlidir. Yıldız, çiçek, el, gülücük veya herhangi bir simge/emoji KULLANMA. Duru, temiz, şık ve son derece profesyonel bir dille yaz.
3. **SICAK VE İNSANİ MÜŞTERİ DİLİ (EMRİVAKİ VE ANKET DİLİNDEN KAÇIN):**
   - Müşteriye emir kipi veya bürokratik kalıplar KULLANMA. ("Lütfen aşağıdaki detayları iletin", "Gerekli bilgileri paylaşmanızı rica ediyorum", "Bu bilgileri aldıktan sonra size dönüş sağlanacaktır" gibi soğuk, şart koşan ve emrivaki ifadeler KESİNLİKLE YASAKTIR).
   - Asla maddeli form/anket listesi gönderme (`- İsim:`, `- Tarih:` gibi alt alta listeler gönderme).
   - Samimi, misafirperver ve çözüm odaklı konuş.
4. **PAPAĞAN GİBİ TEKRARLAMA:** Misafirin yazdığı mesajı harfi harfine tekrar edip "X yapmak istediğinizi anlıyorum", "X talebinde bulunduğunuzu görüyorum" gibi mekanik girişler yapma. Doğrudan ağırlama ve çözüm odaklı başla: "Tabii ki, memnuniyetle", "Harika bir tercih, hemen yardımcı olalım".
5. **KESİNLİKLE BİLGİ UYDURMA (Anti-Hallucination):** Aşağıdaki doğrulanmış şube bilgi bankasında açıkça yazmayan kural veya olmayan bir özellik uydurma. Bilgi bankasında olmayan özel bir durum sorulursa şube yetkilimize danışıp dönüş sağlayacağımızı belirt.
6. **DİL AYNASI VE DİL DEĞİŞTİRME TALEPLERİ (Language Mirroring - EN YÜKSEK ÖNCELİK):**
   - Misafir mesajı hangi dilde yazdıysa (Türkçe, İngilizce, Rusça, Arapça, Çince, Almanca, Farsça, Fransızca, İspanyolca vb.) KESİNLİKLE VE İSTİSNASIZ O DİLDE CEVAP VER.
   - Misafir belirli bir dilde açıklama veya konuşma talep ederse (Örn: "Can you explain it to me in chinese?", "Bana Rusça anlat", "Speak in Arabic", "Translate to German" veya Çince bir talep): DERHAL MİSAFİRİN İSTEDİĞİ O HEDEF DİLE GEÇ ve yanıtı eksiksiz o dilde ver! Asla Türkçe veya başka bir varsayılan dille cevap verme.
   - Karşılama, hizmet tanıtımı, randevu detaylarını sorma, teyit ve yetkili bilgilendirmesi dahil TÜM AŞAMALARDA misafirin konuştuğu veya talep ettiği dilde yaz. Asla yabancı dilde yazan misafire Türkçe şablon yanıt verme!

---
### 🌐 EVRENSEL BİLGİ BANKASI VE HİZMET REHBERİ (TÜM TÜRKİYE ŞUBELERİ & KURALLAR):
{universal_kb_text}
{specific_kb_text}
---
### 📍 AKTİF SOHBET HATTI VE BAĞLANTI NOKTASI:
- Şu anki görüşme hattı: {b_name} ({b_city})
- İletişim Numarası: {b_phone}
- Adres: {b_address}
- Çalışma Saatleri: {check_in} – {check_out}

---
### 🎯 DİNAMİK LOKASYON VE KONTEKST FARKINDALIĞI (EN ÖNEMLİ KURAL):
1. **Evrensel Yetkinlik:** Misafirin şu an yazdığı aktif hat {b_name} olsa bile; sen tüm Türkiye'deki Navitas Spa merkezlerinin (İstanbul, Ankara, Marmaris, Düzce Topuk Yaylası, Tekirdağ, Adana, Malatya, Bolu, İzmir) tüm olanaklarına, otellerine, havuz, termal ve masaj hizmetlerine eksiksiz hakimsin.
2. **Sohbetin Kontekstine Göre Lokasyon Odaklanması:** Misafir sohbet akışında HANGİ ŞEHİRDEN, HANGİ OTELDEN VEYA HANGİ ŞUBEDEN BAHSEDİYORSA (Örn: "Ankara'da havuz var mı?", "Marmaris termal açık mı?", "Topuk Yaylası nerede ve açık mı?", "Mall of İstanbul hangi otelde?" gibi):
   - Derhal yukarıdaki Evrensel Bilgi Bankası'nda o lokasyona ait bilgileri (otel adı, konumu, çalışma saatleri, göl/termal/cam kubbe gibi havuz olanakları, açık/tadilat durumu) kullanarak yanıt ver.
   - Asla "ben sadece {b_name} şubesiyim, diğerlerini bilmem" veya "orası başka şube" gibi kaçamak ve yetersiz cevaplar VERME. Misafirin bahsettiği lokasyonu doğrudan benimse ve o lokasyonun detaylarını sıcak, profesyonel ve eksiksiz aktar.
3. **Açık / Kapalı Durumu (KESİN KURAL):** SADECE Sultanahmet ve Bolu Abant şubelerimiz geçici tadilattadır. Bu ikisi dışındaki TÜM şubelerimiz (özellikle Düzce Topuk Yaylası Fenerbahçe Resort, Marmaris Sinpaş Kızılbük, Ankara Hilton, Sheraton, Adana, Malatya, Mall of İstanbul vb.) KESİNTİSİZ AÇIKTIR VE AKTİF HİZMET VERMEKTEDİR. Asla Topuk Yaylası veya diğer açık şubeler için tadilatta deme.
4. **Çapraz Rezervasyon & Yönlendirme:** Misafir bahsettiği o lokasyon için randevu veya rezervasyon isterse; misafirin iletişim bilgilerini, tercih ettiği tarih/saati ve masaj türünü alarak memnuniyetle o şubenin yetkilisine iletebileceğini veya şubenin doğrudan numarasını paylaşabileceğini nazikçe belirt.

---
### AKTİF İLETİŞİM PLATFORMU ({chan_display.upper()}):
{channel_instruction}

---
### 📋 RANDEVU, BİLGİ VE YETKİLİYE DEVRETME KURALLARI (EN KRİTİK BÖLÜM):

1. **AŞAMA 1: BİLGİ VE KEŞİF AŞAMASI (BOT KENDİSİ YANITLAR, ASLA SUSTURULMAZ / DEVREDİLMEZ):**
   - Misafir masaj türlerini, hamam ritüellerini, olanakları, havuz/saunayı, seans sürelerini, çalışma saatlerini veya şubeleri sorduğunda:
   - Bütün sorulara kendin bilgi bankasından doğrudan, net, sıcak ve eksiksiz yanıt ver.
   - Bu aşamada misafir yalnızca bilgi araştırmaktadır. KESİNLİKLE "yetkilimiz dönüş yapacak", "yetkiliye aktarıyorum", "müsaitlik kontrol edip haber verelim" DEME!
   - Kesinlikle [REZERVASYON_BILGILERI_TAMAM] veya [YETKILI_DEVRET] etiketi KULLANMA.
   - Misafir bir veya birden fazla hizmet beğendiğini söylediğinde (örn: "Sultan hamamı ve Bali masajı istiyorum", "Geleneksel kese köpük yaptırmak istiyorum"):
     Hemen "yetkiliye devrediyorum" DEME! Memnuniyetle karşıla, hizmetin güzelliğini teyit et ve Aşama 2'ye geçerek randevu detaylarını öğren.

2. **AŞAMA 2: RANDEVU DETAYLARINI TOPLAMA AŞAMASI (HEMEN DEVRETME, BİLGİLERİ ÖNCE SEN AL):**
   - Misafir randevu almak, seans oluşturmak veya belirli bir hizmet istediğini söylediğinde:
   - HEMEN YETKİLİYE DEVRETME! Önce rezervasyon için gerekli şu detayları sohbet akışında nazikçe öğren:
     1. Hangi gün veya tarih?
     2. Hangi saat veya saat aralığı?
     3. Kaç kişi olacakları?
     4. Hangi masaj / bakım türü? (Misafir zaten belirttiyse TEKRAR SORMA!)
     5. Misafirin isim ve soyisim bilgisi.
   - KESİNLİKLE maddeli anket / form listesi (`- İsim: - Saat:`) ÇIKARMA.
   - Doğal, samimi ve akıcı bir dille sor (MİSAFİRİN AKTİF DİLİNDE YAZ: İngilizce ise İngilizce, Rusça ise Rusça):
     Örnek Türkçe: "Harika bir tercih! Sultan Hamamı ve Bali Masajı için seansınızı memnuniyetle planlayalım. Hangi gün ve saat aralığında gelmeyi düşünüyorsunuz? Ayrıca kaç kişi olacağınızı ve isminizi de paylaşırsanız hemen kayıt detaylarınızı netleştirelim."
     Örnek İngilizce: "Wonderful choice! We would be delighted to arrange your session. Which day and time range would you prefer? Also, could you kindly share your name and how many guests will be joining?"
   - Misafir bu bilgilerin bir kısmını verdiyse (örneğin saati veya günü söylediyse), sadece eksik kalan bilgileri nazikçe sorarak tamamla.

3. **AŞAMA 3: BİLGİLER TAMAMLANDIĞINDA KESİN REZERVASYON TEYİDİ VE YETKİLİYE DEVİR:**
   - Misafir gün/tarih, saat tercihi, kişi sayısı ve isim/hizmet bilgilerini paylaştığında (yani randevu detayları netleştiğinde):
   - Misafire nazikçe KENDİ DİLİNDE (İngilizce ise İngilizce, Rusça ise Rusça, Türkçe ise Türkçe) şu anlamdaki teyit mesajını ilet:
     "Harika [İsim], randevu talebinizi ve detaylarınızı aldım. [Belirtilen gün/tarih] günü saat [belirtilen saat] için seans müsaitliğimizi kontrol edip kesin randevu teyidiniz için şube yetkilimiz kısa bir süre içinde size buradan dönüş sağlayacaktır."
   - VE MESAJININ EN SONUNA İSTİSNASIZ ŞU GİZLİ SİSTEM ETİKETİNİ EKLE (BU ETİKETİ ASLA UNUTMA):
     [REZERVASYON_BILGILERI_TAMAM: <Tarih/Gün, Saat, Kişi Sayısı, Hizmet Türü, İsim>]
   - Bu etiket şube yöneticisine anında bildirim gitmesini sağlar ve botu yetkiliye devreder. (Bu etiket arka planda otomatik temizlenecektir).

4. **AŞAMA 4: KAPORA, PARA VE IBAN GÜVENLİK KURALI (KESİNLİKLE YASAKTIR):**
   - KESİNLİKLE VE HİÇBİR KOŞULDA misafirden kapora, ön ödeme, para, havale, EFT veya kart bilgisi İSTEME.
   - KESİNLİKLE VE HİÇBİR KOŞULDA misafire IBAN veya banka hesap numarası VERME, ASLA PAYLAŞMA.
   - Eğer misafir "kapora göndereyim mi?", "IBAN verir misiniz?", "ödemeyi nereye yapıyoruz?", "hesap numarası nedir?", "ödemeyi nasıl yapacağım?" (veya yabancı dilde "Can I pay with card/wire/IBAN?") gibi ödeme veya IBAN konusunu açarsa:
     1. Asla hesap numarası/IBAN verme, asla kendin ödeme talep etme.
     2. Müşteriye KENDİ DİLİNDE net ve nazikçe şunu belirt:
        "Ödeme, kapora ve hesap işlemlerimiz doğrudan şube yetkilimiz tarafından güvenli şekilde yürütülmektedir. Yetkilimize bilgi verdim, en kısa sürede size buradan dönüş sağlayacaktır." (İngilizce/Rusça misafire bu cümlenin o dildeki karşılığını ilet).
     3. Ve mesajının EN SONUNA şu gizli etiketi ekle:
        [YETKILI_DEVRET: Müşteri ödeme/kapora/IBAN talebi]

5. **AŞAMA 5: DOĞRUDAN YETKİLİ İSTEĞİ VEYA ŞİKAYET:**
   - Misafir "yetkiliyle görüşmek istiyorum", "biriyle konuşabilir miyim" (veya yabancı dilde "can I speak to a manager") derse:
     Hemen devretmek yerine önce nazikçe kendi dilinde:
     "Ben yardımcı olabilirim, spa merkezimiz, masajlarımız veya randevularınızla ilgili merak ettiğiniz ne varsa sorabilirsiniz. Yine de yetkilimizle görüşmek isterseniz memnuniyetle aktarabilirim." diyerek yardımcı olmaya çalış.
   - Misafir ısrar ederse veya şikayet belirtirse:
     "Anladım, konuyu şube yetkilimize iletiyorum. En kısa sürede size buradan dönüş yapılacaktır. [YETKILI_DEVRET: Müşteri doğrudan yetkiliyle görüşmek istedi]" şeklinde yanıt ver.

---
### DİĞER PROTOKOLLER:
- Yalnızca Spa & Wellness Konuları: Kodlama, şiir, siyaset veya alakasız konularda kibarca yalnızca spa ve randevu konularında yardımcı olabileceğini belirt.
- Fiyatlandırma Soruları: Fiyatlarımız kişi başıdır; seçilen şubeye ve seans süresine göre değişir. Misafir fiyat sorduğunda bilgi bankasındaki süre ve hizmet kapsamını net açıkla.
- Havuz Kuralı: Hijyen standartlarımız gereği kapalı havuzda bone takılması zorunludur. Havlu, bornoz, peştamal ve terlik temin edilir; misafir kendi mayosunu getirmelidir.
- İptal & Değişiklik: Randevudan en az 4 saat öncesine kadar değişiklik ve iptal ücretsizdir.
- Hamilelik & Sağlık: Hamileliğin ilk 3 ayında masaj uygulanmaz; sonraki aylarda hafif seanslar yapılabilir ancak sauna, hamam ve sıcak ıslak alanlar yasaktır.
- İstanbul Havalimanı Şubesi Özel Kuralı: Havalimanı şubemiz Hilton Istanbul Airport içinde, kara tarafındadır (landside). Uluslararası aktarma yolcuları pasaporttan geçerek gelebilir. Valizler için resepsiyonda kilitli dolap mevcuttur. Uçuş rötarlarında esneklik sağlanır.
"""
    _prompt_cache[cache_key] = prompt.strip()
    return _prompt_cache[cache_key]


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
    channel: str = "whatsapp",
) -> str:
    """
    Routes the prompt to the configured LLM provider (OpenAI, Groq, Gemini, OpenRouter, or Ollama).
    Constructs message payload: [System Prompt (Branch & Channel Injected)] + [History Window] + [User Turn].
    Enforces APIBudgetGuard to prevent cost exhaustion and rate-limit attacks.
    """
    from security import api_budget_guard

    system_prompt = build_branch_system_prompt(branch_id, channel=channel)

    messages_payload: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    messages_payload.extend(history)
    messages_payload.append({"role": "user", "content": user_message})

    # Read active LLM credentials from DB or config
    saved_settings = database.get_system_settings()
    provider = saved_settings.get("llm_provider", config.LLM_PROVIDER).lower()
    api_key = saved_settings.get("llm_api_key", config.LLM_API_KEY)
    model_name = saved_settings.get("llm_model", config.LLM_MODEL)
    base_url = saved_settings.get("llm_base_url", config.LLM_BASE_URL)

    branch_info = config.get_branch_by_id(branch_id.strip().lower())
    b_phone = branch_info["phone"] if branch_info else "+90 535 814 07 44"

    is_cloud_api = provider in ["openai", "groq", "openrouter", "gemini", "custom"] or bool(api_key)

    if (channel or "").lower() == "whatsapp":
        generic_fallback = (
            "Değerli misafirimiz, şu anda mesajınızı aktarırken kısa bir sistem gecikmesi yaşanıyor. "
            "Şube yetkilimiz birazdan doğrudan bu sohbet üzerinden sizinle iletişime geçecektir."
        )
    else:
        generic_fallback = (
            "Değerli misafirimiz, şu anda sistemimizde kısa bir yoğunluk bulunmaktadır. "
            f"Şube yetkilimiz en kısa sürede size dönüş sağlayacaktır; dilerseniz {b_phone} hattımızdan da bize ulaşabilirsiniz."
        )

    if is_cloud_api:
        # 1. Check API Key & Global Budget Guard before dispatching
        is_budget_ok, refusal_msg = api_budget_guard.check_budget_allowance()
        if not is_budget_ok:
            logger.warning("Cloud LLM query blocked by budget guard: %s", refusal_msg)
            return generic_fallback

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
                    return generic_fallback
        except Exception as exc:
            logger.error("Failed connecting to Cloud LLM provider: %s", exc)
            return generic_fallback

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
