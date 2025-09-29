import os
import requests
import time
import re
import json
import logging
import random
from datetime import datetime, timedelta
from flask import Flask, request, render_template_string
from threading import Thread, Lock
from bs4 import BeautifulSoup

# ========== KONFIGURACJA ==========
app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Zmienne środowiskowe
DISCORD_WEBHOOK = os.getenv('DISCORD_WEBHOOK', '').strip()
BASE_CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '180'))

# ========== CENNIK IPHONE ==========
IPHONE_PRICE_RANGES = {
    "11": {"min": 200, "max": 350},
    "11 Pro": {"min": 351, "max": 450},
    "11 Pro Max": {"min": 451, "max": 500},
    "12": {"min": 400, "max": 600},
    "12 Pro": {"min": 650, "max": 800},
    "12 Pro Max": {"min": 700, "max": 850},
    "12 mini": {"min": 350, "max": 500},
    "13": {"min": 600, "max": 1000},
    "13 Pro": {"min": 800, "max": 1400},
    "13 Pro Max": {"min": 800, "max": 1500},
    "13 mini": {"min": 600, "max": 850},
    "14": {"min": 800, "max": 1500},
    "14 Pro": {"min": 1000, "max": 1600},
    "14 Pro Max": {"min": 1000, "max": 2000},
    "14 Plus": {"min": 1000, "max": 1700},
    "15": {"min": 1300, "max": 2000},
    "15 Pro": {"min": 2000, "max": 2900},
    "15 Pro Max": {"min": 2000, "max": 3100},
    "15 Plus": {"min": 1000, "max": 2100},
    "16": {"min": 2500, "max": 3300},
    "16 Pro": {"min": 3300, "max": 4000},
    "16 Pro Max": {"min": 3500, "max": 4200},
    "17": {"min": 3500, "max": 4300},
    "17 Pro": {"min": 4300, "max": 5000},
    "17 Pro Max": {"min": 4500, "max": 5200},
}

# ========== KONFIGURACJA DOMYŚLNA ==========
CONFIG = {
    "active_models": list(IPHONE_PRICE_RANGES.keys()),
    "keywords": [],
    "blocked_keywords": ["uszkodzony", "blokada", "część", "części", "tylko części"],
    "url": "https://www.olx.pl/elektronika/telefony/q-iphone/",
    "active": True,
    "max_ad_age_hours": 8760,
    "max_pages": 50,
    "include_damaged": True,
    "ignore_age_limit": True
}

# ========== PLIKI I ŚCIEŻKI ==========
SEEN_ADS_FILE = os.getenv('SEEN_ADS_FILE', 'seen_ads.json')

# ========== KLASY ==========
class MonitorState:
    def __init__(self):
        self.last_found_time = datetime.now()
        self.last_status_time = datetime.now()
        self.consecutive_zero_count = 0
        self.lock = Lock()

# ========== ZMIENNE GLOBALNE ==========
monitor_state = MonitorState()
seen_ads = set()
config_lock = Lock()

# ========== FUNKCJE POMOCNICZE ==========
def load_seen_ads():
    """Ładuje zapisane ogłoszenia z pliku"""
    global seen_ads
    if os.path.exists(SEEN_ADS_FILE):
        try:
            with open(SEEN_ADS_FILE, 'r', encoding='utf-8') as f:
                loaded_ads = json.load(f)
            if isinstance(loaded_ads, list):
                with monitor_state.lock:
                    seen_ads = set(loaded_ads)
                logging.info(f"✅ Załadowano {len(seen_ads)} śledzonych ogłoszeń")
            else:
                logging.error("❌ Nieprawidłowy format pliku seen_ads.json")
        except Exception as e:
            logging.error(f"❌ Błąd ładowania seen_ads: {e}")
    else:
        logging.info("📁 Plik seen_ads.json nie istnieje, zostanie utworzony przy zapisie")

