# ALFA SAT — Telegram Bug Report Webhook
# Deployed to Render.com (Free Tier)
# Handles Approve/Dismiss button callbacks from bug report Telegram messages

from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import requests
import os
import base64
import random
import json
import firebase_admin
from firebase_admin import credentials, firestore

load_dotenv()

app = Flask(__name__)
CORS(app) # Enable CORS for all routes

# --- Firebase Initialization ---
# Render environment variables have a size limit, so we pass the service account JSON as a base64 string
firebase_b64 = os.environ.get("FIREBASE_SERVICE_ACCOUNT_B64")
db = None
if firebase_b64:
    try:
        decoded_cert = base64.b64decode(firebase_b64).decode('utf-8')
        cert_dict = json.loads(decoded_cert)
        cred = credentials.Certificate(cert_dict)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        print("✅ Firebase initialized in webhook")
    except Exception as e:
        print(f"❌ Failed to initialize Firebase: {e}")
else:
    print("⚠️ FIREBASE_SERVICE_ACCOUNT_B64 not found in environment")

BUG_REPORT_BOT_TOKEN = os.environ.get("BUG_REPORT_BOT_TOKEN")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID")

# Fallback tokens list from the environment (comma-separated)
TELEGRAM_BOT_TOKENS = os.environ.get("TELEGRAM_BOT_TOKENS", "").split(",")
if len(TELEGRAM_BOT_TOKENS) == 1 and not TELEGRAM_BOT_TOKENS[0]:
    TELEGRAM_BOT_TOKENS = []


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
            report_id = callback_data.replace("approve_", "")
            
            # 1. Answer callback immediately
            requests.post(f"https://api.telegram.org/bot{BUG_REPORT_BOT_TOKEN}/answerCallbackQuery", json={
                "callback_query_id": callback_id,
                "text": "⏳ Processing approve request...",
            })

            success_msg = "✅ APPROVED — Fix applied to database."
            
            # 2. Process Firestore Update
            if db:
                try:
                    report_ref = db.collection("bug_reports").document(report_id)
                    report_doc = report_ref.get()
                    
                    if report_doc.exists:
                        r_data = report_doc.to_dict()
                        payload = r_data.get("fixPayload", {})
                        
                        action = payload.get("action")
                        field = payload.get("field")
                        new_val = payload.get("newValue")
                        needs_sync = payload.get("requires_pdf_sync", False)
                        
                        if needs_sync:
                            # Flag it for the local PDF sync script
                            report_ref.update({"status": "approved_pending_sync"})
                            success_msg = "✅ APPROVED — Added to Local PDF Sync Queue."
                        elif action in ["update", "update_text", "replace"] and field and new_val is not None:
                            # Apply directly to questions collection
                            test_id = r_data.get("testId")
                            mod = r_data.get("module")
                            q_num = r_data.get("questionNumber")
                            q_doc_id = f"m{mod}_q{q_num}"
                            
                            q_ref = db.collection("tests").document(test_id).collection("questions").document(q_doc_id)
                            q_ref.update({field: new_val, "lastModified": firestore.SERVER_TIMESTAMP})
                            report_ref.update({"status": "resolved"})
                        else:
                            success_msg = "✅ APPROVED — But fix_payload was empty. Manual fix required."
                            report_ref.update({"status": "approved_manual"})
                    else:
                        success_msg = "✅ APPROVED — (Warning: Report metadata not found in database)"
                except Exception as e:
                    print(f"Firestore update failed: {e}")
                    success_msg = f"❌ APPROVED — But database update failed: {e}"

            # 3. Update the original message
            updated_text = original_text + f"\n\n━━━━━━━━━━━━━━━\n{success_msg}"
            requests.post(f"https://api.telegram.org/bot{BUG_REPORT_BOT_TOKEN}/editMessageText", json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": updated_text
            })

        elif callback_data.startswith("dismiss_"):
            report_id = callback_data.replace("dismiss_", "")
            
            requests.post(f"https://api.telegram.org/bot{BUG_REPORT_BOT_TOKEN}/answerCallbackQuery", json={
                "callback_query_id": callback_id,
                "text": "❌ Report dismissed.",
            })

            if db:
                try:
                    # Use set with merge=True to avoid 404 if doc was never created on frontend
                    db.collection("bug_reports").document(report_id).set({"status": "dismissed"}, merge=True)
                except Exception as e:
                    print(f"Failed to update report status for {report_id}: {e}")

            updated_text = original_text + "\n\n━━━━━━━━━━━━━━━\n❌ DISMISSED — No action taken."
            requests.post(f"https://api.telegram.org/bot{BUG_REPORT_BOT_TOKEN}/editMessageText", json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": updated_text
            })

    # Handle text replies to bot messages
    message = data.get("message")
    if message and "reply_to_message" in message and "text" in message:
        replied_msg = message["reply_to_message"]
        replied_text = replied_msg.get("text", "")
        admin_text = message["text"].strip()
        chat_id = message.get("chat", {}).get("id")
        
        # Check if the replied message contains a report ID (handles both Firestore and manual prefix)
        import re
        match = re.search(r"ID: (rpt_[a-zA-Z0-9_-]+|manual_rpt_\d+|[a-zA-Z0-9]{20,})", replied_text)
        
        if match and db:
            report_id = match.group(1)
            
            # Determine field to update (default: passage)
            target_field = "passage"
            new_val = admin_text
            
            # Allow admin to specify field, e.g. "prompt: new text"
            if ":" in admin_text:
                prefix = admin_text.split(":", 1)[0].strip().lower()
                suffix = admin_text.split(":", 1)[1].strip()
                if prefix in ["prompt", "passage", "correctanswer", "a", "b", "c", "d"]:
                    if prefix in ["a", "b", "c", "d"]:
                        # This implies we are updating the nested 'options' object.
                        # For simplicity, we just set the target_field temporarily to handle it below
                        target_field = f"options.{prefix.upper()}"
                        new_val = suffix
                    elif prefix == "correctanswer":
                        target_field = "correctAnswer"
                        new_val = suffix
                    else:
                        target_field = prefix
                        new_val = suffix
            
            try:
                report_ref = db.collection("bug_reports").document(report_id)
                report_doc = report_ref.get()
                
                if report_doc.exists:
                    r_data = report_doc.to_dict()
                    test_id = r_data.get("testId")
                    mod = r_data.get("module")
                    q_num = r_data.get("questionNumber")
                    q_doc_id = f"m{mod}_q{q_num}"
                    
                    q_ref = db.collection("tests").document(test_id).collection("questions").document(q_doc_id)
                    q_ref.update({
                        target_field: new_val, 
                        "lastModified": firestore.SERVER_TIMESTAMP
                    })
                    report_ref.update({"status": "resolved_manual_reply"})
                    
                    # Notify admin of success
                    requests.post(f"https://api.telegram.org/bot{BUG_REPORT_BOT_TOKEN}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"✅ Success! Updated `{target_field}` for Test {test_id}, M{mod} Q{q_num}.",
                        "reply_to_message_id": message["message_id"],
                        "parse_mode": "Markdown"
                    })
                else:
                    requests.post(f"https://api.telegram.org/bot{BUG_REPORT_BOT_TOKEN}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"❌ Error: Report '{report_id}' not found in database.",
                        "reply_to_message_id": message["message_id"]
                    })
            except Exception as e:
                print(f"Reply update failed: {e}")
                requests.post(f"https://api.telegram.org/bot{BUG_REPORT_BOT_TOKEN}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": f"❌ Database update failed: {str(e)}",
                    "reply_to_message_id": message["message_id"]
                })

    return jsonify({"ok": True})

