"""
FastAPI + WebSocket Dashboard for Vienna Voice Agent.

Run with: python -m dashboard.server
Then open: http://localhost:8080
"""

import asyncio
import json
import subprocess
import sys
import os
from pathlib import Path
from typing import Optional, Set
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = Path(__file__).parent
STATIC_DIR = DASHBOARD_DIR / "static"

# Global state
agent_process: Optional[subprocess.Popen] = None
connected_clients: Set[WebSocket] = set()
current_state = {
    "running": False,
    "speaker_verified": False,
    "speaker_score": 0.0,
    "audio_level": -80.0,
    "vad_speaking": False
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    print("🚀 Dashboard server starting...")
    print(f"   Static files: {STATIC_DIR}")
    yield
    # Cleanup on shutdown
    if agent_process and agent_process.poll() is None:
        print("🛑 Stopping agent process...")
        agent_process.terminate()
        agent_process.wait(timeout=5)
    print("👋 Dashboard server stopped.")


app = FastAPI(title="DoQui Dashboard", lifespan=lifespan)


# ============ WebSocket Management ============

async def broadcast(message: dict):
    """Broadcast message to all connected clients."""
    if not connected_clients:
        return
    data = json.dumps(message)
    disconnected = set()
    for client in connected_clients:
        try:
            await client.send_text(data)
        except:
            disconnected.add(client)
    connected_clients.difference_update(disconnected)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket connection for real-time updates."""
    await websocket.accept()
    connected_clients.add(websocket)
    
    # Send current state on connect
    await websocket.send_text(json.dumps({
        "type": "state",
        **current_state
    }))
    
    try:
        while True:
            # Keep connection alive, handle incoming messages
            data = await websocket.receive_text()
            msg = json.loads(data)
            
            if msg.get("type") == "start":
                await start_agent()
            elif msg.get("type") == "stop":
                await stop_agent()
                
    except WebSocketDisconnect:
        connected_clients.discard(websocket)


# ============ Agent Control ============

async def start_agent():
    """Start the Vienna agent subprocess."""
    global agent_process, current_state
    
    if agent_process and agent_process.poll() is None:
        return  # Already running
    
    # Set environment variable for dashboard mode
    env = os.environ.copy()
    env["VIENNA_DASHBOARD_MODE"] = "1"
    
    # Start the agent
    agent_process = subprocess.Popen(
        [sys.executable, "src/main.py", "console"],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    
    current_state["running"] = True
    await broadcast({"type": "status", "running": True})
    
    # Start log reader task
    asyncio.create_task(read_agent_output())


async def stop_agent():
    """Stop the Vienna agent subprocess."""
    global agent_process, current_state
    
    if agent_process and agent_process.poll() is None:
        agent_process.terminate()
        try:
            agent_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            agent_process.kill()
    
    agent_process = None
    current_state["running"] = False
    current_state["speaker_verified"] = False
    current_state["speaker_score"] = 0.0
    current_state["vad_speaking"] = False
    
    await broadcast({"type": "status", "running": False})


async def read_agent_output():
    """Read and parse agent output for real-time updates."""
    global current_state
    
    if not agent_process:
        return
    
    loop = asyncio.get_event_loop()
    
    while agent_process and agent_process.poll() is None:
        try:
            line = await loop.run_in_executor(None, agent_process.stdout.readline)
            if not line:
                break
            
            # Parse log lines for status updates
            line = line.strip()
            
            # Audio level detection
            if "[Audio]" in line and "dBFS" in line:
                try:
                    # Extract dBFS value
                    start = line.find("[-") + 1
                    end = line.find(" dBFS")
                    if start > 0 and end > start:
                        level = float(line[start:end])
                        current_state["audio_level"] = level
                        await broadcast({"type": "audio", "level": level})
                except:
                    pass
            
            # Speaker verification
            if "Speaker VERIFIED" in line or "🎯" in line:
                try:
                    score_idx = line.find("score=")
                    if score_idx > 0:
                        score_str = line[score_idx+6:score_idx+10]
                        score = float(score_str)
                        current_state["speaker_verified"] = True
                        current_state["speaker_score"] = score
                        await broadcast({
                            "type": "speaker",
                            "verified": True,
                            "score": score
                        })
                except:
                    pass
            
            # Unknown speaker
            if "Unknown speaker" in line or "👤" in line:
                try:
                    score_idx = line.find("score=")
                    if score_idx > 0:
                        score_str = line[score_idx+6:score_idx+10]
                        score = float(score_str)
                        current_state["speaker_verified"] = False
                        current_state["speaker_score"] = score
                        await broadcast({
                            "type": "speaker",
                            "verified": False,
                            "score": score
                        })
                except:
                    pass
            
            # VAD speech detection
            if "START_OF_SPEECH" in line or "🎤" in line:
                current_state["vad_speaking"] = True
                await broadcast({"type": "vad", "speaking": True})
            
            if "END_OF_SPEECH" in line or "🔇" in line:
                current_state["vad_speaking"] = False
                await broadcast({"type": "vad", "speaking": False})
            
            # Voice lock status
            if "VOICE LOCK ACTIVE" in line or "🔒" in line:
                current_state["speaker_verified"] = False
                await broadcast({
                    "type": "speaker",
                    "verified": False,
                    "score": current_state["speaker_score"]
                })
            
            if "VOICE LOCK: Speaker verified" in line or "🔓" in line:
                current_state["speaker_verified"] = True
                await broadcast({
                    "type": "speaker",
                    "verified": True,
                    "score": current_state["speaker_score"]
                })
                
        except Exception as e:
            print(f"Log reader error: {e}")
            break
    
    # Agent stopped
    current_state["running"] = False
    await broadcast({"type": "status", "running": False})


# ============ REST API ============

@app.get("/api/status")
async def get_status():
    """Get current agent status."""
    return JSONResponse(current_state)


@app.post("/api/start")
async def api_start():
    """Start the agent via REST API."""
    await start_agent()
    return {"status": "started"}


@app.post("/api/stop")
async def api_stop():
    """Stop the agent via REST API."""
    await stop_agent()
    return {"status": "stopped"}


# ============ Static Files ============

@app.get("/")
async def index():
    """Serve the main dashboard page."""
    return FileResponse(STATIC_DIR / "index.html")


# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ============ Main ============

def main():
    """Run the dashboard server."""
    print("=" * 50)
    print("🌐 DoQui Dashboard")
    print("=" * 50)
    print(f"Open: http://localhost:8080")
    print("Press Ctrl+C to stop")
    print("=" * 50)
    
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8080,
        log_level="warning"
    )


if __name__ == "__main__":
    main()
