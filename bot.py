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

# ─── Квиз ──────────────────────────────────────────────────

QUIZ_STEPS = [
    {
        "key": "goal",
        "block": "Блок I · Цель",
        "question": (
            "Что вы хотите изменить или улучшить в своих волосах?\n\n"
            "Напишите своими словами — например:\n"
            "· Хочу быстрее отрастить волосы\n"
            "· Хочу сделать волосы гуще\n"
            "· Хочу избавиться от жирности\n"
            "· Хочу научиться пользоваться стайлерами"
        ),
        "type": "text"
    },
    {
        "key": "problem",
        "block": "Блок II · Проблемы",
        "question": (
            "Что вас сейчас больше всего беспокоит в ваших волосах?\n\n"
            "Напишите своими словами — например:\n"
            "· Волосы быстро становятся грязными\n"
            "· Есть перхоть\n"
            "· Волосы выпадают\n"
            "· Волосы сухие и ломкие\n"
            "· Не получается сделать укладку"
        ),
        "type": "text"
    },
    {
        "key": "age",
        "block": "Блок III · Ваш профиль",
        "question": "Сколько вам лет?",
        "type": "text"
    },
    {
        "key": "gender",
        "block": "Блок III · Ваш профиль",
        "question": "Ваш пол?",
        "type": "buttons",
        "options": ["Женский", "Мужской", "Предпочитаю не указывать"]
    },
    {
        "key": "country",
        "block": "Блок III · Ваш профиль",
        "question": (
            "В какой стране вы живёте?\n\n"
            "◦ Мы учитываем климат, жёсткость воды\n"
            "◦ и доступность средств в вашем регионе"
        ),
        "type": "text"
    },
    {
        "key": "city",
        "block": "Блок III · Ваш профиль",
        "question": "В каком городе вы живёте?",
        "type": "text"
    },
    {
        "key": "wash_frequency",
        "block": "Блок IV · Повседневные привычки",
        "question": "Как часто вы моете голову?",
        "type": "buttons",
        "options": ["Каждый день", "Через день", "2–3 раза в неделю", "Реже"]
    },
    {
        "key": "styling_time",
        "block": "Блок IV · Повседневные привычки",
        "question": (
            "Сколько времени обычно занимает ваша укладка?\n\n"
            "◦ Это помогает нам понять ваши привычки\n"
            "◦ и подобрать подходящие рекомендации"
        ),
        "type": "buttons",
        "options": ["Не укладываю", "До 5 минут", "5–15 минут", "Более 15 минут"]
    },
]

# ─── База данных ───────────────────────────────────────────

def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    quiz_step INTEGER DEFAULT 0,
                    quiz_done BOOLEAN DEFAULT FALSE,
                    goal TEXT,
                    problem TEXT,
                    age TEXT,
                    gender TEXT,
                    country TEXT,
                    city TEXT,
                    wash_frequency TEXT,
                    styling_time TEXT,
                    hair_analysis TEXT,
                    final_report TEXT,
                    awaiting TEXT DEFAULT 'none',
                    created_at TIMESTAMP DEFAULT NOW()
                );
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
            # Миграция — добавляем колонки если нет
            for col, coltype in [
                ("username", "TEXT"),
                ("first_name", "TEXT"),
                ("quiz_step", "INTEGER DEFAULT 0"),
                ("quiz_done", "BOOLEAN DEFAULT FALSE"),
                ("goal", "TEXT"),
                ("problem", "TEXT"),
                ("age", "TEXT"),
                ("gender", "TEXT"),
                ("country", "TEXT"),
                ("city", "TEXT"),
                ("wash_frequency", "TEXT"),
                ("styling_time", "TEXT"),
                ("hair_analysis", "TEXT"),
                ("final_report", "TEXT"),
                ("awaiting", "TEXT DEFAULT 'none'"),
            ]:
                try:
                    cur.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {coltype};")
                except Exception:
                    pass
        conn.commit()
    print("DB initialized")


