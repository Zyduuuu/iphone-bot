import os
import requests
import time
import re
import json
import logging
import random
from datetime import datetime, timedelta
from flask import Flask, request, render_template_string
from threading import Thread
from bs4 import BeautifulSoup

# Konfiguracja
app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Konfiguracja z environment variables
DISCORD_WEBHOOK = os.getenv('DISCORD_WEBHOOK', '')
BASE_CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '180'))  # 3 minuty bazowo

# Cennik iPhone'ów
IPHONE_PRICE_RANGES = {
    # iPhone 11 Series
    "11": {"min": 200, "max": 350},
    "11 Pro": {"min": 351, "max": 450},
    "11 Pro Max": {"min": 451, "max": 500},
    
    # iPhone 12 Series
    "12": {"min": 400, "max": 600},
    "12 Pro": {"min": 650, "max": 800},
    "12 Pro Max": {"min": 700, "max": 850},
    "12 mini": {"min": 350, "max": 500},
    
    # iPhone 13 Series
    "13": {"min": 600, "max": 1000},
    "13 Pro": {"min": 800, "max": 1400},
    "13 Pro Max": {"min": 800, "max": 1500},
    "13 mini": {"min": 600, "max": 850},
    
    # iPhone 14 Series
    "14": {"min": 800, "max": 1500},
    "14 Pro": {"min": 1000, "max": 1600},
    "14 Pro Max": {"min": 1000, "max": 2000},
    "14 Plus": {"min": 1000, "max": 1700},
    
    # iPhone 15 Series
    "15": {"min": 1300, "max": 2000},
    "15 Pro": {"min": 2000, "max": 2900},
    "15 Pro Max": {"min": 2000, "max": 3100},
    "15 Plus": {"min": 1000, "max": 2100},
    
    # iPhone 16 Series
    "16": {"min": 2500, "max": 3300},
    "16 Pro": {"min": 3300, "max": 4000},
    "16 Pro Max": {"min": 3500, "max": 4200},
    
    # iPhone 17 Series
    "17": {"min": 3500, "max": 4300},
    "17 Pro": {"min": 4300, "max": 5000},
    "17 Pro Max": {"min": 4500, "max": 5200},
}

# Domyślna konfiguracja - BEZ FILTRÓW
CONFIG = {
    "active_models": list(IPHONE_PRICE_RANGES.keys()),
    "keywords": [],  # PUSTE - szuka wszystkich
    "blocked_keywords": [],  # PUSTE - też uszkodzone
    "url": "https://www.olx.pl/elektronika/telefony/iphone/q-iphone/",
    "active": True,
    "max_ad_age_hours": 8760,  # 1 rok - praktycznie bez limitu
    "max_pages": 5  # Nowe: maksymalna liczba stron do sprawdzenia
}

# Plik do zapisu seen_ads
SEEN_ADS_FILE = "seen_ads.json"

# Nowe zmienne dla alertów i statusów
class MonitorState:
    def __init__(self):
        self.last_found_time = datetime.now()
        self.last_status_time = datetime.now()
        self.consecutive_zero_count = 0
        
monitor_state = MonitorState()

def get_random_delay():
    """Losowe opóźnienie od 2 do 7 minut"""
    return random.randint(120, 420)  # 2-7 minut w sekundach

def load_seen_ads():
    """Ładuje zapisane ogłoszenia z pliku"""
    if os.path.exists(SEEN_ADS_FILE):
        try:
            with open(SEEN_ADS_FILE, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except Exception as e:
            logging.error(f"❌ Błąd ładowania seen_ads: {e}")
    return set()

def save_seen_ads():
    """Zapisuje seen_ads do pliku"""
    try:
        with open(SEEN_ADS_FILE, 'w', encoding='utf-8') as f:
            json.dump(list(seen_ads), f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"❌ Błąd zapisywania seen_ads: {e}")

seen_ads = load_seen_ads()

def send_discord_alert(message):
    """Wysyła alert na Discord"""
    if not DISCORD_WEBHOOK:
        logging.error("❌ Brak skonfigurowanego webhooka Discord!")
        return

    embed = {
        "title": "🚨 ALERT SYSTEMU",
        "description": message,
        "color": 0xff0000,
        "timestamp": datetime.now().isoformat(),
        "footer": {"text": "OLX iPhone Hunter PRO • System Alert"}
    }
    
    try:
        response = requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=10)
        if response.status_code == 204:
            logging.info("✅ Wysłano alert systemowy na Discord")
        else:
            logging.error(f"❌ Błąd Discorda: {response.status_code}")
    except Exception as e:
        logging.error(f"❌ Błąd wysyłania alertu: {e}")

