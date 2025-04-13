import aiohttp
import asyncio
import json
import os
import time
import traceback
import wavelink
from typing import Optional, Dict, Any
from dotenv import load_dotenv
load_dotenv()

# Global state to share between modules
BOT_STATE = {
    "bot": None  # Will be set to the bot instance in on_ready
}

class WSNowPlayingClient:
    """Client for sending updates to the web interface via WebSocket"""
    
    def __init__(self):
        self.ws = None
        self.connected = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.last_heartbeat = 0
        self.heartbeat_interval = 30  # seconds
        self.ws_url = None
        self.ws_ip = os.getenv("WS_IP")
        
        # Define allowed sources
        self.allowed_sources = [
            "100.20.92.101",
            "44.225.181.72", 
            "44.227.217.144",
            "cloudflare-website.onrender.com"
        ]
        
        # Validate the WS_IP from env
        if not self.ws_ip or self.ws_ip not in self.allowed_sources:
            self.ws_ip = self.allowed_sources[0]  # Use default if invalid
            
    async def connect(self):
        """Connect to the WebSocket server with retry logic"""
        self.reconnect_attempts = 0
        await self._attempt_connection()
        
    async def _attempt_connection(self):
        """Try to connect to the WebSocket server"""
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            print(f"Max reconnection attempts ({self.max_reconnect_attempts}) reached. Giving up.")
            return
            
        self.reconnect_attempts += 1
        
        # Determine if it's an IP or domain
        if self.ws_ip in self.allowed_sources and not self.ws_ip.replace('.', '').isdigit():
            # It's a domain name
            source_desc = f"Domain: {self.ws_ip}"
            # Use secure WebSocket for domain names
            self.ws_url = f"wss://{self.ws_ip}/ws/nowplaying"
        else:
            # It's an IP address
            source_desc = f"IP: {self.ws_ip}"
            # Use regular WebSocket for IP addresses
            self.ws_url = f"ws://{self.ws_ip}/ws/nowplaying"
            
        print(f"Connecting to WebSocket server at {self.ws_url} ({source_desc}) (Attempt {self.reconnect_attempts}/{self.max_reconnect_attempts})")
        
        try:
            # Use API secret for authentication
            headers = {"x-api-token": os.getenv("API_SECRET", "")}
            
            # Create session and connect
            session = aiohttp.ClientSession()
            self.ws = await session.ws_connect(self.ws_url, headers=headers, timeout=30)
            
            self.connected = True
            print(f"Connected to WebSocket server! Source: {source_desc}")
            
            # Start the receive loop and heartbeat
            asyncio.create_task(self._receive_loop(source_desc))
            asyncio.create_task(self._heartbeat_loop(source_desc))
            
        except Exception as e:
            print(f"Failed to connect to WebSocket server: {e}")
            # Wait before retrying
            await asyncio.sleep(5)
            # Retry connection
            asyncio.create_task(self._attempt_connection())
            
    async def _receive_loop(self, source_desc: str):
        """Continuously receive messages from the WebSocket"""
        if not self.ws:
            return
            
        print(f"Listening for messages from {source_desc}")
        
        try:
            async for msg in self.ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        print(f"Received message from {source_desc}: {data}")
                        
                        # Process commands from web interface
                        if data.get("action") == "status_request":
                            # Send current status
                            await self._send_status_response()
                            
                    except json.JSONDecodeError:
                        print(f"Received invalid JSON from {source_desc}")
                    except Exception as e:
                        print(f"Error processing message from {source_desc}: {e}")
                        
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    print(f"WebSocket connection error: {msg.data}")
                    break
                    
        except Exception as e:
            print(f"Error in receive loop: {e}")
            
        finally:
            self.connected = False
            # Try to reconnect after connection is closed
            await asyncio.sleep(5)
            asyncio.create_task(self._attempt_connection())
            
    async def _heartbeat_loop(self, source_desc: str):
        """Send periodic heartbeats to keep the connection alive"""
        while self.connected and self.ws:
            try:
                # Send a heartbeat message
                await self.ws.send_json({"type": "heartbeat"})
                print(f"Heartbeat sent to {source_desc}")
                self.last_heartbeat = time.time()
                
                # Wait for the next interval
                await asyncio.sleep(self.heartbeat_interval)
                
            except Exception as e:
                print(f"Error sending heartbeat: {e}")
                # If we can't send heartbeats, the connection might be dead
                self.connected = False
                break
                
    async def _send_status_response(self):
        """Send a response to a status request"""
        try:
            # Get the current player state
            bot = BOT_STATE.get("bot")
            if not bot:
                print("Bot not available for status")
                return
                
            # Get the first voice client as our player
            player = None
            for voice_client in bot.voice_clients:
                if isinstance(voice_client, wavelink.Player):
                    player = voice_client
                    break
                    
            # Prepare response data
            if player and player.playing:
                track = player.current
                track_info = {
                    "title": track.title,
                    "artist": track.author,
                    "duration": track.length / 1000,  # Convert to seconds
                    "thumbnail": "static/images/Speechless.png",  # Use default image
                    "position": player.position / 1000  # Convert to seconds
                }
                
                # Also include queue information
                queue = []
                if player.queue:
                    for idx, track in enumerate(list(player.queue)[:10]):
                        queue.append({
                            "title": track.title,
                            "artist": track.author,
                            "duration": track.length / 1000
                        })
                        
                # Send response
                await self.send_now_playing(track=track)
                await self.send_queue_update(player)
                
                # Send command response
                await self.ws.send_json({
                    "type": "command_response",
                    "action": "status_request",
                    "success": True,
                    "message": "Command status request processed"
                })
            else:
                # No track playing
                await self.send_now_playing("No track playing")
                await self.send_queue_update(None)
                
                # Send command response
                await self.ws.send_json({
                    "type": "command_response",
                    "action": "status_request",
                    "success": True,
                    "message": "Command status request processed - No track playing"
                })
                
        except Exception as e:
            print(f"Error sending status response: {str(e)}")
            # Try to send error response
            try:
                if self.ws:
                    await self.ws.send_json({
                        "type": "command_response",
                        "action": "status_request",
                        "success": False,
                        "message": f"Error processing status request: {str(e)}"
                    })
            except:
                pass
                
    async def send_now_playing(self, track=None):
        """Send now playing information to the web interface"""
        if not self.connected or not self.ws:
            return
            
        try:
            if isinstance(track, str):
                # Handle "No track playing" case
                message = {
                    "type": "now_playing",
                    "title": track,
                    "artist": "",
                    "duration": 0,
                    "thumbnail": "static/images/Speechless.png"
                }
            elif track:
                # Handle track object
                message = {
                    "type": "now_playing",
                    "title": track.title,
                    "artist": track.author,
                    "duration": track.length / 1000,  # Convert to seconds
                    "thumbnail": "static/images/Speechless.png"  # Use default image
                }
            else:
                # Empty state
                message = {
                    "type": "now_playing",
                    "title": "No track playing",
                    "artist": "",
                    "duration": 0,
                    "thumbnail": "static/images/Speechless.png"
                }
                
            # Send the message
            await self.ws.send_json(message)
            print(f"Sent now playing update: {message['title']}")
            
        except Exception as e:
            print(f"Error sending now playing update: {e}")
            self.connected = False
            
    async def send_queue_update(self, player):
        """Send queue information to the web interface"""
        if not self.connected or not self.ws:
            return
            
        try:
            queue = []
            if player and player.queue:
                # Convert queue items to simplified dicts
                for track in list(player.queue)[:10]:  # Only send first 10 items
                    queue.append({
                        "title": track.title,
                        "artist": track.author,
                        "duration": track.length / 1000  # Convert to seconds
                    })
                    
            # Create the message
            message = {
                "type": "queue_update",
                "queue": queue
            }
            
            # Send the update
            await self.ws.send_json(message)
            print(f"Sent queue update with {len(queue)} items")
            
        except Exception as e:
            print(f"Error sending queue update: {e}")
            self.connected = False
            
    async def close(self):
        """Close the WebSocket connection cleanly"""
        if self.ws:
            await self.ws.close()
            self.connected = False
            print("WebSocket connection closed")