def get_user(user_id, username=None, first_name=None):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            if not row:
                cur.execute(
                    "INSERT INTO users (user_id, username, first_name, quiz_step, quiz_done) VALUES (%s, %s, %s, 0, FALSE) RETURNING *",
                    (user_id, username, first_name)
                )
                row = cur.fetchone()
                conn.commit()
            return row


def update_user(user_id, **kwargs):
    if not kwargs:
        return
    fields = ", ".join(f"{k} = %s" for k in kwargs)
    values = list(kwargs.values()) + [user_id]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE users SET {fields} WHERE user_id = %s", values)
            conn.commit()


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


def get_user_products_used(user_id):
    """Список названий средств, которыми пользователь отметил, что пользуется."""
    if not user_id:
        return []
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT p.product_name FROM user_products up "
                    "JOIN products p ON p.id = up.product_id "
                    "WHERE up.user_id = %s AND up.uses_product = TRUE",
                    (user_id,)
                )
                return [r["product_name"] for r in cur.fetchall() if r.get("product_name")]
    except Exception as e:
        print(f"DB products used error: {e}")
        return []


# ─── Telegram ──────────────────────────────────────────────

def tg(method, data=None):
    resp = requests.post(f"{API_URL}/{method}", json=data, verify=VERIFY)
    return resp.json()


def send_message(chat_id, text, reply_markup=None, parse_mode=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if parse_mode:
        payload["parse_mode"] = parse_mode
    tg("sendMessage", payload)


def send_quiz_question(chat_id, step_index):
    step = QUIZ_STEPS[step_index]
    total = len(QUIZ_STEPS)
    progress = "·" * (step_index + 1) + "○" * (total - step_index - 1)

    header = f"{step['block']}\n{progress}  {step_index + 1} из {total}\n\n"
    text = header + step["question"]

    if step["type"] == "buttons":
        keyboard = [[{"text": opt, "callback_data": f"quiz_{step['key']}_{i}"}]
                    for i, opt in enumerate(step["options"])]
        send_message(chat_id, text, reply_markup={"inline_keyboard": keyboard})
    else:
        send_message(chat_id, text)


def download_file(file_path):
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    resp = requests.get(url, verify=VERIFY)
    return resp.content


# ─── OpenAI ────────────────────────────────────────────────

def build_profile_text(user, include_hair=False, include_products=False):
    """Собирает текстовый профиль пользователя для промтов."""
    profile = ""
    if user.get("goal"):
        profile += f"Цель пользователя: {user['goal']}\n"
    if user.get("problem"):
        profile += f"Проблема с волосами: {user['problem']}\n"
    if user.get("age"):
        profile += f"Возраст: {user['age']}\n"
    if user.get("gender"):
        profile += f"Пол: {user['gender']}\n"
    if user.get("country") or user.get("city"):
        location = ", ".join(filter(None, [user.get("city"), user.get("country")]))
        profile += f"Местоположение: {location}\n"
    if user.get("wash_frequency"):
        profile += f"Частота мытья головы: {user['wash_frequency']}\n"
    if user.get("styling_time"):
        profile += f"Время на укладку: {user['styling_time']}\n"
    if include_hair and user.get("hair_analysis"):
        profile += f"\nХарактеристика волос (по фото):\n{user['hair_analysis']}\n"
    if include_products:
        products = get_user_products_used(user.get("user_id"))
        if products:
            profile += "\nСредства, которыми пользуется человек:\n"
            for p in products:
                profile += f"· {p}\n"
    return profile


def build_analysis_prompt(user):
    profile = build_profile_text(user)
    profile_block = f"\nПрофиль пользователя:\n{profile}\n" if profile else ""

    return f"""Ты — элегантный персональный эксперт по уходу за волосами и красоте. Твой стиль — дорогой, женственный, заботливый. Никаких грубых слов, только тёплые рекомендации.{profile_block}
Пользователь прислал фото косметического продукта. Проанализируй его и ответь строго по структуре ниже. Используй только эти символы для оформления: ✦ — · ◦. Никаких ❌ или ✅.

✦ ПРОДУКТ
— Название и бренд (если видно)

✦ ОЦЕНКА
— Рейтинг: X / 10
— Одна фраза-вердикт

✦ СОСТАВ · ключевые компоненты
— Компонент: для чего, польза или осторожность
(5–7 самых важных)

✦ ДОСТОИНСТВА
— ...

✦ НА ЧТО ОБРАТИТЬ ВНИМАНИЕ
— ...

✦ КОМУ ПОДОЙДЁТ
— Тип волос / кожи
— Кому лучше избегать

✦ ЛИЧНЫЙ СОВЕТ
— Персональная рекомендация с учётом профиля пользователя

Будь честна — если продукт не стоит своих денег, скажи об этом мягко, но прямо."""


def analyze_image(image_bytes, user=None):
    image_b64 = base64.standard_b64encode(image_bytes).decode()
    prompt = build_analysis_prompt(user or {})
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={
            "model": "gpt-4o-mini",
            "max_tokens": 1200,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}", "detail": "low"}},
                {"type": "text", "text": prompt}
            ]}]
        },
        verify=VERIFY
    )
    return resp.json()["choices"][0]["message"]["content"]


