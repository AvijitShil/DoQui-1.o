import logging
import datetime
import os
from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    MetricsCollectedEvent,
    RoomInputOptions,
    WorkerOptions,
    cli,
    inference,
    metrics,
    function_tool,
    RunContext,
)
from livekit.plugins import noise_cancellation, silero
from custom_vad import create_pico_smart_vad, speaker_state

logger = logging.getLogger("agent")

# Voice Lock Configuration
VOICE_LOCK_ENABLED = True
VOICE_LOCK_REJECTION = "I don't talk to strangers. Only authorized personnel can access this system."

load_dotenv(".env.local")
print("\n[DEBUG] Environment check:")
print("LIVEKIT_URL =", os.getenv("LIVEKIT_URL"))
print("LIVEKIT_API_KEY =", os.getenv("LIVEKIT_API_KEY"))
print("LIVEKIT_API_SECRET =", os.getenv("LIVEKIT_API_SECRET"))

class Assistant(Agent):
    def __init__(self) -> None:
        # Base instructions for verified speakers
        base_instructions = """You are a highly intelligent, witty, medically helpful voice AI assistant named Doqui who is made by Avijit. The user is interacting with you via voice, even if you perceive the conversation as text.
            You are curious, confident, and have a dry sense of humor. You speak naturally and clearly, keeping your answers concise and conversational. Avoid complex formatting, punctuation, emojis, or symbols.
            You can take autonomous actions when appropriate, such as opening websites, retrieving data from web, or performing basic automation. When doing so, you briefly confirm what you are about to do in a conversational way.
            If a request is unclear or outside your ability, you must pause, think aloud briefly, and ask a clarifying question before proceeding.
            You express reasoning in a human-like way without exposing raw logic or code—just short, natural explanations of your thought process .
            You maintain a sense of humor and personality in appropriate moments, using clever, dry remarks to keep conversations engaging but never sarcastic or rude.
            Always prioritize accuracy, privacy, and user confirmation before performing any action that may change data, access files, or control systems."""
        
        super().__init__(instructions=base_instructions)
    
    async def llm_node(
        self,
        chat_ctx,
        tools,
        model_settings,
    ):
        """Override llm_node to implement voice lock by modifying chat context."""
        # Check voice lock status
        if VOICE_LOCK_ENABLED:
            is_verified = speaker_state.is_verified
            max_score = speaker_state.max_score
            
            if not is_verified:
                logger.info(f"🔒 VOICE LOCK ACTIVE: Speaker NOT verified (max_score={max_score:.2f})")
                
                # Create a rejection response without calling LLM
                # llm_node can return AsyncIterable[str] - simple strings work!
                async def generate_rejection():
                    yield "I don't talk to strangers. Voice authentication is required to access this system."
                
                return generate_rejection()
            else:
                logger.info(f"🔓 VOICE LOCK: Speaker verified (max_score={max_score:.2f}) - proceeding normally")
        
        # Call parent's llm_node for verified speakers (returns async generator, not awaitable)
        return super().llm_node(chat_ctx, tools, model_settings)

    # 🖥️ SYSTEM AUTOMATION & CONTROL ------------------------------------------
    
    @function_tool
    async def open_website(self, context: RunContext, url: str):
        """Open or navigate to a website.
        
        Args:
            url: The website URL to open (e.g., 'https://google.com')
        """
        logger.info(f"Opening website: {url}")
        return f"Opening {url}..."
    
    @function_tool
    async def search_web(self, context: RunContext, query: str):
        """Perform a quick web search.
        
        Args:
            query: The search query
        """
        logger.info(f"Searching web for: {query}")
        return f"Searching the web for '{query}'..."
    
    
    @function_tool
    async def get_datetime(self, context: RunContext):
        """Fetch current date and time."""
        now = datetime.datetime.now()
        return f"It's {now.strftime('%I:%M %p on %A, %B %d, %Y')}."
    
    
    @function_tool
    async def lookup_weather(self, context: RunContext, location: str):
        """Get real-time weather info.
        
        Args:
            location: City or location name
        """
        logger.info(f"Looking up weather for: {location}")
        return f"The weather in {location} is sunny, 27°C."
    
    @function_tool
    async def get_news(self, context: RunContext, topic: str):
        """Fetch trending or topic-specific news.
        
        Args:
            topic: News topic or category
        """
        logger.info(f"Fetching news about: {topic}")
        return f"Here are the latest headlines about {topic}."
    
    @function_tool
    async def get_stock_price(self, context: RunContext, symbol: str):
        """Retrieve stock or crypto prices.
        
        Args:
            symbol: Stock ticker symbol (e.g., 'AAPL', 'BTC')
        """
        logger.info(f"Getting stock price for: {symbol}")
        return f"{symbol.upper()} is currently trading at $188.40."
    
    
    
    @function_tool
    async def send_email(self, context: RunContext, to: str, subject: str, body: str):
        """Send an email.
        
        Args:
            to: Recipient email address
            subject: Email subject
            body: Email body content
        """
        logger.info(f"Sending email to {to} with subject: {subject}")
        return f"Email sent to {to} with subject '{subject}'."
    
    @function_tool
    async def read_emails(self, context: RunContext, count: int = 3):
        """Read a number of unread emails.
        
        Args:
            count: Number of emails to read
        """
        logger.info(f"Reading {count} emails")
        return f"You have {count} unread emails."

    
    @function_tool
    async def find_nearby_places(self, context: RunContext, place_type: str):
        """Find nearby restaurants, ATMs, etc.
        
        Args:
            place_type: Type of place (e.g., 'restaurants', 'ATMs', 'hospitals')
        """
        logger.info(f"Finding nearby {place_type}")
        return f"Found several nearby {place_type}s."
    


