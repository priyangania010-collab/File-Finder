# app.py
import os
import asyncio
import re
from flask import Flask, jsonify, redirect, request, abort, send_from_directory
from pymongo import MongoClient
from dotenv import load_dotenv
from pyrogram import Client
from pyrogram.errors import FloodWait
from flask_cors import CORS

# Load environment variables from .env file
load_dotenv()

# --- Configuration (keep these in your .env) ---
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME")
COLLECTION_NAME = os.getenv("COLLECTION_NAME")

# Basic env check
if not all([MONGO_URI, DB_NAME, COLLECTION_NAME, KOYEB_URL, API_ID, API_HASH, BOT_TOKEN, BIN_CHANNEL_ID]):
    raise SystemExit("❌ Missing one or more critical environment variables. Check your .env file.")

app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app)  # allow cross origin for frontend to call API

# --- Initialize Pyrogram Client ---
client = Client(
    "web_bot_session",
    api_id=int(API_ID),
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# --- Database Connection ---
try:
    mongo_client = MongoClient(MONGO_URI)
    db = mongo_client[DB_NAME]
    collection = db[COLLECTION_NAME]
    # Test connection
    mongo_client.server_info()
    print("✅ MongoDB connection successful.")
except Exception as e:
    raise SystemExit(f"❌ Failed to connect to MongoDB: {e}")

# --- Helper Function for Sending Files ---
async def send_file_to_bin(file_id: str) -> int:
    if not client.is_connected:
        await client.start()
    try:
        sent_message = None
        try:
            sent_message = await client.send_video(chat_id=int(BIN_CHANNEL_ID), video=file_id)
        except Exception as e_video:
            print(f"-> Failed to send as video: {e_video}")
            try:
                sent_message = await client.send_document(chat_id=int(BIN_CHANNEL_ID), document=file_id)
            except Exception as e_doc:
                print(f"-> Failed to send as document: {e_doc}")
                raise Exception("Failed to send file as both video and document.")

        if sent_message:
            return sent_message.id
        return None
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return await send_file_to_bin(file_id)
    except Exception as e:
        print(f"send_file_to_bin error for {file_id}: {e}")
        return None

# --- Frontend route: serve index.html ---
@app.route('/')
def index():
    # Serves the static index.html in /static folder
    return send_from_directory('static', 'index.html')

# Serve other static files automatically via Flask static folder.

# --- API Routes used by frontend ---

def _parse_int(val, default):
    try:
        return int(val)
    except Exception:
        return default

@app.route('/api/latest', methods=['GET'])
def api_latest():
    """
    Returns paginated latest records.
    Query params:
      page (1-based), per_page
    """
    page = max(1, _parse_int(request.args.get('page', 1), 1))
    per_page = max(1, _parse_int(request.args.get('per_page', 20), 20))
    skip = (page - 1) * per_page

    try:
        # Attempt to sort by insertion time if possible; fallback to _id descending
        cursor = collection.find({}, {"_id": 1, "file_name": 1, "file_size": 1, "caption": 1, "year": 1, "file_type": 1}) \
                           .sort([("_id", -1)]) \
                           .skip(skip).limit(per_page)
        items = []
        for doc in cursor:
            items.append({
                "id": str(doc.get("_id")),
                "file_name": doc.get("file_name", "N/A"),
                "file_size": doc.get("file_size", 0),
                "caption": doc.get("caption", ""),
                "year": doc.get("year", None),
                "file_type": doc.get("file_type", None)
            })
        return jsonify({"page": page, "per_page": per_page, "items": items})
    except Exception as e:
        print(f"/api/latest error: {e}")
        return jsonify({"error": "Database error"}), 500

@app.route('/api/search', methods=['GET'])
def api_search():
    """
    Search endpoint with optional filters:
      q - query string (required)
      year - filter (optional)
      type - file type filter (optional)
      sort - 'asc' or 'desc' on _id (optional)
      page, per_page - pagination
    """
    q = request.args.get('q', '')
    year = request.args.get('year')
    ftype = request.args.get('type')
    sort = request.args.get('sort', 'desc')
    page = max(1, _parse_int(request.args.get('page', 1), 1))
    per_page = max(1, _parse_int(request.args.get('per_page', 50), 50))
    skip = (page - 1) * per_page

    # validation: q may be empty if caller wants filters only
    try:
        query = {}
        if q:
            safe_q = re.escape(q)
            query[SEARCH_FIELD_NAME] = {"$regex": safe_q, "$options": "i"}

        if year:
            # Accept numeric or string year
            try:
                query["year"] = int(year)
            except Exception:
                query["year"] = year

        if ftype:
            # try match file_type field or extension in filename
            query["$or"] = [
                {"file_type": {"$regex": re.escape(ftype), "$options": "i"}},
                {SEARCH_FIELD_NAME: {"$regex": r"\." + re.escape(ftype) + r"$", "$options": "i"}}
            ]

        sort_dir = -1 if sort.lower() == 'desc' else 1

        cursor = collection.find(query, {"_id": 1, "file_name": 1, "file_size": 1, "caption": 1, "year":1, "file_type":1}) \
                           .sort([("_id", sort_dir)]) \
                           .skip(skip).limit(per_page)

        items = []
        for doc in cursor:
            items.append({
                "id": str(doc.get("_id")),
                "file_name": doc.get("file_name", "N/A"),
                "file_size": doc.get("file_size", 0),
                "caption": doc.get("caption", ""),
                "year": doc.get("year", None),
                "file_type": doc.get("file_type", None)
            })

        return jsonify({"page": page, "per_page": per_page, "items": items})
    except Exception as e:
        print(f"/api/search error: {e}")
        return jsonify({"error": "Database error"}), 500

@app.route('/api/send_link/<file_id>', methods=['GET'])
def api_send_link(file_id):
    """
    Convenience endpoint that constructs and returns the telegram deep link to be opened by frontend.
    It does NOT call pyrogram — frontend can open the returned link.
    """
    if not file_id:
        return jsonify({"error": "file_id required"}), 400
    # Note: replace the constant token prefix if yours differs
    link = f"https://t.me/dhyeyautofilterbot?start=file_1123135015_{file_id}"
    return jsonify({"link": link})

# --- Optional streaming / download endpoints (left as-is) ---
@app.route('/watch/<file_id>', methods=['GET'])
def watch_file(file_id):
    if not file_id:
        abort(400, "File ID is required.")
    try:
        new_message_id = asyncio.run(send_file_to_bin(file_id))
        if new_message_id:
            stream_url = f"{KOYEB_URL}/watch/{new_message_id}"
            return redirect(stream_url)
        else:
            abort(500, "Failed to process the file for streaming.")
    except Exception as e:
        print(f"Error in /watch endpoint: {e}")
        abort(500, "An internal server error occurred.")

@app.route('/download/<file_id>', methods=['GET'])
def download_file(file_id):
    if not file_id:
        abort(400, "File ID is required.")
    try:
        new_message_id = asyncio.run(send_file_to_bin(file_id))
        if new_message_id:
            download_url = f"{KOYEB_URL}/download/{new_message_id}"
            return redirect(download_url)
        else:
            abort(500, "Failed to process the file for download.")
    except Exception as e:
        print(f"Error in /download endpoint: {e}")
        abort(500, "An internal server error occurred.")

if __name__ == '__main__':
    try:
        client.start()
        print("✅ Pyrogram client started.")
    except Exception as e:
        print(f"❌ Failed to start Pyrogram client: {e}")

    # Run Flask app
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)))

    try:
        client.stop()
        print("✅ Pyrogram client stopped.")
    except Exception as e:
        print(f"❌ Error stopping Pyrogram client: {e}")
