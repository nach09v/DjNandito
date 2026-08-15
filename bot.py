import asyncio
import os
import re
import sys
import traceback
import threading
import discord

# Forzar UTF-8 para evitar errores con caracteres especiales
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

# --- CREDENCIALES (desde variables de entorno / Render) ---
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
SPOTIFY_CLIENT_ID = os.environ["SPOTIFY_CLIENT_ID"]
SPOTIFY_CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"]
SPOTIFY_REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8080")
SPOTIFY_REFRESH_TOKEN = os.environ.get("SPOTIFY_REFRESH_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Configuración de Spotify con Caché en Memoria (Refresh Token Maestro)
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

# Configuración de búsqueda de YouTube
YDL_SEARCH_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "extract_flat": "in_playlist",
    "js_runtimes": {"node": {}},
    "extractor_args": {
        "youtube": {
            "player_client": ["android", "web"]
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


# --- INTERFAZ WEB ESTILO SPOTIFY AVANZADO ---
app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DjNandito - Music Hub</title>
    <style>
        body { background-color: #121212; color: #ffffff; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }
        .container { background-color: #000000; padding: 30px; border-radius: 12px; box-shadow: 0 8px 24px rgba(0,0,0,0.8); width: 500px; max-height: 90vh; display: flex; flex-direction: column; }
        
        h1 { color: #ffffff; font-size: 26px; margin-bottom: 20px; text-align: center; font-weight: 700; letter-spacing: -1px; }
        h1 span { color: #1DB954; }

        .now-playing { background: linear-gradient(135deg, #282828 0%, #181818 100%); padding: 15px; border-radius: 10px; margin-bottom: 20px; text-align: center; border: 1px solid #333; }
        .now-playing span { font-size: 11px; color: #1DB954; letter-spacing: 1.5px; font-weight: bold; text-transform: uppercase; }
        .song-title { font-size: 16px; font-weight: 600; color: #fff; margin-top: 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

        .search-box { display: flex; gap: 10px; margin-bottom: 20px; }
        input[type="text"] { flex: 1; padding: 14px 20px; border-radius: 30px; border: none; background-color: #242424; color: white; outline: none; font-size: 15px; transition: 0.3s; }
        input[type="text"]:focus { background-color: #333; box-shadow: 0 0 0 2px #1DB954; }
        .btn-search { background-color: #ffffff; color: #000000; border: none; padding: 14px 24px; border-radius: 30px; font-weight: bold; cursor: pointer; transition: 0.2s; }
        .btn-search:hover { background-color: #1DB954; color: white; transform: scale(1.05); }

        .controls { display: flex; justify-content: center; gap: 15px; margin-bottom: 20px; }
        .btn-control { background-color: transparent; color: #b3b3b3; border: 1px solid #b3b3b3; padding: 10px 20px; border-radius: 30px; font-size: 14px; cursor: pointer; transition: 0.2s; font-weight: 600; }
        .btn-control:hover { color: #fff; border-color: #fff; transform: scale(1.05); }

        #results { flex: 1; overflow-y: auto; padding-right: 5px; }
        #results::-webkit-scrollbar { width: 8px; }
        #results::-webkit-scrollbar-thumb { background-color: #444; border-radius: 4px; }
        
        .result-item { display: flex; align-items: center; padding: 10px; border-radius: 8px; cursor: pointer; transition: 0.2s; gap: 15px; margin-bottom: 5px; }
        .result-item:hover { background-color: #2a2a2a; }
        .result-item:hover .play-overlay { opacity: 1; }
        
        .result-thumb-container { position: relative; width: 60px; height: 60px; flex-shrink: 0; }
        .result-thumb { width: 100%; height: 100%; object-fit: cover; border-radius: 6px; }
        .play-overlay { position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.6); border-radius: 6px; display: flex; justify-content: center; align-items: center; opacity: 0; transition: 0.2s; color: white; font-size: 24px; }
        
        .result-details { flex: 1; text-align: left; overflow: hidden; }
        .result-title { color: #ffffff; font-size: 15px; font-weight: 500; margin-bottom: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .result-subtitle { color: #a7a7a7; font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        
        .loading { text-align: center; color: #b3b3b3; padding: 20px; font-size: 14px; }
    </style>
</head>
<body>
    <div class="container">
        <h1><span>DjNandito</span> Player</h1>
        
        <div class="now-playing">
            <span>Reproduciendo Ahora</span>
            <div id="current-song" class="song-title">Cargando...</div>
        </div>

        <div class="controls">
            <button class="btn-control" onclick="control('pause')">Pausa</button>
            <button class="btn-control" onclick="control('resume')">Reanudar</button>
            <button class="btn-control" onclick="control('skip')">Saltar</button>
        </div>

        <div class="search-box">
            <input type="text" id="query" placeholder="¿Qué quieres escuchar?..." onkeypress="if(event.key === 'Enter') searchSongs()">
            <button class="btn-search" onclick="searchSongs()">Buscar</button>
        </div>

        <div id="results">
            <div style="text-align:center; color:#555; margin-top:20px; font-size:14px;">Busca una canción para ver los resultados aquí.</div>
        </div>
    </div>

    <script>
        function updateStatus() {
            fetch('/api/status')
                .then(res => res.json())
                .then(data => {
                    document.getElementById('current-song').innerText = data.current;
                });
        }
        setInterval(updateStatus, 2000);

        function searchSongs() {
            let q = document.getElementById('query').value;
            if(!q) return;
            document.getElementById('results').innerHTML = '<div class="loading">Buscando en YouTube... 🎵</div>';

            fetch('/api/search', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({query: q})
            })
            .then(res => res.json())
            .then(data => {
                let html = '';
                if(data.results.length === 0) {
                    html = '<div class="loading">No se encontraron resultados para tu búsqueda.</div>';
                } else {
                    data.results.forEach(item => {
                        let safeTitle = item.title.replace(/'/g, "\\'").replace(/"/g, '&quot;');
                        html += `
                            <div class="result-item" onclick="playSong('${item.url}', '${safeTitle}')">
                                <div class="result-thumb-container">
                                    <img class="result-thumb" src="${item.thumbnail}" alt="thumbnail">
                                    <div class="play-overlay">▶</div>
                                </div>
                                <div class="result-details">
                                    <div class="result-title">${item.title}</div>
                                    <div class="result-subtitle">${item.uploader} • ${item.duration}</div>
                                </div>
                            </div>
                        `;
                    });
                }
                document.getElementById('results').innerHTML = html;
            });
        }

        function playSong(url, title) {
            fetch('/api/add', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({url: url, title: title})
            }).then(() => {
                document.getElementById('results').innerHTML = `<div class="loading" style="color: #1DB954; font-weight:bold;">¡"${title}" añadido a la cola correctamente! ✅<br><br><span style="color:#b3b3b3;font-size:12px;font-weight:normal;">Busca otra canción para seguir añadiendo.</span></div>`;
                document.getElementById('query').value = '';
            });
        }

        function control(action) {
            fetch('/api/control/' + action, {method: 'POST'});
        }
    </script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/api/status")
def api_status():
    global current_song_title
    return jsonify({"current": current_song_title})

@app.route("/api/search", methods=["POST"])
def api_search():
    data = request.json
    query = data.get("query")
    results = []

    if query:
        try:
            if not query.startswith("http"):
                search_query = f"ytsearch10:{query}"
            else:
                search_query = query

            with YoutubeDL(YDL_SEARCH_OPTIONS) as ydl:
                info = ydl.extract_info(search_query, download=False)

                if "entries" in info:
                    entradas = info["entries"]
                else:
                    entradas = [info]

                for entry in entradas:
                    if entry:
                        video_id = entry.get('id')
                        url = entry.get('url')

                        if video_id and (not url or not url.startswith("http")):
                            url = f"https://www.youtube.com/watch?v={video_id}"

                        title = entry.get("title", "Desconocido")
                        uploader = entry.get("uploader", entry.get("channel", "Artista Desconocido"))

                        duration_val = entry.get("duration")
                        if isinstance(duration_val, (int, float)):
                            mins, secs = divmod(int(duration_val), 60)
                            duration_str = f"{mins}:{secs:02d}"
                        else:
                            duration_str = ""

                        if video_id:
                            thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                        else:
                            thumbnail = "https://via.placeholder.com/60x60?text=Audio"

                        results.append({
                            "title": title,
                            "url": url,
                            "uploader": uploader,
                            "duration": duration_str,
                            "thumbnail": thumbnail
                        })
        except Exception as e:
            print(f"❌ Error en búsqueda web: {e}")

    return jsonify({"results": results})

@app.route("/api/add", methods=["POST"])
def api_add():
    data = request.json
    url = data.get("url")
    title = data.get("title")
    if url and title:
        asyncio.run_coroutine_threadsafe(song_queue.put((url, title)), bot.loop)
        print(f"✅ Añadido a la cola desde web: {title}")
    return jsonify({"status": "ok"})

@app.route("/api/control/<action>", methods=["POST"])
def api_control(action):
    if bot.voice_clients:
        vc = bot.voice_clients[0]
        if action == "pause" and vc.is_playing():
            vc.pause()
        elif action == "resume" and vc.is_paused():
            vc.resume()
        elif action == "skip" and vc.is_playing():
            vc.stop()
    return jsonify({"status": "ok"})


# --- LÓGICA DE AUDIO DE DISCORD ---
async def get_stream_url(url_or_query):
    ydl_play_opts = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "js_runtimes": {"node": {}},
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"]
            }
        }
    }
    loop = asyncio.get_running_loop()
    try:
        info = await loop.run_in_executor(None, lambda: YoutubeDL(ydl_play_opts).extract_info(url_or_query, download=False))

        if "entries" in info:
            info = info["entries"][0]

        stream_url = info.get("url")
        if not stream_url:
            for f in info.get("formats", []):
                if f.get("acodec") != "none" and f.get("vcodec") == "none":
                    stream_url = f.get("url")
                    break
        return stream_url
    except Exception as e:
        print(f"❌ Error al procesar audio: {e}")
        return None


async def process_queue():
    global current_song_title
    await bot.wait_until_ready()

    while not bot.is_closed():
        try:
            if bot.voice_clients and not song_queue.empty():
                vc = bot.voice_clients[0]
                if not vc.is_playing():
                    url, title = await song_queue.get()
                    print(f"⏳ Procesando audio para: {title}...")

                    stream_url = await get_stream_url(url)

                    if stream_url:
                        vc.play(discord.FFmpegPCMAudio(stream_url, **FFMPEG_OPTIONS))
                        current_song_title = title
                        print(f"🎶 Reproduciendo: {title}")
                    else:
                        print(f"❌ Falló al procesar: {title}")
        except Exception as e:
            print(f"⚠️ Error en cola de reproducción: {e}")

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

                    stream_url = await get_stream_url(f"ytsearch:{query}")

                    vc = bot.voice_clients[0]
                    if vc.is_playing():
                        vc.stop()

                    if stream_url:
                        vc.play(discord.FFmpegPCMAudio(stream_url, **FFMPEG_OPTIONS))
                        current_song_title = query
                        print(f"🎵 [Spotify Automático] {query}")
        except Exception as e:
            pass
        await asyncio.sleep(10)


@bot.event
async def on_ready():
    print(f"--- ¡Bot conectado como {bot.user} ---")
    bot.loop.create_task(sync_spotify_loop())
    bot.loop.create_task(process_queue())


# --- HELPERS DE SPOTIFY ---
def _spotify_item_to_query(item):
    name = item.get('name')
    if not name:
        return None
    artists = item.get('artists', [])
    artist_name = ""
    if artists and isinstance(artists, list) and isinstance(artists[0], dict):
        artist_name = artists[0].get('name', '')
    return f"{name} - {artist_name}".strip()

SPOTIFY_MARKET = os.environ.get("SPOTIFY_MARKET", "AR")

def _fetch_playlist_tracks(playlist_id):
    results = sp.playlist_tracks(playlist_id, market=SPOTIFY_MARKET)
    tracks = list(results.get('items', []))
    while results.get('next'):
        results = sp.next(results)
        tracks.extend(results.get('items', []))
    return tracks


# --- COMANDOS DE DISCORD ---

@bot.command(name="DjNandito")
async def djnandito(ctx):
    if ctx.author.voice:
        channel = ctx.author.voice.channel
        if ctx.voice_client is None:
            await channel.connect()
            await ctx.send(f"¡Conectado a **{channel.name}**!\n🎶 Abre el panel visual web del bot.")
        else:
            await ctx.voice_client.move_to(channel)
            await ctx.send(f"Movido a **{channel.name}**")
    else:
        await ctx.send("¡Primero debes estar en un canal de voz!")


@bot.command(name="play", aliases=["p"])
async def play(ctx, *, query=None):
    if not query:
        await ctx.send("❌ Dime qué canción quieres escuchar. Ejemplo: `!play tajada babasonicos`")
        return

    if not ctx.author.voice:
        await ctx.send("❌ ¡Debes estar en un canal de voz primero!")
        return

    channel = ctx.author.voice.channel
    if ctx.voice_client is None:
        await channel.connect()
    elif ctx.voice_client.channel != channel:
        await ctx.voice_client.move_to(channel)

    msg = await ctx.send(f"🔍 Procesando: `{query}`...")
    loop = asyncio.get_running_loop()

    try:
        if "spotify.com" in query or "spotify:" in query:
            track_match = re.search(r"track[/:]([a-zA-Z0-9]+)", query)
            playlist_match = re.search(r"playlist[/:]([a-zA-Z0-9]+)", query)
            album_match = re.search(r"album[/:]([a-zA-Z0-9]+)", query)

            if track_match:
                track_id = track_match.group(1)
                track = await loop.run_in_executor(None, lambda: sp.track(track_id))
                artist_name = track['artists'][0]['name'] if track.get('artists') else ""
                query = f"{track['name']} {artist_name}"

            elif playlist_match:
                playlist_id = playlist_match.group(1)
                tracks = await loop.run_in_executor(None, lambda: _fetch_playlist_tracks(playlist_id))

                added = 0
                for item in tracks:
                    try:
                        if not isinstance(item, dict):
                            continue
                        track_data = item.get('track') or item.get('item')
                        if track_data is None and item.get('name'):
                            track_data = item
                        if not track_data:
                            continue

                        track_name = _spotify_item_to_query(track_data)
                        if not track_name:
                            continue

                        await song_queue.put((f"ytsearch1:{track_name}", track_name))
                        added += 1
                    except Exception:
                        continue

                if added > 0:
                    await msg.edit(content=f"✅ **Playlist de Spotify:** ¡Se añadieron **{added} canciones** a la cola!")
                    return
                else:
                    await msg.edit(content="❌ Spotify envió los elementos vacíos o están bloqueados en tu región.")
                    return

            elif album_match:
                album_id = album_match.group(1)
                results = await loop.run_in_executor(None, lambda: sp.album_tracks(album_id, market=SPOTIFY_MARKET))
                tracks = results.get('items', [])
                added = 0

                for track in tracks:
                    track_name = _spotify_item_to_query(track) if isinstance(track, dict) else None
                    if not track_name:
                        continue
                    await song_queue.put((f"ytsearch1:{track_name}", track_name))
                    added += 1

                if added > 0:
                    await msg.edit(content=f"✅ **Álbum de Spotify:** ¡Se añadieron **{added} canciones** a la cola!")
                    return

        search_query = f"ytsearch1:{query}" if not query.startswith("http") else query
        info = await loop.run_in_executor(None, lambda: YoutubeDL(YDL_SEARCH_OPTIONS).extract_info(search_query, download=False))

        if "entries" in info and len(info["entries"]) > 0:
            entry = info["entries"][0]
        else:
            entry = info

        video_id = entry.get('id')
        url = entry.get('url')
        if video_id and (not url or not url.startswith("http")):
            url = f"https://www.youtube.com/watch?v={video_id}"

        title = entry.get("title", "Desconocido")
        await song_queue.put((url, title))
        await msg.edit(content=f"✅ **{title}** se ha añadido a la cola.")
        print(f"✅ Añadido a la cola desde chat: {title}")

    except Exception as e:
        traceback.print_exc()
        await msg.edit(content=f"❌ Hubo un error al procesar el enlace o la búsqueda: `{e}`")


def run_flask():
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)

if __name__ == "__main__":
    web_thread = threading.Thread(target=run_flask, daemon=True)
    web_thread.start()
    bot.run(DISCORD_TOKEN)