"""
Eagle Gate: Background Process for Speaker Recognition

This module runs Eagle in a separate process to bypass the Windows DLL loading
issue in LiveKit's subprocess. It communicates via multiprocessing queues.

Usage:
    from eagle_gate import EagleGate
    
    gate = EagleGate(access_key, profile_path)
    gate.start()
    
    # Check speaker
    score = gate.verify_speaker(audio_samples)
    if score > 0.5:
        print("Speaker verified!")
    
    gate.stop()
"""

import os
import sys
import time
import logging
import multiprocessing as mp
from multiprocessing import Process, Queue
from typing import Optional, List
from dataclasses import dataclass

logger = logging.getLogger("eagle_gate")

# Message types for IPC
@dataclass
class AudioRequest:
    """Request to verify audio samples."""
    request_id: int
    samples: List[int]  # PCM samples

@dataclass
class ScoreResponse:
    """Response with speaker score."""
    request_id: int
    score: float  # 0.0 to 1.0, or -1.0 on error
    error: Optional[str] = None

@dataclass
class ShutdownRequest:
    """Request to shut down the Eagle process."""
    pass


def _eagle_worker(
    access_key: str,
    profile_path: str,
    request_queue: Queue,
    response_queue: Queue,
    ready_event: mp.Event
):
    """
    Worker function that runs in a separate process.
    This is where Eagle is initialized and used.
    """
    eagle = None
    
    try:
        # Set up DLL directory
        import platform
        if platform.system() == "Windows":
            site_packages = os.path.join(sys.prefix, "Lib", "site-packages")
            eagle_dll_dir = os.path.join(site_packages, "pveagle", "lib", "windows", "amd64")
            if os.path.exists(eagle_dll_dir):
                os.environ["PATH"] = eagle_dll_dir + os.pathsep + os.environ.get("PATH", "")
                if hasattr(os, "add_dll_directory"):
                    os.add_dll_directory(eagle_dll_dir)
        
        # Import and initialize Eagle
        import pveagle
        
        with open(profile_path, "rb") as f:
            profile_bytes = f.read()
        
        profile = pveagle.EagleProfile.from_bytes(profile_bytes)
        eagle = pveagle.create_recognizer(
            access_key=access_key,
            speaker_profiles=[profile]
        )
        
        frame_length = eagle.frame_length
        print(f"[EagleGate] Initialized successfully (frame_length={frame_length})")
        
        # Signal that we're ready
        ready_event.set()
        
        # Process loop
        while True:
            try:
                request = request_queue.get(timeout=1.0)
                
                if isinstance(request, ShutdownRequest):
                    print("[EagleGate] Shutdown requested")
                    break
                
                if isinstance(request, AudioRequest):
                    try:
                        # Process audio and get score
                        samples = request.samples
                        scores = eagle.process(samples)
                        score = scores[0] if scores else 0.0
                        
                        # Debug: log audio stats and score
                        if len(samples) > 0:
                            max_val = max(abs(min(samples)), abs(max(samples)))
                            print(f"[EagleGate] Processed {len(samples)} samples, max={max_val}, score={score:.3f}")
                        
                        response_queue.put(ScoreResponse(
                            request_id=request.request_id,
                            score=score
                        ))
                    except Exception as e:
                        response_queue.put(ScoreResponse(
                            request_id=request.request_id,
                            score=-1.0,
                            error=str(e)
                        ))
                        
            except mp.queues.Empty:
                continue
            except Exception as e:
                print(f"[EagleGate] Error processing request: {e}")
                
    except Exception as e:
        print(f"[EagleGate] Failed to initialize: {e}")
        import traceback
        traceback.print_exc()
        ready_event.set()  # Signal anyway so main process doesn't hang
        
    finally:
        if eagle:
            eagle.delete()
        print("[EagleGate] Worker exiting")