def send_discord_status():
    """Wysyła status co godzinę na Discord"""
    if not DISCORD_WEBHOOK:
        return

    embed = {
        "title": "📊 STATUS SYSTEMU",
        "description": "🤖 Bot ciągle szuka nowych ogłoszeń iPhone na OLX i działa poprawnie!",
        "color": 0x00ff00,
        "fields": [
            {"name": "🕒 Ostatnie znalezione", "value": f"{monitor_state.last_found_time.strftime('%Y-%m-%d %H:%M:%S')}", "inline": True},
            {"name": "📱 Aktywne modele", "value": f"{len(CONFIG['active_models'])}", "inline": True},
            {"name": "👀 Śledzone oferty", "value": f"{len(seen_ads)}", "inline": True}
        ],
        "timestamp": datetime.now().isoformat(),
        "footer": {"text": "OLX iPhone Hunter PRO • Hourly Status"}
    }
    
    try:
        response = requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=10)
        if response.status_code == 204:
            logging.info("✅ Wysłano status godzinny na Discord")
        else:
            logging.error(f"❌ Błąd statusu Discorda: {response.status_code}")
    except Exception as e:
        logging.error(f"❌ Błąd wysyłania statusu: {e}")

def check_8_hours_alert():
    """Sprawdza czy minęło 8 godzin bez znalezienia ogłoszeń"""
    time_without_results = datetime.now() - monitor_state.last_found_time
    if time_without_results.total_seconds() >= 8 * 3600:  # 8 godzin
        alert_msg = f"⚠️ **BRAK NOWYCH OGŁOSZEŃ OD 8 GODZIN!**\n\nOstatnie znalezione ogłoszenie: {monitor_state.last_found_time.strftime('%Y-%m-%d %H:%M:%S')}\nSprawdzane modele: {len(CONFIG['active_models'])}\nŚledzone oferty: {len(seen_ads)}"
        send_discord_alert(alert_msg)
        monitor_state.last_found_time = datetime.now()  # reset

def check_hourly_status():
    """Sprawdza czy wysłać status co godzinę"""
    time_since_last_status = datetime.now() - monitor_state.last_status_time
    if time_since_last_status.total_seconds() >= 3600:  # 1 godzina
        send_discord_status()
        monitor_state.last_status_time = datetime.now()

# HTML panelu konfiguracyjnego (bez zmian - ten sam template)
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>📱 OLX iPhone Hunter PRO</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }
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
    <h1>📱 OLX iPhone Hunter PRO v3</h1>
    
    {% if message %}
    <div class="status success">{{ message }}</div>
    {% endif %}
    
    <div class="status info">
        <strong>🎯 Ulepszony bot v3:</strong> Losowe opóźnienia 2-7min + Alerty 8h + Status co 1h
    </div>
    
    <form method="POST" action="/config">
        <div class="form-group">
            <h3>📱 Wybierz modele do monitorowania:</h3>
            <div class="model-group">
                {% for model, prices in price_ranges.items() %}
                <div class="model-item">
                    <label>
                        <input type="checkbox" name="active_models" value="{{ model }}" 
                               {% if model in config.active_models %}checked{% endif %}>
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
            <input type="number" name="max_pages" value="{{ config.max_pages }}" min="1" max="10">
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
        <h3>📊 Status systemu v3:</h3>
        <p>🟢 <strong>Aktywny:</strong> {% if config.active %}TAK{% else %}NIE{% endif %}</p>
        <p>⏰ <strong>Interwał skanów:</strong> 2-7 minut (losowy)</p>
        <p>📨 <strong>Webhook Discord:</strong> {% if DISCORD_WEBHOOK %}✅ Skonfigurowany{% else %}❌ Brak{% endif %}</p>
        <p>👀 <strong>Śledzone ogłoszenia:</strong> {{ seen_ads|length }}</p>
        <p>📄 <strong>Sprawdzane strony OLX:</strong> {{ config.max_pages }}</p>
        <p>📱 <strong>Aktywne modele:</strong> {{ config.active_models|length }}/{{ price_ranges|length }}</p>
        <p>🕒 <strong>Ostatnie znalezione:</strong> {{ monitor_state.last_found_time.strftime('%Y-%m-%d %H:%M:%S') }}</p>
        <p>⚠️ <strong>Alert po braku:</strong> 8 godzin</p>
        <p>📊 <strong>Status co:</strong> 1 godzinę</p>
    </div>
