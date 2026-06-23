import os
import base64
import time
import hashlib
from datetime import datetime, timedelta
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ─── Подписка (Telegram Stars) ─────────────────────────────
SUB_PRICE_STARS = 100          # стоимость в Telegram Stars в месяц
SUB_DAYS = 30                  # длительность подписки
SUB_MONTHLY_ACTIONS = 100      # лимит AI-действий в месяц по подписке

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
VERIFY = False

# ─── Квиз ──────────────────────────────────────────────────

QUIZ_STEPS = [
    {
        "key": "goal",
        "block": "🎯 Цель",
        "question": (
            "Что вы хотите изменить или улучшить в своих волосах?\n\n"
            "Напишите своими словами — например:\n"
            "· Быстрее отрастить волосы\n"
            "· Сделать волосы гуще\n"
            "· Избавиться от жирности\n"
            "· Научиться пользоваться стайлерами"
        ),
        "type": "text"
    },
    {
        "key": "problem",
        "block": "🌿 Что беспокоит",
        "question": (
            "Что вас сейчас больше всего беспокоит в ваших волосах?\n\n"
            "Напишите своими словами — например:\n"
            "· Быстро становятся грязными\n"
            "· Перхоть\n"
            "· Выпадение\n"
            "· Сухость и ломкость\n"
            "· Не получается укладка"
        ),
        "type": "text"
    },
    {
        "key": "age",
        "block": "👤 Профиль",
        "question": "Сколько вам лет?",
        "type": "text"
    },
    {
        "key": "gender",
        "block": "👤 Профиль",
        "question": "Ваш пол?",
        "type": "buttons",
        "options": ["Женский", "Мужской", "Предпочитаю не указывать"]
    },
    {
        "key": "country",
        "block": "🌍 Профиль",
        "question": (
            "В какой стране вы живёте?\n\n"
            "Это нужно, чтобы учесть климат, жёсткость воды\n"
            "и доступность средств в вашем регионе."
        ),
        "type": "text"
    },
    {
        "key": "city",
        "block": "🌍 Профиль",
        "question": "В каком городе вы живёте?",
        "type": "text"
    },
    {
        "key": "wash_frequency",
        "block": "💧 Привычки",
        "question": "Как часто вы моете голову?",
        "type": "buttons",
        "options": ["Каждый день", "Через день", "2–3 раза в неделю", "Реже"]
    },
    {
        "key": "styling_time",
        "block": "✨ Привычки",
        "question": (
            "Сколько времени обычно занимает ваша укладка?\n\n"
            "Это помогает понять ваши привычки\n"
            "и подобрать подходящие рекомендации."
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
                    is_subscribed BOOLEAN DEFAULT FALSE,
                    sub_start TIMESTAMP,
                    sub_expires TIMESTAMP,
                    sub_actions_used INTEGER DEFAULT 0,
                    free_plan_used BOOLEAN DEFAULT FALSE,
                    free_hair_used BOOLEAN DEFAULT FALSE,
                    free_product_used BOOLEAN DEFAULT FALSE,
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
                ("is_subscribed", "BOOLEAN DEFAULT FALSE"),
                ("sub_start", "TIMESTAMP"),
                ("sub_expires", "TIMESTAMP"),
                ("sub_actions_used", "INTEGER DEFAULT 0"),
                ("free_plan_used", "BOOLEAN DEFAULT FALSE"),
                ("free_hair_used", "BOOLEAN DEFAULT FALSE"),
                ("free_product_used", "BOOLEAN DEFAULT FALSE"),
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


# ─── Подписка и лимиты ─────────────────────────────────────

# Тип действия -> поле бесплатного флага в БД
FREE_FLAG = {
    "plan": "free_plan_used",
    "hair": "free_hair_used",
    "product": "free_product_used",
}


def subscription_active(user):
    """Активна ли подписка (не истёк срок и есть остаток действий)."""
    if not user.get("is_subscribed"):
        return False
    expires = user.get("sub_expires")
    if not expires:
        return False
    try:
        if isinstance(expires, str):
            expires = datetime.fromisoformat(expires)
        if expires < datetime.utcnow():
            return False
    except Exception:
        return False
    return (user.get("sub_actions_used") or 0) < SUB_MONTHLY_ACTIONS


def sub_actions_left(user):
    if not subscription_active(user):
        return 0
    return SUB_MONTHLY_ACTIONS - (user.get("sub_actions_used") or 0)


def can_use_ai(user, action):
    """Можно ли выполнить AI-действие.
    Возвращает (True/False, способ: 'free' | 'sub' | None)."""
    # Бесплатный «первый раз» для данного типа действия
    flag = FREE_FLAG.get(action)
    if flag and not user.get(flag):
        return True, "free"
    # Иначе — по активной подписке
    if subscription_active(user):
        return True, "sub"
    return False, None


def consume_ai_action(user_id, way, action):
    """Списывает использование: либо бесплатный флаг, либо +1 к счётчику подписки."""
    if way == "free":
        flag = FREE_FLAG.get(action)
        if flag:
            update_user(user_id, **{flag: True})
    elif way == "sub":
        u = get_user(user_id)
        update_user(user_id, sub_actions_used=(u.get("sub_actions_used") or 0) + 1)


def activate_subscription(user_id):
    """Активирует/продлевает подписку на SUB_DAYS дней, обнуляет счётчик."""
    now = datetime.utcnow()
    expires = now + timedelta(days=SUB_DAYS)
    update_user(
        user_id,
        is_subscribed=True,
        sub_start=now.isoformat(),
        sub_expires=expires.isoformat(),
        sub_actions_used=0,
    )


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


def send_subscription_invoice(chat_id):
    """Счёт на оплату подписки через Telegram Stars (валюта XTR)."""
    tg("sendInvoice", {
        "chat_id": chat_id,
        "title": "Премиум-подписка ✦",
        "description": (
            f"{SUB_MONTHLY_ACTIONS} разборов и анализов фото в месяц. "
            "Персональные планы, анализ волос и косметики без ограничений в рамках лимита."
        ),
        "payload": "premium_monthly",
        "currency": "XTR",
        "prices": [{"label": "Подписка на месяц", "amount": SUB_PRICE_STARS}],
    })


def offer_subscription(chat_id, reason=""):
    """Показывает мягкое предложение оформить подписку + кнопку оплаты."""
    text = (
        (reason + "\n\n" if reason else "")
        + "✦ Премиум-доступ\n\n"
        f"Бесплатный лимит исчерпан. Подписка открывает {SUB_MONTHLY_ACTIONS} "
        "AI-действий в месяц — персональные планы и анализ фото волос и косметики.\n\n"
        f"Стоимость: {SUB_PRICE_STARS} ⭐ Telegram Stars в месяц."
    )
    send_message(chat_id, text, reply_markup={"inline_keyboard": [[
        {"text": f"Оформить за {SUB_PRICE_STARS} ⭐", "callback_data": "buy_sub"}
    ]]})


def send_quiz_question(chat_id, step_index):
    step = QUIZ_STEPS[step_index]
    total = len(QUIZ_STEPS)
    done = step_index + 1
    # Элегантный прогресс: заполненные ромбы и тонкие разделители
    progress = "◆ " * done + "◇ " * (total - done)

    header = (
        f"{step['block']}   ·   шаг {done} из {total}\n"
        f"{progress.strip()}\n\n"
    )
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


def classify_photo(image_bytes):
    """Определяет, что на фото: волосы/селфи или косметическое средство.
    Возвращает 'hair' или 'product'."""
    image_b64 = base64.standard_b64encode(image_bytes).decode()
    prompt = (
        "Посмотри на фото и определи, что на нём изображено. "
        "Ответь СТРОГО одним словом без знаков препинания:\n"
        "· hair — если это волосы, причёска, голова или селфи человека\n"
        "· product — если это косметическое/уходовое средство (флакон, тюбик, банка, упаковка)\n"
        "Если сомневаешься между двумя — выбери то, что занимает большую часть кадра."
    )
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": "gpt-4o-mini",
                "max_tokens": 5,
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}", "detail": "low"}},
                    {"type": "text", "text": prompt}
                ]}]
            },
            verify=VERIFY
        )
        answer = resp.json()["choices"][0]["message"]["content"].strip().lower()
        return "hair" if "hair" in answer else "product"
    except Exception as e:
        print(f"Classify error: {e}")
        return "product"


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
    prompt = f"""Ты — профессиональный консультант по волосам. Пиши честно, по делу и структурно, как эксперт, а не как продавец. Без комплиментов, без лести, без фраз вроде «у вас прекрасные стремления». Не оценивай человека — оценивай ситуацию с волосами и давай конкретику. Тон спокойный, уважительный, не приторный.

Данные о человеке:
{profile}

Составь разбор и план под его цель. Опирайся строго на эти данные: профиль, привычки, состояние волос, используемые средства (если они есть — честно скажи, что оставить, а что заменить и почему). Только про волосы и связанное с ними здоровье. Не выдумывай факты, которых нет в данных. Давай конкретные действия и цифры, где уместно (частота, что искать в составе, какие нутриенты).

Структура (оформление только символами ✦ — · ◦, без ✅ и ❌, без эмодзи):

✦ СИТУАЦИЯ
— 2–3 строки: что в данных указывает на текущее состояние и в чём суть задачи

✦ ПРИЧИНЫ
— Почему так происходит, на основе привычек и профиля

✦ УХОД
— Конкретные шаги: частота мытья, температура воды, техника

✦ СРЕДСТВА
— Что искать в составе и что использовать; с разбором уже используемых средств

✦ ПИТАНИЕ И ЗДОРОВЬЕ
— Конкретные нутриенты для волос: белок, омега-3, железо, цинк, витамины, коллаген — с примерами продуктов

✦ ПЕРВЫЙ ШАГ
— Один конкретный шаг на сегодня

Будь честным и точным. Если данных мало для какого-то раздела — так и скажи, не придумывай."""
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
        "Команды:\n"
        "· /start — пройти опрос заново\n"
        "· /stats — посмотреть ваш профиль и перейти к разбору\n\n"
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

    user = get_user(user_id)
    allowed, way = can_use_ai(user, "plan")
    if not allowed:
        offer_subscription(chat_id, "Первый персональный план вы уже получили.")
        return

    send_message(chat_id,
        "✦ Готовлю ваш персональный разбор...\n\n"
        "◦ Изучаю профиль\n"
        "◦ Сверяю привычки и средства\n"
        "◦ Составляю план"
    )
    try:
        report = generate_final_report(user)
        update_user(user_id, final_report=report)
        consume_ai_action(user_id, way, "plan")
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