def extract_product_name(analysis):
    for line in analysis.split("\n"):
        if "ПРОДУКТ" in line.upper():
            continue
        if "—" in line and len(line) > 5:
            name = line.replace("—", "").strip()
            if name and len(name) < 80:
                return name
    return "Продукт"


def analyze_hair(image_bytes):
    """GPT описывает структуру и особенности волос по фото/селфи."""
    image_b64 = base64.standard_b64encode(image_bytes).decode()
    prompt = (
        "Ты — деликатный эксперт-трихолог. На фото волосы или селфи человека. "
        "Кратко и тактично опиши то, что видно о волосах: примерный тип (прямые/волнистые/кудрявые), "
        "густоту, состояние (блеск, сухость, ломкость, пушистость), длину, видимые особенности кожи головы. "
        "Если чего-то не видно — не выдумывай. Пиши спокойно и по делу, без оценок внешности человека, "
        "только характеристики волос. 4–6 коротких строк, оформление — символами · и —, без ✅ и ❌."
    )
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={
            "model": "gpt-4o-mini",
            "max_tokens": 500,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}", "detail": "low"}},
                {"type": "text", "text": prompt}
            ]}]
        },
        verify=VERIFY
    )
    return resp.json()["choices"][0]["message"]["content"]


def generate_final_report(user):
    """Итоговый персональный разбор по всей базе пользователя."""
    profile = build_profile_text(user, include_hair=True, include_products=True)
    prompt = f"""Ты — элегантный личный эксперт по волосам. Твой стиль — дорогой, женственный, заботливый, поддерживающий. Только тёплые формулировки, никаких грубых слов.

Вот всё, что мы знаем о человеке:
{profile}

Составь персональный разбор и план, чтобы помочь достичь её цели. Опирайся на её профиль, привычки, состояние волос и средства, которыми она пользуется (если есть — отметь, что стоит оставить, а что заменить). Отвечай только про волосы и связанное с ними здоровье.

Структура ответа (оформление только символами ✦ — · ◦, без ✅ и ❌):

✦ КОРОТКО
— Тёплое резюме ситуации в 2–3 строки

✦ ЧТО ПРОИСХОДИТ С ВОЛОСАМИ
— Разбор причин на основе профиля

✦ ПЛАН · УХОД
— Конкретные шаги по уходу и мытью

✦ ПЛАН · СРЕДСТВА
— Что искать в составе, что использовать; с учётом её средств

✦ ПЛАН · ПИТАНИЕ И ЗДОРОВЬЕ
— Что важно для волос: жиры, белок, коллаген, витамины и т.д.

✦ ПЕРВЫЙ ШАГ
— Один простой шаг, с которого начать уже сегодня

Будь конкретной и честной, но мягкой."""
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={
            "model": "gpt-4o-mini",
            "max_tokens": 1500,
            "messages": [{"role": "user", "content": prompt}]
        },
        verify=VERIFY
    )
    return resp.json()["choices"][0]["message"]["content"]


