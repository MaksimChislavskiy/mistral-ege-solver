import os
import re
import json
import time
import base64
from typing import List, Optional, Dict

import pandas as pd

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from mistralai import Mistral
from mistralai.models.sdkerror import SDKError


# ==========================================================
# CONFIG
# ==========================================================

load_dotenv()

API_KEY = os.getenv("MISTRAL_API_KEY")

if not API_KEY:
    raise ValueError("MISTRAL_API_KEY not found")

client = Mistral(api_key=API_KEY)

INPUT_FILE = "https://alexlarin.net/ege/2026/trvar540.pdf"

IMAGES_DIR = "images"
OUTPUT_XLSX = "tasks.xlsx"

os.makedirs(IMAGES_DIR, exist_ok=True)

DELAY_BETWEEN_REQUESTS = 3
MAX_RETRIES = 5
RETRY_DELAY = 10


# ==========================================================
# OCR STRUCTURED OUTPUT SCHEMA
# ==========================================================

class TaskSchema(BaseModel):
    task_num: str = Field(
        description="Task number exactly as written"
    )

    condition: str = Field(
        description="Full task condition in LaTeX format"
    )

    page_number: int = Field(
        description="Page number where task starts"
    )


class DocumentSchema(BaseModel):
    tasks: List[TaskSchema]


# ==========================================================
# SOLUTION SCHEMA
# ==========================================================

class SolutionSchema(BaseModel):
    solution: str
    answer: str


# ==========================================================
# OCR
# ==========================================================

def run_ocr(pdf_url: str):

    print("Running OCR...")

    schema = DocumentSchema.model_json_schema()

    response = client.ocr.process(
        model="mistral-ocr-latest",
        document={
            "type": "document_url",
            "document_url": pdf_url
        },
        include_image_base64=True,
        document_annotation_format={
            "type": "json_schema",
            "json_schema": {
                "name": "tasks",
                "strict": True,
                "schema": schema
            }
        }
    )

    with open(
        "ocr_full_dump.json",
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            response.model_dump(),
            f,
            ensure_ascii=False,
            indent=2
        )

    print("OCR completed")

    return response


# ==========================================================
# TASK EXTRACTION
# ==========================================================

