# main.py
import discord
from discord.ext import commands
import wavelink
import os
import asyncio
from dotenv import load_dotenv
from lavalink import connect_lavalink
from connect import WSNowPlayingClient, BOT_STATE

load_dotenv()
token = os.getenv("Token")

# Define the allowed connection sources
ALLOWED_WS_SOURCES = [
    "100.20.92.101",
    "44.225.181.72", 
    "44.227.217.144",
    "cloudflare-website.onrender.com"
]

# Set default WS_IP if not provided in .env
if not os.getenv("WS_IP"):
    print("WS_IP not found in .env, using the first allowed source as default")
    os.environ["WS_IP"] = ALLOWED_WS_SOURCES[0]

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Initialize the WS client
ws_client = WSNowPlayingClient()
def ws_task_callback(task: asyncio.Task):
    try:
        # Try to retrieve the result to trigger exceptions, if any.
        task.result()
    except Exception as e:
        # Print a clean error message without a full traceback.
        print(f"WebSocket connection task ended: {e}")


@bot.event
async def on_ready():
    print(f'Bot ready: {bot.user}')
    await connect_lavalink(bot)
    # Set the bot instance in the shared state
    BOT_STATE["bot"] = bot
    # Start the WebSocket connection
    bot.loop.create_task(ws_client.connect())
    print("WebSocket client initialized")

@bot.event
async def on_wavelink_track_start(payload: wavelink.TrackStartEventPayload) -> None:
    """Event fired when a track starts playing."""
    player = payload.player
    track = payload.track
    
    # Send now playing update to the web interface
    await ws_client.send_now_playing(track=track)
    
    # Also update queue information
    await ws_client.send_queue_update(player)

@bot.event
async def on_wavelink_track_end(payload: wavelink.TrackEndEventPayload) -> None:
    """Event fired when a track ends."""
    player = payload.player
    
    # If queue is empty and no next track, update status
    if not player.queue and payload.reason == 'FINISHED':
        await asyncio.sleep(1)  # Wait a bit to see if a new track starts
        if not player.playing:
            await ws_client.send_now_playing("No track playing")

@bot.command()
async def play(ctx: commands.Context, *, search: str):
    """Play a track from search query."""
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.send("You must be in a voice channel to play music.")
    
    channel = ctx.author.voice.channel
    # Connect to voice if not already
    player: wavelink.Player = ctx.voice_client or await channel.connect(cls=wavelink.Player)
    
    # Search for tracks
    tracks = await wavelink.Playable.search(search)
    if not tracks:
        return await ctx.send("No tracks found.")
    
    track = tracks[0]
    await player.set_volume(100)
    
    # If already playing, add to queue
    if player.playing:
        player.queue.put(track)
        await ctx.send(f"🎵 Added to queue: `{track.title}`")
        # Update the queue on the web interface
        await ws_client.send_queue_update(player)
    else:
        await player.play(track)
        await ctx.send(f"🎶 Now playing: `{track.title}`")
        # Send now playing update to the web interface
        await ws_client.send_now_playing(track=track)


@bot.command()
async def pause(ctx: commands.Context):
    """Pause or resume the current track."""
    player: wavelink.Player = ctx.voice_client
    if not player:
        return await ctx.send("Nothing is playing.")
        
    if player.is_paused():
        await player.pause(False)  # Fixed: False to resume
        await ctx.send("▶️ Resumed playback.")
    else:
        await player.pause(True)  # Fixed: True to pause
        await ctx.send("⏸️ Paused playback.")
        
@bot.command()
async def skip(ctx: commands.Context):
    """Skip the current track."""
    player: wavelink.Player = ctx.voice_client
    if not player:
        return await ctx.send("Nothing is playing.")
        
    await player.stop()
    await ctx.send("⏭️ Skipped current track.")

@bot.command()
async def loop(ctx: commands.Context):
    """Toggle loop mode for the queue."""
    player: wavelink.Player = ctx.voice_client
    if not player:
        return await ctx.send("Nothing is playing.")
    
    # Toggle loop mode
    player.queue.loop = not getattr(player.queue, 'loop', False)
    loop_status = "enabled" if player.queue.loop else "disabled"
    await ctx.send(f"🔄 Loop mode {loop_status}.")

@bot.command()
async def shuffle(ctx: commands.Context):
    """Shuffle the current queue."""
    player: wavelink.Player = ctx.voice_client
    if not player or len(player.queue) < 2:
        return await ctx.send("Not enough songs in queue to shuffle.")
    
    player.queue.shuffle()
    await ctx.send("🔀 Queue shuffled.")
    # Update the queue on the web interface
    await ws_client.send_queue_update(player)

@bot.command()
async def queue(ctx: commands.Context):
    """Show the current queue."""
    player: wavelink.Player = ctx.voice_client
    if not player or not player.queue:
        return await ctx.send("The queue is empty.")
    
    upcoming = list(player.queue)
    queue_list = "\n".join(f"{i+1}. {track.title}" for i, track in enumerate(upcoming[:10]))
    
    if len(upcoming) > 10:
        queue_list += f"\n...and {len(upcoming) - 10} more."
    
    await ctx.send(f"**Current Queue:**\n{queue_list}")

@bot.command()
async def stop(ctx: commands.Context):
    """Stop playback and disconnect."""
    player: wavelink.Player = ctx.voice_client
    if not player:
        return await ctx.send("Bot not connected.")
    
    await player.stop()
    await player.disconnect()
    await ctx.send("Disconnected.")
    
    # Update web interface that nothing is playing
    await ws_client.send_now_playing("No track playing")

# Add a cleanup handler for the WebSocket client
@bot.event
async def on_close():
    """Clean up resources when the bot is shutting down."""
    await ws_client.close()

# Run your bot
if __name__ == "__main__":
    bot.run(token)
