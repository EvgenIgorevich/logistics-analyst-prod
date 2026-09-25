import os
import re
import logging
import sqlite3
from datetime import datetime

import requests
from bs4 import BeautifulSoup

# --- 1. НАСТРОЙКА ---
# Токен и chat_id теперь берутся из переменных окружения (GitHub Secrets),
# а не хранятся в коде. В workflow .yml добавьте:
#   env:
#     TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
#     TG_CHAT_ID: ${{ secrets.TG_CHAT_ID }}
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")

# ВАЖНО: эти URL нужно периодически проверять вручную (открыть в браузере).
# Новостные сайты меняют структуру разделов без предупреждения —
# именно из-за этого отчёт приходил пустым: оба старых URL отдавали 404.
# Ниже — общие ленты сайтов (более стабильны, чем тематические теги/разделы,
# которые часто переименовывают или удаляют).
NEWS_SOURCES = [
    {"name": "РБК", "url": "https://www.rbc.ru/", "type": "rbc"},
    {"name": "Коммерсантъ", "url": "https://www.kommersant.ru/", "type": "kommersant"},
]

KEYWORDS = [
    "санкц", "запрет", "ограничени", "таможн", "фрахт", "логист", "поставк",
    "границ", "перевозк", "контейнер", "дефицит", "эмбарго", "swift", "свифт",
    "расчет", "расчёт", "конфликт", "закрыт", "блокад", "пошлин", "налог",
    "маршрут", "порт", "терминал", "санкционн",
]

