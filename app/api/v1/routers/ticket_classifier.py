import os
import io
import json
import tempfile
import platform
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from app.api.models import Ticket, TicketPublic, User, Category, SubCategory, SubCategoryTeam
from app.api.db import get_session
from typing import Annotated
from google import genai
from google.genai import types
from pydantic import BaseModel

from app.api.v1.deps import get_current_active_user

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

router = APIRouter(
    prefix="/tickets",
    tags=["tickets"]
)


class TicketClassification(BaseModel):
    category: str
    subcategory: str
    priority: str
    description: str


def _read_linux_cpu_flags() -> set[str]:
    if platform.system().lower() != "linux":
        return set()
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return set()

    flags: set[str] = set()
    for line in cpuinfo.splitlines():
        if line.startswith("flags") or line.startswith("Features"):
            parts = line.split(":", 1)
            if len(parts) == 2:
                flags.update(parts[1].strip().split())
    return flags


def _can_use_pedalboard_safely() -> bool:
    """Best-effort guard to avoid SIGILL on older CPUs.

    Some native wheels may require newer x86_64 instruction sets. We gate usage
    behind CPU flags so the API can still start even on older hosts.
    """

    machine = platform.machine().lower()
    if machine not in {"x86_64", "amd64"}:
        return True

    flags = _read_linux_cpu_flags()
    if not flags:
        return True

    # Conservative: require AVX2 for pedalboard usage.
    return "avx" in flags and "avx2" in flags


def _audio_preprocessing_enabled() -> bool:
    value = os.getenv("ATC_AUDIO_PREPROCESSING", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _process_audio_if_enabled(audio_bytes: bytes) -> bytes:
    if not _audio_preprocessing_enabled():
        return audio_bytes

    if not _can_use_pedalboard_safely():
        return audio_bytes

    # Lazy-import native deps so app startup doesn't crash.
    from pedalboard.io import AudioFile  # type: ignore
    from pedalboard import (  # type: ignore
        Pedalboard,
        NoiseGate,
        Compressor,
        LowShelfFilter,
        Gain,
    )
    import noisereduce as nr  # type: ignore

    temp_in_name = ""
    temp_out_name = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_in:
            temp_in.write(audio_bytes)
            temp_in.flush()
            temp_in_name = temp_in.name
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_out:
            temp_out_name = temp_out.name

        with AudioFile(temp_in_name) as f_in:
            sr = f_in.samplerate
            audio = f_in.read(f_in.frames)

        reduced_noise = nr.reduce_noise(
            y=audio,
            sr=sr,
            stationary=True,
            prop_decrease=1.0,
        )

        board = Pedalboard(
            [
                NoiseGate(threshold_db=-30.0, ratio=1.5, release_ms=250.0),
                Compressor(threshold_db=-16.0, ratio=2.5),
                LowShelfFilter(cutoff_frequency_hz=400.0, gain_db=10.0, q=1.0),
                Gain(gain_db=10.0),
            ]
        )

        effected = board(reduced_noise, sample_rate=sr)
        num_channels = effected.shape[0] if getattr(effected, "ndim", 1) > 1 else 1

        with AudioFile(temp_out_name, "w", samplerate=sr, num_channels=num_channels) as f_out:
            f_out.write(effected)

        return Path(temp_out_name).read_bytes()
    finally:
        if temp_in_name and os.path.exists(temp_in_name):
            os.remove(temp_in_name)
        if temp_out_name and os.path.exists(temp_out_name):
            os.remove(temp_out_name)


@router.get("/", response_model=list[TicketPublic], status_code=status.HTTP_200_OK)
async def get_tickets(
    current_user: Annotated[User, Depends(get_current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Get all tickets for the current user."""
    result = await session.exec(select(Ticket).where(Ticket.user_id == current_user.id))
    tickets = result.all()
    return tickets


@router.post("/", response_model=TicketPublic, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    current_user: Annotated[User, Depends(get_current_active_user)],
    file: Annotated[UploadFile, File(description="Audio recording of the employee complaint")],
    session: Annotated[AsyncSession, Depends(get_session)],
    company_id: int
):
    """
    Accept an audio complaint, transcribe and classify it using Gemini AI,
    then persist and return the generated helpdesk ticket.
    """
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty audio file provided.",
        )

    # Process the audio (optional): noise reduction, normalization, and compression.
    # This is intentionally lazy-imported to avoid crashing the server on hosts
    # where native wheels can't run.
    try:
        processed_audio_bytes = _process_audio_if_enabled(audio_bytes)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Audio processing failed: {str(e)}",
        )

    mime_type = "audio/wav"

    # Upload audio to the Gemini Files API
    try:
        uploaded_file = client.files.upload(
            file=io.BytesIO(processed_audio_bytes),
            config=types.UploadFileConfig(
                mime_type=mime_type,
                display_name=(file.filename or "complaint_audio") + "_denoised.wav",
            ),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to upload audio to AI service: {str(e)}",
        )
    
    # Extract category and subcategory information for the classification prompt
    # Filter taxonomy by company and client visibility.
    category_with_sub = await session.exec(
        select(Category.category_in_english, SubCategory.subcategory_in_english)
        .join(SubCategoryTeam, SubCategoryTeam.category_id == Category.id)
        .join(SubCategory, SubCategoryTeam.sub_category_id == SubCategory.id)
        .where(
            SubCategoryTeam.company_id == company_id,
            SubCategoryTeam.is_client_visible == 1,
        )
        .distinct()
        .order_by(Category.category_in_english, SubCategory.subcategory_in_english)
    )
    rows = category_with_sub.all()

    grouped: dict[str, list[str]] = {}
    for category_name, subcategory_name in rows:
        grouped.setdefault(category_name, [])
        if subcategory_name not in grouped[category_name]:
            grouped[category_name].append(subcategory_name)

    grouped_for_prompt = json.dumps(grouped, ensure_ascii=False, sort_keys=True, indent=2)

    
    classification_prompt = f"""You are an intelligent helpdesk ticket classifier for an organization.

Listen to the audio complaint carefully. The audio may be in English, Bangla, or a mix (Banglish).

Your tasks:
1. Understand the nature and context of the complaint.
2. Classify it into the most appropriate category, subcategory, and responsible team from the taxonomy below.
3. Determine priority based on urgency and business impact.
4. Write a concise English summary of the complaint.

Available taxonomy:
{grouped_for_prompt}

Priority guidelines:
- Low: General inquiry or non-urgent minor issue.
- Medium: Issue affecting the employee's productivity but a workaround exists.
- High: Significant impact on work with no workaround available.
- Critical: Severe issue affecting multiple people, data security, or business continuity.

Respond ONLY with a valid JSON object using exactly these fields:
{{
  "category": "<one of the categories above>",
  "subcategory": "<one of the subcategories for that category>",
  "priority": "<Low | Medium | High | Critical>",
  "description": "<2-3 sentence summary of the complaint in English>"
}}"""

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Part.from_uri(
                    file_uri=uploaded_file.uri,
                    mime_type=uploaded_file.mime_type,
                ),
                classification_prompt,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        classification = TicketClassification(**json.loads(response.text))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ticket classification failed: {str(e)}",
        )
    finally:
        # Remove the audio file from Gemini's storage after processing
        try:
            client.files.delete(name=uploaded_file.name)
        except Exception:
            pass

    ticket = Ticket(
        category=classification.category,
        subcategory=classification.subcategory,
        priority=classification.priority,
        description=classification.description,
        status="Open",
        user_id=current_user.id,
    )

    session.add(ticket)
    await session.commit()
    await session.refresh(ticket)

    return ticket