def show_profile(chat_id, user_id, username="", first_name=""):
    """Показывает пользователю его сохранённый профиль."""

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

    # Статус подписки
    lines.append("")
    lines.append("✦ Подписка")
    lines.append("")
    if subscription_active(user):
        expires = user.get("sub_expires")
        try:
            if isinstance(expires, str):
                expires = datetime.fromisoformat(expires)
            date_str = expires.strftime("%d.%m.%Y")
        except Exception:
            date_str = "—"
        lines.append("— Статус: активна")
        lines.append(f"— Действует до: {date_str}")
        lines.append(f"— Осталось действий: {sub_actions_left(user)} из {SUB_MONTHLY_ACTIONS}")
    else:
        lines.append("— Статус: бесплатный доступ")
        free_left = []
        if not user.get("free_plan_used"):
            free_left.append("план")
        if not user.get("free_hair_used"):
            free_left.append("фото волос")
        if not user.get("free_product_used"):
            free_left.append("фото средства")
        if free_left:
            lines.append("— Бесплатно ещё доступно: " + ", ".join(free_left))
        else:
            lines.append(f"— Бесплатный лимит исчерпан · подписка {SUB_PRICE_STARS} ⭐/мес")

    send_message(chat_id, "\n".join(lines),
        reply_markup={"inline_keyboard": [[
            {"text": "Продолжить ›", "callback_data": "menu_open"}
        ]]}
    )


