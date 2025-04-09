import wavelink
import os
from dotenv import load_dotenv

load_dotenv()

# Lavalink configuration
LAVALINK_HOST = os.getenv("LAVALINK_HOST")
LAVALINK_PORT = os.getenv("LAVALINK_PORT")
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD")
LAVALINK_SECURE = os.getenv("LAVALINK_SECURE", "false").lower() == "true"

async def connect_lavalink(bot):
    """Connect to the Lavalink node using the provided bot instance."""
    protocol = "https" if LAVALINK_SECURE else "http"
    node_uri = f"{protocol}://{LAVALINK_HOST}:{LAVALINK_PORT}"

    node = wavelink.Node(
        uri=node_uri,
        password=LAVALINK_PASSWORD,
    )

    await wavelink.Pool.connect(
        client=bot,
        nodes=[node]
    )

    print(f"[Lavalink] Connected to node at {node_uri}")
