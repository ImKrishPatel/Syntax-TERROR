
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

client = genai.Client(api_key=API_KEY) if API_KEY else None

SYSTEM_INSTRUCTION = """
You are the friendly AI assistant on the Syntax-TERROR wildfire website.

Help visitors understand the website and its features.
You can also answer general questions.
Be friendly, clear, and concise.
Never invent wildfire statistics or claim to have accessed live website data.
If you do not know something, say so.
"""


def get_chat_response(messages):
    if client is None:
        raise RuntimeError("Gemini API key is not configured.")

    contents = []

    for message in messages[-20:]:
        contents.append(
            types.Content(
                role="user" if message["role"] == "user" else "model",
                parts=[types.Part.from_text(text=message["content"])]
            )
        )

    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    temperature=0.7,
                    max_output_tokens=800
                )
            )

            if not response.text:
                raise RuntimeError("Gemini returned an empty response.")

            return response.text

        except Exception as error:
            error_message = str(error)

            # Retry only temporary service or rate-limit errors.
            if not any(code in error_message for code in ["503", "429", "500", "UNAVAILABLE", "RESOURCE_EXHAUSTED"]):
                raise

            if attempt == 2:
                raise

            time.sleep(2 ** attempt)

    raise RuntimeError("Gemini could not respond after multiple attempts.")
