import os
import base64
import time
import hashlib
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
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


def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id SERIAL PRIMARY KEY,
                    image_hash TEXT UNIQUE,
                    product_name TEXT,
                    analysis TEXT,
                    scan_count INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS user_products (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    product_id INTEGER REFERENCES products(id),
                    uses_product BOOLEAN,
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(user_id, product_id)
                );
            """)
        conn.commit()
    print("DB initialized")


def get_cached_analysis(image_hash):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, product_name, analysis FROM products WHERE image_hash = %s", (image_hash,))
                row = cur.fetchone()
                if row:
                    cur.execute("UPDATE products SET scan_count = scan_count + 1 WHERE image_hash = %s", (image_hash,))
                    conn.commit()
                return row
    except Exception as e:
        print(f"DB cache error: {e}")
        return None


def save_analysis(image_hash, product_name, analysis):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO products (image_hash, product_name, analysis) VALUES (%s, %s, %s) "
                    "ON CONFLICT (image_hash) DO NOTHING RETURNING id",
                    (image_hash, product_name, analysis)
                )
                row = cur.fetchone()
                conn.commit()
                if row:
                    return row["id"]
                cur.execute("SELECT id FROM products WHERE image_hash = %s", (image_hash,))
                return cur.fetchone()["id"]
    except Exception as e:
        print(f"DB save error: {e}")
        return None


def save_user_product(user_id, product_id, uses_product):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO user_products (user_id, product_id, uses_product) VALUES (%s, %s, %s) "
                    "ON CONFLICT (user_id, product_id) DO UPDATE SET uses_product = %s",
                    (user_id, product_id, uses_product, uses_product)
                )
                conn.commit()
    except Exception as e:
        print(f"DB user product error: {e}")


def tg(method, data=None):
    resp = requests.post(f"{API_URL}/{method}", json=data, verify=VERIFY)
    return resp.json()


def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    tg("sendMessage", payload)


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
            "model": "gpt-4o-mini",
            "max_tokens": 1000,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}", "detail": "low"}},
                {"type": "text", "text": ANALYSIS_PROMPT}
            ]}]
        },
        verify=VERIFY
    )
    return resp.json()["choices"][0]["message"]["content"]


def extract_product_name(analysis):
    for line in analysis.split("\n"):
        if "ПРОДУКТ" in line or "PRODUCT" in line.upper():
            return line.split(":", 1)[-1].strip()
    return "Неизвестный продукт"


def handle_start(msg):
    chat_id = msg["chat"]["id"]
    name = msg.get("from", {}).get("first_name", "")
    greeting = f"Привет, {name}! 👋" if name else "Привет! 👋"
    send_message(chat_id,
        greeting + "\n\nЯ — бот-косметолог 🧴\n"
        "Разбираю состав косметики и говорю честно.\n\n"
        "📸 Пришли фото продукта — расскажу:\n"
        "• Что за бренд и продукт\n"
        "• Оценка от 1 до 10 ⭐\n"
        "• Ключевые компоненты состава\n"
        "• Для какого типа волос / кожи подходит\n"
        "• Плюсы и минусы\n"
        "• Честный совет\n\n"
        "Отправляй фото — начнём! 🚀"
    )


def handle_photo(msg):
    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    send_message(chat_id, "🔍 Анализирую продукт, подожди немного...")
    try:
        photo = msg["photo"][-1]
        file_info = tg("getFile", {"file_id": photo["file_id"]})
        image_bytes = download_file(file_info["result"]["file_path"])
        image_hash = hashlib.md5(image_bytes).hexdigest()
        cached = get_cached_analysis(image_hash)
        if cached:
            analysis = cached["analysis"]
            product_id = cached["id"]
            send_message(chat_id, analysis)
            send_message(chat_id,
                "📦 Этот продукт уже в нашей базе — ответ из кэша ✅\n\nТы пользуешься этим продуктом?",
                reply_markup={"inline_keyboard": [[
                    {"text": "✅ Да", "callback_data": f"uses_yes_{product_id}"},
                    {"text": "❌ Нет", "callback_data": f"uses_no_{product_id}"}
                ]]}
            )
        else:
            analysis = analyze_image(image_bytes)
            product_name = extract_product_name(analysis)
            product_id = save_analysis(image_hash, product_name, analysis)
            send_message(chat_id, analysis)
            if product_id:
                send_message(chat_id,
                    "Ты пользуешься этим продуктом?",
                    reply_markup={"inline_keyboard": [[
                        {"text": "✅ Да", "callback_data": f"uses_yes_{product_id}"},
                        {"text": "❌ Нет", "callback_data": f"uses_no_{product_id}"}
                    ]]}
                )
    except Exception as e:
        print(f"Error analyzing: {e}")
        send_message(chat_id, "❌ Ошибка при анализе. Попробуй ещё раз.")


def handle_callback(callback):
    query_id = callback["id"]
    user_id = callback["from"]["id"]
    chat_id = callback["message"]["chat"]["id"]
    data = callback.get("data", "")
    tg("answerCallbackQuery", {"callback_query_id": query_id})
    if data.startswith("uses_yes_") or data.startswith("uses_no_"):
        uses = data.startswith("uses_yes_")
        product_id = int(data.split("_")[-1])
        save_user_product(user_id, product_id, uses)
        if uses:
            send_message(chat_id, "✅ Записал! Продукт добавлен в твою коллекцию 📚")
        else:
            send_message(chat_id, "👍 Понял, спасибо за ответ!")


def main():
    print("Initializing DB...")
    for attempt in range(10):
        try:
            init_db()
            break
        except Exception as e:
            print(f"DB connect attempt {attempt+1} failed: {e}")
            time.sleep(5)
    print("Bot started...")
    offset = None
    while True:
        try:
            params = {"timeout": 30, "allowed_updates": ["message", "callback_query"]}
            if offset:
                params["offset"] = offset
            resp = requests.get(f"{API_URL}/getUpdates", params=params, timeout=35, verify=VERIFY)
            data = resp.json()
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                if "callback_query" in update:
                    handle_callback(update["callback_query"])
                    continue
                msg = update.get("message", {})
                if not msg:
                    continue
                chat_id = msg.get("chat", {}).get("id")
                if not chat_id:
                    continue
                if msg.get("text") == "/start":
                    handle_start(msg)
                elif msg.get("photo"):
                    handle_photo(msg)
                elif msg.get("text"):
                    send_message(chat_id, "📸 Отправь фото косметики — я сделаю анализ!")
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(3)


if __name__ == "__main__":
    main()
