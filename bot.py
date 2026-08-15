import asyncio
import os
import re
import sys
import traceback
import threading
import discord

# Forzar UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from discord.ext import commands
from flask import Flask, jsonify, render_template_string, request
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from yt_dlp import YoutubeDL
from dotenv import load_dotenv

load_dotenv()

# --- CREDENCIALES ---
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
SPOTIFY_CLIENT_ID = os.environ["SPOTIFY_CLIENT_ID"]
SPOTIFY_CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"]
SPOTIFY_REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8080")
SPOTIFY_REFRESH_TOKEN = os.environ.get("SPOTIFY_REFRESH_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Configuración Spotify
cache_handler = spotipy.cache_handler.MemoryCacheHandler(token_info={
    "access_token": "",
    "refresh_token": SPOTIFY_REFRESH_TOKEN,
    "scope": "user-read-currently-playing user-read-playback-state playlist-read-private playlist-read-collaborative",
    "expires_in": 3600,
    "token_type": "Bearer",
    "expires_at": 0
})

sp_oauth = SpotifyOAuth(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI,
    cache_handler=cache_handler
)
sp = spotipy.Spotify(auth_manager=sp_oauth)

# Configuración YouTube Robusta
YDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "extract_flat": "in_playlist",
    "cookiefile": "cookies.txt",
    "ignoreerrors": True,
    "geo_bypass": True,
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "js_runtimes": {"node": {}},
    "extractor_args": {
        "youtube": {
            "player_client": ["web", "android"]
        }
    }
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

song_queue = asyncio.Queue()
current_song_title = "Nada reproduciéndose"
last_played_track = None

# --- WEB ---
app = Flask(__name__)
@app.route("/")
def index(): return "<h1>DjNandito Bot Online</h1>"
@app.route("/api/status")
def api_status(): return jsonify({"current": current_song_title})

# --- LÓGICA ---
async def get_stream_url(url_or_query):
    loop = asyncio.get_running_loop()
    try:
        info = await loop.run_in_executor(None, lambda: YoutubeDL(YDL_OPTIONS).extract_info(url_or_query, download=False))
        if "entries" in info: info = info["entries"][0]
        return info.get("url")
    except Exception as e:
        print(f"❌ Error al procesar audio: {e}")
        return None

async def process_queue():
    global current_song_title
    await bot.wait_until_ready()
    while not bot.is_closed():
        if bot.voice_clients and not song_queue.empty():
            vc = bot.voice_clients[0]
            if not vc.is_playing():
                url, title = await song_queue.get()
                stream = await get_stream_url(url)
                if stream:
                    vc.play(discord.FFmpegPCMAudio(stream, **FFMPEG_OPTIONS))
                    current_song_title = title
        await asyncio.sleep(1)

async def sync_spotify_loop():
    global last_played_track, current_song_title
    await bot.wait_until_ready()
    loop = asyncio.get_running_loop()
    while not bot.is_closed():
        try:
            current = await loop.run_in_executor(None, sp.current_playback)
            if current and current.get("is_playing"):
                track = current["item"]
                query = f"{track['name']} - {track['artists'][0]['name']}"
                if query != last_played_track and bot.voice_clients:
                    last_played_track = query
                    stream = await get_stream_url(f"ytsearch:{query}")
                    vc = bot.voice_clients[0]
                    if vc.is_playing(): vc.stop()
                    if stream:
                        vc.play(discord.FFmpegPCMAudio(stream, **FFMPEG_OPTIONS))
                        current_song_title = query
        except: pass
        await asyncio.sleep(15)

@bot.event
async def on_ready():
    print(f"--- ¡Bot conectado como {bot.user} ---")
    bot.loop.create_task(sync_spotify_loop())
    bot.loop.create_task(process_queue())

@bot.command(name="play", aliases=["p"])
async def play(ctx, *, query=None):
    if not ctx.author.voice: return await ctx.send("¡Entra a un canal de voz!")
    if ctx.voice_client is None: await ctx.author.voice.channel.connect()
    
    msg = await ctx.send("🔍 Procesando...")
    loop = asyncio.get_running_loop()
    
    try:
        if "spotify.com" in query:
            match = re.search(r"playlist[/:]([a-zA-Z0-9]+)", query)
            if match:
                tracks = await loop.run_in_executor(None, lambda: sp.playlist_tracks(match.group(1)).get('items', []))
                for item in tracks:
                    data = item.get('track') or item.get('item')
                    if data:
                        name = f"{data['name']} - {data['artists'][0]['name']}"
                        await song_queue.put((f"ytsearch1:{name}", name))
                return await msg.edit(content="✅ Playlist añadida.")
        
        info = await loop.run_in_executor(None, lambda: YoutubeDL(YDL_OPTIONS).extract_info(f"ytsearch1:{query}", download=False))
        entry = info["entries"][0] if "entries" in info else info
        await song_queue.put((entry['url'], entry['title']))
        await msg.edit(content=f"✅ {entry['title']} añadido.")
    except Exception as e:
        await msg.edit(content=f"❌ Error: {e}")

def run_flask(): app.run(host="0.0.0.0", port=5000)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(DISCORD_TOKEN)
