import os
import re
import io
import subprocess
from datetime import datetime
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Konteyner & B/L Takip Masası", page_icon="🚢", layout="wide")

@st.cache_resource
def setup_playwright():
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True)
    except Exception:
        pass

setup_playwright()
from playwright.sync_api import sync_playwright

# Standart Öz Mal Haritası
KNOWN_PREFIXES = {
    'AKK': 'AKKON',
    'ARK': 'ARKAS',
    'TRK': 'TURKON',
    'MED': 'MEDKON',
    'MSC': 'MSC',
    'MAE': 'MAERSK',
    'MSK': 'MAERSK',
    'CMA': 'CMA',
    'HLC': 'HAPAG',
    'SLL': 'SEALEAD',
    'COS': 'COSCO',
    'ONE': 'ONE',
    'ZIM': 'ZIM'
}

# 1. CMA CGM
def check_cma(page, code):
    try:
        url = f"https://www.cma-cgm.com/ebusiness/tracking/search?SearchBy=Container&Reference={code}"
        page.goto(url, timeout=25000, wait_until="domcontentloaded")
        page.wait_for_timeout(3500)
        content = page.content()
        
        if "No container found" in content or "not found" in content.lower():
            return None
            
        status = "In Transit"
        if "Discharged" in content or "Tahliye" in content:
            status = "Tahliye Edildi (POD)"
        elif "Delivered" in content:
            status = "Teslim Edildi"
        elif "Loaded" in content:
            status = "Gemide (Loaded)"
            
        loc_match = re.search(r'\b(ONNE|MERSIN|AMBARLI|PORT SAID|JEBEL ALI|TANGER|DURBAN|CASABLANCA|ANTWERP)\b', content, re.IGNORECASE)
        loc = loc_match.group(0).upper() if loc_match else "POD Limanı"
        
        date_match = re.findall(r'\b\d{1,2}[\s\-\/]+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\s\-\/]+\d{4}\b', content, re.IGNORECASE)
        eta = date_match[-1] if date_match else "Limanda / Tamamlandı"
        
        return {"carrier": "CMA CGM", "loc": loc, "status": status, "eta": eta}
    except Exception:
        return None

# 2. MAERSK
def check_maersk(page, code):
    try:
        url = f"https://www.maersk.com/tracking/{code}"
        page.goto(url, timeout=25000, wait_until="domcontentloaded")
        page.wait_for_timeout(3500)
        content = page.content()
        
        if "no results" in content.lower() or "not found" in content.lower():
            return None
            
        status = "In Transit"
        if "Discharged" in content:
            status = "Tahliye Edildi (POD)"
        elif "Gate out" in content or "Delivered" in content:
            status = "Teslim / Boş İade"
            
        loc_match = re.search(r'\b(MERSIN|AMBARLI|IZMIR|JEBEL ALI|PORT SAID|ONNE|TANGER|BREMERHAVEN|ROTTERDAM)\b', content, re.IGNORECASE)
        loc = loc_match.group(0).upper() if loc_match else "Varış Terminali"
        
        date_match = re.findall(r'\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{4}\b', content, re.IGNORECASE)
        eta = date_match[-1] if date_match else "Tamamlandı"
        
        return {"carrier": "MAERSK", "loc": loc, "status": status, "eta": eta}
    except Exception:
        return None

# 3. HAPAG-LLOYD
def check_hapag(page, code):
    try:
        url = f"https://www.hapag-lloyd.com/en/online-business/track/track-by-container-solution.html?container={code}"
        page.goto(url, timeout=25000, wait_until="domcontentloaded")
        page.wait_for_timeout(3500)
        content = page.content()
        
        if "could not be found" in content.lower() or "no data" in content.lower():
            return None
            
        status = "In Transit"
        if "delivered" in content.lower(): status = "Teslim Edildi"
        elif "discharged" in content.lower(): status = "Tahliye Edildi (POD)"
        
        loc_match = re.search(r'\b(MERSIN|IZMIR|AMBARLI|GENOA|ANTWERP|HAMBURG|JEBEL ALI)\b', content, re.IGNORECASE)
        loc = loc_match.group(0).upper() if loc_match else "Hapag Terminali"
        
        return {"carrier": "HAPAG-LLOYD", "loc": loc, "status": status, "eta": "Takip Ediliyor"}
    except Exception:
        return None

# 4. SEALEAD
def check_sealead(page, code):
    try:
        url = f"https://sealead.com/tracking/?tracking_type=cntr&tracking_number={code}"
        page.goto(url, timeout=25000, wait_until="domcontentloaded")
        page.wait_for_timeout(3500)
        content = page.content()
        
        if "invalid" in content.lower() or "no records" in content.lower():
            return None
            
        return {"carrier": "SEALEAD", "loc": "Akdeniz / Kızıldeniz Hattı", "status": "In Transit", "eta": "Aktif Sefer"}
    except Exception:
        return None