def save_seen_ads():
    """Zapisuje seen_ads do pliku"""
    try:
        with monitor_state.lock:
            ads_list = list(seen_ads)
        with open(SEEN_ADS_FILE, 'w', encoding='utf-8') as f:
            json.dump(ads_list, f, ensure_ascii=False, indent=2)
        logging.info(f"💾 Zapisano {len(ads_list)} ogłoszeń do pliku")
    except Exception as e:
        logging.error(f"❌ Błąd zapisywania seen_ads: {e}")

def get_random_delay():
    """Losowe opóźnienie od 2 do 7 minut (w sekundach)"""
    return random.randint(120, 420)

# ========== FUNKCJE DISCORD ==========
def send_discord_notification(ad):
    """Wysyła powiadomienie na Discord (embed)"""
    if not DISCORD_WEBHOOK:
        logging.error("❌ Brak skonfigurowanego webhooka Discord!")
        return False
    embed = {
        "title": f"📱 {ad['title'][:200]}",
        "url": ad['url'],
        "color": 0x00ff00,
        "fields": [
            {"name": "💰 Cena", "value": f"**{ad['price']}**", "inline": True},
            {"name": "📱 Model", "value": f"{ad['model']}", "inline": True},
            {"name": "📍 Lokalizacja", "value": ad['location'][:100], "inline": True},
            {"name": "🕒 Dodano", "value": ad['time'][:50], "inline": True},
            {"name": "🎯 Zakres cenowy", "value": f"{ad['price_range']['min']}-{ad['price_range']['max']} zł", "inline": True}
        ],
        "thumbnail": {"url": ad.get('image') or ''},
        "footer": {"text": f"OLX iPhone Hunter • {ad.get('time_ago','')[:40]}"},
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }
    try:
        response = requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=10)
        if response.status_code in (200, 204):
            logging.info(f"✅ Wysłano na Discord: iPhone {ad['model']} - {ad['price']}")
            return True
        else:
            logging.error(f"❌ Błąd Discorda: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logging.error(f"❌ Błąd wysyłania na Discord: {e}")
        return False

def send_discord_alert(message):
    """Wysyła alert na Discord"""
    if not DISCORD_WEBHOOK:
        return
    embed = {
        "title": "🚨 ALERT SYSTEMU",
        "description": message,
        "color": 0xff0000,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "footer": {"text": "OLX iPhone Hunter PRO • System Alert"}
    }
    try:
        requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=10)
        logging.info("✅ Wysłano alert systemowy na Discord")
    except Exception as e:
        logging.error(f"❌ Błąd wysyłania alertu: {e}")

def send_discord_status():
    """Wysyła status co godzinę na Discord"""
    if not DISCORD_WEBHOOK:
        return
    with monitor_state.lock:
        last_found = monitor_state.last_found_time
    embed = {
        "title": "📊 STATUS SYSTEMU",
        "description": "🤖 Bot ciągle szuka nowych ogłoszeń iPhone na OLX!",
        "color": 0x00ff00,
        "fields": [
            {"name": "🕒 Ostatnie znalezione", "value": f"{last_found.strftime('%Y-%m-%d %H:%M:%S')}", "inline": True},
            {"name": "📱 Aktywne modele", "value": f"{len(CONFIG['active_models'])}", "inline": True},
            {"name": "👀 Śledzone oferty", "value": f"{len(seen_ads)}", "inline": True}
        ],
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "footer": {"text": "OLX iPhone Hunter PRO • Hourly Status"}
    }
    try:
        requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=10)
        logging.info("✅ Wysłano status godzinny na Discord")
    except Exception as e:
        logging.error(f"❌ Błąd wysyłania statusu: {e}")

def check_8_hours_alert():
    """Sprawdza czy minęło 8 godzin bez znalezienia ogłoszeń"""
    with monitor_state.lock:
        time_without_results = datetime.now() - monitor_state.last_found_time
        if time_without_results.total_seconds() >= 8 * 3600:
            alert_msg = (f"⚠️ **BRAK NOWYCH OGŁOSZEŃ OD 8 GODZIN!**\n\n"
                       f"Ostatnie znalezione: {monitor_state.last_found_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                       f"Aktywne modele: {len(CONFIG['active_models'])}\n"
                       f"Śledzone oferty: {len(seen_ads)}")
            send_discord_alert(alert_msg)
            with monitor_state.lock:
                monitor_state.last_found_time = datetime.now()

