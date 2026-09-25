import requests
import xml.etree.ElementTree as ET
import os

# --- ЖЕСТКИЕ НАСТРОЙКИ ---
TELEGRAM_TOKEN = "8753776194:AAHzwXLTApxGh4J_LAgCGLneDcpd8aEnIsg"
TG_CHAT_ID = "-1004421613528"

# Берем только одну ленту для чистоты эксперимента
TEST_SOURCE_URL = "https://www.rbc.ru/rss/transport/"

def job():
    try:
        print("Пытаюсь скачать ленту...")
        response = requests.get(TEST_SOURCE_URL, timeout=10)
        
        if response.status_code != 200:
            message = f"❌ ОШИБКА САЙТА: Код {response.status_code}"
        else:
            root = ET.fromstring(response.text)
            
            # Ищем ВСЕ новости без фильтров
            items = root.findall('.//item')
            
            if not items:
                message = "🛑 Лента скачана успешно, но она ПУСТАЯ внутри."
            else:
                lines = ["📰 ТОП-5 новостей из ленты РБК Транспорт:"]
                for i, item in enumerate(items[:5], 1):
                    title = item.find('title').text.strip() if item.find('title') is not None else "Нет заголовка"
                    link = item.find('link').text.strip() if item.find('link') is not None else "Нет ссылки"
                    
                    lines.append(f"\n{i}. {title}")
                    lines.append(f"Ссылка: {link}")
                
                message = "\n".join(lines)

    except Exception as e:
        message = f"🔴 КРИТИЧЕСКАЯ ОШИБКА СКРИПТА:\n{e}"

    # Отправляем то, что увидели
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": message}
    
    try:
        resp = requests.post(api_url, params=payload, timeout=10)
        print(f"Статус отправки Telegram: {resp.status_code}")
    except Exception as e:
        print(f"Ошибка отправки сообщения: {e}")

if __name__ == "__main__":
    job()
