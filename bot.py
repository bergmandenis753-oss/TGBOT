import os
import base64
import time
import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
VERIFY = False

ANALYSIS_PROMPT = """Ты эксперт-косметолог и химик. Пользователь прислал фото косметического продукта.

Ответь по структуре:

🧴 ПРОДУКТ: [название и бренд если видно]

⭐ РЕЙТИНГ: [X/10] — одна фраза-вердикт

📋 СОСТАВ (ключевые компоненты):
- [компонент]: [для чего он, польза или вред]
(перечисли 5-8 самых важных)

✅ ПЛЮСЫ:
- ...

❌ МИНУСЫ / ПРЕДУПРЕЖДЕНИЯ:
- ...

👤 ПОДХОДИТ ДЛЯ:
- Тип волос/кожи: [сухие, жирные, нормальные, окрашенные...]
- НЕ подходит: [кому лучше избегать]

💡 СОВЕТ: [практическая рекомендация]

Будь честным — если продукт плохой, скажи об этом."""


def tg(method, data=None):
    resp = requests.post(f"{API_URL}/{method}", json=data, verify=VERIFY)
    return resp.json()


def download_file(file_path):
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    resp = requests.get(url, verify=VERIFY)
    return resp.content


def analyze_image(image_bytes):
    image_b64 = base64.standard_b64encode(image_bytes).decode()
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={
            "model": "gpt-4o",
            "max_tokens": 1500,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                {"type": "text", "text": ANALYSIS_PROMPT}
            ]}]
        },
        verify=VERIFY
    )
    return resp.json()["choices"][0]["message"]["content"]


def send_message(chat_id, text):
    tg("sendMessage", {"chat_id": chat_id, "text": text})


def main():
    print("Bot started...")
    offset = None
    while True:
        try:
            params = {"timeout": 30}
            if offset:
                params["offset"] = offset
            resp = requests.get(f"{API_URL}/getUpdates", params=params, timeout=35, verify=VERIFY)
            data = resp.json()
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                if not chat_id:
                    continue

                if msg.get("text") == "/start":
                    name = msg.get("from", {}).get("first_name", "")
                    greeting = f"Привет, {name}! 👋" if name else "Привет! 👋"
                    send_message(chat_id,
                        greeting + "\n\n"
                        "Я — бот-косметолог 🧴\n"
                        "Разбираю состав косметики и говорю честно — стоит ли она своих денег.\n\n"
                        "📸 Просто пришли фото продукта, и я расскажу:\n"
                        "• Что за бренд и продукт\n"
                        "• Оценка от 1 до 10 ⭐\n"
                        "• Ключевые компоненты состава\n"
                        "• Для какого типа волос / кожи подходит\n"
                        "• Плюсы и минусы\n"
                        "• Мой честный совет\n\n"
                        "Работает с любой косметикой:\n"
                        "шампунь, крем, маска, тушь, сыворотка — всё 💄\n\n"
                        "Отправляй фото — начнём! 🚀"
                    )

                elif msg.get("photo"):
                    send_message(chat_id, "🔍 Анализирую продукт, подожди немного...")
                    try:
                        photo = msg["photo"][-1]
                        file_info = tg("getFile", {"file_id": photo["file_id"]})
                        image_bytes = download_file(file_info["result"]["file_path"])
                        analysis = analyze_image(image_bytes)
                        send_message(chat_id, analysis)
                    except Exception as e:
                        print(f"Error analyzing: {e}")
                        send_message(chat_id, "❌ Ошибка. Попробуй ещё раз.")

                elif msg.get("text"):
                    send_message(chat_id, "📸 Отправь фото косметики — я сделаю анализ!")

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(3)


if __name__ == "__main__":
    main()