# ─── Обработчики ───────────────────────────────────────────

def handle_start(msg):
    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    username = msg["from"].get("username", "")
    first_name = msg["from"].get("first_name", "")

    name = first_name or "дорогая"

    send_message(chat_id,
        f"Добро пожаловать, {name} ✦\n\n"
        "Я — ваш личный эксперт по красоте и уходу за волосами.\n\n"
        "Моя миссия — исполнять ваши мечты о красивых, здоровых волосах. "
        "Я разбираю составы косметики, помогаю решить проблемы с волосами, "
        "подбираю уход под ваш тип и климат, и всегда говорю честно.\n\n"
        "◦ Анализ состава любого средства\n"
        "◦ Персональные рекомендации по уходу\n"
        "◦ Советы по укладке и восстановлению\n"
        "◦ Помощь с выбором средств в вашем регионе\n\n"
        "Прежде чем начать — позвольте узнать вас чуть лучше.\n"
        "Небольшой опрос займёт всего пару минут ◦"
    )

    # ПРИНУДИТЕЛЬНО сбрасываем квиз БЕЗ update_user
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # Сначала проверим есть ли пользователь
                cur.execute("SELECT user_id FROM users WHERE user_id = %s", (user_id,))
                exists = cur.fetchone()

                if exists:
                    # Обновляем если есть
                    cur.execute(
                        "UPDATE users SET quiz_step = 0, quiz_done = FALSE, username = %s, first_name = %s WHERE user_id = %s",
                        (username, first_name, user_id)
                    )
                else:
                    # Создаём если нет
                    cur.execute(
                        "INSERT INTO users (user_id, username, first_name, quiz_step, quiz_done) VALUES (%s, %s, %s, 0, FALSE)",
                        (user_id, username, first_name)
                    )
                conn.commit()
    except Exception as e:
        print(f"Error resetting quiz: {e}")

    time.sleep(1)

    # Отправляем первый вопрос квиза
    send_quiz_question(chat_id, 0)


def handle_quiz_text_answer(msg, user):
    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    step_index = user["quiz_step"]

    if step_index >= len(QUIZ_STEPS):
        return False

    step = QUIZ_STEPS[step_index]
    if step["type"] != "text":
        return False

    # Сохраняем ответ
    update_user(user_id, **{step["key"]: msg["text"]})

    # Следующий шаг
    next_step = step_index + 1
    if next_step >= len(QUIZ_STEPS):
        finish_quiz(chat_id, user_id)
    else:
        update_user(user_id, quiz_step=next_step)
        send_quiz_question(chat_id, next_step)

    return True


def handle_quiz_callback(callback, user):
    query_id = callback["id"]
    user_id = callback["from"]["id"]
    chat_id = callback["message"]["chat"]["id"]
    data = callback.get("data", "")

    tg("answerCallbackQuery", {"callback_query_id": query_id})

    # Защита от повторного нажатия: если квиз уже завершён — игнорируем.
    fresh = get_user(user_id)
    if fresh.get("quiz_done"):
        return False

    # data = "quiz_KEY_INDEX" — KEY может содержать подчёркивания (wash_frequency, styling_time),
    # поэтому парсим с конца: последний сегмент — индекс, всё между "quiz_" и ним — ключ.
    if not data.startswith("quiz_"):
        return False
    body = data[len("quiz_"):]
    key, sep, index_str = body.rpartition("_")
    if not sep or not index_str.isdigit():
        return False
    option_index = int(index_str)

    # Находим шаг по key
    step = next((s for s in QUIZ_STEPS if s["key"] == key), None)
    if not step or step["type"] != "buttons":
        return False

    value = step["options"][option_index]
    update_user(user_id, **{key: value})

    step_index = QUIZ_STEPS.index(step)
    next_step = step_index + 1

    if next_step >= len(QUIZ_STEPS):
        finish_quiz(chat_id, user_id)
    else:
        update_user(user_id, quiz_step=next_step)
        send_quiz_question(chat_id, next_step)

    return True


