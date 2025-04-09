# connect.py
import asyncio
import json
import aiohttp
import os
from dotenv import load_dotenv
import wavelink

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
        """Connect to the FastAPI WebSocket endpoint."""
        while True:
            try:
                if not self.connected:
                    print(f"Connecting to WebSocket server at {WS_URL}")
                    # Create a new session if needed
                    if self.session is None or self.session.closed:
                        self.session = aiohttp.ClientSession()
                    
                    self.ws = await self.session.ws_connect(WS_URL, headers=self.headers)
                    self.connected = True
                    print("Connected to WebSocket server!")
                    
                    # Start the listener for incoming messages
                    asyncio.create_task(self.listen())
                    return  # Successfully connected, exit the connect loop
            except Exception as e:
                print(f"Failed to connect to WebSocket: {e}")
                self.connected = False
                
                # Close session if it exists and is open
                if self.session and not self.session.closed:
                    await self.session.close()
                self.session = None
                
                # Wait before trying again
                await asyncio.sleep(self.reconnect_interval)
    
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
    
    async def handle_command(self, data):
        """Handle commands received from the web interface."""
        try:
            command = json.loads(data)
            action = command.get("action")
            payload = command.get("payload", {})
            bot = BOT_STATE.get("bot")
            
            if not bot:
                print("Bot not initialized yet")
                return
            
            print(f"Processing command: {action} with payload: {payload}")
                
            # Find active voice client (assume only one per bot for now)
            active_player = None
            for guild in bot.guilds:
                if guild.voice_client and isinstance(guild.voice_client, wavelink.Player):
                    active_player = guild.voice_client
                    break
            
            if action == "play_pause" and active_player:
                # Toggle play/pause on the current player
                if active_player.is_playing():
                    if active_player.is_paused():
                        await active_player.resume()
                        print("Resumed playback")
                    else:
                        await active_player.pause()
                        print("Paused playback")
            
            elif action == "skip" and active_player:
                # Skip the current track
                if active_player.is_playing():
                    await active_player.stop()
                    print("Skipped current track")
            
            elif action == "previous" and active_player:
                # Previous track functionality would require track history
                print("Previous track not implemented yet")
            
            elif action == "stop" and active_player:
                # Stop playback and disconnect
                await active_player.stop()
                await active_player.disconnect()
                print("Stopped playback and disconnected")
                # Update web interface that nothing is playing
                await self.send_now_playing("No track playing")
            
            elif action == "loop" and active_player:
                # Toggle loop mode if queue is available
                if hasattr(active_player, 'queue'):
                    active_player.queue.loop = not getattr(active_player.queue, 'loop', False)
                    loop_status = "enabled" if active_player.queue.loop else "disabled"
                    print(f"Loop mode {loop_status}")
            
            elif action == "shuffle" and active_player:
                # Shuffle the queue if available
                if hasattr(active_player, 'queue') and len(active_player.queue) > 1:
                    active_player.queue.shuffle()
                    print("Queue shuffled")
                    
            elif action == "search":
                # Handle search request
                query = payload.get("query")
                if query:
                    print(f"Searching for: {query}")
                    # Search functionality would be implemented here
                    # For now, just acknowledge the search
                    search_response = {
                        "type": "search_result",
                        "query": query,
                        "results": ["Song would be searched here"]
                    }
                    await self.ws.send_str(json.dumps(search_response))
            
        except json.JSONDecodeError:
            print(f"Failed to parse command: {data}")
        except Exception as e:
            print(f"Error handling command: {e}")
    
    async def send_now_playing(self, track_info=None, track=None):
        """Send now playing information to the web interface."""
        if not self.connected:
            print("Not connected to WebSocket server, can't send now playing info")
            return
            
        # If a wavelink track object is provided, format it properly
        if track:
            # Format for wavelink v3
            message = {
                "type": "now_playing",
                "title": track.title,
                "artist": track.author,
                "thumbnail": getattr(track, "artwork", None) or getattr(track, "thumbnail", None) or "https://i.imgur.com/opTLRNC.png",
                "duration": int(track.length / 1000) if hasattr(track, 'length') else 0,
                "position": 0  # You'd need to track this separately
            }
        else:
            # Simple string format if no track object
            message = {
                "type": "now_playing",
                "title": track_info or "No track playing",
                "artist": "",
                "thumbnail": "https://i.imgur.com/opTLRNC.png",
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