def extract_tasks(ocr_response):

    print("Extracting tasks...")

    data = json.loads(
        ocr_response.document_annotation
    )

    with open(
        "tasks_raw.json",
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    print(
        f"Found {len(data['tasks'])} tasks"
    )

    return data["tasks"]


# ==========================================================
# SAVE OCR IMAGES
# ==========================================================

def save_images(ocr_response):

    print("Saving OCR images...")

    image_info = {}

    for page_idx, page in enumerate(
        ocr_response.pages,
        start=1
    ):

        if not hasattr(page, "images"):
            continue

        for img in page.images:

            filename = img.id

            filepath = os.path.join(
                IMAGES_DIR,
                filename
            )

            with open(
                filepath,
                "wb"
            ) as f:

                f.write(
                    base64.b64decode(
                        img.image_base64.split(",")[-1]
                    )
                )

            image_info[img.id] = {
                "page": page_idx,
                "filename": filename,
                "top": img.top_left_y,
                "bottom": img.bottom_right_y,
                "left": img.top_left_x,
                "right": img.bottom_right_x
            }

            print(
                f"saved: {filename}"
            )

    return image_info


# ==========================================================
# SAVE MARKDOWN
# ==========================================================

def save_markdown_pages(ocr_response):

    with open(
        "full_text.md",
        "w",
        encoding="utf-8"
    ) as f:

        for page in ocr_response.pages:

            f.write(page.markdown)
            f.write("\n\n")


# ==========================================================
# TASK POSITION PARSER
# ==========================================================

TASK_PATTERN = re.compile(
    r"(?m)^(\d+)\.\s"
)

IMAGE_PATTERN = re.compile(
    r"!\[(.*?)\]\((.*?)\)"
)


def parse_page_structure(markdown):

    items = []

    task_matches = list(
        TASK_PATTERN.finditer(markdown)
    )

    image_matches = list(
        IMAGE_PATTERN.finditer(markdown)
    )

    for m in task_matches:

        items.append({
            "type": "task",
            "task_num": m.group(1),
            "position": m.start()
        })

    for m in image_matches:

        items.append({
            "type": "image",
            "image_name": m.group(1),
            "position": m.start()
        })

    items.sort(
        key=lambda x: x["position"]
    )

    return items


# ==========================================================
# MATCH IMAGES TO TASKS
# ==========================================================

def build_task_image_mapping(
    tasks,
    ocr_response
):

    print("Matching tasks and images...")

    mapping = {}

    tasks_by_page = {}

    for task in tasks:

        page = task["page_number"]

        tasks_by_page.setdefault(
            page,
            []
        ).append(task)

    for page_idx, page in enumerate(
        ocr_response.pages,
        start=1
    ):

        page_tasks = tasks_by_page.get(
            page_idx,
            []
        )

        if not page_tasks:
            continue

        structure = parse_page_structure(
            page.markdown
        )

        current_task = None

        for item in structure:

            if item["type"] == "task":

                current_task = item["task_num"]

            elif (
                item["type"] == "image"
                and current_task is not None
            ):

                image_name = item["image_name"]

                if current_task not in mapping:

                    mapping[current_task] = image_name

    with open(
        "task_image_mapping.json",
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            mapping,
            f,
            ensure_ascii=False,
            indent=2
        )

    print(
        f"Mapped {len(mapping)} images"
    )

    return mapping

# ==========================================================
# SOLVE SINGLE TASK
# ==========================================================

def solve_task(task_text: str):

    for attempt in range(MAX_RETRIES):

        try:

            response = client.chat.parse(
                model="mistral-large-latest",
                response_format=SolutionSchema,
                messages=[
                    {
                        "role": "system",
                        "content": """
Ты эксперт ЕГЭ по профильной математике.

Реши задачу подробно.

Требования:

1. Дай пошаговое решение.
2. Все формулы пиши в LaTeX.
3. Не используй markdown-блоки.
4. В поле solution должно быть только решение.
5. В поле answer только итоговый ответ.
6. Если задача содержит рисунок, учитывай его только если он присутствует в условии.
"""
                    },
                    {
                        "role": "user",
                        "content": task_text
                    }
                ]
            )

            parsed = (
                response
                .choices[0]
                .message
                .parsed
            )

            return parsed

        except SDKError as e:

            print(
                f"SDKError attempt "
                f"{attempt + 1}: {e}"
            )

            time.sleep(RETRY_DELAY)

        except Exception as e:

            print(
                f"Error attempt "
                f"{attempt + 1}: {e}"
            )

            time.sleep(RETRY_DELAY)

    return SolutionSchema(
        solution="Generation failed",
        answer=""
    )


# ==========================================================
# SOLVE ALL TASKS
# ==========================================================

def solve_all_tasks(
    tasks,
    image_mapping
):

    rows = []

    total = len(tasks)

    for idx, task in enumerate(
        tasks,
        start=1
    ):

        task_num = task["task_num"]

        print(
            f"[{idx}/{total}] "
            f"Solving task {task_num}"
        )

        result = solve_task(
            task["condition"]
        )

        rows.append({
            "task_num":
                task_num,

            "condition":
                task["condition"],

            "image_name":
                image_mapping.get(
                    task_num,
                    ""
                ),

            "solution":
                result.solution,

            "answer":
                result.answer
        })

        time.sleep(
            DELAY_BETWEEN_REQUESTS
        )

    return rows


# ==========================================================
# EXCEL EXPORT
# ==========================================================

def create_excel(rows):

    print("Creating Excel...")

    df = pd.DataFrame(rows)

    df.to_excel(
        OUTPUT_XLSX,
        index=False
    )

    print(
        f"Saved: {OUTPUT_XLSX}"
    )


# ==========================================================
# DEBUG OUTPUT
# ==========================================================

def save_rows_json(rows):

    with open(
        "final_tasks.json",
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            rows,
            f,
            ensure_ascii=False,
            indent=2
        )


# ==========================================================
# MAIN
# ==========================================================

def main():

    print("=" * 60)
    print("START PIPELINE")
    print("=" * 60)

    # --------------------------------------------------
    # OCR
    # --------------------------------------------------

    ocr_response = run_ocr(
        INPUT_FILE
    )

    # --------------------------------------------------
    # markdown dump
    # --------------------------------------------------

    save_markdown_pages(
        ocr_response
    )

    # --------------------------------------------------
    # tasks
    # --------------------------------------------------

    tasks = extract_tasks(
        ocr_response
    )

    # --------------------------------------------------
    # images
    # --------------------------------------------------

    save_images(
        ocr_response
    )

    # --------------------------------------------------
    # task ↔ image mapping
    # --------------------------------------------------

    image_mapping = (
        build_task_image_mapping(
            tasks,
            ocr_response
        )
    )

    print()

    print("IMAGE MAPPING")

    for k, v in image_mapping.items():

        print(
            f"Task {k} -> {v}"
        )

    print()

    # --------------------------------------------------
    # solve tasks
    # --------------------------------------------------

    rows = solve_all_tasks(
        tasks,
        image_mapping
    )

    # --------------------------------------------------
    # debug json
    # --------------------------------------------------

    save_rows_json(
        rows
    )

    # --------------------------------------------------
    # excel
    # --------------------------------------------------

    create_excel(
        rows
    )

    print("=" * 60)
    print("DONE")
    print("=" * 60)


# ==========================================================
# ENTRYPOINT
# ==========================================================

if __name__ == "__main__":

    main()
