# TGBOT

Telegram bot that analyzes cosmetic product photos with OpenAI.

## Features

- `/start` greets the user
- Accepts product photos in Telegram
- Downloads the photo from Telegram
- Sends the image to OpenAI
- Replies with a structured cosmetic product analysis

## Environment

Create a `.env` file locally or set these variables in your hosting service:

```bash
BOT_TOKEN=your_telegram_bot_token_here
OPENAI_API_KEY=your_openai_api_key_here
```

Never commit real tokens to GitHub.

## Local Run

```bash
pip install -r requirements.txt
python bot.py
```

## Docker Run

```bash
docker build -t tgbot .
docker run --env-file .env tgbot
```

## Deploy

For Railway, Render, Fly.io, or another worker host, set the environment
variables above and run:

```bash
python bot.py
```
