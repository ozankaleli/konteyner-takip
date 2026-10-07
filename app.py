import os
import re
import io
import subprocess
from datetime import datetime
import pandas as pd
import streamlit as st

# Sayfa Yapılandırması
st.set_page_config(page_title="Konteyner & B/L Takip Masası", page_icon="🚢", layout="wide")

# Playwright tarayıcı motorunu sunucuda ilk açılışta kur
@st.cache_resource
def setup_playwright():
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True)
    except Exception as e:
        pass

setup_playwright()
from playwright.sync_api import sync_playwright

CARRIER_RULES = {
    r'^AKK': 'AKKON',
    r'^ARK': 'ARKAS',
    r'^TRK': 'TURKON',
    r'^MED': 'MEDKON',
    r'^MSC': 'MSC',
    r'^MAE|^MSK': 'MAERSK',
    r'^CMA': 'CMA',
    r'^HLC': 'HAPAG',
    r'^COS': 'COSCO',
    r'^WHL': 'WANHAI',
    r'^YML': 'YANGMING'
}

def identify_carrier(code, hint=None):
    code = code.strip().upper()
    if hint:
        return hint.upper()
    for pattern, carrier in CARRIER_RULES.items():
        if re.search(pattern, code):
            return carrier
    return "KIRALIK / GENEL"

def track_cma(page, code):
    try:
        url = f"https://www.cma-cgm.com/ebusiness/tracking/search?SearchBy=Container&Reference={code}"
        page.goto(url, timeout=30000)
        page.wait_for_timeout(4000)
        content = page.content()
        
        status = "Discharged / Arrived POD" if ("Discharged" in content or "Arrived" in content) else "In Transit"
        
        loc_match = re.search(r'(ONNE|MERSIN|AMBARLI|PORT SAID|JEBEL ALI|TANGER|DURBAN|CASABLANCA)', content, re.IGNORECASE)
        location = loc_match.group(0).upper() if loc_match else "POD / Varış Terminali"
        
        date_match = re.search(r'\d{1,2}\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{4}', content, re.IGNORECASE)
        eta = date_match.group(0) if date_match else "Limanda / Tamamlandı"
        
        return location, status, eta
    except Exception:
        return "CMA Beklemede", "Sorgulanamadı", "-"

def track_akkon(page, code):
    try:
        page.goto("https://www.akkonlines.com/tr/tracking", timeout=25000)
        page.fill("input[name='search']", code)
        page.press("input[name='search']", "Enter")
        page.wait_for_timeout(3500)
        
        status = page.locator(".status-text").first.inner_text() if page.locator(".status-text").count() > 0 else "Aktif / Seferde"
        location = page.locator(".current-port").first.inner_text() if page.locator(".current-port").count() > 0 else "Liman Bilgisi"
        eta = page.locator(".eta-date").first.inner_text() if page.locator(".eta-date").count() > 0 else "Limanda"
        return location, status, eta
    except Exception:
        return "Akkon Beklemede", "Sorgulanamadı", "-"

# Arayüz
st.title("🚢 Canlı Konteyner & B/L Takip Masası")
st.write("20-30 yük numaranızı alt alta yapıştırın. Kiralık konteynerler için yanına hat adını ekleyebilirsiniz (`TIIU2680745:CMA` gibi).")

raw_input = st.text_area(
    "Konteyner / B/L Numaraları (Her satıra bir adet):",
    height=180,
    placeholder="TIIU2680745:CMA\nAKKU1234567\nMSCU9876543\nARKU5544332"
)

if st.button("🚀 Toplu Sorgulamayı Başlat", type="primary"):
    lines = [x.strip() for x in raw_input.split("\n") if len(x.strip()) >= 7]
    
    if not lines:
        st.warning("Lütfen en az bir geçerli numara girin.")
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
                hint = parts[1].strip().upper() if len(parts) > 1 else None
                
                carrier = identify_carrier(code, hint)
                status_text.text(f"Sorgulanıyor ({idx+1}/{total}): {code} - Hat: {carrier}")
                
                loc, stat, eta = "-", "-", "-"
                if carrier == "CMA":
                    loc, stat, eta = track_cma(page, code)
                elif carrier == "AKKON":
                    loc, stat, eta = track_akkon(page, code)
                else:
                    stat = "Kiralık Konteyner (Örn: NUMARA:CMA şeklinde belirtin)"
                
                results.append({
                    "Sıra": idx + 1,
                    "Konteyner / B/L No": code,
                    "Armatör / Hat": carrier,
                    "Mevcut Konum / Liman": loc,
                    "Güncel Statü": stat,
                    "Varış / ETA": eta,
                    "Sorgu Tarihi": datetime.now().strftime("%d.%m.%Y %H:%M")
                })
                
                progress_bar.progress((idx + 1) / total)
            
            browser.close()
            status_text.success("Tüm sorgular tamamlandı!")

        df = pd.DataFrame(results)
        st.dataframe(df, use_container_width=True)
        
        # Excel Dosyası Üretimi
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name="Konteyner_Takip")
        
        st.download_button(
            label="📥 Excel Olarak İndir (.xlsx)",
            data=buffer.getvalue(),
            file_name=f"Konteyner_Takip_Raporu_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