# 5. AKKON LINES
def check_akkon(page, code):
    try:
        page.goto("https://www.akkonlines.com/tr/tracking", timeout=20000)
        page.fill("input[name='search']", code)
        page.press("input[name='search']", "Enter")
        page.wait_for_timeout(3000)
        content = page.content()
        
        if "bulunamadı" in content.lower() or "kayıt yok" in content.lower():
            return None
            
        status = page.locator(".status-text").first.inner_text() if page.locator(".status-text").count() > 0 else "Aktif"
        loc = page.locator(".current-port").first.inner_text() if page.locator(".current-port").count() > 0 else "Mersin / Akkon Port"
        eta = page.locator(".eta-date").first.inner_text() if page.locator(".eta-date").count() > 0 else "Limanda"
        return {"carrier": "AKKON LINES", "loc": loc, "status": status, "eta": eta}
    except Exception:
        return None

# Akıllı ShipsGo Tarama Fonksiyonu
def smart_track(page, code, manual_hint=None):
    clean_code = code.strip().upper()
    prefix = clean_code[:3]
    
    # 1. Kullanıcı elle ipucu verdiyse doğrudan o hatta git
    if manual_hint:
        h = manual_hint.upper()
        if "CMA" in h: return check_cma(page, clean_code) or {"carrier": "CMA", "loc": "Kayıt Bulunamadı", "status": "Pasif", "eta": "-"}
        if "MAE" in h: return check_maersk(page, clean_code) or {"carrier": "MAERSK", "loc": "Kayıt Bulunamadı", "status": "Pasif", "eta": "-"}
        if "HAP" in h: return check_hapag(page, clean_code) or {"carrier": "HAPAG", "loc": "Kayıt Bulunamadı", "status": "Pasif", "eta": "-"}
        if "SEA" in h: return check_sealead(page, clean_code) or {"carrier": "SEALEAD", "loc": "Kayıt Bulunamadı", "status": "Pasif", "eta": "-"}
        if "AKK" in h: return check_akkon(page, clean_code) or {"carrier": "AKKON", "loc": "Kayıt Bulunamadı", "status": "Pasif", "eta": "-"}

    # 2. Öz Mal İse Doğrudan İlgili Hatta Git
    if prefix in KNOWN_PREFIXES:
        carrier = KNOWN_PREFIXES[prefix]
        if carrier == "AKKON": res = check_akkon(page, clean_code)
        elif carrier == "MAERSK": res = check_maersk(page, clean_code)
        elif carrier == "CMA": res = check_cma(page, clean_code)
        elif carrier == "HAPAG": res = check_hapag(page, clean_code)
        elif carrier == "SEALEAD": res = check_sealead(page, clean_code)
        else: res = None
        if res: return res

    # 3. KİRALIK KONTEYNER (CAIU, TIIU, TGHU vb.) -> SHIPSGO GİBİ SIRAYLA HATLARI DENE
    search_engines = [check_maersk, check_cma, check_hapag, check_sealead, check_akkon]
    for engine in search_engines:
        res = engine(page, clean_code)
        if res: # Hangi hat "Bu bende var" derse onu döndür
            return res
            
    return {"carrier": "Bilinmeyen Hat / Bulunamadı", "loc": "-", "status": "Aktif Sefer Kaydı Yok", "eta": "-"}

# Arayüz
st.title("🚢 Akıllı Konteyner & B/L Takip Masası (ShipsGo Modu)")
st.write("Numaraları doğrudan yapıştırın. `CAIU`, `TIIU` gibi kiralık kodlar hat yazmasanız bile otomatik taranır.")

raw_input = st.text_area(
    "Konteyner Numaraları (Alt alta yapıştırın):",
    height=160,
    placeholder="TIIU2680745\nCAIU9639822\nAKKU1234567"
)

if st.button("🚀 Otomatik Tara ve Sorgula", type="primary"):
    lines = [x.strip() for x in raw_input.split("\n") if len(x.strip()) >= 7]
    
    if not lines:
        st.warning("Lütfen en az bir numara girin.")
    else:
        results = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
            page = context.new_page()
            
            total = len(lines)
            for idx, item in enumerate(lines):
                parts = item.split(":")
                code = parts[0].strip().upper()
                hint = parts[1].strip() if len(parts) > 1 else None
                
                status_text.text(f"Akıllı Tarama Yapılıyor ({idx+1}/{total}): {code}...")
                
                res = smart_track(page, code, hint)
                
                results.append({
                    "Sıra": idx + 1,
                    "Konteyner No": code,
                    "Tespit Edilen Armatör": res["carrier"],
                    "Mevcut Konum / Liman": res["loc"],
                    "Güncel Statü": res["status"],
                    "Varış / ETA": res["eta"],
                    "Sorgu Zamanı": datetime.now().strftime("%d.%m.%Y %H:%M")
                })
                
                progress_bar.progress((idx + 1) / total)
            
            browser.close()
            status_text.success("Tüm konteynerler tarandı!")

        df = pd.DataFrame(results)
        st.dataframe(df, use_container_width=True)
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name="Konteyner_Raporu")
        
        st.download_button(
            label="📥 Excel Olarak İndir (.xlsx)",
            data=buffer.getvalue(),
            file_name=f"Konteyner_Takip_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