</body>
</html>
"""

def send_discord_notification(ad):
    """Wysyła powiadomienie na Discord"""
    if not DISCORD_WEBHOOK:
        logging.error("❌ Brak skonfigurowanego webhooka Discord!")
        return

    embed = {
        "title": f"📱 {ad['title'][:200]}",
        "url": ad['url'],
        "color": 0x00ff00,
        "fields": [
            {"name": "💰 Cena", "value": f"**{ad['price']}**", "inline": True},
            {"name": "💸 W Twoim budżecie", "value": f"**TAK**", "inline": True},
            {"name": "📍 Lokalizacja", "value": ad['location'], "inline": True},
            {"name": "🕒 Dodano", "value": ad['time'], "inline": True},
            {"name": "📱 Model", "value": f"iPhone {ad['model']}", "inline": True},
            {"name": "🎯 Zakres cenowy", "value": f"{ad['price_range']['min']}-{ad['price_range']['max']} zł", "inline": True}
        ],
        "thumbnail": {"url": ad['image']},
        "footer": {"text": f"OLX iPhone Hunter v3 • {ad['time_ago']}"}
    }
    
    try:
        response = requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=10)
        if response.status_code == 204:
            logging.info(f"✅ Wysłano: iPhone {ad['model']} - {ad['price']}")
        else:
            logging.error(f"❌ Błąd Discorda: {response.status_code}")
    except Exception as e:
        logging.error(f"❌ Błąd wysyłania: {e}")

def extract_price(price_text):
    """Wyodrębnia cenę z tekstu"""
    if not price_text:
        return None
    try:
        numbers = re.findall(r'\d+', price_text.replace(" ", "").replace("zł", ""))
        return float(numbers[0]) if numbers else None
    except:
        return None

def extract_model_and_variant(title):
    """Rozpoznaje model iPhone i wariant z tytułu - ULEPSZONE!"""
    title_lower = title.lower()
    
    # ULEPSZONE: Więcej wzorców i lepsze dopasowanie
    patterns = [
        (r"iphone[\s\-]*(\d+)[\s\-]*(pro max|pro[\s\-]*max|promax)", " Pro Max"),
        (r"iphone[\s\-]*(\d+)[\s\-]*(pro|pro)", " Pro"),
        (r"iphone[\s\-]*(\d+)[\s\-]*(mini|mini)", " mini"),
        (r"iphone[\s\-]*(\d+)[\s\-]*(plus|plus)", " Plus"),
        (r"iphone[\s\-]*(\d+)", ""),  # Podstawowy model
        (r"i[\s\-]*phone[\s\-]*(\d+)", ""),  # Dla "i phone 11"
        (r"iphone[\s\-]*(\d+)[\s\-]*max", " Pro Max"),  # Dla "iphone 11 max"
    ]
    
    for pattern, variant in patterns:
        match = re.search(pattern, title_lower, re.IGNORECASE)
        if match:
            model = match.group(1)
            # Sprawdź czy to poprawny model (11-17)
            if model.isdigit() and 11 <= int(model) <= 17:
                return f"{model}{variant}"
    
    return None

def parse_olx_time(time_text):
    """Parsuje czas OLX na obiekt datetime"""
    if not time_text:
        return None
        
    time_text = time_text.lower()
    now = datetime.now()
    
    try:
        if "teraz" in time_text or "przed chwilą" in time_text:
            return now
        elif "dzisiaj" in time_text:
            return now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif "wczoraj" in time_text:
            return now - timedelta(days=1)
        else:
            # Spróbuj znaleźć liczbę godzin/minut
            hours_match = re.search(r'(\d+)\s*godzin', time_text)
            minutes_match = re.search(r'(\d+)\s*minut', time_text)
            
            if hours_match:
                hours_ago = int(hours_match.group(1))
                return now - timedelta(hours=hours_ago)
            elif minutes_match:
                minutes_ago = int(minutes_match.group(1))
                return now - timedelta(minutes=minutes_ago)
    except Exception as e:
        logging.error(f"❌ Błąd parsowania czasu: {e}")
    
    return None

def is_within_time_limit(ad_time, max_hours=8760):
    """Sprawdza czy ogłoszenie jest w limicie czasowym - teraz praktycznie bez limitu"""
    return True  # Zawsze zwraca True - brak limitu czasu

def check_filters(title, price, model):
    """Sprawdza wszystkie filtry"""
    title_lower = title.lower()
    
    # Filtry modeli
    if not model or model not in CONFIG['active_models']:
        return False
    
    # Filtry cenowe dla konkretnego modelu
    price_range = IPHONE_PRICE_RANGES.get(model)
    if not price_range or not price:
        return False
    
    if price < price_range['min'] or price > price_range['max']:
        return False
    
    # Wymagane słowa kluczowe - teraz puste, więc zawsze przechodzi
    if CONFIG['keywords']:
        if not any(keyword.lower() in title_lower for keyword in CONFIG['keywords']):
            return False
    
    # Zablokowane słowa - teraz puste, więc zawsze przechodzi
    if CONFIG['blocked_keywords']:
        if any(blocked.lower() in title_lower for blocked in CONFIG['blocked_keywords']):
            return False
    
    return True

def check_olx_page(page_url):
    """Sprawdza pojedynczą stronę OLX"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15'
        }
        response = requests.get(page_url, headers=headers, timeout=30)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        new_ads = []
        ads = soup.find_all("div", {"data-cy": "l-card"})
        
        for ad in ads:
            try:
                # Link
                link_elem = ad.find("a")
                if not link_elem or not link_elem.get('href'):
                    continue
                    
                link = link_elem['href']
                if not link.startswith('http'):
                    link = 'https://www.olx.pl' + link
                
                if link in seen_ads:
                    continue
                
                # Tytuł
                title_elem = ad.find("h6")
                if not title_elem:
                    continue
                    
                title = title_elem.get_text().strip()
                if not title:
                    continue
                
                # Cena
                price_elem = ad.find("p", {"data-testid": "ad-price"})
                price = extract_price(price_elem.get_text() if price_elem else "")
                
                # Model i wariant - ULEPSZONE ROZPOZNAWANIE
                model = extract_model_and_variant(title)
                
                # Czas ogłoszenia
                location_elem = ad.find("p", {"data-testid": "location-date"})
                time_text = location_elem.get_text().strip() if location_elem else ""
                ad_time = parse_olx_time(time_text)
                
                # Sprawdź czy ogłoszenie jest świeże
                if not is_within_time_limit(ad_time, CONFIG['max_ad_age_hours']):
                    continue
                
                # Filtry
                if not check_filters(title, price, model):
                    continue
                
                # Lokalizacja
                location_parts = time_text.split("\n")
                
                # Obrazek
                img_elem = ad.find("img")
                image_url = img_elem.get('src', '') if img_elem else ''
                
                ad_data = {
                    'url': link,
                    'title': title,
                    'price': f"{int(price)} zł",
                    'location': location_parts[0] if location_parts else "Brak",
                    'time': time_text,
                    'time_ago': time_text,
                    'image': image_url,
                    'model': model,
                    'price_range': IPHONE_PRICE_RANGES.get(model, {"min": 0, "max": 0})
                }
                
                seen_ads.add(link)
                new_ads.append(ad_data)
                logging.info(f"✅ Znaleziono: iPhone {model} - {price} zł ({time_text})")
                
            except Exception as e:
                continue
                
        return new_ads
        
    except Exception as e:
        logging.error(f"❌ Błąd OLX (strona {page_url}): {e}")
        return []