def handle_stats(msg):
    """Команда /stats из обычного сообщения."""
    chat_id = msg["chat"]["id"]
    frm = msg.get("from", {})
    show_profile(chat_id, frm["id"], frm.get("username", ""), frm.get("first_name", ""))


def send_action_menu(chat_id):
    """Меню из трёх действий после «Продолжить»."""
    send_message(chat_id,
        "Что хотите сделать дальше?",
        reply_markup={"inline_keyboard": [
            [{"text": "✦ Приступить к разбору волос", "callback_data": "act_report"}],
            [{"text": "📷 Прислать фото (волосы или средство)", "callback_data": "act_photo"}],
            [{"text": "👤 Мой профиль", "callback_data": "act_profile"}],
        ]}
    )


def process_hair_photo(chat_id, user_id, image_bytes, then_report=True):
    """Анализ фото волос → запись в БД. При then_report — затем выдать разбор."""
    user = get_user(user_id)
    allowed, way = can_use_ai(user, "hair")
    if not allowed:
        offer_subscription(chat_id, "Бесплатный анализ волос вы уже использовали.")
        return

    send_message(chat_id, "✦ Изучаю ваши волосы...")
    try:
        hair = analyze_hair(image_bytes)
        update_user(user_id, hair_analysis=hair)
        consume_ai_action(user_id, way, "hair")
        send_message(chat_id, "✦ Что видно о ваших волосах:\n\n" + hair)
    except Exception as e:
        print(f"Error hair analysis: {e}")
        send_message(chat_id, "◦ Не удалось разобрать фото волос.")
    if then_report:
        deliver_final_report(chat_id, user_id)


