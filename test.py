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


# ==========================================================
# SCHEMAS
# ==========================================================

class BoundingBox(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float


class TaskSchema(BaseModel):
    task_num: str = Field(
        ...,
        description="Номер задачи"
    )

    condition: str = Field(
        ...,
        description=(
            "Полное условие задачи. "
            "Все математические выражения должны быть обернуты в $...$"
        )
    )

    page_number: int

    bbox: BoundingBox

    has_image: bool = False


class DocumentSchema(BaseModel):
    tasks: List[TaskSchema]


class SolutionSchema(BaseModel):
    solution: str
    answer: str


# ==========================================================
# OCR
# ==========================================================

def extract_tasks(pdf_path: str):

    schema = DocumentSchema.model_json_schema()

    # response = client.ocr.process(
    #     model="mistral-ocr-latest",
    #     document={
    #         "type": "document_url",
    #         "document_url": pdf_path
    #     },
    #     document_annotation_format={
    #         "type": "json_schema",
    #         "json_schema": {
    #             "name": "tasks",
    #             "strict": True,
    #             "schema": schema
    #         }
    #     }
    # )
    response = client.ocr.process(
        model="mistral-ocr-latest",
        document={
            "type": "document_url",
            "document_url": INPUT_FILE
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
    
    for page in response.pages:
        print(page.model_dump().keys())

    return json.loads(response.document_annotation)["tasks"]

print("=" * 50)
print("TASKS FOUND:", len(tasks))
print("=" * 50)

for t in tasks:
    print(
        t["task_num"],
        t.get("page_number"),
        t.get("has_image")
    )
print("=" * 50)

for i, page in enumerate(ocr_response.pages):

    print(
        f"PAGE {i+1}"
    )

    d = page.model_dump()

    print(d.keys())

    if "images" in d:
        print(
            "images:",
            len(d["images"])
        )
# ==========================================================
# GET PAGE IMAGES + OBJECTS
# ==========================================================

def load_document_structure(pdf_path):

    response = client.ocr.process(
        model="mistral-ocr-latest",
        document={
            "type": "document_url",
            "document_url": pdf_path
        },
        include_image_base64=True
    )

    return response


# ==========================================================
# PAGE RENDERING
# ==========================================================

def save_page_images(ocr_response):

    page_files = {}

    for page_idx, page in enumerate(ocr_response.pages):

        page_num = page_idx + 1

        if hasattr(page, "image_base64"):

            filename = f"page_{page_num}.png"

            with open(filename, "wb") as f:
                f.write(
                    base64.b64decode(
                        page.image_base64.split(",")[-1]
                    )
                )

            page_files[page_num] = filename

    return page_files


# ==========================================================
# INTERSECTION
# ==========================================================

def intersects(box1, box2):

    return not (
        box1["x2"] < box2["x1"]
        or box1["x1"] > box2["x2"]
        or box1["y2"] < box2["y1"]
        or box1["y1"] > box2["y2"]
    )


# ==========================================================
# CROP IMAGE
# ==========================================================

def crop_task_image(
    page_image_path,
    bbox,
    task_num
):

    img = Image.open(page_image_path)

    crop = img.crop(
        (
            int(bbox["x1"]),
            int(bbox["y1"]),
            int(bbox["x2"]),
            int(bbox["y2"]),
        )
    )

    filename = f"task_{task_num}.png"

    filepath = os.path.join(
        IMAGES_DIR,
        filename
    )

    crop.save(filepath)

    return filename


# # ==========================================================
# # SOLUTION GENERATION
# # ==========================================================

# def generate_solution(condition):

#     schema = SolutionSchema.model_json_schema()

#     response = client.chat.parse(
#         model="mistral-large-latest",
#         messages=[
#             {
#                 "role": "system",
#                 "content": (
#                     "Ты эксперт по математике. "
#                     "Решай задачи пошагово. "
#                     "Все формулы выводи через LaTeX "
#                     "в формате $...$."
#                 )
#             },
#             {
#                 "role": "user",
#                 "content": condition
#             }
#         ],
#         response_format=SolutionSchema  # Передаем модель напрямую
#     )

#     # Получаем распарсенный ответ
#     parsed_response = response.choices[0].message.parsed

#     return parsed_response.solution, parsed_response.answer

# ==========================================================
# SOLUTION GENERATION WITH RETRY
# ==========================================================

def generate_solution_with_retry(condition, task_num, retries=MAX_RETRIES):
    """
    Генерирует решение с повторными попытками при ошибках rate limit
    """
    for attempt in range(retries):
        try:
            response = client.chat.parse(
                model="mistral-large-latest",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Ты эксперт по математике. "
                            "Решай задачи пошагово. "
                            "Все формулы выводи через LaTeX "
                            "в формате $...$."
                        )
                    },
                    {
                        "role": "user",
                        "content": condition
                    }
                ],
                response_format=SolutionSchema
            )

            parsed_response = response.choices[0].message.parsed
            return parsed_response.solution, parsed_response.answer

        except SDKError as e:
            if "429" in str(e) or "rate_limited" in str(e):
                wait_time = RETRY_DELAY * (attempt + 1)
                print(f"  Rate limit exceeded for task {task_num}. Waiting {wait_time}s...")
                time.sleep(wait_time)
                
                if attempt == retries - 1:
                    print(f"  Failed to generate solution for task {task_num} after {retries} attempts.")
                    return "Error: Rate limit exceeded", "Error"
            else:
                raise e
        
        except Exception as e:
            print(f"  Error generating solution for task {task_num}: {e}")
            if attempt == retries - 1:
                return f"Error: {str(e)}", "Error"
            time.sleep(RETRY_DELAY)


# ==========================================================
# MAIN
# ==========================================================

def main():

    print("OCR...")

    tasks = extract_tasks(INPUT_FILE)

    print("=" * 50)
    print("TASKS FOUND:", len(tasks))
    print("=" * 50)

    for t in tasks:
        print(
            t.get("task_num"),
            t.get("page_number"),
            t.get("has_image")
        )

    print("Loading structure...")

    ocr_response = load_document_structure(
        INPUT_FILE
    )
    print("=" * 50)

    for i, page in enumerate(ocr_response.pages):

        print(f"PAGE {i+1}")

        d = page.model_dump()

        print(d.keys())

        if "images" in d:
            print(
                "images:",
                len(d["images"])
            )

    page_files = save_page_images(
        ocr_response
    )

    results = []

    for task in tasks:

        task_num = task["task_num"]

        condition = task["condition"]

        page_number = task["page_number"]

        image_name = None

        if task["has_image"]:

            page_path = page_files.get(
                page_number
            )

            if page_path:

                image_name = crop_task_image(
                    page_path,
                    task["bbox"],
                    task_num
                )

        print(
            f"Generating solution for task {task_num}"
        )

        solution, answer = generate_solution_with_retry(
            condition, task_num
        )

        results.append(
            {
                "task_num": task_num,
                "condition": condition,
                "image_name": image_name,
                "solution": solution,
                "answer": answer,
            }
        )

    df = pd.DataFrame(results)

    df.to_excel(
        OUTPUT_XLSX,
        index=False
    )

    print(
        f"Saved {OUTPUT_XLSX}"
    )


if __name__ == "__main__":
    main()