# Simple in-memory cache for resolved image URLs
# Key: file_id, Value: (download_url, expiration_timestamp)
image_cache = {}
CACHE_TTL = 3600  # 1 hour

@app.route("/resolve-image", methods=["POST"])
def resolve_image():
    if not TELEGRAM_BOT_TOKENS:
        return jsonify({"error": "No bot tokens available"}), 500
    
    data = request.get_json(silent=True)
    if not data or "file_id" not in data:
        return jsonify({"error": "Missing file_id"}), 400
        
    file_id = data["file_id"]
    
    # 1. Check server-side cache
    import time
    now = time.time()
    if file_id in image_cache:
        url, expires = image_cache[file_id]
        if now < expires:
            return jsonify({"success": True, "url": url, "cached": True})
        else:
            del image_cache[file_id]

    # 2. Try bots round-robin or until one works
    for token in TELEGRAM_BOT_TOKENS:
        try:
            res = requests.get(f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}", timeout=5)
            result = res.json()
            if result.get("ok"):
                file_path = result["result"]["file_path"]
                download_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
                
                # Store in cache
                image_cache[file_id] = (download_url, now + CACHE_TTL)
                
                return jsonify({"success": True, "url": download_url, "cached": False})
        except Exception as e:
            print(f"Resolve failed on token {token[:10]}...: {e}")
            continue
            
    return jsonify({"error": "Failed to resolve image with any token"}), 500

