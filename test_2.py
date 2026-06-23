import inspect
import os
import json
import base64
import time
from typing import List, Optional

import pandas as pd
from PIL import Image
from pydantic import BaseModel, Field
from mistralai import Mistral
from mistralai.models.sdkerror import SDKError
from dotenv import load_dotenv

# ==========================================================
# CONFIG
# ==========================================================

load_dotenv()
API_KEY = os.getenv("MISTRAL_API_KEY")
client = Mistral(api_key=API_KEY)

INPUT_FILE = "https://alexlarin.net/ege/2026/trvar540.pdf"

IMAGES_DIR = "images"
OUTPUT_XLSX = "tasks.xlsx"

os.makedirs(IMAGES_DIR, exist_ok=True)

# Настройки для rate limiting
DELAY_BETWEEN_REQUESTS = 3  # секунд между запросами
MAX_RETRIES = 5
RETRY_DELAY = 5  # секунд ожидания при повторной попытке
print(inspect.signature(client.chat.parse))
#=========================================================
# Шаг 1. Упростить схему
#=========================================================

class TaskSchema(BaseModel):
    task_num: str
    condition: str
    page_number: int


class DocumentSchema(BaseModel):
    tasks: List[TaskSchema]

#========================================================
# Шаг 2. Получить задачи
#========================================================
def extract_tasks(pdf_url):

    schema = DocumentSchema.model_json_schema()

    response = client.ocr.process(
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
        }
    )

    data = json.loads(
        response.document_annotation
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
    

    with open(
        "full_text.md",
        "w",
        encoding="utf-8"
    ) as f:

        for page in response.pages:

            f.write(
                page.markdown
            )

            f.write("\n\n")

    return data["tasks"]

#==========================================================
# Шаг 3. Получить изображения OCR
#==========================================================
def extract_images(pdf_url):

    response = client.ocr.process(
        model="mistral-ocr-latest",
        document={
            "type": "document_url",
            "document_url": pdf_url
        },
        include_image_base64=True
    )

    with open(
        "ocr_dump.json",
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            response.model_dump(),
            f,
            ensure_ascii=False,
            indent=2
        )

    image_counter = 1

    for page_idx, page in enumerate(response.pages):

        print(
            f"PAGE {page_idx+1}"
        )

        if not hasattr(page, "images"):
            continue

        for img in page.images:

            filename = (
                f"page_{page_idx+1}"
                f"_img_{image_counter}.png"
            )

            filepath = os.path.join(
                IMAGES_DIR,
                filename
            )

            with open(filepath, "wb") as f:

                f.write(
                    base64.b64decode(
                        img.image_base64.split(",")[-1]
                    )
                )

            print(
                "saved:",
                filename
            )

            image_counter += 1

#==========================================================
# Шаг 4. Excel без решений
#==========================================================
def create_excel(tasks):

    rows = []

    for task in tasks:

        rows.append({
            "task_num": task["task_num"],
            "condition": task["condition"],
            "image_name": "",
            "solution": "",
            "answer": ""
        })

    df = pd.DataFrame(rows)

    df.to_excel(
        "tasks.xlsx",
        index=False
    )

#=================================================================
# Шаг 5. Main
#=================================================================
def main():

    tasks = extract_tasks(
        INPUT_FILE
    )

    print(
        "FOUND TASKS:",
        len(tasks)
    )

    for task in tasks:

        print(
            task["task_num"],
            task["page_number"]
        )

    extract_images(
        INPUT_FILE
    )

    create_excel(
        tasks
    )

if __name__ == "__main__":
    print("START")

    main()

    print("DONE")