def check_hourly_status():
    """Sprawdza czy wysłać status co godzinę"""
    with monitor_state.lock:
        time_since_last_status = datetime.now() - monitor_state.last_status_time
        if time_since_last_status.total_seconds() >= 3600:
            send_discord_status()
            with monitor_state.lock:
                monitor_state.last_status_time = datetime.now()

# ========== PARSOWANIE I FILTROWANIE ==========
def extract_price(price_text):
    """Wyodrębnia cenę z tekstu (obsługuje spacje, nbsp, przecinki, kropki)"""
    if not price_text:
        return None
    try:
        s = str(price_text)
        s = s.replace("\xa0", " ").replace(" ", "").replace("zł", "").replace("PLN", "").replace(",", ".")
        m = re.search(r'(\d+(?:[.]\d+)?)', s)
        if not m:
            return None
        val = float(m.group(1))
        # jeśli liczba całkowita -> zwróć int
        if val.is_integer():
            return int(val)
        return val
    except Exception as e:
        logging.debug(f"❌ Błąd parsowania ceny '{price_text}': {e}")
        return None

def extract_model_and_variant(title):
    """Rozpoznaje model i wariant (odporne na wielkość liter i znaki specjalne)."""
    if not title:
        return None
    norm = re.sub(r'[^A-Za-z0-9\s]', ' ', title).lower()
    norm = re.sub(r'\s+', ' ', norm).strip()
    # Spróbuj wykryć warianty w kolejności od najbardziej specyficznych
    m = re.search(r'iphone\s*(\d+)\s*(pro\s*max|promax|promax|pro max)\b', norm)
    if m:
        num = m.group(1)
        candidate = f"{num} pro max"
    else:
        m = re.search(r'iphone\s*(\d+)\s*(pro)\b', norm)
        if m:
            num = m.group(1)
            candidate = f"{num} pro"
        else:
            m = re.search(r'iphone\s*(\d+)\s*(mini)\b', norm)
            if m:
                num = m.group(1)
                candidate = f"{num} mini"
            else:
                m = re.search(r'iphone\s*(\d+)\s*(plus)\b', norm)
                if m:
                    num = m.group(1)
                    candidate = f"{num} plus"
                else:
                    m = re.search(r'iphone\s*(\d+)\b', norm)
                    if m:
                        num = m.group(1)
                        candidate = f"{num}"
                    else:
                        return None
    # Dopasuj do klucza w IPHONE_PRICE_RANGES (ignoruj wielkość liter)
    candidate = candidate.strip()
    for key in IPHONE_PRICE_RANGES.keys():
        if key.lower() == candidate:
            return key
    # nie znaleziono dokładnego dopasowania, spróbuj uprościć (np. '11 pro' -> '11 Pro')
    for key in IPHONE_PRICE_RANGES.keys():
        if key.lower().startswith(candidate):
            return key
    return None

def parse_olx_time(time_text):
    """Parsuje tekst OLX na datetime (dzisiaj/wczoraj/godz/ minuty / dni)"""
    if not time_text:
        return None
    txt = str(time_text).lower()
    now = datetime.now()
    try:
        if "teraz" in txt or "przed chwil" in txt:
            return now
        if "dzisiaj" in txt:
            m = re.search(r'(\d{1,2}):(\d{2})', txt)
            if m:
                h, mi = int(m.group(1)), int(m.group(2))
                return now.replace(hour=h, minute=mi, second=0, microsecond=0)
            return now
        if "wczoraj" in txt:
            m = re.search(r'(\d{1,2}):(\d{2})', txt)
            if m:
                h, mi = int(m.group(1)), int(m.group(2))
                return (now - timedelta(days=1)).replace(hour=h, minute=mi, second=0, microsecond=0)
            return now - timedelta(days=1)
        # relative matches
        if (m := re.search(r'(\d+)\s*godz', txt)):
            return now - timedelta(hours=int(m.group(1)))
        if (m := re.search(r'(\d+)\s*min', txt)):
            return now - timedelta(minutes=int(m.group(1)))
        if (m := re.search(r'(\d+)\s*dni', txt)):
            return now - timedelta(days=int(m.group(1)))
    except Exception as e:
        logging.debug(f"❌ Błąd parsowania czasu '{time_text}': {e}")
        return None

