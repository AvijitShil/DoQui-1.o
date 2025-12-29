"""
PicoSmartVAD: Identity-Gated Voice Activity Detection

This module provides a custom VAD implementation using Picovoice Cobra (VAD)
and Eagle (Speaker Recognition) to create an identity-gated speech detector.

The VAD only emits speech events when:
1. Cobra detects voice activity (probability > 0.5)
2. Eagle recognizes the enrolled speaker (score > 0.5)

Features:
- Extends LiveKit's VAD interface properly
- Circuit breaker pattern: Graceful fallback if Picovoice fails
- Fail-open behavior: If Eagle fails during processing, speech passes through
- 300ms silence threshold for end-of-speech detection
"""

import os
import logging
import asyncio
import time
from typing import Optional, List, Union
from dotenv import load_dotenv

# Load environment variables from project root
# Try multiple paths since the script might run from different directories
import pathlib
_current_dir = pathlib.Path(__file__).parent
_project_root = _current_dir.parent
for _env_path in [_project_root / ".env.local", ".env.local", _current_dir / ".env.local"]:
    if _env_path.exists():
        load_dotenv(_env_path)
        break

logger = logging.getLogger("pico_smart_vad")

# Global state for voice lock feature
# Tracks speaker verification status for the current/last speech segment
class SpeakerVerificationState:
    """Tracks speaker verification status for voice lock feature."""
    def __init__(self):
        self.is_verified = False  # True if verified speaker was detected
        self.max_score = 0.0      # Max speaker score during speech
        self.current_score = 0.0  # Current/latest speaker score
        self.speech_active = False
        
    def start_speech(self):
        """Called when speech starts."""
        self.speech_active = True
        self.max_score = 0.0
        self.is_verified = False
        
    def update_score(self, score: float, threshold: float = 0.5):
        """Update with new speaker score."""
        self.current_score = score
        if score > self.max_score:
            self.max_score = score
        if score >= threshold:
            self.is_verified = True
            
    def end_speech(self):
        """Called when speech ends."""
        self.speech_active = False
        # is_verified remains set until next speech starts

# Global instance - accessible from agent
speaker_state = SpeakerVerificationState()

# CRITICAL: Set up Eagle DLL directory BEFORE importing livekit.agents
# livekit.agents loads DLLs that can interfere with Eagle's DLL loading on Windows
def _setup_eagle_dll_directory_early():
    """Set up Windows DLL search path for Eagle BEFORE other imports."""
    import platform
    import sys
    
    if platform.system() != "Windows":
        return
    
    try:
        # Use sys.prefix which works correctly in venvs
        # This is more reliable than site.getsitepackages() in subprocesses
        site_packages = os.path.join(sys.prefix, "Lib", "site-packages")
        eagle_dll_dir = os.path.join(site_packages, "pveagle", "lib", "windows", "amd64")
        
        if os.path.exists(eagle_dll_dir):
            # Method 1: Add to PATH (most reliable for subprocess)
            current_path = os.environ.get("PATH", "")
            if eagle_dll_dir not in current_path:
                os.environ["PATH"] = eagle_dll_dir + os.pathsep + current_path
                print(f"[EAGLE DLL] Added to PATH: {eagle_dll_dir}")
            
            # Method 2: SetDllDirectoryW (Windows kernel API)
            try:
                import ctypes
                ctypes.windll.kernel32.SetDllDirectoryW(eagle_dll_dir)
                print(f"[EAGLE DLL] SetDllDirectoryW called")
            except Exception:
                pass
            
            # Method 3: os.add_dll_directory (Python 3.8+)
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(eagle_dll_dir)
                    print(f"[EAGLE DLL] add_dll_directory called")
                except Exception:
                    pass
        else:
            print(f"[EAGLE DLL] Directory not found: {eagle_dll_dir}")
    except Exception as e:
        print(f"[EAGLE DLL] Setup error: {e}")

