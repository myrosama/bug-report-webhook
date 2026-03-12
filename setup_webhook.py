#!/usr/bin/env python3
"""
Register the Telegram webhook after deploying to Render.com
Usage: python setup_webhook.py <your-render-url>
Example: python setup_webhook.py https://alfa-bug-report.onrender.com
"""
import sys
import requests

# Hardcoded for registration script to avoid dependency issues
BOT_TOKEN = "8669991204:AAHjxhG_yZIc2GioLGvrcAbrdbUP8ZoXins"

if len(sys.argv) < 2:
    print("Usage: python setup_webhook.py <your-render-url>")
    sys.exit(1)

render_url = sys.argv[1].rstrip("/")
webhook_url = f"{render_url}/webhook"

print(f"Setting webhook to: {webhook_url}")

resp = requests.post(
    f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
    json={"url": webhook_url}
)

print(f"Response: {resp.json()}")

if resp.json().get("ok"):
    print("✅ Webhook registered successfully!")
else:
    print("❌ Webhook registration failed!")