class EagleGate:
    """
    Eagle Gate - Speaker Recognition in a Background Process
    
    This class manages a background process that runs Eagle for speaker
    recognition. It's designed to work around the Windows DLL loading
    issue in LiveKit's subprocess.
    """
    
    def __init__(
        self,
        access_key: str,
        profile_path: str,
        timeout: float = 15.0  # Increased timeout for DLL loading in subprocess
    ):
        self.access_key = access_key
        self.profile_path = profile_path
        self.timeout = timeout
        
        self._process: Optional[Process] = None
        self._request_queue: Optional[Queue] = None
        self._response_queue: Optional[Queue] = None
        self._ready_event: Optional[mp.Event] = None
        self._request_counter = 0
        self._pending_requests: dict = {}
        self._is_running = False
    
    def start(self) -> bool:
        """Start the Eagle background process."""
        if self._is_running:
            return True
        
        try:
            ctx = mp.get_context("spawn")
            
            self._request_queue = ctx.Queue()
            self._response_queue = ctx.Queue()
            self._ready_event = ctx.Event()
            
            self._process = ctx.Process(
                target=_eagle_worker,
                args=(
                    self.access_key,
                    self.profile_path,
                    self._request_queue,
                    self._response_queue,
                    self._ready_event
                ),
                name="eagle_gate_worker",
                daemon=True
            )
            self._process.start()
            
            # Wait for initialization
            if self._ready_event.wait(timeout=self.timeout):
                # Check if process is still alive
                if self._process.is_alive():
                    self._is_running = True
                    logger.info("✅ EagleGate started successfully")
                    return True
                else:
                    logger.warning("⚠️ EagleGate process died during initialization")
                    return False
            else:
                logger.warning("⚠️ EagleGate initialization timed out")
                return False
                
        except Exception as e:
            logger.error(f"❌ Failed to start EagleGate: {e}")
            return False
    
    def stop(self):
        """Stop the Eagle background process."""
        if not self._is_running:
            return
        
        try:
            if self._request_queue:
                self._request_queue.put(ShutdownRequest())
            
            if self._process:
                self._process.join(timeout=2.0)
                if self._process.is_alive():
                    self._process.terminate()
                    
        except Exception as e:
            logger.error(f"Error stopping EagleGate: {e}")
            
        finally:
            self._is_running = False
            self._process = None
            self._request_queue = None
            self._response_queue = None
    
    def is_running(self) -> bool:
        """Check if Eagle Gate is running."""
        return self._is_running and self._process is not None and self._process.is_alive()
    
    def verify_speaker(self, audio_samples: List[int], timeout: float = 0.5) -> float:
        """
        Verify speaker from audio samples.
        
        Args:
            audio_samples: List of 16-bit PCM samples (512 samples = 32ms at 16kHz)
            timeout: Max time to wait for response
            
        Returns:
            Speaker score (0.0 to 1.0), or -1.0 on error
        """
        if not self.is_running():
            return -1.0
        
        try:
            self._request_counter += 1
            request_id = self._request_counter
            
            # Send request
            self._request_queue.put(AudioRequest(
                request_id=request_id,
                samples=audio_samples
            ))
            
            # Wait for response
            start_time = time.time()
            while time.time() - start_time < timeout:
                try:
                    response = self._response_queue.get(timeout=0.01)
                    if isinstance(response, ScoreResponse):
                        if response.request_id == request_id:
                            return response.score
                except mp.queues.Empty:
                    continue
            
            return -1.0  # Timeout
            
        except Exception as e:
            logger.error(f"Error in verify_speaker: {e}")
            return -1.0
    
    def verify_speaker_async(self, audio_samples: List[int]) -> int:
        """
        Submit audio for async verification.
        
        Returns:
            Request ID for later retrieval
        """
        if not self.is_running():
            return -1
        
        self._request_counter += 1
        request_id = self._request_counter
        
        self._request_queue.put(AudioRequest(
            request_id=request_id,
            samples=audio_samples
        ))
        
        return request_id
    
    def get_result(self, timeout: float = 0.01) -> Optional[ScoreResponse]:
        """Get a result from the response queue (non-blocking)."""
        if not self.is_running():
            return None
        
        try:
            return self._response_queue.get(timeout=timeout)
        except mp.queues.Empty:
            return None


# Convenience function to create and start Eagle Gate
def create_eagle_gate(
    access_key: Optional[str] = None,
    profile_path: Optional[str] = None
) -> Optional[EagleGate]:
    """
    Create and start an Eagle Gate instance.
    
    Args:
        access_key: Picovoice access key (defaults to env var)
        profile_path: Path to speaker profile (defaults to avijit_profile.eagle)
        
    Returns:
        EagleGate instance if successful, None otherwise
    """
    import pathlib
    from dotenv import load_dotenv
    
    # Load env
    project_root = pathlib.Path(__file__).parent.parent
    for env_path in [project_root / ".env.local", ".env.local"]:
        if env_path.exists():
            load_dotenv(env_path)
            break
    
    # Get access key
    if not access_key:
        access_key = os.getenv("PICOVOICE_ACCESS_KEY")
    
    if not access_key:
        logger.error("No Picovoice access key provided")
        return None
    
    # Get profile path
    if not profile_path:
        profile_path = str(project_root / "avijit_profile.eagle")
    
    if not os.path.exists(profile_path):
        logger.error(f"Speaker profile not found: {profile_path}")
        return None
    
    # Create and start gate
    gate = EagleGate(access_key, profile_path)
    
    if gate.start():
        return gate
    else:
        return None


if __name__ == "__main__":
    # Test Eagle Gate
    print("Testing Eagle Gate...")
    
    gate = create_eagle_gate()
    
    if gate:
        print("Eagle Gate is running!")
        
        # Test with dummy audio (will return low score)
        dummy_samples = [0] * 512
        score = gate.verify_speaker(dummy_samples)
        print(f"Test score (silence): {score}")
        
        gate.stop()
        print("Eagle Gate stopped")
    else:
        print("Failed to start Eagle Gate")
