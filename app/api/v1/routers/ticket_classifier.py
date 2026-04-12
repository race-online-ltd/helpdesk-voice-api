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
from typing import Annotated, Optional
from google import genai
from google.genai import types
from pydantic import BaseModel

from app.api.v1.deps import get_current_active_user

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
DEBUG = os.getenv("APP_ENV", "production").lower() == "development"

router = APIRouter(
    prefix="/tickets",
    tags=["tickets"]
)


class TicketClassification(BaseModel):
    category: Optional[str] = None
    category_id: Optional[int] = None
    subcategory: Optional[str] = None
    subcategory_id: Optional[int] = None
    priority: str = "Low"
    description: Optional[str] = None

    @property
    def is_null_ticket(self) -> bool:
        return (
            self.category is None
            and self.category_id is None
            and self.subcategory is None
            and self.subcategory_id is None
            and self.description is None
        )


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
        select(
            Category.id,
            Category.category_in_english,
            SubCategory.id,
            SubCategory.subcategory_in_english,
        )
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

    taxonomy_by_category_id: dict[int, dict] = {}
    for category_id_db, category_name, subcategory_id_db, subcategory_name in rows:
        if category_id_db is None or subcategory_id_db is None:
            continue

        entry = taxonomy_by_category_id.setdefault(
            int(category_id_db),
            {"category": category_name, "category_id": int(category_id_db), "subcategories": []},
        )
        sub_list: list[dict] = entry["subcategories"]
        if not any(s["subcategory_id"] == int(subcategory_id_db) for s in sub_list):
            sub_list.append({"subcategory": subcategory_name, "subcategory_id": int(subcategory_id_db)})

    taxonomy_for_prompt = json.dumps(
        list(taxonomy_by_category_id.values()),
        ensure_ascii=False,
        indent=2,
    )

    
    classification_prompt = f"""You are an intelligent helpdesk ticket classifier for a telecom and ISP organization.

You will receive a transcription or audio input of a customer or staff complaint. The input may be in English, Bangla, or a mix of both (Banglish).

---

## STEP 1 — AUDIO VALIDITY CHECK (Do this FIRST before anything else)

Before attempting any classification, assess whether the input contains a real, intelligible complaint.

Reject the input and return a null-ticket if ANY of the following are true:
- The input is empty, blank, or contains only whitespace
- The input is only filler sounds (e.g., "um", "uh", "hello", "test", "ok")
- The input is too short or vague to identify any specific issue (e.g., fewer than 5 meaningful words)
- The audio is background noise, static, silence, or completely unintelligible
- The input contains no actionable complaint or request

If rejected, respond ONLY with this exact JSON and nothing else:
{{
  "category": null,
  "category_id": null,
  "subcategory": null,
  "subcategory_id": null,
  "priority": "Low",
  "description": null
}}

---

## STEP 2 — CLASSIFICATION (Only if input passed Step 1)

1. Carefully understand the nature, context, and technical details of the complaint.
2. Classify it using ONLY the categories and subcategories listed in the Available Taxonomy below. Do NOT invent or infer categories outside this list.
3. If the complaint does not clearly fit any available category or subcategory, return the null-ticket JSON from Step 1 instead of guessing.
4. Assign priority based on the guidelines below.
5. Write a concise 2–3 sentence summary in English.

---

## Available Taxonomy (you must ONLY use entries from this list):
{taxonomy_for_prompt}

---

## Priority Guidelines:
- Low: General inquiry or non-urgent minor issue with no service impact.
- Medium: Issue affecting productivity but a workaround exists.
- High: Significant service impact with no workaround available.
- Critical: Severe issue affecting multiple users, data security, or business continuity.

---

## Output Rules:
- Respond ONLY with a valid JSON object — no explanation, no preamble, no markdown.
- Every selected category and subcategory MUST exactly match a value from the Available Taxonomy.
- The description must be in English, even if the original complaint was in Bangla or Banglish.
- Never fabricate or guess category_id or subcategory_id values — use only what is provided to you.

{{
  "category": "<must be an exact category name from the taxonomy>",
  "category_id": "<the corresponding category ID from the database>",
  "subcategory": "<must be an exact subcategory name under that category>",
  "subcategory_id": "<the corresponding subcategory ID from the database>",
  "priority": "<Low | Medium | High | Critical>",
  "description": "<2–3 sentence English summary of the complaint>"
}}"""

    classification: Optional[TicketClassification] = None

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

        raw_response = response.text
        parsed = json.loads(raw_response)
        classification = TicketClassification(**parsed)
    except json.JSONDecodeError as e:
        detail={
            "error_code": "AI_INVALID_RESPONSE",
            "message": "AI service returned a malformed response. Please try again.",
        }
        if DEBUG:
            detail["debug"] = str(e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=detail,
        )
    except Exception as e:
        detail={
            "error_code": "CLASSIFICATION_FAILED",
            "message": "Ticket classification encountered an unexpected error.",
        }
        if DEBUG:
            detail["debug"] = str(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=detail,
        )

    finally:
        try:
            client.files.delete(name=uploaded_file.name)
        except Exception:
            pass
    # Null-ticket: AI determined audio was empty, unintelligible, or not actionable
    if classification is None or classification.is_null_ticket:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_code": "NULL_TICKET",
                "message": "The audio did not contain a valid or intelligible complaint. No ticket was created.",
            },
        )

    # Validate category_id is within the allowed taxonomy for this company
    if classification.category_id not in taxonomy_by_category_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_code": "INVALID_CATEGORY",
                "message": "The AI selected a category not permitted for this account. Please re-record with more detail.",
            },
        )

    # Validate subcategory_id belongs to the selected category
    allowed_sub_ids = {
        s["subcategory_id"]
        for s in taxonomy_by_category_id[classification.category_id]["subcategories"]
    }
    if classification.subcategory_id not in allowed_sub_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_code": "INVALID_SUBCATEGORY",
                "message": "The AI selected a subcategory not permitted under the chosen category. Please re-record with more detail.",
            },
        )

    ticket = Ticket(
        company_id=company_id,
        category=classification.category,
        category_id=classification.category_id,
        subcategory=classification.subcategory,
        subcategory_id=classification.subcategory_id,
        priority=classification.priority,
        description=classification.description,
        status="Open",
        user_id=current_user.id,
    )

    session.add(ticket)
    await session.commit()
    await session.refresh(ticket)

    return ticket