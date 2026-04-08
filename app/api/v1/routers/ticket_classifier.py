import os
import io
import json
import tempfile

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from app.api.models import Ticket, TicketPublic, User, Category, SubCategory, SubCategoryTeam
from app.api.db import get_session
from typing import Annotated
from google import genai
from google.genai import types
from pydantic import BaseModel
from pedalboard.io import AudioFile
from pedalboard import Pedalboard, NoiseGate, Compressor, LowShelfFilter, Gain
import noisereduce as nr

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

    # Process the audio: noise reduction, normalization, and compression
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_in, tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_out:
        temp_in.write(audio_bytes)
        temp_in.flush()
        temp_in_name = temp_in.name
        temp_out_name = temp_out.name

    try:
        with AudioFile(temp_in_name) as f_in:
            sr = f_in.samplerate
            audio = f_in.read(f_in.frames)

        reduced_noise = nr.reduce_noise(y=audio, sr=sr, stationary=True, prop_decrease=1.0)

        board = Pedalboard([
            NoiseGate(threshold_db=-30.0, ratio=1.5, release_ms=250.0),
            Compressor(threshold_db=-16.0, ratio=2.5),
            LowShelfFilter(cutoff_frequency_hz=400.0, gain_db=10.0, q=1.0),
            Gain(gain_db=10.0)
        ])

        effected = board(reduced_noise, sample_rate=sr)
        num_channels = effected.shape[0] if effected.ndim > 1 else 1

        with AudioFile(temp_out_name, "w", samplerate=sr, num_channels=num_channels) as f_out:
            f_out.write(effected)

        with open(temp_out_name, "rb") as f_processed:
            processed_audio_bytes = f_processed.read()
            
    except Exception as e:
        if os.path.exists(temp_in_name):
            os.remove(temp_in_name)
        if os.path.exists(temp_out_name):
            os.remove(temp_out_name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Audio processing failed: {str(e)}",
        )

    if os.path.exists(temp_in_name):
        os.remove(temp_in_name)
    if os.path.exists(temp_out_name):
        os.remove(temp_out_name)

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
    category_with_sub = await session.exec(
        select(Category.category_in_english, SubCategory.subcategory_in_english)
        .join(SubCategoryTeam, SubCategoryTeam.category_id == Category.id)
        .join(SubCategory, SubCategoryTeam.sub_category_id == SubCategory.id)
        .distinct()
    )
    rows = category_with_sub.all()

    grouped: dict[str, list[str]] = {}
    for category_name, subcategory_name in rows:
        grouped.setdefault(category_name, [])
        if subcategory_name not in grouped[category_name]:
            grouped[category_name].append(subcategory_name)

    classification_prompt = f"""You are an intelligent helpdesk ticket classifier for an organization.

Listen to the audio complaint carefully. The audio may be in English, Bangla, or a mix (Banglish).

Your tasks:
1. Understand the nature and context of the complaint.
2. Classify it into the most appropriate category, subcategory, and responsible team from the taxonomy below.
3. Determine priority based on urgency and business impact.
4. Write a concise English summary of the complaint.

Available taxonomy:
{grouped}

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