def check_olx():
    """Główna funkcja sprawdzania OLX - TERAZ Z PAGINACJĄ!"""
    if not CONFIG['active']:
        return []

    all_new_ads = []
    
    # Sprawdź WSZYSTKIE strony (paginacja)
    for page in range(1, CONFIG['max_pages'] + 1):
        if page == 1:
            page_url = CONFIG['url']
        else:
            page_url = f"{CONFIG['url']}?page={page}"
        
        logging.info(f"🔍 Sprawdzam stronę OLX: {page}")
        page_ads = check_olx_page(page_url)
        all_new_ads.extend(page_ads)
        
        # Małe opóźnienie między stronami żeby nie zablokować
        time.sleep(1)
    
    logging.info(f"📊 Znaleziono {len(all_new_ads)} nowych ogłoszeń z {CONFIG['max_pages']} stron")
    return all_new_ads

def monitoring_loop():
    """Główna pętla monitorowania 24/7"""
    logging.info("🟢 Uruchomiono monitoring OLX v3 - Losowe opóźnienia 2-7min + Alerty 8h + Status co 1h")
    
    while True:
        try:
            if CONFIG['active'] and DISCORD_WEBHOOK:
                ads = check_olx()
                
                if ads:
                    for ad in ads:
                        send_discord_notification(ad)
                    monitor_state.last_found_time = datetime.now()
                    monitor_state.consecutive_zero_count = 0
                    
                    # Zapisz seen_ads po znalezieniu nowych ogłoszeń
                    save_seen_ads()
                else:
                    monitor_state.consecutive_zero_count += 1
                
                # Sprawdź alerty i statusy
                check_8_hours_alert()
                check_hourly_status()
                
            # LOSOWE OPÓŹNIENIE 2-7 minut
            delay = get_random_delay()
            minutes = delay // 60
            seconds = delay % 60
            logging.info(f"⏰ Losowe opóźnienie: {minutes}min {seconds}s przed następnym skanem...")
            
            # Oczekiwanie z możliwością przerwania co sekundę
            for i in range(delay):
                time.sleep(1)
                    
        except Exception as e:
            logging.error(f"❌ Błąd pętli: {e}")
            # Krótsze oczekiwanie przy błędzie
            time.sleep(60)

