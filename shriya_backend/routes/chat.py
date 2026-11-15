from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Literal

from shriya_backend.services.chatbot import get_chat_response

router = APIRouter(prefix="/api/chat", tags=["AI Chatbot"])


class ChatMessage(BaseModel):
    role: Literal["user", "model"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=20)


@router.post("")
def chat(request: ChatRequest):
    if request.messages[-1].role != "user":
        raise HTTPException(
            status_code=400,
            detail="The last message must be from the user."
        )

    try:
        reply = get_chat_response(
            [message.model_dump() for message in request.messages]
        )
        return {"reply": reply}

    except Exception as error:
        print(f"Chatbot error: {error}")
        raise HTTPException(
            status_code=502,
            detail="The AI assistant could not respond. Please try again."
        )
