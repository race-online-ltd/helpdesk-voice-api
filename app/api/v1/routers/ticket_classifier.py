import os
import io
import json

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlmodel.ext.asyncio.session import AsyncSession
from app.api.models import Ticket, TicketPublic, User
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

# Predefined helpdesk taxonomy: category -> subcategories + responsible team
HELPDESK_TAXONOMY: dict[str, dict] = {
    "IT Support": {
        "subcategories": ["Hardware Issue", "Software Issue", "Network/Connectivity", "Account Access", "Email & Communication", "Other IT"],
        "team": "IT Support Team",
    },
    "HR": {
        "subcategories": ["Leave & Attendance", "Payroll & Benefits", "Recruitment", "Employee Relations", "Policy Inquiry", "Other HR"],
        "team": "Human Resources Team",
    },
    "Finance": {
        "subcategories": ["Reimbursement", "Invoice & Billing", "Budget Inquiry", "Expense Report", "Other Finance"],
        "team": "Finance Team",
    },
    "Facilities": {
        "subcategories": ["Maintenance & Repair", "Cleaning & Housekeeping", "Room Booking", "Safety & Security", "Other Facilities"],
        "team": "Facilities Management Team",
    },
    "Administration": {
        "subcategories": ["Travel & Logistics", "Procurement", "Documentation", "General Inquiry", "Other Admin"],
        "team": "Administration Team",
    },
}


class TicketClassification(BaseModel):
    category: str
    subcategory: str
    assigned_team: str
    priority: str
    description: str


def _taxonomy_context() -> str:
    return "\n".join(
        f"- Category: {cat}, Subcategories: {', '.join(info['subcategories'])}, Team: {info['team']}"
        for cat, info in HELPDESK_TAXONOMY.items()
    )


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

    mime_type = file.content_type or "audio/mpeg"

    # Upload audio to the Gemini Files API
    try:
        uploaded_file = client.files.upload(
            file=io.BytesIO(audio_bytes),
            config=types.UploadFileConfig(
                mime_type=mime_type,
                display_name=file.filename or "complaint_audio",
            ),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to upload audio to AI service: {str(e)}",
        )

    classification_prompt = f"""You are an intelligent helpdesk ticket classifier for an organization.

Listen to the audio complaint carefully. The audio may be in English, Bangla, or a mix (Banglish).

Your tasks:
1. Understand the nature and context of the complaint.
2. Classify it into the most appropriate category, subcategory, and responsible team from the taxonomy below.
3. Determine priority based on urgency and business impact.
4. Write a concise English summary of the complaint.

Available taxonomy:
{_taxonomy_context()}

Priority guidelines:
- Low: General inquiry or non-urgent minor issue.
- Medium: Issue affecting the employee's productivity but a workaround exists.
- High: Significant impact on work with no workaround available.
- Critical: Severe issue affecting multiple people, data security, or business continuity.

Respond ONLY with a valid JSON object using exactly these fields:
{{
  "category": "<one of the categories above>",
  "subcategory": "<one of the subcategories for that category>",
  "assigned_team": "<the responsible team>",
  "priority": "<Low | Medium | High | Critical>",
  "description": "<2-3 sentence summary of the complaint in English>"
}}"""

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
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
        assigned_team=classification.assigned_team,
        priority=classification.priority,
        description=classification.description,
        status="Open",
        user_id=current_user.id,
    )

    session.add(ticket)
    await session.commit()
    await session.refresh(ticket)

    return ticket
