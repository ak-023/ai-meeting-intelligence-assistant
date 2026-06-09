from fastapi import FastAPI, Request, UploadFile, File
from langchain_openai import ChatOpenAI
import httpx
import pdfplumber
import json
import re

app = FastAPI()

# LLM CONFIG
llm = ChatOpenAI(
    base_url="https://genailab.tcs.in",
    model="azure_ai/genailab-maas-DeepSeek-V3-0324",
    api_key="YOUR_API_KEY",
    http_client=httpx.Client(verify=False)
)

MEMORY_DB = {
    "history": []
}

# --------------------
# UTILITIES
# --------------------

def clean_text(text):
    return re.sub(r"\s+", " ", text).strip()


def chunk_text(text, max_len=2000):
    return [text[i:i + max_len] for i in range(0, len(text), max_len)]


def ensure_list(val):
    return val if isinstance(val, list) else []


def normalize_output(data):
    return {
        "decisions": ensure_list(data.get("decisions")),
        "action_items": ensure_list(data.get("action_items")),
        "risks": ensure_list(data.get("risks")),
        "summary": data.get("summary", ""),
        "insights": data.get(
            "insights",
            {
                "missing_owners": [],
                "pending_items": [],
                "decision_changes": []
            }
        )
    }


def safe_parse_json(text):
    try:
        return json.loads(text)
    except:
        pass

    text = text.strip()

    text = re.sub(r"^```json", "", text)
    text = re.sub(r"```$", "", text)

    match = re.search(r"\{.*\}", text, re.DOTALL)

    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass

    return {}


def dedupe(items, key=None):
    seen = set()
    result = []

    for item in items:
        value = item if key is None else item.get(key)

        if value and value not in seen:
            seen.add(value)
            result.append(item)

    return result


# --------------------
# PROMPT
# --------------------

def build_prompt(text, previous_context=""):
    return f"""
You are a meeting intelligence assistant.

Return ONLY valid JSON.

Format:
{{
  "decisions": ["string"],
  "action_items": [
    {{
      "task":"string",
      "owner":"string",
      "status":"Pending|In Progress|Completed",
      "priority":"High|Medium|Low"
    }}
  ],
  "risks": [
    {{
      "issue":"string",
      "severity":"High|Medium|Low"
    }}
  ],
  "summary":"string",
  "insights": {{
    "missing_owners":["string"],
    "pending_items":["string"],
    "decision_changes":["string"]
  }}
}}

Previous Meeting Context:
{previous_context}

Meeting Transcript:
{text}
"""


# --------------------
# CORE PROCESSING
# --------------------

async def process_transcript(transcript):

    transcript = clean_text(transcript)

    chunks = chunk_text(transcript)

    previous_context = "\n".join(
        MEMORY_DB["history"][-3:]
    )

    result = {
        "decisions": [],
        "action_items": [],
        "risks": [],
        "summary": "",
        "insights": {
            "missing_owners": [],
            "pending_items": [],
            "decision_changes": []
        }
    }

    for chunk in chunks:

        try:

            prompt = build_prompt(
                chunk,
                previous_context
            )

            response = llm.invoke(prompt)

            parsed = safe_parse_json(
                response.content
            )

            if not parsed:

                fallback = llm.invoke(
                    f"Summarize this meeting:\n{chunk}"
                )

                result["summary"] += (
                    fallback.content + " "
                )

                continue

            parsed = normalize_output(parsed)

            result["decisions"].extend(
                parsed["decisions"]
            )

            result["action_items"].extend(
                parsed["action_items"]
            )

            result["risks"].extend(
                parsed["risks"]
            )

            result["summary"] += (
                parsed["summary"] + " "
            )

            for k in result["insights"]:
                result["insights"][k].extend(
                    parsed["insights"].get(k, [])
                )

        except Exception as e:
            print("Error:", e)

    result["decisions"] = list(
        set(result["decisions"])
    )

    result["action_items"] = dedupe(
        result["action_items"],
        "task"
    )

    result["risks"] = dedupe(
        result["risks"],
        "issue"
    )

    try:
        final_summary = llm.invoke(
            f"Create a concise executive summary:\n{result['summary']}"
        )

        result["summary"] = (
            final_summary.content
        )

    except:
        pass

    MEMORY_DB["history"].append(
        result["summary"]
    )

    return result


# --------------------
# TEXT ENDPOINT
# --------------------

@app.post("/process")
async def process(request: Request):

    body = await request.json()

    transcript = body.get(
        "transcript",
        ""
    )

    return await process_transcript(
        transcript
    )


# --------------------
# PDF ENDPOINT
# --------------------

@app.post("/process-pdf")
async def process_pdf(
        file: UploadFile = File(...)
):

    text = ""

    with pdfplumber.open(
            file.file
    ) as pdf:

        for page in pdf.pages:
            text += (
                page.extract_text()
                or ""
            )

    return await process_transcript(
        text
    )


# --------------------
# Q&A ENDPOINT
# --------------------

@app.post("/ask")
async def ask(request: Request):

    body = await request.json()

    question = body.get(
        "question",
        ""
    )

    context = "\n".join(
        MEMORY_DB["history"][-3:]
    )

    response = llm.invoke(
        f"""
Context:
{context}

Question:
{question}
"""
    )

    return {
        "answer": response.content
    }