# Strona główna - panel konfiguracyjny
@app.route('/')
def dashboard():
    return render_template_string(HTML_TEMPLATE, 
                                config=CONFIG, 
                                price_ranges=IPHONE_PRICE_RANGES,
                                seen_ads=seen_ads,
                                monitor_state=monitor_state,
                                DISCORD_WEBHOOK=DISCORD_WEBHOOK)

@app.route('/config', methods=['POST'])
def update_config():
    """Aktualizuje konfigurację przez formularz web"""
    global CONFIG
    
    try:
        # Pobierz aktywne modele z formularza
        CONFIG['active_models'] = request.form.getlist('active_models')
        
        # Pozostałe ustawienia
        keywords = request.form.get('keywords', '')
        CONFIG['keywords'] = [k.strip() for k in keywords.split(',') if k.strip()]
        
        blocked = request.form.get('blocked_keywords', '')
        CONFIG['blocked_keywords'] = [k.strip() for k in blocked.split(',') if k.strip()]
        
        CONFIG['max_ad_age_hours'] = int(request.form.get('max_ad_age_hours', 8760))
        CONFIG['max_pages'] = int(request.form.get('max_pages', 5))
        CONFIG['active'] = 'active' in request.form
        
        message = "✅ Konfiguracja zapisana! Bot v3 działa z losowymi opóźnieniami 2-7min."
        logging.info(f"🔧 Zaktualizowano konfigurację v3")
        
    except Exception as e:
        message = f"❌ Błąd: {e}"
        logging.error(message)
    
    return render_template_string(HTML_TEMPLATE, 
                                config=CONFIG, 
                                price_ranges=IPHONE_PRICE_RANGES,
                                message=message,
                                seen_ads=seen_ads,
                                monitor_state=monitor_state,
                                DISCORD_WEBHOOK=DISCORD_WEBHOOK)

# Uruchomienie aplikacji
if __name__ == '__main__':
    # Uruchom wątek monitorujący w tle
    monitor_thread = Thread(target=monitoring_loop)
    monitor_thread.daemon = True
    monitor_thread.start()
    
    # Uruchom serwer Flask
    port = int(os.getenv('PORT', 5000))
    logging.info(f"🌐 Serwer web uruchomiony na porcie {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