def finish_quiz(chat_id, user_id):
    update_user(user_id, quiz_done=True, quiz_step=len(QUIZ_STEPS), awaiting="hair_photo")
    send_message(chat_id,
        "✦ Профиль создан\n\n"
        "Благодарю за ответы — теперь я знаю вас лучше.\n\n"
        "Чтобы разбор был точнее, пришлите фото своих волос или селфи — "
        "я оценю их структуру и особенности. Это по желанию.\n\n"
        "◦ Отправьте фото — или нажмите «Пропустить»",
        reply_markup={"inline_keyboard": [[
            {"text": "Пропустить", "callback_data": "skip_hair"}
        ]]}
    )


def deliver_final_report(chat_id, user_id):
    """Генерирует и отправляет итоговый персональный разбор по всей базе."""
    update_user(user_id, awaiting="none")
    send_message(chat_id,
        "✦ Готовлю ваш персональный разбор...\n\n"
        "◦ Изучаю профиль\n"
        "◦ Сверяю привычки и средства\n"
        "◦ Составляю план"
    )
    try:
        user = get_user(user_id)
        report = generate_final_report(user)
        update_user(user_id, final_report=report)
        send_message(chat_id, report)
        send_message(chat_id,
            "◦ Это ваш персональный план ✦\n\n"
            "В любой момент пришлите фото косметического средства — "
            "и я разберу его состав специально для вас."
        )
    except Exception as e:
        print(f"Error final report: {e}")
        send_message(chat_id,
            "◦ Не удалось составить разбор сейчас. Попробуйте ещё раз чуть позже."
        )


def handle_stats(msg):
    """Показывает пользователю его сохранённый профиль."""
    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    username = msg["from"].get("username", "")
    first_name = msg["from"].get("first_name", "")

    user = get_user(user_id, username=username, first_name=first_name)

    if not user.get("quiz_done"):
        send_message(chat_id,
            "◦ Ваш профиль ещё не заполнен.\n"
            "Напишите /start, чтобы пройти небольшой опрос."
        )
        return

    lines = ["✦ Ваш профиль", ""]

    def add(label, value):
        if value:
            lines.append(f"— {label}: {value}")

    handle = user.get("username")
    add("Логин", f"@{handle}" if handle else None)
    add("Имя", user.get("first_name"))
    add("Возраст", user.get("age"))
    add("Пол", user.get("gender"))
    location = ", ".join(filter(None, [user.get("city"), user.get("country")]))
    add("Местоположение", location)

    lines.append("")
    lines.append("✦ Цели и привычки")
    lines.append("")
    add("Цель", user.get("goal"))
    add("Что беспокоит", user.get("problem"))
    add("Частота мытья", user.get("wash_frequency"))
    add("Время на укладку", user.get("styling_time"))

    if user.get("hair_analysis"):
        lines.append("")
        lines.append("✦ Характеристика волос")
        lines.append("")
        lines.append(user["hair_analysis"])

    products = get_user_products_used(user_id)
    if products:
        lines.append("")
        lines.append("✦ Средства, которыми вы пользуетесь")
        lines.append("")
        for p in products:
            lines.append(f"· {p}")

    send_message(chat_id, "\n".join(lines))


