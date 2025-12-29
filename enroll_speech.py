#!/usr/bin/env python3
"""
Voice Enrollment for LiveKit-compatible Eagle.

This script captures audio and applies the SAME processing as the LiveKit agent,
ensuring the voice profile matches what Eagle sees at runtime.

Key difference from standard enrollment:
- Applies 3x amplification (same as in custom_vad.py)
- Uses the same audio format as LiveKit's audio stream
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv(".env.local")

PICOVOICE_ACCESS_KEY = os.getenv("PICOVOICE_ACCESS_KEY")

if not PICOVOICE_ACCESS_KEY:
    print("❌ ERROR: PICOVOICE_ACCESS_KEY not found in .env.local")
    sys.exit(1)

try:
    import pveagle
    import pvrecorder
except ImportError as e:
    print(f"❌ ERROR: Missing dependency: {e}")
    print("   Please run: pip install pveagle pvrecorder")
    sys.exit(1)


# Same amplification as custom_vad.py
AMPLIFICATION_GAIN = 3.0


def amplify_audio(samples: list) -> list:
    """Apply same amplification as LiveKit agent."""
    return [min(max(int(s * AMPLIFICATION_GAIN), -32768), 32767) for s in samples]


def get_feedback_message(feedback) -> str:
    """Get human-readable message for enrollment feedback."""
    messages = {
        pveagle.EagleProfilerEnrollFeedback.AUDIO_OK: "✅ Audio OK",
        pveagle.EagleProfilerEnrollFeedback.AUDIO_TOO_SHORT: "⚠️  Audio too short - keep speaking",
        pveagle.EagleProfilerEnrollFeedback.UNKNOWN_SPEAKER: "⚠️  Unknown speaker detected",
        pveagle.EagleProfilerEnrollFeedback.NO_VOICE_FOUND: "⚠️  No voice found - speak louder",
        pveagle.EagleProfilerEnrollFeedback.QUALITY_ISSUE: "⚠️  Quality issue - reduce background noise",
    }
    return messages.get(feedback, f"⚠️  Feedback: {feedback}")


def draw_progress_bar(percentage: float, width: int = 40) -> str:
    """Create a text-based progress bar."""
    filled = int(width * percentage / 100)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {percentage:5.1f}%"


def main():
    print("=" * 60)
    print("🎙️  LIVEKIT-COMPATIBLE EAGLE ENROLLMENT")
    print("=" * 60)
    print("\nThis utility creates a voice profile that matches LiveKit's")
    print("audio processing (including 3x amplification).")
    print("\nTips for best results:")
    print("  • Speak in a quiet environment")
    print("  • Use the SAME microphone you'll use with the agent")
    print("  • Speak normally for about 15-30 seconds")
    print()

    # List audio devices
    devices = pvrecorder.PvRecorder.get_available_devices()
    print("📱 Available Audio Devices:")
    for i, device in enumerate(devices):
        print(f"   [{i}] {device}")
    
    device_index = -1
    if len(devices) > 1:
        try:
            choice = input("\nSelect device index (or Enter for default): ").strip()
            if choice:
                device_index = int(choice)
                if device_index < 0 or device_index >= len(devices):
                    device_index = -1
        except ValueError:
            device_index = -1

    print("\n🔧 Initializing Eagle Profiler...")
    
    try:
        profiler = pveagle.create_profiler(access_key=PICOVOICE_ACCESS_KEY)
        print(f"   Sample rate: {profiler.sample_rate} Hz")
        print(f"   Frame length: {profiler.min_enroll_samples}")
        print(f"   Amplification: {AMPLIFICATION_GAIN}x (matches LiveKit agent)")
        
        recorder = pvrecorder.PvRecorder(
            frame_length=profiler.min_enroll_samples,
            device_index=device_index
        )
        
        print(f"\n🎤 Using device: {recorder.selected_device}")
        print("\n" + "=" * 60)
        print("Press ENTER to start, then speak normally...")
        print("=" * 60)
        input()
        
        recorder.start()
        print("\n🔴 RECORDING - Start speaking now!\n")
        
        percentage = 0.0
        last_feedback = None
        frame_count = 0
        
        while percentage < 100.0:
            try:
                # Read audio
                pcm = recorder.read()
                frame_count += 1
                
                # Apply same amplification as LiveKit agent
                amplified_pcm = amplify_audio(pcm)
                
                # Show audio level periodically
                if frame_count % 10 == 0:
                    max_val = max(abs(min(amplified_pcm)), abs(max(amplified_pcm)))
                    print(f"\r{draw_progress_bar(percentage)} level={max_val:5d}", end="", flush=True)
                
                # Enroll with amplified audio
                percentage, feedback = profiler.enroll(amplified_pcm)
                
                if feedback != last_feedback:
                    msg = get_feedback_message(feedback)
                    print(f"\n   {msg}")
                    last_feedback = feedback
                    
            except KeyboardInterrupt:
                print("\n\n⚠️  Enrollment cancelled.")
                recorder.stop()
                profiler.delete()
                sys.exit(0)
        
        recorder.stop()
        print(f"\n\n✅ Enrollment complete!")
        
        # Export profile
        print("\n💾 Exporting voice profile...")
        profile = profiler.export()
        
        output_path = "avijit_profile.eagle"
        with open(output_path, "wb") as f:
            f.write(profile.to_bytes())
        
        print(f"   Profile saved to: {output_path}")
        print(f"   Profile size: {profile.size} bytes")
        
        profiler.delete()
        recorder.delete()
        
        print("\n" + "=" * 60)
        print("🎉 SUCCESS! Your LiveKit-compatible profile is ready.")
        print(f"   File: {os.path.abspath(output_path)}")
        print("\nNow enable Eagle in custom_vad.py:")
        print("   self.eagle_disabled = False")
        print("=" * 60)
        
    except pveagle.EagleError as e:
        print(f"\n❌ Eagle Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