@app.route("/upload-image", methods=["POST"])
def upload_image():
    if not TELEGRAM_BOT_TOKENS or not TELEGRAM_CHANNEL_ID:
        return jsonify({"error": "Missing bot tokens or channel config"}), 500
        
    data = request.get_json(silent=True)
    if not data or "image_base64" not in data:
        return jsonify({"error": "Missing image base64 data"}), 400
        
    try:
        # Get random token
        token = random.choice(TELEGRAM_BOT_TOKENS)
        
        # Parse base64
        base64_str = data["image_base64"].split(",")[1] if "," in data["image_base64"] else data["image_base64"]
        image_data = base64.b64decode(base64_str)
        
        filename = data.get("filename", "image.png")
        
        # Send to Telegram
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        files = {"photo": (filename, image_data, "image/png")}
        data_payload = {"chat_id": TELEGRAM_CHANNEL_ID}
        
        response = requests.post(url, data=data_payload, files=files)
        result = response.json()
        
        if result.get("ok"):
            photo_array = result["result"]["photo"]
            file_id = photo_array[-1]["file_id"]
            return jsonify({"success": True, "url": f"tg://{file_id}"})
        else:
            return jsonify({"error": "Telegram upload failed", "details": result}), 500
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/send-bug-report", methods=["POST"])
def send_bug_report():
    if not BUG_REPORT_BOT_TOKEN or not ADMIN_CHAT_ID:
        return jsonify({"error": "Missing bug report bot config"}), 500
        
    data = request.get_json(silent=True)
    if not data or "message" not in data:
        return jsonify({"error": "Missing report message"}), 400
        
    try:
        url = f"https://api.telegram.org/bot{BUG_REPORT_BOT_TOKEN}/sendMessage"
        
        # Build inline keyboard
        report_id = data.get("report_id", f"rpt_{random.randint(1000, 9999)}")
        inline_keyboard = {
            "inline_keyboard": [[
                {"text": "✅ Approve Fix", "callback_data": f"approve_{report_id}"},
                {"text": "❌ Dismiss", "callback_data": f"dismiss_{report_id}"}
            ]]
        }
        
        payload = {
            "chat_id": ADMIN_CHAT_ID,
            "text": data["message"],
            "parse_mode": "MarkdownV2",
            "reply_markup": inline_keyboard
        }
        
        response = requests.post(url, json=payload)
        
        # Fallback for MarkdownV2 parse errors
        if not response.json().get("ok"):
            payload["parse_mode"] = ""  # Strip parsing
            response = requests.post(url, json=payload)
            
        # Handle optional screenshot attachment
        if "screenshot_base64" in data and response.json().get("ok"):
            try:
                base64_str = data["screenshot_base64"]
                if "," in base64_str:
                    base64_str = base64_str.split(",")[1]
                
                image_data = base64.b64decode(base64_str)
                filename = data.get("screenshot_filename", "screenshot.png")
                
                photo_url = f"https://api.telegram.org/bot{BUG_REPORT_BOT_TOKEN}/sendPhoto"
                files = {"photo": (filename, image_data, "image/png")}
                caption_data = {
                    "chat_id": ADMIN_CHAT_ID,
                    "caption": f"📎 Screenshot for Q{data.get('questionNumber', '?')} report"
                }
                requests.post(photo_url, data=caption_data, files=files)
            except Exception as e:
                print(f"Failed to send screenshot: {e}")
                
        return jsonify({"success": True})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