def is_within_time_limit(ad_time, max_hours=None):
    """Sprawdza limit czasu. Jeśli CONFIG['ignore_age_limit'] True => zawsze True"""
    if CONFIG.get('ignore_age_limit', True):
        return True
    if not ad_time:
        return False
    if max_hours is None:
        max_hours = CONFIG.get('max_ad_age_hours', 8760)
    return (datetime.now() - ad_time).total_seconds() <= max_hours * 3600

def check_filters(title, price, model):
    """Sprawdza wszystkie filtry: model, cena, wymagane słowa, blokady (z uwzględnieniem include_damaged)."""
    title_lower = title.lower()
    # model
    if not model or model not in CONFIG['active_models']:
        return False
    # price
    price_range = IPHONE_PRICE_RANGES.get(model)
    if not price_range or price is None:
        return False
    try:
        if float(price) < float(price_range['min']) or float(price) > float(price_range['max']):
            return False
    except Exception:
        return False
    # required keywords
    if CONFIG.get('keywords'):
        if not any(k.lower() in title_lower for k in CONFIG.get('keywords', [])):
            return False
    # blocked keywords: tylko gdy include_damaged == False
    if not CONFIG.get('include_damaged', False):
        if CONFIG.get('blocked_keywords'):
            if any(b.lower() in title_lower for b in CONFIG.get('blocked_keywords', [])):
                return False
    return True

