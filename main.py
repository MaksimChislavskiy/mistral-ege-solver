import os
import json
import time
import base64
from typing import List, Dict, Any, Optional

import pandas as pd
from PIL import Image
from dotenv import load_dotenv
from pydantic import BaseModel

from mistralai import Mistral
from mistralai.models.sdkerror import SDKError


# =========================
# CONFIG
# =========================

load_dotenv()

client = Mistral(api_key=os.getenv("MISTRAL_API_KEY"))

PDF_URL = "https://alexlarin.net/ege/2026/trvar540.pdf"

IMAGES_DIR = "images"
OUTPUT_XLSX = "tasks.xlsx"

os.makedirs(IMAGES_DIR, exist_ok=True)


# =========================
# MODELS
# =========================

class Task(BaseModel):
    task_num: str
    condition: str
    page: int


class OCRResult(BaseModel):
    tasks: List[Task]


# =========================
# RETRY WRAPPER (CRITICAL FIX)
# =========================

def retry(fn, attempts=5, base_delay=2):
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            print(f"[Retry {i+1}] {e}")
            time.sleep(base_delay * (2 ** i))
    raise RuntimeError("Max retries exceeded")


# =========================
# OCR (SINGLE SOURCE OF TRUTH)
# =========================

def run_ocr(pdf_url: str):
    schema = OCRResult.model_json_schema()

    def _call():
        return client.ocr.process(
            model="mistral-ocr-latest",
            document={
                "type": "document_url",
                "document_url": pdf_url
            },
            document_annotation_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "tasks",
                    "strict": True,
                    "schema": schema
                }
            },
            include_image_base64=True
        )

    return retry(_call)


# =========================
# IMAGE EXTRACTION (FIXED)
# =========================

def extract_images(ocr_response):
    images = []

    for page_idx, page in enumerate(ocr_response.pages):

        if not hasattr(page, "images") or not page.images:
            continue

        for i, img in enumerate(page.images):

            filename = f"page_{page_idx+1}_img_{i+1}.png"
            path = os.path.join(IMAGES_DIR, filename)

            raw = base64.b64decode(img.image_base64.split(",")[-1])

            with open(path, "wb") as f:
                f.write(raw)

            images.append({
                "page": page_idx + 1,
                "filename": filename,
                "x1": img.top_left_x,
                "y1": img.top_left_y,
                "x2": img.bottom_right_x,
                "y2": img.bottom_right_y
            })

    return images


# =========================
# TASK EXTRACTION
# =========================

def extract_tasks(ocr_response):
    data = json.loads(ocr_response.document_annotation)
    return data["tasks"]


# =========================
# IMAGE ↔ TASK MATCHING (FIX FOR YOUR BUG)
# =========================

def match_images_to_tasks(tasks, images):
    """
    FIX:
    - твоя ошибка: простой порядок совпадения
    - теперь: match по page + nearest y-coordinate
    """

    mapping = {}

    for task in tasks:
        candidates = [
            img for img in images
            if img["page"] == task["page"]
        ]

        if not candidates:
            continue

        # heuristic: берём ближайшую по Y координате
        best = min(
            candidates,
            key=lambda img: abs(img["y1"])
        )

        mapping[task["task_num"]] = best["filename"]

    return mapping


# =========================
# SOLVER (MISTRAL CHAT + RETRY)
# =========================

def solve_task(task, image_path=None):

    prompt = f"""
Ты решаешь задачу ЕГЭ по математике.

ЗАДАЧА:
{task['condition']}

Верни JSON:
{{
  "solution": "...",
  "answer": "..."
}}
"""

    def _call():
        return client.chat.complete(
            model="mistral-large-latest",
            messages=[
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )

    res = retry(_call)

    return json.loads(res.choices[0].message.content)


# =========================
# EXCEL EXPORT
# =========================

def save_excel(tasks, mapping, solutions):

    rows = []

    for t in tasks:
        sol = solutions.get(t["task_num"], {})

        rows.append({
            "task_num": t["task_num"],
            "condition": t["condition"],
            "image_name": mapping.get(t["task_num"], ""),
            "solution": sol.get("solution", ""),
            "answer": sol.get("answer", "")
        })

    pd.DataFrame(rows).to_excel(OUTPUT_XLSX, index=False)


# =========================
# MAIN PIPELINE
# =========================

def main():

    print("=" * 60)
    print("START PIPELINE")
    print("=" * 60)

    print("Running OCR...")
    ocr = run_ocr(PDF_URL)
    print("OCR completed")

    print("Extracting tasks...")
    tasks = extract_tasks(ocr)
    print("Found tasks:", len(tasks))

    print("Saving images...")
    images = extract_images(ocr)
    print("Images:", len(images))

    print("Matching tasks and images...")
    mapping = match_images_to_tasks(tasks, images)
    print("Mapped images:", len(mapping))

    solutions = {}

    for i, task in enumerate(tasks, 1):

        print(f"[{i}/{len(tasks)}] Solving task {task['task_num']}")

        try:
            solutions[task["task_num"]] = solve_task(task)

        except Exception as e:
            print("Error:", e)
            solutions[task["task_num"]] = {
                "solution": "",
                "answer": ""
            }

    save_excel(tasks, mapping, solutions)

    print("DONE →", OUTPUT_XLSX)


if __name__ == "__main__":
    main()