# Run the DLL setup immediately BEFORE any other imports
_setup_eagle_dll_directory_early()

# Now import LiveKit VAD interfaces (AFTER Eagle DLL setup)
from livekit import rtc
from livekit.agents import vad as agents_vad
from livekit.agents.utils import aio

# Try to import Picovoice modules
# On Windows, we need to set up DLL directories BEFORE importing pveagle
PICOVOICE_AVAILABLE = True
_eagle_dll_handle = None

def _setup_eagle_dll_directory():
    """Set up Windows DLL search path for Eagle."""
    global _eagle_dll_handle
    import platform
    
    if platform.system() != "Windows":
        return
    
    try:
        # Get the Eagle library path
        import pveagle._util as eagle_util
        eagle_lib_path = eagle_util.default_library_path()
        eagle_dll_dir = os.path.dirname(eagle_lib_path)
        
        if not os.path.exists(eagle_dll_dir):
            logger.debug(f"Eagle DLL directory does not exist: {eagle_dll_dir}")
            return
        
        logger.debug(f"Setting up Eagle DLL directory: {eagle_dll_dir}")
        
        # Method 1: Add to PATH environment variable (subprocess-safe)
        current_path = os.environ.get("PATH", "")
        if eagle_dll_dir not in current_path:
            os.environ["PATH"] = eagle_dll_dir + os.pathsep + current_path
            logger.debug("Added Eagle DLL dir to PATH")
        
        # Method 2: Use SetDllDirectoryW (Windows kernel API)
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetDllDirectoryW(eagle_dll_dir)
            logger.debug("Called SetDllDirectoryW")
        except Exception as e:
            logger.debug(f"SetDllDirectoryW failed: {e}")
        
        # Method 3: Use os.add_dll_directory (Python 3.8+)
        if hasattr(os, "add_dll_directory"):
            try:
                _eagle_dll_handle = os.add_dll_directory(eagle_dll_dir)
                logger.debug("Called os.add_dll_directory")
            except Exception as e:
                logger.debug(f"os.add_dll_directory failed: {e}")
                
    except Exception as e:
        logger.debug(f"Could not setup Eagle DLL directory: {e}")

try:
    import pvcobra
    
    # Set up Eagle DLL directory before importing pveagle
    _setup_eagle_dll_directory()
    
    import pveagle
except ImportError as e:
    PICOVOICE_AVAILABLE = False
    logger.warning(f"Picovoice modules not available: {e}")


