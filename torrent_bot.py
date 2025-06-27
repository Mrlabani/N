import os
import logging
import asyncio
import time
import shutil
import subprocess
from pymongo import MongoClient
from pyrogram import Client, filters
from pyrogram.types import Message
import libtorrent as lt

# --- Config ---
API_ID = int(os.getenv("API_ID", 12345))
API_HASH = os.getenv("API_HASH", "your_api_hash")
BOT_TOKEN = os.getenv("BOT_TOKEN", "your_bot_token")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
OWNER_ID = int(os.getenv("OWNER_ID", 123456789))

# --- Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Bot & DB ---
app = Client("torrentBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
client = MongoClient(MONGO_URI)
db = client["torrent_bot"]
logs_col = db["logs"]

# --- Helpers ---
def human_readable(size):
    power = 1024
    n = 0
    units = ["B", "KB", "MB", "GB", "TB"]
    while size >= power and n < len(units) - 1:
        size /= power
        n += 1
    return f"{size:.2f} {units[n]}"

async def progress_bar(current, total):
    percent = current * 100 / total if total else 0
    bar = "█" * int(percent / 10) + "░" * (10 - int(percent / 10))
    return f"[{bar}] {percent:.2f}%"

async def send_progress(msg, path, torrent_handle):
    while not torrent_handle.is_seed():
        status = torrent_handle.status()
        downloaded = human_readable(status.total_done)
        total = human_readable(status.total_wanted)
        bar = await progress_bar(status.total_done, status.total_wanted)
        try:
            await msg.edit(f"📥 **Downloading...**\n{bar}\n**{downloaded}/{total}**")
        except:
            pass
        await asyncio.sleep(5)
    return True

# --- Telegram Bot Commands ---
@app.on_message(filters.command("start"))
async def start(_, msg: Message):
    await msg.reply_text("👋 Welcome to the **Torrent Downloader Bot**.\nSend me a magnet link or upload a `.torrent` file.")

@app.on_message(filters.document & filters.private)
async def handle_torrent_file(_, msg: Message):
    file_path = await msg.download()
    await process_torrent(file_path, msg)

@app.on_message(filters.text & filters.private)
async def handle_text(_, msg: Message):
    if "magnet:" in msg.text:
        await process_torrent(msg.text, msg)

# --- Core Logic ---
async def process_torrent(input_data, msg: Message):
    download_dir = f"downloads/{msg.from_user.id}_{int(time.time())}"
    os.makedirs(download_dir, exist_ok=True)

    session = lt.session()
    session.listen_on(6881, 6891)

    params = {
        'save_path': download_dir,
        'storage_mode': lt.storage_mode_t.storage_mode_sparse,
    }

    if input_data.endswith(".torrent"):
        info = lt.torrent_info(input_data)
        params['ti'] = info
    else:
        params = lt.parse_magnet_uri(input_data)
        params['save_path'] = download_dir

    handle = session.add_torrent(params)
    await msg.reply("⏬ **Starting download...**")
    status_msg = await msg.reply("🔄 Waiting for metadata...")

    while not handle.has_metadata():
        await asyncio.sleep(1)

    await send_progress(status_msg, download_dir, handle)

    await msg.reply("✅ **Download complete. Uploading to Telegram...**")
    await upload_files(msg, download_dir)

    logs_col.insert_one({
        "user_id": msg.from_user.id,
        "input": input_data,
        "path": download_dir,
        "timestamp": int(time.time())
    })

    shutil.rmtree(download_dir, ignore_errors=True)

async def upload_files(msg: Message, directory: str):
    MAX_SIZE = 2 * 1024 * 1024 * 1024  # 2GB limit

    async def ffmpeg_split(filepath, original_name):
        base, ext = os.path.splitext(filepath)
        split_pattern = f"{base}_part%03d{ext}"
        try:
            subprocess.run([
                "ffmpeg", "-i", filepath,
                "-c", "copy",
                "-f", "segment",
                "-segment_time", "900",  # 15 minutes
                "-reset_timestamps", "1",
                split_pattern
            ], check=True)

            # Upload parts
            parts = sorted([f for f in os.listdir(directory) if f.startswith(os.path.basename(base)) and f.endswith(ext)])
            for i, part in enumerate(parts, 1):
                part_path = os.path.join(directory, part)
                part_size = os.path.getsize(part_path)
                caption = f"📁 `{original_name}` (Part {i})\n💾 `{human_readable(part_size)}`"
                try:
                    await msg.reply_document(part_path, caption=caption)
                except Exception as e:
                    await msg.reply_text(f"❌ Failed to upload Part {i}:\n`{e}`")
                os.remove(part_path)
        except subprocess.CalledProcessError:
            await msg.reply_text("❌ `ffmpeg` failed to split the file.")

    for root, dirs, files in os.walk(directory):
        for file in files:
            filepath = os.path.join(root, file)
            size = os.path.getsize(filepath)

            if size <= MAX_SIZE:
                caption = f"📁 `{file}`\n💾 `{human_readable(size)}`"
                try:
                    await msg.reply_document(filepath, caption=caption)
                except Exception as e:
                    await msg.reply_text(f"❌ Failed to upload `{file}`:\n`{e}`")
            else:
                await msg.reply_text(f"🔪 `{file}` is too large. Splitting with ffmpeg...")
                await ffmpeg_split(filepath, file)

# --- Start Bot ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    os.system(f"python3 -m http.server {port} &")  # Render port workaround
    app.run()
      