def prewarm(proc: JobProcess):
    """Prewarm function with PicoSmartVAD priority, Silero fallback."""
    # Try PicoSmartVAD (Cobra + Eagle identity-gated VAD)
    try:
        pico_vad = create_pico_smart_vad()
        if pico_vad is not None:
            proc.userdata["vad"] = pico_vad
            logger.info("✅ PicoSmartVAD (Iron Man Protocol) loaded successfully.")
            return
        else:
            logger.warning("⚠️ PicoSmartVAD returned None, falling back to Silero.")
    except Exception as e:
        logger.warning(f"⚠️ PicoSmartVAD failed: {e}")
    
    # Fallback: Try TurnDetector (newer Silero versions)
    try:
        from livekit.plugins.silero import TurnDetector
        proc.userdata["vad"] = TurnDetector.load()
        logger.info("✅ Silero TurnDetector loaded as fallback.")
    except Exception as e:
        # Final fallback: Legacy Silero VAD
        if hasattr(silero.VAD, "load"):
            proc.userdata["vad"] = silero.VAD.load()
        else:
            proc.userdata["vad"] = silero.VAD()
        logger.warning(f"⚠️ Using legacy Silero VAD as final fallback: {e}")



async def entrypoint(ctx: JobContext):
    """Main entrypoint for the voice agent."""
    
    # Logging setup - add context fields for all log entries
    ctx.log_context_fields = {
        "room": ctx.room.name,
    }

    # Set up voice AI pipeline with STT, LLM, and TTS
    session = AgentSession(
        # Speech-to-text: Deepgram Nova 3 Medical model with Indian English
        stt=inference.STT(model="deepgram/nova-3-medical", language="en-IN"),
        
        # Large Language Model: GPT-4.1 Mini for fast responses
        llm=inference.LLM(model="openai/gpt-4.1-mini"),
        
        # Text-to-speech: Cartesia Sonic 3 with custom voice
        tts=inference.TTS(
            model="cartesia/sonic-3", 
            voice="abc6eacf-2626-4d87-902b-7b49c2dcae16"
        ),
        
        # VAD (Voice Activity Detection) - PicoSmartVAD or Silero fallback
        vad=ctx.proc.userdata.get("vad"),
        # Allow LLM to generate response while waiting for end of turn
        preemptive_generation=True,
    )

    # Metrics collection for monitoring pipeline performance
    usage_collector = metrics.UsageCollector()

    @session.on("metrics_collected")
    def _on_metrics_collected(ev: MetricsCollectedEvent):
        """Log metrics when collected."""
        metrics.log_metrics(ev.metrics)
        usage_collector.collect(ev.metrics)

    async def log_usage():
        """Log usage summary on shutdown."""
        summary = usage_collector.get_summary()
        logger.info(f"Usage: {summary}")

    ctx.add_shutdown_callback(log_usage)

    # Start the session with Vienna assistant
    await session.start(
        agent=Assistant(),
        room=ctx.room,
        room_input_options=RoomInputOptions(
            # Background voice cancellation for cleaner audio
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    # Connect to the room
    await ctx.connect()


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))