class PicoSmartVAD(agents_vad.VAD):
    """
    Identity-gated Voice Activity Detection using Picovoice Cobra + Eagle.
    
    Extends LiveKit's VAD interface properly.
    """
    
    def __init__(
        self,
        *,
        access_key: Optional[str] = None,
        profile_path: Optional[str] = None,
        cobra_threshold: float = 0.5,
        eagle_threshold: float = 0.5,
        silence_duration_ms: int = 300,
        min_speech_duration: float = 0.1,
        max_buffered_speech: float = 60.0,
        padding_duration: float = 0.1,
    ):
        """
        Initialize PicoSmartVAD.
        
        Args:
            access_key: Picovoice access key (defaults to env var)
            profile_path: Path to Eagle speaker profile
            cobra_threshold: VAD probability threshold (0.0 to 1.0)
            eagle_threshold: Speaker recognition threshold (0.0 to 1.0)
            silence_duration_ms: Silence duration to trigger end-of-speech
            min_speech_duration: Minimum speech duration in seconds
            max_buffered_speech: Maximum buffered speech duration
            padding_duration: Padding duration in seconds
        """
        super().__init__(capabilities=agents_vad.VADCapabilities(update_interval=0.1))
        
        self.access_key = access_key or os.getenv("PICOVOICE_ACCESS_KEY")
        self.profile_path = profile_path or "avijit_profile.eagle"
        self.cobra_threshold = cobra_threshold
        self.eagle_threshold = eagle_threshold
        self.silence_duration_ms = silence_duration_ms
        self.min_speech_duration = min_speech_duration
        self.max_buffered_speech = max_buffered_speech
        self.padding_duration = padding_duration
        
        # State flags
        self.fallback_mode = False
        self.eagle_disabled = False  # Enabled - use enroll_livekit.py for compatible profiles
        
        # Picovoice instances
        self._cobra: Optional["pvcobra.Cobra"] = None
        self._eagle_gate = None  # Eagle Gate background process (replaces direct Eagle)
        self._sample_rate = 16000
        self._frame_length = 512
        
        # Initialize
        self._initialize()
    
    @property
    def model(self) -> str:
        return "picovoice/cobra-eagle"
    
    @property
    def provider(self) -> str:
        return "picovoice"
    
    def _initialize(self) -> None:
        """Initialize Picovoice engines with circuit breaker pattern."""
        if not PICOVOICE_AVAILABLE:
            logger.warning("Picovoice not available. Using fallback mode.")
            self.fallback_mode = True
            return
        
        if not self.access_key:
            logger.warning("No Picovoice access key found. Using fallback mode.")
            self.fallback_mode = True
            return
        
        # Initialize Cobra (VAD)
        try:
            self._cobra = pvcobra.create(access_key=self.access_key)
            self._frame_length = self._cobra.frame_length
            self._sample_rate = self._cobra.sample_rate
            logger.info(f"✅ Cobra VAD initialized (frame_length={self._frame_length})")
        except Exception as e:
            logger.error(f"❌ Failed to initialize Cobra: {e}")
            self.fallback_mode = True
            return
        
        # Initialize Eagle Gate (Speaker Recognition in Background Process)
        # This uses a separate process to bypass Windows DLL loading issues
        try:
            # Use absolute path for profile
            if not os.path.isabs(self.profile_path):
                self.profile_path = str(_project_root / self.profile_path)
            
            logger.debug(f"Eagle: Looking for profile at {self.profile_path}")
            logger.debug(f"Eagle: Access key present: {bool(self.access_key)}")
            
            if not os.path.exists(self.profile_path):
                logger.warning(f"⚠️ Speaker profile not found: {self.profile_path}")
                logger.warning("   Eagle disabled. Run enroll_avijit.py to create profile.")
                self.eagle_disabled = True
            else:
                # Import and start Eagle Gate
                from eagle_gate import EagleGate
                
                logger.debug("Eagle: Starting Eagle Gate background process...")
                self._eagle_gate = EagleGate(
                    access_key=self.access_key,
                    profile_path=self.profile_path
                )
                
                if self._eagle_gate.start():
                    logger.info(f"✅ Eagle Gate initialized (background process)")
                    logger.info(f"   Profile: {self.profile_path}")
                else:
                    logger.warning("⚠️ Failed to start Eagle Gate")
                    logger.warning("   Speaker recognition disabled. Using Cobra-only VAD.")
                    self.eagle_disabled = True
                    self._eagle_gate = None
                
        except Exception as e:
            logger.warning(f"⚠️ Failed to initialize Eagle Gate: {e}")
            logger.warning("   Speaker recognition disabled. Using Cobra-only VAD.")
            import traceback
            logger.debug(f"Eagle Gate traceback: {traceback.format_exc()}")
            self.eagle_disabled = True
            self._eagle_gate = None
    
    def stream(self) -> "PicoSmartVADStream":
        """Create a new VAD stream."""
        return PicoSmartVADStream(self)
    
    def process_pcm(self, pcm: List[int]) -> tuple[float, float]:
        """
        Process PCM samples and return voice probability and speaker score.
        
        Returns:
            Tuple of (voice_probability, speaker_score)
            speaker_score is -1.0 if Eagle is disabled
        """
        if self.fallback_mode or not self._cobra:
            return (0.0, -1.0)
        
        voice_prob = 0.0
        speaker_score = -1.0
        
        try:
            voice_prob = self._cobra.process(pcm)
            
            # ALWAYS send audio to Eagle Gate (even during silence) so it can build up state
            # Eagle needs continuous audio to warm up its internal speaker model
            if self._eagle_gate and not self.eagle_disabled:
                try:
                    # Amplify audio for Eagle (LiveKit audio is quieter than direct mic)
                    gain = 3.0  # Amplification factor
                    amplified_pcm = [min(max(int(s * gain), -32768), 32767) for s in pcm]
                    
                    # Use Eagle Gate to verify speaker (runs in background process)
                    speaker_score = self._eagle_gate.verify_speaker(amplified_pcm, timeout=0.3)
                    
                    # Only log when voice is detected
                    if voice_prob > self.cobra_threshold:
                        if speaker_score >= 0:
                            max_level = max(abs(min(amplified_pcm)), abs(max(amplified_pcm))) if amplified_pcm else 0
                            if speaker_score >= self.eagle_threshold:
                                logger.info(f"🎯 Speaker VERIFIED: score={speaker_score:.2f} level={max_level}")
                            else:
                                logger.info(f"👤 Unknown speaker: score={speaker_score:.2f} level={max_level}")
                        else:
                            # Timeout, fail-open
                            speaker_score = 1.0
                except Exception as e:
                    logger.debug(f"Eagle Gate processing error (fail-open): {e}")
                    speaker_score = 1.0
                    
        except Exception as e:
            logger.error(f"Frame processing error: {e}")
            voice_prob = 1.0
            speaker_score = 1.0
        
        return (voice_prob, speaker_score)
    
    def delete(self) -> None:
        """Release resources."""
        if self._cobra:
            try:
                self._cobra.delete()
            except Exception:
                pass
            self._cobra = None
        
        if self._eagle_gate:
            try:
                self._eagle_gate.stop()
            except Exception:
                pass
            self._eagle_gate = None


