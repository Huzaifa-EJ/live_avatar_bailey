from dotenv import load_dotenv
import logging
import prompts
import asyncio
import json

from livekit import agents, rtc
from livekit.agents import AgentServer, AgentSession,TurnHandlingOptions, Agent, inference, room_io
from livekit.plugins import ai_coustics, silero, tavus, liveavatar

load_dotenv(".env")

logger = logging.getLogger("bizapp-avatar")
logging.basicConfig(level=logging.INFO)

# Must match the field order in widget/index.html
FIELD_ORDER = ["first_name", "last_name", "email", "phone", "business_name", "industry"]

# Fields whose raw value is safe to speak aloud
SPEAKABLE = {"first_name", "business_name", "industry"}

class FormFlow:
    """Tracks which fields are done and produces the next instruction."""

    def __init__(self) -> None:
        self.completed: dict[str, str] = {}
        self.submitted = False

    def on_field(self, field: str, value: str, valid: bool) -> str | None:
        if field not in FIELD_ORDER or self.submitted:
            return None
        if not valid and field in prompts.FIELD_VALIDATION_HINT:
            return prompts.FIELD_VALIDATION_HINT[field]
        self.completed[field] = value
        template = prompts.ON_FIELD_COMPLETED.get(field)
        if template is None:
            return None
        spoken_value = value if field in SPEAKABLE else ""
        return template.format(value=spoken_value)

    def on_submit(self) -> str:
        self.submitted = True
        return prompts.ON_SUBMIT

class BizAppGuide(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=prompts.BASE_INSTRUCTIONS,
        )

server = AgentServer()

@server.rtc_session(agent_name="aria")
async def my_agent(ctx: agents.JobContext):
    flow = FormFlow()

    session = AgentSession(
        stt=inference.STT(model="deepgram/flux-general-multi"),
        llm=inference.LLM(model="google/gemini-2.5-flash"),
        tts=inference.TTS(
            model="cartesia/sonic-3",   
            voice="a33f7a4c-100f-41cf-a1fd-5822e8fc253f",
            extra_kwargs={"emotion": "happy"},
        ),
        vad=silero.VAD.load(),
        turn_handling=TurnHandlingOptions(
            turn_detection=inference.TurnDetector(),
        ),
    )

    avatar = liveavatar.AvatarSession(
      avatar_id="073b60a9-89a8-45aa-8902-c358f64d2852",  # ID of the LiveAvatar avatar to use
   )

    # avatar = tavus.AvatarSession(
    #   replica_id="r3f427f43c9d",  # ID of the Tavus replica to use
    #   persona_id="pce053745836",  # ID of the Tavus persona to use (see preceding section for configuration details)
    # )

    await avatar.start(session, room=ctx.room)

    # Queue so the sync data callback can hand work to the async world
    # without racing generate_reply() calls.
    instruction_q: asyncio.Queue[str] = asyncio.Queue()

    def on_data(packet: rtc.DataPacket) -> None:
        if packet.topic != "form":
            return
        try:
            msg = json.loads(packet.data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.warning("Bad data packet, ignoring")
            return

        event = msg.get("event")
        instruction: str | None = None

        if event == "session_started":
            instruction = prompts.WELCOME
        elif event == "field_completed":
            instruction = flow.on_field(
                field=msg.get("field", ""),
                value=str(msg.get("value", ""))[:200],
                valid=bool(msg.get("valid", True)),
            )
        elif event == "form_submitted":
            instruction = flow.on_submit()

        if instruction:
            instruction_q.put_nowait(instruction)

    ctx.room.on("data_received", on_data)

    async def instruction_worker() -> None:
        """Serialize form-driven replies. If the visitor completes fields
        faster than the avatar can speak, drain the backlog and keep only
        the newest instruction so the avatar never narrates stale fields."""
        while True:
            instruction = await instruction_q.get()
            while not instruction_q.empty():
                instruction = instruction_q.get_nowait()
            try:
                session.interrupt()  # stop any in-flight speech, newest state wins
            except Exception:
                pass
            await session.generate_reply(instructions=instruction)

    worker_task = asyncio.create_task(instruction_worker())

    async def _cleanup():
        worker_task.cancel()

    ctx.add_shutdown_callback(_cleanup)

    await session.start(
        room=ctx.room,
        agent=BizAppGuide(),
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=ai_coustics.audio_enhancement(model=ai_coustics.EnhancerModel.QUAIL_VF_S),
            ),
        ),
    )

    session.generate_reply(instructions=prompts.WELCOME)

if __name__ == "__main__":
    agents.cli.run_app(server)