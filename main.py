import requests
from bs4 import BeautifulSoup
import sqlite3
from datetime import datetime
import logging
import re

# --- 1. НАСТРОЙКА ---
TELEGRAM_TOKEN = "8753776194:AAHzwXLTApxGh4J_LAgCGLneDcpd8aEnIsg"
TG_CHAT_ID = "-1004421613528"

NEWS_SOURCES = [
    {"name": "РБК Транспорт", "url": "https://www.rbc.ru/tags/?tag=%D0%BB%D0%BE%D0%B3%D0%B8%D1%81%D1%82%D0%B8%D0%BA%D0%B0-%D0%B8-%D1%82%D1%80%D0%B0%D0%BD%D1%81%D0%BF%D0%BE%D1%80%D1%82"},
    {"name": "Коммерсантъ Транспорт", "url": "https://www.kommersant.ru/transport"}
]

KEYWORDS = ["санкц", "запрет", "ограничение", "таможн", "фрахт", "логист", "поставк", "границ", "перевозк", "контейнер", "дефицит", "эмбарго", "swift", "расчет", "конфликт", "закрыт", "блокад"]
DB_PATH = "news_archive.db"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS news 
                  (id INTEGER PRIMARY KEY, source TEXT, url TEXT UNIQUE, title TEXT, summary TEXT, published_date TEXT, added_date TEXT)''')
conn.commit()

def fetch_news_from_source(source):
    """Собирает статьи с одного источника."""
    articles = []
    base_url = ""
    if "rbc.ru" in source['url']: base_url = "https://www.rbc.ru"
    elif "kommersant.ru" in source['url']: base_url = "https://www.kommersant.ru"
    
    try:
        response = requests.get(source['url'], timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
        soup = BeautifulSoup(response.text, 'html.parser')
        
        if "rbc.ru" in source['url']:
            items = soup.find_all("a", class_="news-feed__item__link")
            for item in items:
                link = base_url + item['href']
                title = item.get_text(strip=True)
                articles.append({"title": title, "url": link})
        elif "kommersant.ru" in source['url']:
            items = soup.find_all("div", class_="article__preview")
            for item in items:
                t_elem = item.find("h2", class_="article__title").find("a")
                if t_elem:
                    articles.append({"title": t_elem.get_text(strip=True), "url": base_url + t_elem['href']})
    except Exception as e:
        logging.error(f"Ошибка доступа к {source['name']}: {e}")
    return articles

def is_new_article(url, title):
    """Проверяет по базе, была ли новость уже сохранена."""
    cursor.execute("SELECT 1 FROM news WHERE url=? OR title=?", (url, title))
    return not cursor.fetchone()

def analyze_impact(title, text):
    """Анализирует текст новости на предмет последствий."""
    impact_points = []
    t = text.lower()
    
    # Правила анализа под международную логистику
    if any(w in t for w in ["красн", "море", "йемен", "хус"]):
        impact_points.append("🔴 КРИТИЧНО: Атаки беспилотников в Красном море.")
        impact_points.append("- Рост ставок морского фрахта из Азии в 2-5 раз.")
        impact_points.append("- Суда перенаправляют вокруг Африки (+10-14 дней пути).")
        
    if any(w in t for w in ["свифт", "swift", "отключени", "банк корреспонд"]):
        impact_points.append("⚠️ ФИНАНСЫ: Проблемы с международными переводами.")
        impact_points.append("- Риск задержек оплаты поставщикам и линиям.")
        
    if any(w in t for w in ["санкц", "эмбарго"]) and ("росси" in t or "рф" in t):
        impact_points.append("🇷🇺 САНКЦИИ: Изменения списков подсанкционных товаров.")
        impact_points.append("- Проверьте свои коды ТН ВЭД на актуальность.")
        
    if "таможн" in t and ("грузин" in t or "турц" in t or "китай" in t):
        impact_points.append("🏛️ ТАМОЖНЯ: Возможны очереди на границах указанных стран.")
        impact_points.append("- Увеличение сроков таможенного оформления.")

    if not impact_points:
        impact_points.append("✅ Прямых угроз цепочкам поставок в данной новости не обнаружено.")
        
    return "\n".join(impact_points)

def send_telegram_report(items):
    """Формирует и отправляет отчет в Telegram."""
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    if not items:
        message = "📄 Аналитический отчет по логистике.\n\nСтатус: ✅ Новых критических рисков за неделю не зафиксировано."
    else:
        msg_parts = [f"🚛 <b>Еженедельный мониторинг логистики</b>\n"]
        msg_parts.append(f"<i>Дата формирования: {datetime.now().strftime('%d.%m.%Y')}</i>\n")
        
        for i, it in enumerate(items, 1):
            msg_parts.extend([
                f"\n{i}. <b>{it['title']}</b>",
                f"Ссылка: {it['url']}",
                "<b>Влияние на бизнес:</b>",
                it['analysis']
            ])
            
        message = "\n".join(msg_parts)
        
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    
    try:
        resp = requests.post(api_url, params=payload, timeout=10)
        if resp.status_code == 200:
            logging.info("SUCCESS: Отчет успешно доставлен в группу.")
        else:
            logging.error(f"CRITICAL FAIL: HTTP {resp.status_code} | Ответ сервера: {resp.text}")
            
    except Exception as e:
        logging.error(f"EXCEPTION during sending: {e}")

def job():
    """Основная задача сбора данных."""
    logging.info("Запуск еженедельной задачи...")
    all_articles = []
    
    # --- ЭТАП 1: СБОР ССЫЛОК ---
    for s in NEWS_SOURCES:
        try:
            r = requests.get(s['url'], timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
            soup = BeautifulSoup(r.text, 'html.parser')
            
            items = []
            if "rbc.ru" in s['url']:
                items = soup.find_all("a", class_="news-feed__item__link")
                base = "https://www.rbc.ru"
            elif "kommersant.ru" in s['url']:
                divs = soup.find_all("div", class_="article__preview")
                items = [{"title": d.find("h2").find("a").get_text(strip=True), "href": d.find("h2").find("a")['href']} for d in divs]
                base = "https://www.kommersant.ru"

            print(f"--- НАЙДЕНО {len(items)} статей на {s['name']} ---") # ДЕБАГ-ЛОГ
            for item in items:
                title = item['title'] if isinstance(item, dict) else item.get_text(strip=True)
                link = item['href'] if isinstance(item, dict) else base + item['href']
                
                # Фильтр по ключевым словам только в заголовке
                if any(re.search(kw, title.lower()) for kw in KEYWORDS):
                    all_articles.append({"title": title, "url": link})
                    print(f"ПОДОШЛА: {title}") # ДЕБАГ-ЛОГ
                    
        except Exception as e:
            logging.error(f"Ошибка доступа к {s['name']}: {e}")

    # Если нет подходящих статей - выходим раньше времени
    if not all_articles:
        logging.info("Статей с ключевыми словами не найдено.")
        send_telegram_report([]) # Отправит "Отчет чист"
        return 

    # --- ЭТАП 2: ПРОВЕРКА БАЗЫ И АНАЛИЗ ---
    report_items = []
    cursor.execute('''CREATE TABLE IF NOT EXISTS news (id INTEGER PRIMARY KEY, url TEXT UNIQUE)'''); conn.commit()
    
    for a in all_articles:
        # ВНИМАНИЕ: Здесь мы проверяем ТОЛЬКО URL, так как название может меняться
        cursor.execute("SELECT 1 FROM news WHERE url=?", (a['url'],))
        if cursor.fetchone():
            print(f"ДУБЛИКАТ ПРОПУЩЕН: {a['url']}")
            continue
            
        print(f"НОВАЯ СТАТЬЯ ОБРАБАТЫВАЕТСЯ: {a['title']}")
        
        try:
            r = requests.get(a['url'], timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
            soup = BeautifulSoup(r.text, 'html.parser')
            paragraphs = soup.find_all("p", class_="article__text__paragraph") if "rbc.ru" in a['url'] else soup.find_all("div", itemprop="articleBody")
            full_text = "\n".join([p.get_text(strip=True) for p in paragraphs])
            
            analysis = analyze_impact(a['title'], full_text)
            
            # Сохраняем ссылку, чтобы не прислать её дважды в будущем
            cursor.execute("INSERT INTO news VALUES (NULL, ?)", (a['url'],)); conn.commit()
            
            report_items.append({
                "title": a['title'],
                "url": a['url'],
                "analysis": analysis
            })
        except Exception as e:
            logging.error(f"Ошибка обработки текста {a['url']}: {e}")
            continue
            
    send_telegram_report(report_items)
    conn.close()
    logging.info("Работа завершена.")

if __name__ == "__main__":
    job()
