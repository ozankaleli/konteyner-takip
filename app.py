import os
import re
import io
import subprocess
from datetime import datetime
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Lojistik Konteyner & B/L Takip Masası", page_icon="🚢", layout="wide")

@st.cache_resource
def setup_playwright():
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True)
    except Exception:
        pass

setup_playwright()
from playwright.sync_api import sync_playwright

def parse_date(date_str):
    """Farklı armatör tarih formatlarını datetime nesnesine çevirir"""
    if not date_str or date_str == "-":
        return None
    cleaned = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', str(date_str)).strip()
    formats = [
        "%d %b %Y", "%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d",
        "%d %B %Y", "%b %d, %Y", "%d %b %Y %H:%M"
    ]
    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt)
        except Exception:
            continue
    return None

def calculate_transit_days(dep_date_str, arr_date_str):
    """POL ve POD tarihleri arasındaki transit süresini hesaplar"""
    d1 = parse_date(dep_date_str)
    d2 = parse_date(arr_date_str)
    if d1 and d2:
        diff = (d2 - d1).days
        return f"{diff} Gün" if diff >= 0 else "-"
    return "-"

# 1. CMA CGM MOTORU
def check_cma(page, code):
    try:
        url = f"https://www.cma-cgm.com/ebusiness/tracking/search?SearchBy=Container&Reference={code}"
        page.goto(url, timeout=30000, wait_until="networkidle")
        page.wait_for_timeout(4000)
        
        content = page.content()
        
        # Hatalı/Boş arama kontrolü
        if "No matching container found" in content or "not found" in content.lower() or "No data available" in content:
            return None
        
        # Sayfada container numarası geçiyor mu kontrolü
        if code not in content.upper():
            return None

        # POL (Yükleme Limanı ve Tarihi)
        pol_match = re.search(r'(MERSIN|AMBARLI|ALIAGA|IZMIR|GEMLIK|PORT SAID)', content, re.IGNORECASE)
        pol = pol_match.group(0).upper() if pol_match else "Yükleme Limanı"
        
        # POD (Tahliye Limanı)
        pod_match = re.search(r'(ONNE|TANGER|DURBAN|CASABLANCA|ANTWERP|JEBEL ALI|DAKAR)', content, re.IGNORECASE)
        pod = pod_match.group(0).upper() if pod_match else "Varış Limanı"

        # Tarihler
        dates = re.findall(r'\b\d{1,2}[\s\-\/]+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\s\-\/]+\d{4}\b', content, re.IGNORECASE)
        
        departure_date = dates[0] if len(dates) >= 1 else "-"
        arrival_date = dates[-1] if len(dates) >= 2 else (dates[0] if dates else "-")

        # Statü Kararı
        if "discharged" in content.lower() or "tahliye" in content.lower():
            status = "Tahliye Edildi (Vardı)"
        elif "delivered" in content.lower() or "empty return" in content.lower():
            status = "Teslim Edildi / Boş İade"
        elif "loaded" in content.lower():
            status = "Gemide / Seferde"
        else:
            status = "Seferde (In Transit)"

        transit_days = calculate_transit_days(departure_date, arrival_date)

        return {
            "carrier": "CMA CGM",
            "pol": pol,
            "pol_date": departure_date,
            "pod": pod,
            "pod_date": arrival_date,
            "transit_days": transit_days,
            "status": status
        }
    except Exception:
        return None

# 2. MAERSK MOTORU
def check_maersk(page, code):
    try:
        url = f"https://www.maersk.com/tracking/{code}"
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(4500)
        
        content = page.content()
        
        # Kesin negatif doğrulaması (Maersk kayıt yoksa hemen geçilsin)
        if "could not find any results" in content.lower() or "no records found" in content.lower() or "search returned no results" in content.lower():
            return None
        
        # Maersk arayüzünde aktif veri kartı yoksa None dön
        if "tracking-result" not in content.lower() and "stepper" not in content.lower() and code not in content.upper():
            return None

        # Limanlar
        pol_match = re.search(r'(MERSIN|AMBARLI|IZMIR|PORT SAID|JEBEL ALI)', content, re.IGNORECASE)
        pol = pol_match.group(0).upper() if pol_match else "Yükleme Limanı"

        pod_match = re.search(r'(AMBARLI|MERSIN|BREMERHAVEN|ROTTERDAM|ONNE|TANGER|JEBEL ALI)', content, re.IGNORECASE)
        pod = pod_match.group(0).upper() if pod_match else "Varış Limanı"

        # Tarihler
        dates = re.findall(r'\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{4}\b', content, re.IGNORECASE)
        departure_date = dates[0] if len(dates) >= 1 else "-"
        arrival_date = dates[-1] if len(dates) >= 2 else (dates[0] if dates else "-")

        # Statü (Vardı mı yoksa daha varmadı mı?)
        if "discharged" in content.lower():
            status = "Tahliye Edildi (Vardı)"
        elif "delivered" in content.lower() or "gate out" in content.lower():
            status = "Teslim Edildi"
        elif "load" in content.lower():
            status = "Gemide / Yolda (Varmadı)"
        else:
            status = "Yolda / Seferde (Varmadı)"

        transit_days = calculate_transit_days(departure_date, arrival_date)

        return {
            "carrier": "MAERSK",
            "pol": pol,
            "pol_date": departure_date,
            "pod": pod,
            "pod_date": arrival_date,
            "transit_days": transit_days,
            "status": status
        }
    except Exception:
        return None