# ========== MONITOROWANIE OLX ==========
def check_olx_page(page_url):
    """Sprawdza pojedynczą stronę OLX i zwraca listę nowych ogłoszeń"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        logging.info(f"🌐 Pobieram stronę: {page_url}")
        resp = requests.get(page_url, headers=headers, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        new_ads = []
        # heurystyka: szukaj linków ofert
        anchors = soup.find_all('a', href=True)
        candidates = []
        for a in anchors:
            href = a['href']
            if '/oferta/' in href:
                candidates.append(a)
        logging.info(f"📄 Znaleziono {len(candidates)} potencjalnych ofert (anchor z '/oferta/')")
        for anchor in candidates:
            try:
                # link
                link = anchor['href']
                if not link.startswith('http'):
                    link = 'https://www.olx.pl' + link
                # czy już widziany
                with monitor_state.lock:
                    if link in seen_ads:
                        continue
                # tytuł
                title = anchor.get_text(separator=' ', strip=True)
                if not title or len(title) < 3:
                    # spróbuj elementów wewnątrz anchor
                    t_el = anchor.find(['h3', 'h2', 'h4', 'strong'])
                    title = t_el.get_text(strip=True) if t_el else title
                if not title or len(title) < 3:
                    continue
                # cena - szukaj w najbliższych elementach rodzica
                price = None
                parent = anchor
                # climb a few levels to find price
                for _ in range(3):
                    texts = parent.find_all(text=re.compile(r'\d[\d\s,.]*\s*(zł|pln)?', re.IGNORECASE))
                    if texts:
                        price = extract_price(texts[0])
                        break
                    if parent.parent is None:
                        break
                    parent = parent.parent
                # jeśli dalej brak ceny, ignoruj (opcjonalnie można wysyłać oferty bez ceny)
                if price is None:
                    continue
                # model
                model = extract_model_and_variant(title)
                if not model:
                    continue
                # czas i lokalizacja - szukaj w najbliższych tekstach
                time_text = ""
                parent = anchor
                for _ in range(4):
                    texts = parent.find_all(text=True)
                    for txt in texts:
                        s = str(txt).strip()
                        if re.search(r'(teraz|przed chwil|dzisiaj|wczoraj|godz|minut|dni)', s, re.IGNORECASE):
                            time_text = s
                            break
                    if time_text:
                        break
                    if parent.parent is None:
                        break
                    parent = parent.parent
                ad_time = parse_olx_time(time_text) if time_text else None
                if not is_within_time_limit(ad_time, CONFIG.get('max_ad_age_hours')):
                    # jeśli ograniczenie czasu włączone i nie mieści się -> pomin
                    continue
                # filtry (model/price/keywords/blocked)
                if not check_filters(title, price, model):
                    continue
                # lokalizacja - heurystyka: teksty obok czasu często zawierają lokalizację
                location = "Brak lokalizacji"
                if time_text and ' - ' in time_text:
                    location = time_text.split(' - ')[0].strip()
                else:
                    # spróbuj znaleźć krótszy tekst w rodzicu, który nie jest ceną
                    parent = anchor.parent
                    found_loc = None
                    if parent:
                        for txt in parent.find_all(text=True, limit=20):
                            s = str(txt).strip()
                            if s and len(s) < 60 and not re.search(r'\d+\s*zł|pln|godz|min|teraz|wczoraj|dzisiaj', s, re.IGNORECASE):
                                found_loc = s
                                break
                        if found_loc:
                            location = found_loc
                # obrazek
                img_url = ""
                img = anchor.find('img')
                if img:
                    img_url = img.get('data-src') or img.get('src') or ''
                    if img_url.startswith('//'):
                        img_url = 'https:' + img_url
                # buduj obiekt ogłoszenia
                ad_data = {
                    'url': link,
                    'title': title,
                    'price': f"{int(price)} zł" if isinstance(price, (int, float)) else str(price),
                    'location': location,
                    'time': time_text or '',
                    'time_ago': time_text or '',
                    'image': img_url,
                    'model': model,
                    'price_range': IPHONE_PRICE_RANGES.get(model, {"min": 0, "max": 0})
                }
                # oznacz jako widziane i dodaj
                with monitor_state.lock:
                    seen_ads.add(link)
                new_ads.append(ad_data)
                logging.info(f"✅ Znaleziono: {model} | {price} zł | {title[:50]}")
            except Exception as e:
                logging.debug(f"❌ Błąd przetwarzania kandydatu: {e}")
                continue
        return new_ads
    except requests.exceptions.RequestException as e:
        logging.error(f"❌ Błąd sieci (strona {page_url}): {e}")
        return []
    except Exception as e:
        logging.error(f"❌ Błąd parsowania strony (strona {page_url}): {e}")
        return []

def check_olx():
    """Przechodzi przez kolejne strony i zbiera nowe ogłoszenia"""
    if not CONFIG.get('active', True):
        logging.info("⏸️ Monitoring nieaktywny")
        return []
    all_new_ads = []
    max_pages = max(1, min(int(CONFIG.get('max_pages', 50)), 100)) # ograniczenie do 100
    for page in range(1, max_pages + 1):
        if page == 1:
            page_url = CONFIG['url']
        else:
            # dodawanie parametru page (bez komplikacji)
            if '?' in CONFIG['url']:
                page_url = f"{CONFIG['url']}&page={page}"
            else:
                page_url = f"{CONFIG['url']}?page={page}"
        logging.info(f"🔍 Sprawdzam stronę {page}/{max_pages}: {page_url}")
        page_ads = check_olx_page(page_url)
        all_new_ads.extend(page_ads)
        # krótka przerwa między pobraniami stron
        if page < max_pages:
            time.sleep(1.5)
    logging.info(f"📊 Podsumowanie: {len(all_new_ads)} nowych ogłoszeń z {max_pages} stron")
    return all_new_ads

# ========== GŁÓWNA PĘTLA MONITORUJĄCA ==========
def monitoring_loop():
    """Główna pętla monitorowania 24/7"""
    logging.info("🟢 Uruchomiono monitoring OLX")
    logging.info(f"📱 Aktywne modele: {len(CONFIG['active_models'])}")
    logging.info(f"🔗 Webhook Discord: {'✅' if DISCORD_WEBHOOK else '❌'}")
    load_seen_ads()
    while True:
        try:
            if CONFIG.get('active', True) and DISCORD_WEBHOOK:
                ads = check_olx()
                if ads:
                    success_count = 0
                    for ad in ads:
                        if send_discord_notification(ad):
                            success_count += 1
                    with monitor_state.lock:
                        monitor_state.last_found_time = datetime.now()
                        monitor_state.consecutive_zero_count = 0
                    save_seen_ads()
                    logging.info(f"📨 Wysłano {success_count}/{len(ads)} ogłoszeń na Discord")
                else:
                    with monitor_state.lock:
                        monitor_state.consecutive_zero_count += 1
                    logging.info(f"🔍 Brak nowych ogłoszeń (seria: {monitor_state.consecutive_zero_count})")
                check_8_hours_alert()
                check_hourly_status()
                # LOSOWE OPÓŹNIENIE 2-7 min
                delay = get_random_delay()
                minutes = delay // 60
                seconds = delay % 60
                logging.info(f"⏰ Następne skanowanie za {minutes}min {seconds}s...")
                time.sleep(delay)
        except Exception as e:
            logging.error(f"❌ Błąd pętli: {e}")
            time.sleep(60)

# ========== PANEL WEB ==========
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>📱 OLX iPhone Hunter PRO</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: Arial, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; }
        .form-group { margin: 20px 0; }
        label { display: block; margin: 8px 0; }
        input, select, textarea { width: 100%; padding: 10px; margin: 5px 0; }
        .model-group { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px; margin: 15px 0; }
        .model-item { background: #f8f9fa; padding: 10px; border-radius: 5px; }
        button { background: #007bff; color: white; padding: 12px 25px; border: none; cursor: pointer; border-radius: 5px; }
        .status { padding: 15px; margin: 15px 0; border-radius: 5px; }
        .success { background: #d4edda; color: #155724; }
        .info { background: #d1ecf1; color: #0c5460; }
    </style>
</head>
<body>
    <h1>📱 OLX iPhone Hunter PRO</h1>
    {% if message %}
        <div class="status success">{{ message }}</div>
    {% endif %}
    <div class="status info">
        <strong>🎯 Bot monitorujący OLX</strong><br>
        Losowe opóźnienia 2-7min • Alerty 8h • Status co 1h
    </div>
    <form method="POST" action="/config">
        <div class="form-group">
            <h3>📱 Wybierz modele do monitorowania:</h3>
            <div class="model-group">
                {% for model, prices in price_ranges.items() %}
                <div class="model-item">
                    <label>
                        <input type="checkbox" name="active_models" value="{{ model }}" {% if model in config.active_models %}checked{% endif %}>
                        <strong>iPhone {{ model }}</strong><br>
                        <small>{{ prices.min }} - {{ prices.max }} zł</small>
                    </label>
                </div>
                {% endfor %}
            </div>
        </div>
        <div class="form-group">
            <h3>🔍 Filtry zaawansowane:</h3>
            <label>Wymagane słowa (oddziel przecinkiem):</label>
            <textarea name="keywords" placeholder="Stan bardzo dobry, Stan idealny">{{ config.keywords | join(', ') }}</textarea>
            <label>Zablokowane słowa (oddziel przecinkiem):</label>
            <textarea name="blocked_keywords" placeholder="uszkodzony, tylko części, blokada">{{ config.blocked_keywords | join(', ') }}</textarea>
        </div>
        <div class="form-group">
            <label>Maksymalna liczba stron OLX do sprawdzenia:</label>
            <input type="number" name="max_pages" value="{{ config.max_pages }}" min="1" max="100">
        </div>
        <div class="form-group">
            <label>
                <input type="checkbox" name="include_damaged" {% if config.include_damaged %}checked{% endif %}>
                🔧 Pokazuj uszkodzone oferty (np. blokada, części)
            </label>
        </div>
        <div class="form-group">
            <label>
                <input type="checkbox" name="ignore_age_limit" {% if config.ignore_age_limit %}checked{% endif %}>
                🕰️ Pominąć limit wieku ogłoszeń (wysyłaj też stare)
            </label>
        </div>
        <div class="form-group">
            <label>
                <input type="checkbox" name="active" {% if config.active %}checked{% endif %}>
                🟢 Aktywny monitoring
            </label>
        </div>
        <button type="submit">💾 Zapisz konfigurację</button>
    </form>
    <div style="margin-top: 30px; padding: 20px; background: #f8f9fa; border-radius: 5px;">
        <h3>📊 Status systemu:</h3>
        <p>🟢 <strong>Aktywny:</strong> {% if config.active %}TAK{% else %}NIE{% endif %}</p>
        <p>⏰ <strong>Interwał skanów:</strong> 2-7 minut (losowy)</p>
        <p>📨 <strong>Webhook Discord:</strong> {% if DISCORD_WEBHOOK %}✅ Skonfigurowany{% else %}❌ Brak{% endif %}</p>
        <p>👀 <strong>Śledzone ogłoszenia:</strong> {{ seen_ads_count }}</p>
        <p>📄 <strong>Sprawdzane strony OLX:</strong> {{ config.max_pages }}</p>
        <p>📱 <strong>Aktywne modele:</strong> {{ config.active_models|length }}/{{ price_ranges|length }}</p>
        <p>🕒 <strong>Ostatnie znalezione:</strong> {{ last_found_time }}</p>
        <p>🔧 <strong>Pokazuj uszkodzone:</strong> {% if config.include_damaged %}TAK{% else %}NIE{% endif %}</p>
        <p>🕰️ <strong>Pomiń limit wieku:</strong> {% if config.ignore_age_limit %}TAK{% else %}NIE{% endif %}</p>
    </div>
</body>
</html>
"""

