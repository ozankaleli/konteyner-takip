import io
import re
import requests
import pandas as pd
from datetime import datetime
import streamlit as st

st.set_page_config(page_title="Hızlı Konteyner & B/L Takip Masası", page_icon="🚢", layout="wide")

# Gerçek mobil tarayıcı kimliği (Bot korumalarını atlatmak için)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"
}

def parse_date(date_str):
    if not date_str or date_str == "-":
        return None
    cleaned = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', str(date_str)).strip()
    for fmt in ["%d %b %Y", "%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d %B %Y", "%Y-%m-%dT%H:%M:%SZ"]:
        try:
            return datetime.strptime(cleaned, fmt)
        except Exception:
            continue
    return None

def calculate_transit_days(d1_str, d2_str):
    t1 = parse_date(d1_str)
    t2 = parse_date(d2_str)
    if t1 and t2:
        diff = (t2 - t1).days
        return f"{diff} Gün" if diff >= 0 else "-"
    return "-"

# 1. CMA CGM Doğrudan Veri Çekici
def query_cma(code):
    try:
        url = f"https://www.cma-cgm.com/ebusiness/tracking/search?SearchBy=Container&Reference={code}"
        resp = requests.get(url, headers=HEADERS, timeout=12)
        if resp.status_code != 200 or "not found" in resp.text.lower():
            return None
        
        text = resp.text
        if code not in text.upper():
            return None
            
        # Statü tespiti
        status = "Seferde (In Transit)"
        if "discharged" in text.lower() or "tahliye" in text.lower():
            status = "Tahliye Edildi (Vardı)"
        elif "delivered" in text.lower():
            status = "Teslim Edildi"
            
        # Limanlar
        ports = re.findall(r'\b(MERSIN|AMBARLI|ALIAGA|IZMIR|ONNE|PORT SAID|JEBEL ALI|TANGER|DURBAN)\b', text, re.IGNORECASE)
        pol = ports[0].upper() if len(ports) > 0 else "Yükleme Limanı"
        pod = ports[-1].upper() if len(ports) > 1 else (ports[0].upper() if ports else "Varış Limanı")
        
        # Tarihler
        dates = re.findall(r'\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{4}\b', text, re.IGNORECASE)
        dep_date = dates[0] if len(dates) >= 1 else "-"
        arr_date = dates[-1] if len(dates) >= 2 else (dates[0] if dates else "-")
        
        return {
            "carrier": "CMA CGM",
            "pol": pol,
            "pol_date": dep_date,
            "pod": pod,
            "pod_date": arr_date,
            "transit": calculate_transit_days(dep_date, arr_date),
            "status": status
        }
    except Exception:
        return None

# 2. MAERSK Doğrudan Veri Çekici
def query_maersk(code):
    try:
        # Maersk açık API uç noktası
        api_url = f"https://api.maersk.com/track/{code}"
        resp = requests.get(api_url, headers=HEADERS, timeout=12)
        
        if resp.status_code == 200:
            data = resp.json()
            # JSON formatından doğrudan veri okuma
            containers = data.get("containers", [])
            if containers:
                cntr = containers[0]
                status = cntr.get("status", "Seferde")
                pol = cntr.get("origin", "Yükleme Limanı")
                pod = cntr.get("destination", "Varış Limanı")
                eta = cntr.get("eta", "-")
                dep = cntr.get("departureDate", "-")
                return {
                    "carrier": "MAERSK",
                    "pol": pol,
                    "pol_date": dep,
                    "pod": pod,
                    "pod_date": eta,
                    "transit": calculate_transit_days(dep, eta),
                    "status": status
                }
        
        # Web sayfası yedek kontrolü
        web_url = f"https://www.maersk.com/tracking/{code}"
        resp_web = requests.get(web_url, headers=HEADERS, timeout=12)
        text = resp_web.text
        if "no results" in text.lower() or "could not find" in text.lower() or code not in text.upper():
            return None
            
        status = "Tahliye Edildi (Vardı)" if "discharged" in text.lower() else "Seferde (Varmadı)"
        ports = re.findall(r'\b(MERSIN|AMBARLI|IZMIR|BREMERHAVEN|ROTTERDAM|ONNE|PORT SAID|JEBEL ALI)\b', text, re.IGNORECASE)
        pol = ports[0].upper() if len(ports) > 0 else "Yükleme Limanı"
        pod = ports[-1].upper() if len(ports) > 1 else "Varış Limanı"
        
        dates = re.findall(r'\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{4}\b', text, re.IGNORECASE)
        dep_date = dates[0] if len(dates) >= 1 else "-"
        arr_date = dates[-1] if len(dates) >= 2 else "-"
        
        return {
            "carrier": "MAERSK",
            "pol": pol,
            "pol_date": dep_date,
            "pod": pod,
            "pod_date": arr_date,
            "transit": calculate_transit_days(dep_date, arr_date),
            "status": status
        }
    except Exception:
        return None

# 3. AKKON LINES
def query_akkon(code):
    try:
        url = f"https://www.akkonlines.com/tr/tracking?search={code}"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        text = resp.text
        if "kayıt bulunamadı" in text.lower() or code not in text.upper():
            return None
            
        return {
            "carrier": "AKKON LINES",
            "pol": "Mersin Port",
            "pol_date": "-",
            "pod": "Varış Limanı",
            "pod_date": "-",
            "transit": "-",
            "status": "Aktif Sefer"
        }
    except Exception:
        return None

# Akıllı Hat Yönlendirici
def track_container(code):
    clean_code = code.strip().upper()
    
    # Sırayla hatları doğrudan sına
    engines = [query_cma, query_maersk, query_akkon]
    for engine in engines:
        res = engine(clean_code)
        if res:
            return res
            
    return {
        "carrier": "Kayıt Yok / Belirsiz",
        "pol": "-",
        "pol_date": "-",
        "pod": "-",
        "pod_date": "-",
        "transit": "-",
        "status": "Aktif Sefer Bulunamadı"
    }

# Arayüz
st.title("🚢 Hızlı Konteyner & B/L Takip Masası")
st.write("Kiralık (`CAIU`, `TIIU` vb.) veya öz mal fark etmeksizin numaraları alt alta yazın. Cep telefonundan da doğrudan kullanılabilir.")

raw_input = st.text_area(
    "Konteyner Numaraları:",
    height=150,
    placeholder="TIIU2680745\nCAIU9639822"
)

if st.button("🚀 Sorgulamayı Başlat", type="primary"):
    lines = [x.strip() for x in raw_input.split("\n") if len(x.strip()) >= 7]
    
    if not lines:
        st.warning("Lütfen en az bir geçerli numara girin.")
    else:
        results = []
        progress_bar = st.progress(0)
        total = len(lines)
        
        for idx, code in enumerate(lines):
            info = track_container(code)
            results.append({
                "Sıra": idx + 1,
                "Konteyner No": code,
                "Armatör": info["carrier"],
                "Yükleme Limanı (POL)": info["pol"],
                "Yükleme Tarihi": info["pol_date"],
                "Varış Limanı (POD)": info["pod"],
                "Varış Tarihi (ETA/ATA)": info["pod_date"],
                "Transit Süresi": info["transit"],
                "Statü": info["status"]
            })
            progress_bar.progress((idx + 1) / total)
            
        df = pd.DataFrame(results)
        st.dataframe(df, use_container_width=True)
        
        # Excel
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name="Takip_Raporu")
            
        st.download_button(
            label="📥 Excel Olarak İndir (.xlsx)",
            data=buffer.getvalue(),
            file_name=f"Konteyner_Raporu_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
