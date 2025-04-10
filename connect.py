# connect.py
import asyncio
import json
import aiohttp
import os
from dotenv import load_dotenv
import wavelink
import traceback 

# Load environment variables
load_dotenv()
API_SECRET = os.getenv("API_SECRET")
WS_URL = os.getenv("WS_URL", "ws://127.0.0.1:8000/ws/nowplaying")

# Shared state between modules
BOT_STATE = {
    "bot": None,  # Will store the bot instance
    "current_track": None,  # Will store the current track info
    "queue": []  # Will store the current queue
}

class WSNowPlayingClient:
    """Client to connect to the FastAPI backend via WebSocket."""
    
    def __init__(self):
        self.ws = None
        self.connected = False
        self.reconnect_interval = 5  # seconds
        self.headers = {"x-api-token": API_SECRET}
        self.session = None
    
    async def connect(self):
        """Connect to the FastAPI WebSocket endpoint with a max of 5 attempts."""
        attempt = 0
        while attempt < 5:
            try:
                if not self.connected:
                    print(f"Connecting to WebSocket server at {WS_URL} (Attempt {attempt + 1}/5)")
                    # Create a new session if needed
                    if self.session is None or self.session.closed:
                        self.session = aiohttp.ClientSession()
                    
                    self.ws = await self.session.ws_connect(WS_URL, headers=self.headers)
                    self.connected = True
                    print("Connected to WebSocket server!")
                    
                    # Start the listener for incoming messages
                    task = asyncio.create_task(self.listen())
                    task.add_done_callback(self._task_exception_handler)
                    return   # Successfully connected, exit the connect loop
            except Exception as e:
                attempt += 1
                print(f"Failed to connect to WebSocket on attempt {attempt}")
                self.connected = False
                
                # Close session if it exists and is open
                if self.session and not self.session.closed:
                    await self.session.close()
                self.session = None

                # Wait before trying again if we haven't reached the maximum attempts yet
                if attempt < 5:
                    await asyncio.sleep(self.reconnect_interval)
        
        # Handle the case when maximum attempts have been reached
        print("Unable to connect to the WebSocket server.")
        # Optionally, raise an exception or perform other error handling here.
        return
    
    # In connect.py - Fix the handle_command method:
