# ALFA SAT — Telegram Bug Report Webhook
# Deployed to Render.com (Free Tier)
# Handles Approve/Dismiss button callbacks from bug report Telegram messages

from flask import Flask, request, jsonify
from dotenv import load_dotenv
import requests
import os

load_dotenv()

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")

@app.route("/", methods=["GET"])
def index():
    return "ALFA SAT Bug Report Webhook — Running ✅"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"ok": True})

    # Handle callback queries (button clicks)
    callback = data.get("callback_query")
    if callback:
        callback_id = callback["id"]
        callback_data = callback.get("data", "")
        message = callback.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        message_id = message.get("message_id")
        original_text = message.get("text", "")

        if callback_data.startswith("approve_"):
            # Answer the callback (removes loading spinner on button)
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery", json={
                "callback_query_id": callback_id,
                "text": "✅ Report approved! Fix will be applied.",
                "show_alert": True
            })

            # Update the original message to show it was approved
            updated_text = original_text + "\n\n━━━━━━━━━━━━━━━\n✅ APPROVED — Fix will be applied."
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText", json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": updated_text
            })

        elif callback_data.startswith("dismiss_"):
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery", json={
                "callback_query_id": callback_id,
                "text": "❌ Report dismissed.",
                "show_alert": True
            })

            updated_text = original_text + "\n\n━━━━━━━━━━━━━━━\n❌ DISMISSED — No action taken."
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText", json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": updated_text
            })

    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