# 3. AKKON LINES MOTORU
def check_akkon(page, code):
    try:
        page.goto("https://www.akkonlines.com/tr/tracking", timeout=20000)
        page.fill("input[name='search']", code)
        page.press("input[name='search']", "Enter")
        page.wait_for_timeout(3500)
        
        content = page.content()
        if "kayıt bulunamadı" in content.lower() or "no records" in content.lower():
            return None
            
        pol = "Mersin / Çıkış"
        pod = page.locator(".current-port").first.inner_text() if page.locator(".current-port").count() > 0 else "Varış Limanı"
        arr_date = page.locator(".eta-date").first.inner_text() if page.locator(".eta-date").count() > 0 else "-"
        status = page.locator(".status-text").first.inner_text() if page.locator(".status-text").count() > 0 else "Seferde"

        return {
            "carrier": "AKKON LINES",
            "pol": pol,
            "pol_date": "-",
            "pod": pod,
            "pod_date": arr_date,
            "transit_days": "-",
            "status": status
        }
    except Exception:
        return None

# Akıllı Hat Dağıtıcı (Doğrulamalı)
def smart_track(page, code):
    clean_code = code.strip().upper()
    
    # Kiralık veya öz mal ayrımı yapmaksızın hatları sırayla sorgula
    engines = [check_cma, check_maersk, check_akkon]
    for engine in engines:
        data = engine(page, clean_code)
        if data: # Hat gerçek bir veri döndürdüyse kabul et
            return data
            
    return {
        "carrier": "Bulunamadı",
        "pol": "-",
        "pol_date": "-",
        "pod": "-",
        "pod_date": "-",
        "transit_days": "-",
        "status": "Aktif Sefer Kaydı Tespit Edilemedi"
    }

# Arayüz
st.title("🚢 Detaylı Lojistik Konteyner & B/L Takip Masası")
st.write("Konteyner numaralarını alt alta girin. Hat, liman, yükleme/varış tarihleri ve transit süreleri otomatik hesaplanır.")

raw_input = st.text_area(
    "Konteyner Numaraları:",
    height=150,
    placeholder="TIIU2680745\nCAIU9639822"
)

if st.button("🚀 Detaylı Sorgulamayı Başlat", type="primary"):
    lines = [x.strip() for x in raw_input.split("\n") if len(x.strip()) >= 7]
    
    if not lines:
        st.warning("Lütfen en az bir geçerli konteyner numarası girin.")
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
            for idx, code in enumerate(lines):
                status_text.text(f"Detaylı Takip Yapılıyor ({idx+1}/{total}): {code}...")
                
                info = smart_track(page, code)
                
                results.append({
                    "Sıra": idx + 1,
                    "Konteyner No": code,
                    "Armatör": info["carrier"],
                    "Yükleme Limanı (POL)": info["pol"],
                    "Yükleme Tarihi": info["pol_date"],
                    "Varış Limanı (POD)": info["pod"],
                    "Varış Tarihi (ETA/ATA)": info["pod_date"],
                    "Transit Süresi": info["transit_days"],
                    "Statü / Durum": info["status"]
                })
                
                progress_bar.progress((idx + 1) / total)
            
            browser.close()
            status_text.success("Tüm konteynerlerin detaylı sefer dökümü tamamlandı!")

        df = pd.DataFrame(results)
        st.dataframe(df, use_container_width=True)
        
        # Excel
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name="Lojistik_Rapor")
        
        st.download_button(
            label="📥 Excel Olarak İndir (.xlsx)",
            data=buffer.getvalue(),
            file_name=f"Konteyner_Detayli_Rapor_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