class PicoSmartVADStream(agents_vad.VADStream):
    """
    VAD Stream that processes audio using Picovoice Cobra + Eagle.
    
    Properly extends LiveKit's VADStream abstract class.
    """
    
    def __init__(self, vad: PicoSmartVAD):
        super().__init__(vad)
        self._pico_vad = vad
        
        # Audio buffer for frame management
        self._audio_buffer: List[int] = []
        
        # Speech state
        self._is_speaking = False
        self._speech_frames: List[rtc.AudioFrame] = []
        self._speech_samples = 0
        self._silence_samples = 0
        self._samples_processed = 0
        self._speech_start_time = 0.0
        
        # Speaker verification tracking for voice lock
        self._speech_speaker_scores: List[float] = []
        
        # Calculate silence threshold in samples
        self._silence_threshold_samples = int(
            vad.silence_duration_ms * vad._sample_rate / 1000
        )
        
        # Debug counters
        self._frame_count = 0
        self._speech_detect_count = 0
    
    async def _main_task(self) -> None:
        """Main processing task that reads from input channel and emits events."""
        import struct
        
        async for item in self._input_ch:
            if isinstance(item, self._FlushSentinel):
                # Handle flush - emit end of speech if speaking
                if self._is_speaking:
                    await self._emit_end_of_speech()
                continue
            
            frame: rtc.AudioFrame = item
            self._frame_count += 1
            
            # Debug: Log audio frame properties on first frame
            if self._frame_count == 1:
                logger.info(f"🎵 Audio Frame Properties:")
                logger.info(f"   Sample Rate: {frame.sample_rate} Hz")
                logger.info(f"   Channels: {frame.num_channels}")
                logger.info(f"   Samples/Channel: {frame.samples_per_channel}")
                logger.info(f"   Data length: {len(frame.data)} bytes")
                logger.info(f"   Expected by Eagle: 16000 Hz")
                if frame.sample_rate != 16000:
                    logger.warning(f"⚠️  SAMPLE RATE MISMATCH! LiveKit={frame.sample_rate}Hz, Eagle expects 16000Hz")
            
            if self._frame_count % 100 == 1:
                logger.debug(f"PicoSmartVAD processing frame #{self._frame_count}")
            
            # Convert AudioFrame to PCM samples
            try:
                audio_data = bytes(frame.data)
                num_samples = len(audio_data) // 2
                if num_samples == 0:
                    continue
                samples = list(struct.unpack(f'{num_samples}h', audio_data))
                
                # Resample if needed (LiveKit uses 24kHz, Eagle/Cobra expect 16kHz)
                if frame.sample_rate != 16000 and frame.sample_rate > 0:
                    # Calculate resampling ratio
                    ratio = 16000 / frame.sample_rate  # 16000/24000 = 0.666...
                    
                    # Simple linear interpolation resampling
                    new_length = int(len(samples) * ratio)
                    resampled = []
                    for i in range(new_length):
                        # Find the corresponding position in the original samples
                        pos = i / ratio
                        idx = int(pos)
                        frac = pos - idx
                        
                        if idx + 1 < len(samples):
                            # Linear interpolation between adjacent samples
                            val = int(samples[idx] * (1 - frac) + samples[idx + 1] * frac)
                        else:
                            val = samples[idx] if idx < len(samples) else 0
                        resampled.append(val)
                    
                    samples = resampled
                    if self._frame_count == 1:
                        logger.info(f"🔄 Resampling: {frame.sample_rate}Hz → 16000Hz ({num_samples} → {len(samples)} samples)")
                        
            except Exception as e:
                logger.debug(f"Error converting audio frame: {e}")
                continue
            
            # Add to buffer
            self._audio_buffer.extend(samples)
            
            # Process complete frames
            required_length = self._pico_vad._frame_length
            
            while len(self._audio_buffer) >= required_length:
                pcm_frame = self._audio_buffer[:required_length]
                self._audio_buffer = self._audio_buffer[required_length:]
                
                await self._process_pcm_frame(pcm_frame, frame)
    
    async def _process_pcm_frame(self, pcm: List[int], original_frame: rtc.AudioFrame) -> None:
        """Process a single PCM frame and emit events."""
        voice_prob, speaker_score = self._pico_vad.process_pcm(pcm)
        
        frame_samples = len(pcm)
        self._samples_processed += frame_samples
        current_time = self._samples_processed / self._pico_vad._sample_rate
        
        # Determine if this is valid speech
        is_valid_speech = voice_prob > self._pico_vad.cobra_threshold
        
        # If Eagle is enabled, also check speaker identity
        if not self._pico_vad.eagle_disabled and speaker_score >= 0:
            is_valid_speech = is_valid_speech and speaker_score > self._pico_vad.eagle_threshold
        
        if is_valid_speech:
            self._silence_samples = 0
            self._speech_samples += frame_samples
            self._speech_frames.append(original_frame)
            
            self._speech_detect_count += 1
            if self._speech_detect_count % 50 == 1:
                logger.debug(f"Cobra detected speech: prob={voice_prob:.2f}, speaker={speaker_score:.2f}")
            
            if not self._is_speaking:
                # Start of speech
                self._is_speaking = True
                self._speech_start_time = current_time
                self._speech_speaker_scores = []  # Reset scores for new speech
                
                # Update global speaker state
                speaker_state.start_speech()
                
                event = agents_vad.VADEvent(
                    type=agents_vad.VADEventType.START_OF_SPEECH,
                    samples_index=self._samples_processed,
                    timestamp=current_time,
                    speech_duration=0.0,
                    silence_duration=0.0,
                    frames=list(self._speech_frames),
                    probability=voice_prob,
                    speaking=True,
                )
                self._event_ch.send_nowait(event)
                logger.debug(f"🎤 START_OF_SPEECH at {current_time:.2f}s")
            
            # Track speaker score for voice lock
            if speaker_score >= 0:
                self._speech_speaker_scores.append(speaker_score)
                speaker_state.update_score(speaker_score, self._pico_vad.eagle_threshold)
            
            # Emit INFERENCE_DONE event
            inference_event = agents_vad.VADEvent(
                type=agents_vad.VADEventType.INFERENCE_DONE,
                samples_index=self._samples_processed,
                timestamp=current_time,
                speech_duration=self._speech_samples / self._pico_vad._sample_rate,
                silence_duration=0.0,
                frames=[original_frame],
                probability=voice_prob,
                speaking=True,
            )
            self._event_ch.send_nowait(inference_event)
            
        else:
            self._silence_samples += frame_samples
            
            # Emit INFERENCE_DONE for silence too
            inference_event = agents_vad.VADEvent(
                type=agents_vad.VADEventType.INFERENCE_DONE,
                samples_index=self._samples_processed,
                timestamp=current_time,
                speech_duration=self._speech_samples / self._pico_vad._sample_rate,
                silence_duration=self._silence_samples / self._pico_vad._sample_rate,
                frames=[original_frame],
                probability=voice_prob,
                speaking=False,
            )
            self._event_ch.send_nowait(inference_event)
            
            if self._is_speaking:
                # Check if enough silence to trigger end-of-speech
                if self._silence_samples >= self._silence_threshold_samples:
                    # Log voice lock status before end of speech
                    if self._speech_speaker_scores:
                        max_score = max(self._speech_speaker_scores)
                        avg_score = sum(self._speech_speaker_scores) / len(self._speech_speaker_scores)
                        verified = speaker_state.is_verified
                        status = "🔓 VERIFIED" if verified else "🔒 LOCKED"
                        logger.info(f"{status} - max_score={max_score:.2f}, avg={avg_score:.2f}")
                    
                    await self._emit_end_of_speech()
    
    async def _emit_end_of_speech(self) -> None:
        """Emit end of speech event."""
        current_time = self._samples_processed / self._pico_vad._sample_rate
        speech_duration = self._speech_samples / self._pico_vad._sample_rate
        silence_duration = self._silence_samples / self._pico_vad._sample_rate
        
        event = agents_vad.VADEvent(
            type=agents_vad.VADEventType.END_OF_SPEECH,
            samples_index=self._samples_processed,
            timestamp=current_time,
            speech_duration=speech_duration,
            silence_duration=silence_duration,
            frames=list(self._speech_frames),
            probability=0.0,
            speaking=False,
        )
        self._event_ch.send_nowait(event)
        logger.debug(f"🔇 END_OF_SPEECH at {current_time:.2f}s (speech={speech_duration:.2f}s)")
        
        # Reset state
        self._is_speaking = False
        self._speech_frames = []
        self._speech_samples = 0
        
        # Update global speaker state
        speaker_state.end_speech()


def create_pico_smart_vad(
    access_key: Optional[str] = None,
    profile_path: Optional[str] = None,
    cobra_threshold: float = 0.5,
    eagle_threshold: float = 0.5,
    silence_duration_ms: int = 300,
) -> Optional[PicoSmartVAD]:
    """
    Factory function to create a PicoSmartVAD instance.
    
    Returns None if initialization fails completely.
    """
    try:
        vad = PicoSmartVAD(
            access_key=access_key,
            profile_path=profile_path,
            cobra_threshold=cobra_threshold,
            eagle_threshold=eagle_threshold,
            silence_duration_ms=silence_duration_ms,
        )
        
        if vad.fallback_mode:
            logger.warning("PicoSmartVAD in fallback mode (Picovoice unavailable)")
            return None
        
        return vad
        
    except Exception as e:
        logger.error(f"Failed to create PicoSmartVAD: {e}")
        return None
