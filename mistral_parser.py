import os
import re
import json
import base64
from typing import List, Dict, Optional
from PIL import Image
import pandas as pd
import fitz
from mistralai import Mistral
from config import API_KEY

client = Mistral(api_key=API_KEY)

# ========== 1. КОНВЕРТАЦИЯ PDF В ИЗОБРАЖЕНИЯ ==========
def pdf_to_images(pdf_path: str, dpi: int = 150) -> List[str]:
    """Конвертирует каждую страницу в PNG (уменьшил dpi для экономии токенов)"""
    pdf = fitz.open(pdf_path)
    image_paths = []
    
    for page_num in range(len(pdf)):
        page = pdf[page_num]
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        
        img_path = f"page_{page_num + 1}.png"
        pix.save(img_path)
        image_paths.append(img_path)
        print(f"  📄 Страница {page_num + 1} → {img_path}")
    
    pdf.close()
    return image_paths

# ========== 2. ОТПРАВКА В MISTRAL (УЛУЧШЕННЫЙ) ==========
def extract_tasks_from_page(image_path: str, page_num: int, expected_tasks: List[int]) -> Dict:
    """Извлекает задачи с одной страницы"""
    
    with open(image_path, "rb") as f:
        base64_image = base64.b64encode(f.read()).decode('utf-8')
    
    tasks_str = ", ".join(map(str, expected_tasks))
    
    prompt = f"""Ты анализируешь страницу {page_num} ЕГЭ по математике.

На этой странице задачи с номерами: {tasks_str}.

Верни JSON без markdown-обертки, только чистый JSON:
{{"tasks": [{{"task_num": "1", "condition": "...", "has_drawing": false, "drawing_bbox": null, "solution": "", "answer": ""}}]}}

ПРАВИЛА:
1. Для каждой задачи из {tasks_str} создай объект.
2. condition - полное условие с LaTeX.
3. has_drawing = true только если есть чертёж.
4. drawing_bbox = [x1,y1,x2,y2] - координаты чертежа в пикселях.
5. solution и answer для задач 1-12 оставь пустыми.
6. НЕ используй markdown (```json), верни ТОЛЬКО JSON.
7. НЕ обрезай ответ, включи все задачи."""

    try:
        response = client.chat.complete(
            model="mistral-large-latest",
            messages=[
                {"role": "system", "content": "Ты эксперт ЕГЭ. Отвечаешь ТОЛЬКО чистым JSON без markdown."},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": f"data:image/png;base64,{base64_image}"}
                ]}
            ],
            temperature=0.0,
            max_tokens=16000  # Увеличил лимит
        )
        
        raw = response.choices[0].message.content
        
        # Убираем markdown-обертку
        raw = re.sub(r'^```json\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        raw = raw.strip()
        
        # Ищем JSON
        json_match = re.search(r'\{.*\}$', raw, re.DOTALL)
        if json_match:
            raw = json_match.group(0)
        
        data = json.loads(raw)
        print(f"  ✅ Найдено задач: {len(data.get('tasks', []))}")
        return data
        
    except json.JSONDecodeError as e:
        print(f"  ⚠️ Ошибка JSON: {e}")
        print(f"  📝 Текст: {raw[:300]}")
        return {"tasks": []}
    except Exception as e:
        print(f"  ⚠️ Ошибка API: {e}")
        return {"tasks": []}

# ========== 3. ВЫРЕЗАНИЕ ЧЕРТЕЖЕЙ ==========
def crop_drawing(image_path: str, bbox: List[int], task_num: str) -> Optional[str]:
    """Вырезает чертёж по координатам"""
    try:
        img = Image.open(image_path)
        x1, y1, x2, y2 = bbox
        
        width, height = img.size
        x1 = max(0, min(x1, width))
        x2 = max(0, min(x2, width))
        y1 = max(0, min(y1, height))
        y2 = max(0, min(y2, height))
        
        if x2 - x1 < 10 or y2 - y1 < 10:
            return None
            
        cropped = img.crop((x1, y1, x2, y2))
        os.makedirs("drawings", exist_ok=True)
        filename = f"drawings/task_{task_num}.png"
        cropped.save(filename)
        print(f"  ✂️ Вырезан чертёж для задачи {task_num}")
        return filename
        
    except Exception as e:
        print(f"  ⚠️ Не удалось вырезать: {e}")
        return None

# ========== 4. ОСНОВНОЙ ПАЙПЛАЙН ==========
def run_pipeline(pdf_path: str, output_excel: str = "ege_tasks_full.xlsx"):
    print("\n" + "="*70)
    print("🚀 ПАРСИНГ ВСЕХ ЗАДАЧ 1-19 С ВЫРЕЗАНИЕМ ЧЕРТЕЖЕЙ")
    print("="*70)
    
    # 1. Конвертируем PDF
    print("\n📸 Конвертация PDF в изображения...")
    images = pdf_to_images(pdf_path, dpi=150)
    
    # 2. Определяем, какие задачи на какой странице
    page_tasks = {
        0: list(range(1, 4)),    # стр.1: задачи 1-3
        1: list(range(4, 13)),   # стр.2: задачи 4-12
        2: list(range(13, 20))   # стр.3: задачи 13-19
    }
    
    # 3. Обрабатываем каждую страницу
    print("\n🤖 Анализ страниц через Mistral API...")
    all_tasks = {}
    drawings_info = {}
    
    for page_idx, img_path in enumerate(images, 1):
        print(f"\n--- Страница {page_idx} ---")
        expected = page_tasks.get(page_idx - 1, [])
        
        if not expected:
            print(f"  ⏭️ Пропускаем (нет задач)")
            continue
            
        result = extract_tasks_from_page(img_path, page_idx, expected)
        
        for task in result.get("tasks", []):
            task_num = str(task.get("task_num", ""))
            if task_num and task_num not in all_tasks:
                all_tasks[task_num] = task
                
                # Вырезаем чертёж
                if task.get("has_drawing") and task.get("drawing_bbox"):
                    bbox = task["drawing_bbox"]
                    if len(bbox) == 4 and bbox != [0,0,0,0]:
                        filename = crop_drawing(img_path, bbox, task_num)
                        if filename:
                            drawings_info[task_num] = filename
    
    # 4. Добавляем пропущенные задачи
    for num in range(1, 20):
        num_str = str(num)
        if num_str not in all_tasks:
            all_tasks[num_str] = {
                "task_num": num_str,
                "condition": "[НЕ НАЙДЕНА]",
                "has_drawing": False,
                "solution": "",
                "answer": ""
            }
    
    # 5. Сохраняем в Excel
    print("\n📊 Сохранение результатов...")
    rows = []
    for task_num in sorted(all_tasks.keys(), key=int):
        task = all_tasks[task_num]
        condition = task.get("condition", "")
        # Если condition - словарь (для задач с подпунктами), преобразуем
        if isinstance(condition, dict):
            condition = "\n".join([f"{k}) {v}" for k, v in condition.items()])
        
        rows.append({
            "task_num": task_num,
            "condition": condition,
            "image_name": f"drawings/task_{task_num}.png" if task_num in drawings_info else "",
            "solution": task.get("solution", ""),
            "answer": task.get("answer", "")
        })
    
    df = pd.DataFrame(rows)
    df.to_excel(output_excel, index=False)
    
    # 6. Очистка временных файлов
    for img in images:
        if os.path.exists(img):
            os.remove(img)
    
    print(f"\n✅ ГОТОВО! Файл: {output_excel}")
    print(f"📊 Всего задач: {len(rows)}")
    print(f"✂️ Вырезано чертежей: {len(drawings_info)}")
    
    return all_tasks

# ========== 5. ЗАПУСК ==========
if __name__ == "__main__":
    pdf_file = "trvar540.pdf"
    
    if not os.path.exists(pdf_file):
        print(f"❌ Файл {pdf_file} не найден!")
    else:
        result = run_pipeline(pdf_file, "ege_tasks_full.xlsx")


==================================================================

import os
import json
import base64
from mistralai import Mistral
from pydantic import BaseModel, Field
from typing import Optional, List

# Инициализация клиента
API_KEY = "WIg4HpK3VJ6tc4zL52pWGiWf2DhFvEga"
client = Mistral(api_key=API_KEY)
pdf_url = "https://alexlarin.net/ege/2026/trvar540.pdf"

os.makedirs("extracted_images", exist_ok=True)

# --- 1. СХЕМЫ ДАННЫХ ДЛЯ СТРУКТУРИРОВАННОГО OCR ---
class MathTask(BaseModel):
    task_number: str = Field(..., description="Номер задачи (число от 1 до 19)")
    task_text: str = Field(..., description="Полное условие задачи")
    page_number: int = Field(..., description="Номер страницы (начиная с 1)")

class DocumentTasks(BaseModel):
    tasks: List[MathTask] = Field(..., description="Список всех найденных задач")


# --- ШАГ 1: Извлекаем чистый текст задач ---
print("Шаг 1: Извлечение условий задач через Structured OCR...")
tasks_schema = DocumentTasks.model_json_schema()
task_response = client.ocr.process(
    model="mistral-ocr-latest",
    document={"type": "document_url", "document_url": pdf_url},
    document_annotation_format={
        "type": "json_schema",
        "json_schema": {"name": "tasks_schema", "strict": True, "schema": tasks_schema}
    }
)
extracted_tasks = json.loads(task_response.document_annotation)["tasks"]


# --- ШАГ 2: Получаем все медиа-данные из документа ---
print("Шаг 2: Получение медиа-данных и структуры страниц...")
ocr_response = client.ocr.process(
    model="mistral-ocr-latest",
    document={"type": "document_url", "document_url": pdf_url},
    include_image_base64=True
)

# Группируем картинки по физическим страницам с сохранением их координат
page_images = {}

for page_idx, page in enumerate(ocr_response.pages):
    print('Страница', page_idx + 1)
    page_num = page_idx + 1
    page_images[page_num] = []
    
    if hasattr(page, 'images') and page.images:
        print("images:", len(page.images))
        for img in page.images:
            # for i, img in enumerate(page.images):
            #     d = img.model_dump()

            #     print(
            #         f"img#{i}",
            #         "id=", d["id"],
            #         "x1=", d["top_left_x"],
            #         "y1=", d["top_left_y"],
            #         "x2=", d["bottom_right_x"],
            #         "y2=", d["bottom_right_y"],
            #     )
            #     w = d["bottom_right_x"] - d["top_left_x"]
            #     h = d["bottom_right_y"] - d["top_left_y"]

            #     print(
            #         f"img#{i}",
            #         f"x={d['top_left_x']:.1f}",
            #         f"y={d['top_left_y']:.1f}",
            #         f"w={w:.1f}",
            #         f"h={h:.1f}",
            #     )
            print(img.model_dump().keys())
            page_images[page_num].append({
                "id": img.id,
                "base64": img.image_base64,
                "top": img.top_left_y if img.top_left_y is not None else 0,
                "left": img.top_left_x if img.top_left_x is not None else 0
            })
        # Сортируем картинки на странице строго сверху вниз
        page_images[page_num].sort(key=lambda x: x["top"])


# --- ШАГ 3: Таргетированное распределение картинок по задачам ---
print("Шаг 3: Синхронизация картинок по координатам и структуре...")
final_result = []

for task in extracted_tasks:
    t_num = task["task_number"]
    p_num = task["page_number"]
    t_text = task["task_text"]
    
    matched_image_path = None
    
    # 1. Задача №1 (Остроугольный треугольник на 1-й странице)
    if t_num == "1":
        p1_images = page_images.get(1, [])
        # Игнорируем элементы в самом верху (шапка, логотипы, QR-коды обычно имеют top < 150-200)
        # Настоящий рисунок к задаче расположен ниже текста инструкции
        valid_geometry_imgs = [
            img for img in p1_images
            if img["top"] > 100
        ]
        
        if valid_geometry_imgs:
            target_img = valid_geometry_imgs[0]  # Первый настоящий рисунок на странице
            matched_image_path = f"extracted_images/task_1.jpg"
            print(
                t_num,
                target_img["id"],
                target_img["top"]
            )
            with open(matched_image_path, "wb") as f:
                f.write(base64.b64decode(target_img["base64"].split(',')[-1]))

    # 2. Задача №3 (Стереометрия — прямая призма)
    # По структуре вариантов Ларина, задача 3 идет следом за планиметрией.
    # Если на 1-й странице нашлась вторая «валидная» картинка ниже первой — это она. 
    # Если нет, ищем её на 2-й странице.
    elif t_num == "3":
        target_img = None
        p1_images = page_images.get(1, [])
        valid_geometry_imgs = [img for img in p1_images if img["top"] > 100]
        
        if len(valid_geometry_imgs) >= 2:
            target_img = valid_geometry_imgs[1]  # Вторая картинка на 1-й странице
        else:
            # Если уползла на 2-ю страницу, берем там самую первую
            p2_images = page_images.get(2, [])
            if p2_images:
                target_img = p2_images[0]
                
        if target_img:
            print(
                t_num,
                target_img["id"],
                target_img["top"]
            )
            matched_image_path = f"extracted_images/task_3.jpg"
            with open(matched_image_path, "wb") as f:
                f.write(base64.b64decode(target_img["base64"].split(',')[-1]))

    # 3. Задача №11 (График функции)
    elif t_num == "11":
        target_img = None
        # График функции обычно находится на 2-й или 3-й странице
        for p in [2, 3]:
            imgs = page_images.get(p, [])
            if imgs:
                # Если на стр. 2 уже забрали призму для задачи 3, графиком будет следующая картинка
                if p == 2 and t_num == "11" and len(imgs) >= 2:
                    target_img = imgs[1]
                else:
                    target_img = imgs[0]
                break
                
        if target_img:
            print(
                t_num,
                target_img["id"],
                target_img["top"]
            )
            matched_image_path = f"extracted_images/task_11.jpg"
            with open(matched_image_path, "wb") as f:
                f.write(base64.b64decode(target_img["base64"].split(',')[-1]))

    final_result.append({
        "task_number": t_num,
        "task_text": t_text,
        "image_path": matched_image_path,
        "page_number": p_num
    })

# --- ШАГ 4: СОРТИРОВКА И КВЕНЧИНГ ---
def safe_sort_key(item):
    try:
        return int(item['task_number'])
    except ValueError:
        return 999

final_result.sort(key=safe_sort_key)

with open("combined_tasks.json", "w", encoding="utf-8") as f:
    json.dump(final_result, f, ensure_ascii=False, indent=4)

print("\n" + "="*50)
print("ИТОГОВЫЙ СИНХРОНИЗИРОВАННЫЙ СПИСОК:")
print("="*50)
for item in final_result:
    img_status = f"📸 Найдена ({item['image_path']})" if item['image_path'] else "📝 Без картинки"
    print(f"Задача №{item['task_number']} (Стр. {item['page_number']}) — {img_status}")