# In connect.py - Fix the handle_command method:
    async def handle_command(self, data):
        try:
            command = json.loads(data)
            action = command.get("action")
            payload = command.get("payload", {})
            bot = BOT_STATE.get("bot")

            if not bot:
                print("Bot not initialized yet")
                return
            
            print(f"Processing command: {action} with payload: {payload}")
            
            # Find active player and guild
            active_player = None
            active_guild = None
            active_channel = None
            
            for guild in bot.guilds:
                # Find a guild with an active voice client
                if guild.voice_client and isinstance(guild.voice_client, wavelink.Player):
                    active_player = guild.voice_client
                    active_guild = guild
                    active_channel = next((channel for channel in guild.text_channels), None)
                    break
            
            # If no active guild found but we have guilds, use the first one
            if not active_guild and bot.guilds:
                active_guild = bot.guilds[0]
                active_channel = next((channel for channel in active_guild.text_channels), None)
            
            # For "stop" command, we proceed even if no active_player
            if action == "stop":
                if active_player:
                    await active_player.stop()
                    await active_player.disconnect()
                    print("Stopped playback and disconnected")
                else:
                    print("No active player to stop")
                
                # Update web interface that nothing is playing
                await self.send_now_playing("No track playing")
                
                # Notify in Discord if channel is available
                if active_channel:
                    await active_channel.send("⏹️ Stopped playback and disconnected.")
                
                return
            
            # For search command, handle specially since we might need to connect
            if action == "search":
                await self.handle_search(query=payload.get("query"), 
                                        bot=bot, 
                                        active_guild=active_guild,
                                        active_player=active_player,
                                        active_channel=active_channel)
                return
            
            # For other commands, we need an active player
            if not active_player:
                print(f"No active player found for command: {action}")
                error_response = {
                    "type": "command_response",
                    "action": action,
                    "success": False,
                    "message": "Bot is not connected to a voice channel"
                }
                await self.ws.send_str(json.dumps(error_response))
                return
            
            # Handle other commands
            success = True
            message = f"Command {action} executed"
            
            if action == "play_pause":
                # Fixed: Use .playing property instead of is_playing() method
                if active_player.playing:
                    if active_player.paused:  # Use paused property instead of is_paused()
                        await active_player.pause(False)  # False to resume
                        print("Resumed playback")
                        message = "Resumed playback"
                        if active_channel:
                            await active_channel.send("▶️ Resumed playback.")
                    else:
                        await active_player.pause(True)  # True to pause
                        print("Paused playback")
                        message = "Paused playback"
                        if active_channel:
                            await active_channel.send("⏸️ Paused playback.")
                else:
                    success = False
                    message = "Nothing is playing"
            
            elif action == "skip":
                # Fixed: Use .playing property instead of is_playing() method
                if active_player.playing:
                    await active_player.stop()
                    print("Skipped current track")
                    message = "Skipped track"
                    if active_channel:
                        await active_channel.send("⏭️ Skipped current track.")
                else:
                    success = False
                    message = "Nothing is playing"
                    
            elif action == "previous":
                # Previous track is not natively supported by wavelink
                # Would need to keep track of history yourself
                success = False
                message = "Previous track function not implemented"
                print("Previous track not implemented yet")
            
            elif action == "loop":
                # Toggle loop mode
                if hasattr(active_player, 'queue'):
                    active_player.queue.loop = not getattr(active_player.queue, 'loop', False)
                    loop_status = "enabled" if active_player.queue.loop else "disabled"
                    message = f"Loop mode {loop_status}"
                    print(f"Loop mode {loop_status}")
                    if active_channel:
                        await active_channel.send(f"🔄 Loop mode {loop_status}.")
                else:
                    success = False
                    message = "Queue not available for looping"
            
            elif action == "shuffle":
                if hasattr(active_player, 'queue') and active_player.queue:
                    active_player.queue.shuffle()
                    message = "Queue shuffled"
                    print("Queue shuffled")
                    if active_channel:
                        await active_channel.send("🔀 Queue shuffled.")
                    
                    # Update the queue display
                    await self.send_queue_update(active_player)
                else:
                    success = False
                    message = "Queue empty or not available"
            
            # Send response back to web interface
            response = {
                "type": "command_response",
                "action": action,
                "success": success,
                "message": message
            }
            await self.ws.send_str(json.dumps(response))
            
        except json.JSONDecodeError:
            print(f"Failed to parse command: {data}")
        except Exception as e:
            print(f"Error handling command: {e}")
            traceback_str = traceback.format_exc()
            print(traceback_str)

    async def listen(self):
        """Listen for messages from the server."""
        try:
            async for msg in self.ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    print(f"Received message from web server: {msg.data}")
                    # Handle commands from the web interface here
                    await self.handle_command(msg.data)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    print(f"WebSocket connection closed or error: {msg.type}")
                    self.connected = False
                    break
        except Exception as e:
            print(f"Error in WebSocket listener: {e}")
            self.connected = False
        finally:
            print("WebSocket listener ended, will attempt to reconnect...")
            # Close the current session
            if self.session and not self.session.closed:
                await self.session.close()
            self.session = None
            self.connected = False
            # Schedule reconnection
            asyncio.create_task(self.connect())            


    async def handle_search(self, query, bot, active_guild, active_player, active_channel):
        """Handle search request from web interface."""
        if not query:
            return
        
        print(f"Searching for: {query}")
        
        try:
            # If no active guild, can't proceed
            if not active_guild:
                print("No guild available for bot")
                search_response = {
                    "type": "search_result",
                    "success": False,
                    "message": "Bot is not in any server"
                }
                await self.ws.send_str(json.dumps(search_response))
                return
            
            # If no active player, try to join a voice channel
            if not active_player:
                # Find a suitable voice channel in the active guild
                voice_channel = None
                for channel in active_guild.voice_channels:
                    if channel.members:  # Join where people are
                        voice_channel = channel
                        break
                
                # If no channel with members, take the first one
                if not voice_channel and active_guild.voice_channels:
                    voice_channel = active_guild.voice_channels[0]
                
                if voice_channel:
                    try:
                        active_player = await voice_channel.connect(cls=wavelink.Player)
                        print(f"Connected to voice channel: {voice_channel.name}")
                    except Exception as connect_error:
                        print(f"Error connecting to voice channel: {connect_error}")
                        search_response = {
                            "type": "search_result",
                            "success": False,
                            "message": f"Error connecting to voice: {str(connect_error)}"
                        }
                        await self.ws.send_str(json.dumps(search_response))
                        return
                else:
                    print("No voice channels available in guild")
                    search_response = {
                        "type": "search_result",
                        "success": False,
                        "message": "No voice channels available"
                    }
                    await self.ws.send_str(json.dumps(search_response))
                    return
            
            # Now search and play the track
            tracks = await wavelink.Playable.search(query)
            
            if not tracks:
                print("No tracks found")
                search_response = {
                    "type": "search_result",
                    "success": False,
                    "message": "No tracks found"
                }
                await self.ws.send_str(json.dumps(search_response))
                return
            
            track = tracks[0]  # Take the first result
            
            # If already playing, add to queue
            if active_player.playing:
                active_player.queue.put(track)
                message = f"Added to queue: {track.title}"
                print(message)
                
                # Update queue display
                await self.send_queue_update(active_player)
                
                # Notify in Discord
                if active_channel:
                    await active_channel.send(f"🎵 Added to queue: `{track.title}`")
            else:
                # Start playing
                await active_player.play(track)
                message = f"Now playing: {track.title}"
                print(message)
                
                # Notify in Discord
                if active_channel:
                    await active_channel.send(f"🎶 Now playing: `{track.title}`")
            
            # Send response to web interface
            search_response = {
                "type": "search_result",
                "success": True,
                "message": message,
                "track": {
                    "title": track.title,
                    "artist": track.author,
                    "thumbnail": getattr(track, "artwork", None) or getattr(track, "thumbnail", None) or "https://i.imgur.com/opTLRNC.png",
                    "duration": int(track.length / 1000) if hasattr(track, 'length') else 0
                }
            }
            await self.ws.send_str(json.dumps(search_response))
            
        except Exception as e:
            print(f"Error in search handler: {e}")
            import traceback
            traceback_str = traceback.format_exc()
            print(traceback_str)
            
            search_response = {
                "type": "search_result",
                "success": False,
                "message": f"Error: {str(e)}"
            }
            await self.ws.send_str(json.dumps(search_response))
    
    # In connect.py - Update the send_now_playing method to use a default image when the thumbnail URL fails:
    async def send_now_playing(self, track_info=None, track=None):
        if not self.connected:
            print("Not connected to WebSocket server, can't send now playing info")
            return
            
        # Always use Speechless.png for thumbnail
        default_thumbnail = "/static/images/Speechless.png"
            
        # If a wavelink track object is provided, format it properly
        if track:
            # Format for wavelink v3
            message = {
                "type": "now_playing",
                "title": track.title,
                "artist": track.author,
                "thumbnail": default_thumbnail,
                "duration": int(track.length / 1000) if hasattr(track, 'length') else 0,
                "position": 0  # You'd need to track this separately
            }
        else:
            # Simple string format if no track object
            message = {
                "type": "now_playing",
                "title": track_info or "No track playing",
                "artist": "",
                "thumbnail": default_thumbnail,
                "duration": 0,
                "position": 0
            }
            
        # Store current track info in the global state
        BOT_STATE["current_track"] = message
            
        try:
            await self.ws.send_str(json.dumps(message))
            print(f"Sent now playing: {message['title']}")
        except Exception as e:
            print(f"Failed to send now playing info: {e}")
            self.connected = False

    async def send_queue_update(self, player=None):
        """Send queue information to the web interface."""
        if not self.connected:
            print("Not connected to WebSocket server, can't send queue update")
            return
        
        queue_tracks = []
        if player and hasattr(player, 'queue'):
            queue_tracks = [{"title": track.title, "artist": track.author} for track in player.queue]
        
        message = {
            "type": "queue_update",
            "queue": queue_tracks
        }
        
        try:
            await self.ws.send_str(json.dumps(message))
            print(f"Sent queue update with {len(queue_tracks)} tracks")
        except Exception as e:
            print(f"Failed to send queue update: {e}")
            self.connected = False
            
    async def close(self):
        """Close the WebSocket connection and clean up."""
        if self.ws:
            await self.ws.close()
        if self.session and not self.session.closed:
            await self.session.close()
        self.connected = False
        print("WebSocket connection closed")