@app.route('/')
def dashboard():
    with monitor_state.lock:
        last_found = monitor_state.last_found_time.strftime('%Y-%m-%d %H:%M:%S')
        seen_ads_count = len(seen_ads)
    return render_template_string(HTML_TEMPLATE, config=CONFIG, price_ranges=IPHONE_PRICE_RANGES, seen_ads_count=seen_ads_count, last_found_time=last_found, DISCORD_WEBHOOK=DISCORD_WEBHOOK)

@app.route('/config', methods=['POST'])
def update_config():
    """Aktualizuje konfigurację przez formularz web"""
    global CONFIG
    try:
        with config_lock:
            CONFIG['active_models'] = request.form.getlist('active_models') or []
            keywords = request.form.get('keywords', '')
            CONFIG['keywords'] = [k.strip() for k in keywords.split(',') if k.strip()]
            blocked = request.form.get('blocked_keywords', '')
            CONFIG['blocked_keywords'] = [k.strip() for k in blocked.split(',') if k.strip()]
            # max_pages z walidacją
            try:
                mp = int(request.form.get('max_pages', CONFIG.get('max_pages', 50)))
                CONFIG['max_pages'] = max(1, min(mp, 100))
            except ValueError:
                CONFIG['max_pages'] = CONFIG.get('max_pages', 50)
            CONFIG['include_damaged'] = 'include_damaged' in request.form
            CONFIG['ignore_age_limit'] = 'ignore_age_limit' in request.form
            CONFIG['active'] = 'active' in request.form
        message = "✅ Konfiguracja zapisana!"
        logging.info(f"🔧 Zaktualizowano konfigurację - modele: {len(CONFIG['active_models'])}")
    except Exception as e:
        message = f"❌ Błąd: {e}"
        logging.error(message)
    with monitor_state.lock:
        last_found = monitor_state.last_found_time.strftime('%Y-%m-%d %H:%M:%S')
        seen_ads_count = len(seen_ads)
    return render_template_string(HTML_TEMPLATE, config=CONFIG, price_ranges=IPHONE_PRICE_RANGES, message=message, seen_ads_count=seen_ads_count, last_found_time=last_found, DISCORD_WEBHOOK=DISCORD_WEBHOOK)

# ========== URUCHOMIENIE ==========
if __name__ == '__main__':
    load_seen_ads()
    monitor_thread = Thread(target=monitoring_loop)
    monitor_thread.daemon = True
    monitor_thread.start()
    port = int(os.getenv('PORT', 5000))
    logging.info(f"🌐 Serwer web uruchomiony na porcie {port}")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