def process_product_photo(chat_id, user, image_bytes):
    """Анализ фото косметического средства."""
    user_id = user["user_id"]

    allowed, way = can_use_ai(user, "product")
    if not allowed:
        offer_subscription(chat_id, "Бесплатный анализ косметики вы уже использовали.")
        return

    send_message(chat_id,
        "✦ Анализирую средство...\n\n"
        "◦ Изучаю состав\n"
        "◦ Сверяю с вашим профилем\n"
        "◦ Готовлю отчёт"
    )
    try:
        image_hash = hashlib.md5(image_bytes).hexdigest()
        cached = get_cached_analysis(image_hash)
        if cached:
            analysis = cached["analysis"]
            product_id = cached["id"]
            consume_ai_action(user_id, way, "product")
            send_message(chat_id, analysis)
            note = "◦ Это средство уже было в нашей базе\n\nВы пользуетесь им?"
        else:
            analysis = analyze_image(image_bytes, user=user)
            product_name = extract_product_name(analysis)
            product_id = save_analysis(image_hash, product_name, analysis)
            consume_ai_action(user_id, way, "product")
            send_message(chat_id, analysis)
            note = "Вы пользуетесь этим средством?"
        if product_id:
            send_message(chat_id, note,
                reply_markup={"inline_keyboard": [[
                    {"text": "Да, использую", "callback_data": f"uses_yes_{product_id}"},
                    {"text": "Нет, присматриваюсь", "callback_data": f"uses_no_{product_id}"}
                ]]}
            )
    except Exception as e:
        print(f"Error analyzing product: {e}")
        send_message(chat_id,
            "◦ Произошла ошибка при анализе.\n"
            "Пожалуйста, попробуйте ещё раз или отправьте другое фото."
        )


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

    # Скачиваем фото один раз
    try:
        photo = msg["photo"][-1]
        file_info = tg("getFile", {"file_id": photo["file_id"]})
        image_bytes = download_file(file_info["result"]["file_path"])
    except Exception as e:
        print(f"Error downloading photo: {e}")
        send_message(chat_id, "◦ Не удалось загрузить фото. Попробуйте ещё раз.")
        return

    # Если бот ждёт фото волос после квиза — это точно волосы
    if user.get("awaiting") == "hair_photo":
        process_hair_photo(chat_id, user_id, image_bytes, then_report=True)
        return

    # Иначе сами определяем, что на фото: волосы или средство
    kind = classify_photo(image_bytes)
    if kind == "hair":
        process_hair_photo(chat_id, user_id, image_bytes, then_report=False)
    else:
        process_product_photo(chat_id, user, image_bytes)


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

    # Меню после /stats
    if data == "menu_open":
        send_action_menu(chat_id)
        return
    if data == "act_report":
        deliver_final_report(chat_id, user_id)
        return
    if data == "act_photo":
        send_message(chat_id,
            "📷 Пришлите фото — волос/селфи или косметического средства.\n"
            "Я сама пойму, что на фото, и сделаю нужный разбор."
        )
        return
    if data == "act_profile":
        frm = callback.get("from", {})
        show_profile(chat_id, user_id, frm.get("username", ""), frm.get("first_name", ""))
        return

    # Оплата подписки
    if data == "buy_sub":
        send_subscription_invoice(chat_id)
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
            params = {"timeout": 30, "allowed_updates": ["message", "callback_query", "pre_checkout_query"]}
            if offset:
                params["offset"] = offset

            resp = requests.get(f"{API_URL}/getUpdates", params=params, timeout=35, verify=VERIFY)
            data = resp.json()

            for update in data.get("result", []):
                offset = update["update_id"] + 1

                # Подтверждение оплаты до списания Stars — нужно ответить ok
                if "pre_checkout_query" in update:
                    pcq = update["pre_checkout_query"]
                    tg("answerPreCheckoutQuery", {"pre_checkout_query_id": pcq["id"], "ok": True})
                    continue

                if "callback_query" in update:
                    handle_callback(update["callback_query"])
                    continue

                msg = update.get("message", {})
                if not msg:
                    continue

                # Успешная оплата → активируем подписку
                if msg.get("successful_payment"):
                    uid = msg.get("from", {}).get("id")
                    cid = msg.get("chat", {}).get("id")
                    if uid:
                        activate_subscription(uid)
                        send_message(cid,
                            "✦ Подписка активирована\n\n"
                            f"Вам доступно {SUB_MONTHLY_ACTIONS} AI-действий на {SUB_DAYS} дней.\n"
                            "Присылайте фото волос или косметики и запрашивайте новые планы."
                        )
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
