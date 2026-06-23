import os
import base64
import time
import hashlib
import datetime
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

FREE_SCANS = 1
STARS_PRICE = 100
SUB_DAYS = 30

ANALYSIS_PROMPT = "Ty ekspert-kosmetolog. Otvet po strukture: PRODUKT: [nazvanie] REJTING: [X/10] SOSTAV: - komponenty PLYUSY: ... MINUSY: ... PODHODIT: ... SOVET: ..."


def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id SERIAL PRIMARY KEY, image_hash TEXT UNIQUE,
                    product_name TEXT, analysis TEXT,
                    scan_count INTEGER DEFAULT 1, created_at TIMESTAMP DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS user_products (
                    id SERIAL PRIMARY KEY, user_id BIGINT,
                    product_id INTEGER REFERENCES products(id),
                    uses_product BOOLEAN, created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(user_id, product_id)
                );
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY, free_scans_used INTEGER DEFAULT 0,
                    subscription_until TIMESTAMP, created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            # Migrate old DB - add columns if missing
            cur.execute("""
                ALTER TABLE users ADD COLUMN IF NOT EXISTS free_scans_used INTEGER DEFAULT 0;
                ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_until TIMESTAMP;
            """)
        conn.commit()
    print("DB initialized")


def get_user(user_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            if not row:
                cur.execute("INSERT INTO users (user_id) VALUES (%s) RETURNING *", (user_id,))
                row = cur.fetchone()
                conn.commit()
            return row


def has_active_sub(user):
    return user["subscription_until"] and user["subscription_until"] > datetime.datetime.now()


def user_can_scan(user_id):
    user = get_user(user_id)
    if has_active_sub(user):
        return True
    return user["free_scans_used"] < FREE_SCANS


def increment_free_scans(user_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET free_scans_used = free_scans_used + 1 WHERE user_id = %s", (user_id,))
            conn.commit()


def activate_subscription(user_id):
    until = datetime.datetime.now() + datetime.timedelta(days=SUB_DAYS)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET subscription_until = %s WHERE user_id = %s", (until, user_id))
            conn.commit()
    return until


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
                    "INSERT INTO products (image_hash, product_name, analysis) VALUES (%s, %s, %s) ON CONFLICT (image_hash) DO NOTHING RETURNING id",
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
                    "INSERT INTO user_products (user_id, product_id, uses_product) VALUES (%s, %s, %s) ON CONFLICT (user_id, product_id) DO UPDATE SET uses_product = %s",
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


def send_invoice(chat_id, user_id):
    tg("sendInvoice", {
        "chat_id": chat_id,
        "title": "Podpiska na 30 dnej",
        "description": "Bezlimitnyj analiz kosmetiki na 30 dnej. Chestnyj otzyv, sostav, rejting.",
        "payload": f"sub_{user_id}",
        "currency": "XTR",
        "prices": [{"label": "Podpiska 30 dnej", "amount": STARS_PRICE}],
    })


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
        if "PRODUKT" in line.upper() or "PRODUCT" in line.upper():
            return line.split(":", 1)[-1].strip()
    return "Unknown"


def handle_start(msg):
    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    name = msg.get("from", {}).get("first_name", "")
    get_user(user_id)
    greeting = f"Privet, {name}! ð" if name else "Privet! ð"
    send_message(chat_id,
        greeting + "\n\nYa - bot-kosmetolog ð§´\n"
        "Razbiraju sostav kosmetiki i govorju chestno.\n\n"
        "ð¸ Prosti foto produkta - rasskazhu:\n"
        "- Brend i nazvanie\n- Ocenka 1-10 â­\n"
        "- Sostav i komponenty\n- Dlya kakogo tipa volos/kozhi\n"
        "- Plyusy i minusy\n- Chestnyj sovet\n\n"
        f"ð U tebya est {FREE_SCANS} besplatnyj analiz.\n"
        f"Dalee - podpiska {STARS_PRICE} â­ v mesyac.\n\n"
        "Otpravlyaj foto - nachnem! ð"
    )


def handle_photo(msg):
    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    if not user_can_scan(user_id):
        send_message(chat_id,
            f"ð Besplatnyj analiz ispol'zovan.\n\n"
            f"Oformi podpisku za {STARS_PRICE} â­ v mesyac - skaniraj bez ogranichenij! ð"
        )
        send_invoice(chat_id, user_id)
        return
    send_message(chat_id, "ð Analiziruyu produkt, podozdi nemnogo...")
    try:
        photo = msg["photo"][-1]
        file_info = tg("getFile", {"file_id": photo["file_id"]})
        image_bytes = download_file(file_info["result"]["file_path"])
        image_hash = hashlib.md5(image_bytes).hexdigest()
        user = get_user(user_id)
        if not has_active_sub(user):
            increment_free_scans(user_id)
        cached = get_cached_analysis(image_hash)
        if cached:
            send_message(chat_id, cached["analysis"])
            send_message(chat_id,
                "ð¦ Produkt uzhe v baze - otvet iz kesha â\n\nTy polzueshsya etim produktom?",
                reply_markup={"inline_keyboard": [[{"text": "â Da", "callback_data": f"uses_yes_{cached['id']}"}, {"text": "â Net", "callback_data": f"uses_no_{cached['id']}"}]]}
            )
        else:
            analysis = analyze_image(image_bytes)
            product_id = save_analysis(image_hash, extract_product_name(analysis), analysis)
            send_message(chat_id, analysis)
            if product_id:
                send_message(chat_id, "Ty polzueshsya etim produktom?",
                    reply_markup={"inline_keyboard": [[{"text": "â Da", "callback_data": f"uses_yes_{product_id}"}, {"text": "â Net", "callback_data": f"uses_no_{product_id}"}]]}
                )
    except Exception as e:
        print(f"Error: {e}")
        send_message(chat_id, "â Oshibka. Poprobuj eshche raz.")


def handle_callback(callback):
    tg("answerCallbackQuery", {"callback_query_id": callback["id"]})
    user_id = callback["from"]["id"]
    chat_id = callback["message"]["chat"]["id"]
    data = callback.get("data", "")
    if data.startswith("uses_yes_") or data.startswith("uses_no_"):
        uses = data.startswith("uses_yes_")
        product_id = int(data.split("_")[-1])
        save_user_product(user_id, product_id, uses)
        send_message(chat_id, "â Zapisal v kollekciju! ð" if uses else "ð Ponyatno, spasibo!")


def handle_pre_checkout(pq):
    tg("answerPreCheckoutQuery", {"pre_checkout_query_id": pq["id"], "ok": True})


def handle_successful_payment(msg):
    user_id = msg["from"]["id"]
    chat_id = msg["chat"]["id"]
    until = activate_subscription(user_id)
    send_message(chat_id, f"ð Oplata proshla! Podpiska aktivna do {until.strftime('%d.%m.%Y')}.\n\nSkaniraj bez ogranichenij! ð¸")


def main():
    print("Initializing DB...")
    for attempt in range(10):
        try:
            init_db()
            break
        except Exception as e:
            print(f"DB attempt {attempt+1} failed: {e}")
            time.sleep(5)
    print("Bot started...")
    offset = None
    while True:
        try:
            params = {"timeout": 30, "allowed_updates": ["message", "callback_query", "pre_checkout_query"]}
            if offset:
                params["offset"] = offset
            resp = requests.get(f"{API_URL}/getUpdates", params=params, timeout=35, verify=VERIFY)
            data = resp.json()
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                if "pre_checkout_query" in update:
                    handle_pre_checkout(update["pre_checkout_query"])
                elif "callback_query" in update:
                    handle_callback(update["callback_query"])
                else:
                    msg = update.get("message", {})
                    if not msg:
                        continue
                    chat_id = msg.get("chat", {}).get("id")
                    if not chat_id:
                        continue
                    if msg.get("successful_payment"):
                        handle_successful_payment(msg)
                    elif msg.get("text") == "/start":
                        handle_start(msg)
                    elif msg.get("text") == "/subscribe":
                        send_invoice(chat_id, msg["from"]["id"])
                    elif msg.get("photo"):
                        handle_photo(msg)
                    elif msg.get("text"):
                        send_message(chat_id, "ð¸ Otprav foto kosmetiki!")
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(3)


if __name__ == "__main__":
    main()