def handle_photo(msg):
    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    username = msg["from"].get("username", "")
    first_name = msg["from"].get("first_name", "")

    user = get_user(user_id, username=username, first_name=first_name)

    # Если квиз не пройден
    if not user.get("quiz_done"):
        step_index = user.get("quiz_step", 0)
        send_message(chat_id,
            "◦ Прежде чем начать анализ, завершите небольшой опрос.\n"
            "Это займёт пару минут и поможет мне дать персональные рекомендации."
        )
        send_quiz_question(chat_id, step_index)
        return

    # Ожидаем фото волос/селфи после квиза
    if user.get("awaiting") == "hair_photo":
        send_message(chat_id, "✦ Изучаю ваши волосы...")
        try:
            photo = msg["photo"][-1]
            file_info = tg("getFile", {"file_id": photo["file_id"]})
            image_bytes = download_file(file_info["result"]["file_path"])
            hair = analyze_hair(image_bytes)
            update_user(user_id, hair_analysis=hair)
            send_message(chat_id, "✦ Вот что я увидела о ваших волосах:\n\n" + hair)
        except Exception as e:
            print(f"Error hair analysis: {e}")
            send_message(chat_id, "◦ Не удалось разобрать фото волос, но это не помешает — перейдём к разбору.")
        deliver_final_report(chat_id, user_id)
        return

    send_message(chat_id,
        "✦ Анализирую средство...\n\n"
        "◦ Изучаю состав\n"
        "◦ Сверяю с вашим профилем\n"
        "◦ Готовлю персональный отчёт"
    )

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
                "◦ Это средство уже было в нашей базе\n\n"
                "Вы пользуетесь им?",
                reply_markup={"inline_keyboard": [[
                    {"text": "Да, использую", "callback_data": f"uses_yes_{product_id}"},
                    {"text": "Нет, присматриваюсь", "callback_data": f"uses_no_{product_id}"}
                ]]}
            )
        else:
            analysis = analyze_image(image_bytes, user=user)
            product_name = extract_product_name(analysis)
            product_id = save_analysis(image_hash, product_name, analysis)
            send_message(chat_id, analysis)
            if product_id:
                send_message(chat_id,
                    "Вы пользуетесь этим средством?",
                    reply_markup={"inline_keyboard": [[
                        {"text": "Да, использую", "callback_data": f"uses_yes_{product_id}"},
                        {"text": "Нет, присматриваюсь", "callback_data": f"uses_no_{product_id}"}
                    ]]}
                )

    except Exception as e:
        print(f"Error analyzing: {e}")
        send_message(chat_id,
            "◦ Произошла ошибка при анализе.\n"
            "Пожалуйста, попробуйте ещё раз или отправьте другое фото."
        )


def handle_callback(callback):
    query_id = callback["id"]
    user_id = callback["from"]["id"]
    chat_id = callback["message"]["chat"]["id"]
    data = callback.get("data", "")

    user = get_user(user_id)

    # Квиз
    if data.startswith("quiz_"):
        handle_quiz_callback(callback, user)
        return

    tg("answerCallbackQuery", {"callback_query_id": query_id})

    # Пропуск фото волос → сразу к итоговому разбору
    if data == "skip_hair":
        if user.get("awaiting") == "hair_photo":
            deliver_final_report(chat_id, user_id)
        return

    if data.startswith("uses_yes_") or data.startswith("uses_no_"):
        uses = data.startswith("uses_yes_")
        product_id = int(data.split("_")[-1])
        save_user_product(user_id, product_id, uses)
        if uses:
            send_message(chat_id, "✦ Записала в вашу коллекцию ◦")
        else:
            send_message(chat_id, "◦ Поняла, спасибо за ответ.")


# ─── Главный цикл ──────────────────────────────────────────

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

                user_id = msg.get("from", {}).get("id")
                if not user_id:
                    continue

                text = msg.get("text", "")
                command = text.split()[0].split("@")[0] if text.startswith("/") else ""

                if command == "/start":
                    handle_start(msg)
                elif command in ("/stats", "/stas", "/profile"):
                    handle_stats(msg)
                elif msg.get("photo"):
                    handle_photo(msg)
                elif msg.get("text"):
                    # Текстовый ответ на квиз
                    user = get_user(user_id)
                    if not user.get("quiz_done"):
                        handled = handle_quiz_text_answer(msg, user)
                        if not handled:
                            send_message(chat_id, "◦ Отправьте фото косметического средства — и я сделаю анализ.")
                    elif user.get("awaiting") == "hair_photo":
                        send_message(chat_id,
                            "◦ Пришлите фото волос или селфи — или нажмите «Пропустить».",
                            reply_markup={"inline_keyboard": [[
                                {"text": "Пропустить", "callback_data": "skip_hair"}
                            ]]}
                        )
                    else:
                        send_message(chat_id, "◦ Отправьте фото косметического средства — и я сделаю анализ.")

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(3)


if __name__ == "__main__":
    main()
