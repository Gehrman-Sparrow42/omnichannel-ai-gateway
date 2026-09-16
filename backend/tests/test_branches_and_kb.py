import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import database
import llm_router


def test_seventeen_navitas_branches_catalog_integrity():
    branches = config.list_all_branches()
    assert len(branches) == 17

    expected_ids = [
        "istanbul-airport", "mall-of-istanbul", "topkapi", "laleli", "sultanahmet",
        "5-levent", "mahmutbey", "halkali", "ankara-hilton", "ankara-sheraton",
        "cerkezkoy", "topuk-yaylasi", "marmaris-lumira", "adana-hilton",
        "malatya-movenpick", "bolu-abant", "izmir-bomonti"
    ]

    actual_ids = [b["id"] for b in branches]
    for exp_id in expected_ids:
        assert exp_id in actual_ids

    # Each branch must have essential operational attributes
    for b in branches:
        assert b["name"]
        assert b["city"]
        assert b["address"]
        assert b["phone"]
        assert b["ntfy_topic"]
        assert b["pricing"]


def test_dynamic_prompt_builder_injects_navitas_data():
    prompt_airport = llm_router.build_branch_system_prompt("istanbul-airport")
    assert "İstanbul Havalimanı" in prompt_airport
    assert "Hilton Istanbul Airport" in prompt_airport
    assert "07:00" in prompt_airport

    prompt_ankara = llm_router.build_branch_system_prompt("ankara-hilton")
    assert "Ankara Hilton" in prompt_ankara
    assert "Ankara" in prompt_ankara


def test_live_kb_update_and_cache_invalidation():
    b_id = "mall-of-istanbul"
    new_kb_content = "# Mall of İstanbul Güncellenmiş Özel Kural\n\n- Özel VIP süit rezervasyonlarında çift masajı 60 dk uygulanır."

    # Update in DB
    success = database.update_branch_knowledge(b_id, new_kb_content, updated_by="admin@navitasspa.com")
    assert success is True

    # Invalidate cache
    llm_router.invalidate_branch_prompt_cache(b_id)

    # Re-build prompt and verify new rule is immediately present
    updated_prompt = llm_router.build_branch_system_prompt(b_id)
    assert "Mall of İstanbul Güncellenmiş Özel Kural" in updated_prompt
    assert "VIP süit" in updated_prompt


def test_anti_hallucination_and_multilingual_directives():
    prompt = llm_router.build_branch_system_prompt("topkapi")
    # Anti-hallucination assertions
    assert "Anti-Hallucination" in prompt or "KESİNLİKLE BİLGİ UYDURMA" in prompt
    assert "doğrulanmış şube bilgi bankasında açıkça yazmayan" in prompt
    assert "şube yetkilimize danışıp" in prompt

    # Multi-lingual mirroring assertions
    assert "DİL AYNASI" in prompt or "Language Mirroring" in prompt
    assert "Misafir mesajı hangi dilde yazdıysa" in prompt
    assert "KESİNLİKLE VE İSTİSNASIZ O DİLDE CEVAP VER" in prompt

    # Verify get_llm_backend_info helper
    backend_info = llm_router.get_llm_backend_info()
    assert backend_info["provider"]
    assert backend_info["model"]
    assert backend_info["hallucination_guard"] == "active"
    assert backend_info["multilingual_mirror"] == "active"