DB_PATH = "/tmp/logistics_news.db"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS news
                  (id INTEGER PRIMARY KEY, source TEXT, url TEXT UNIQUE, title TEXT,
                   summary TEXT, published_date TEXT, added_date TEXT)''')
conn.commit()


def fetch_news_from_source(source):
    """Собирает статьи с одного источника. Возвращает список статей и логирует диагностику."""
    articles = []
    base_url = "https://www.rbc.ru" if source["type"] == "rbc" else "https://www.kommersant.ru"

    try:
        response = requests.get(source['url'], timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
        logging.info(f"[{source['name']}] HTTP статус: {response.status_code}, размер ответа: {len(response.text)} байт")

        if response.status_code != 200:
            logging.error(f"[{source['name']}] Источник вернул код {response.status_code} — страница недоступна или URL устарел.")
            return articles

        soup = BeautifulSoup(response.text, 'html.parser')

        if source["type"] == "rbc":
            # Основные карточки на главной РБК
            items = soup.find_all("a", class_=re.compile(r"main__feed__link|news-feed__item__link"))
            for item in items:
                href = item.get('href', '')
                if not href:
                    continue
                link = href if href.startswith('http') else base_url + href
                title = item.get_text(strip=True)
                if title:
                    articles.append({"title": title, "url": link})

        elif source["type"] == "kommersant":
            items = soup.find_all("a", class_=re.compile(r"uho__link|article__preview|rubric_lenta__item_link"))
            for item in items:
                href = item.get('href', '')
                if not href:
                    continue
                link = href if href.startswith('http') else base_url + href
                title = item.get_text(strip=True)
                if title:
                    articles.append({"title": title, "url": link})

        logging.info(f"[{source['name']}] Найдено статей на странице: {len(articles)}")
        if len(articles) == 0:
            logging.warning(
                f"[{source['name']}] 0 статей — вероятно, изменилась HTML-разметка сайта. "
                f"Откройте {source['url']} в браузере, найдите блок с новостной лентой "
                f"и обновите CSS-селектор (class_=re.compile(...)) в fetch_news_from_source()."
            )

    except Exception as e:
        logging.error(f"[{source['name']}] Ошибка доступа: {e}")

    return articles


def is_new_article(url, title):
    cursor.execute("SELECT 1 FROM news WHERE url=? OR title=?", (url, title))
    return not cursor.fetchone()


def analyze_impact(title, text):
    impact_points = []
    t = (title + " " + text).lower()

    if any(w in t for w in ["красн", "море", "йемен", "хус"]):
        impact_points.append("🔴 КРИТИЧНО: возможны атаки/риски в Красном море.")
        impact_points.append("- Риск роста ставок морского фрахта из Азии.")
        impact_points.append("- Возможны маршруты в обход Африки (+10-14 дней пути).")

    if any(w in t for w in ["свифт", "swift", "отключени", "банк корреспонд"]):
        impact_points.append("⚠️ ФИНАНСЫ: проблемы с международными переводами.")
        impact_points.append("- Риск задержек оплаты поставщикам и линиям.")

    if any(w in t for w in ["санкц", "эмбарго"]) and ("росси" in t or "рф" in t):
        impact_points.append("🇷🇺 САНКЦИИ: изменения списков подсанкционных товаров.")
        impact_points.append("- Проверьте коды ТН ВЭД на актуальность.")

    if "таможн" in t and any(c in t for c in ["грузин", "турц", "китай", "казахстан", "эмират", "оаэ"]):
        impact_points.append("🏛️ ТАМОЖНЯ: возможны очереди/изменения на границах указанных стран.")
        impact_points.append("- Увеличение сроков таможенного оформления.")

    if "пошлин" in t or "налог" in t:
        impact_points.append("💰 НАЛОГИ/ПОШЛИНЫ: возможное изменение стоимости импорта/экспорта.")

    if not impact_points:
        impact_points.append("✅ Прямых угроз цепочкам поставок в данной новости не обнаружено.")

    return "\n".join(impact_points)


def send_telegram_message(text):
    if not TELEGRAM_TOKEN or not TG_CHAT_ID:
        logging.error("TELEGRAM_TOKEN или TG_CHAT_ID не заданы (переменные окружения пустые).")
        return
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(api_url, params=payload, timeout=10)
        if resp.status_code == 200:
            logging.info("SUCCESS: сообщение доставлено в группу.")
        else:
            logging.error(f"CRITICAL FAIL: HTTP {resp.status_code} | Ответ сервера: {resp.text}")
    except Exception as e:
        logging.error(f"EXCEPTION при отправке: {e}")


def send_telegram_report(items, total_sources, total_raw_articles):
    if not items:
        # Раньше здесь было тихое "всё хорошо" — теперь показываем диагностику,
        # чтобы поломку источника было видно сразу из отчёта, а не только из логов Actions.
        message = (
            "📄 <b>Аналитический отчет по логистике</b>\n\n"
            f"Источников проверено: {total_sources}\n"
            f"Всего статей собрано: {total_raw_articles}\n"
            f"Релевантных новых новостей: 0\n\n"
        )
        if total_raw_articles == 0:
            message += "⚠️ Все источники вернули 0 статей — вероятно, сломались селекторы или сайты недоступны. Проверьте логи GitHub Actions."
        else:
            message += "Статус: ✅ Новых критических рисков за период не зафиксировано."
    else:
        msg_parts = [f"🚛 <b>Еженедельный мониторинг логистики</b>\n"]
        msg_parts.append(f"<i>Дата формирования: {datetime.now().strftime('%d.%m.%Y')}</i>\n")
        for i, it in enumerate(items, 1):
            msg_parts.extend([
                f"\n{i}. <b>{it['title']}</b>",
                f"Ссылка: {it['url']}",
                "<b>Влияние на бизнес:</b>",
                it['analysis'],
            ])
        message = "\n".join(msg_parts)

    send_telegram_message(message)


def job():
    logging.info("Запуск задачи мониторинга...")
    all_articles = []
    for s in NEWS_SOURCES:
        all_articles.extend(fetch_news_from_source(s))

    total_raw = len(all_articles)
    logging.info(f"Всего статей собрано со всех источников: {total_raw}")

    report_items = []
    for a in all_articles:
        if not is_new_article(a['url'], a['title']):
            continue

        try:
            r = requests.get(a['url'], timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, 'html.parser')

            paragraphs = []
            if "rbc.ru" in a['url']:
                paragraphs = soup.find_all("p", class_=re.compile(r"article__text__paragraph|article__text"))
            elif "kommersant.ru" in a['url']:
                paragraphs = soup.find_all("div", itemprop="articleBody")

            full_text = "\n".join([p.get_text(strip=True) for p in paragraphs])

            # Фильтр по ключевым словам теперь проверяет заголовок И текст статьи,
            # а не только заголовок — иначе релевантные новости без "триггерного"
            # слова в заголовке молча отбрасывались.
            combined = (a['title'] + " " + full_text).lower()
            if not any(re.search(kw, combined) for kw in KEYWORDS):
                continue

            analysis = analyze_impact(a['title'], full_text)

            cursor.execute(
                "INSERT INTO news VALUES (NULL, ?, ?, ?, ?, ?, ?)",
                (a['url'].split('/')[2], a['url'], a['title'], full_text[:500],
                 datetime.now().isoformat(), datetime.now().isoformat()),
            )
            conn.commit()

            report_items.append({"title": a['title'], "url": a['url'], "analysis": analysis})
        except Exception as e:
            logging.error(f"Ошибка обработки статьи {a['url']}: {e}")
            continue

    send_telegram_report(report_items, len(NEWS_SOURCES), total_raw)
    conn.close()
    logging.info("Работа завершена.")


if __name__ == "__main__":
    job()
