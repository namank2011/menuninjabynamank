# Quick Menu Agent - Ollama + ShopVerse Bulk Upload

This backend extracts menu items from images, PDFs, Excel files, Word files, CSV, or TXT and writes them into the ShopVerse `Product Bulk` upload sheet.

## What it does

- Uses Ollama locally for menu extraction.
- Supports image menus using an Ollama vision model.
- Supports PDFs: text PDF first; scanned PDF fallback uses vision page images.
- Keeps variations and prices separated by `#`.
- Uses the uploaded ShopVerse bulk upload template.
- Creates a review JSON so the team can verify low-confidence or missing price rows before final upload.


## Supported input files

The `menu_file` upload accepts:

- Images: `.jpg`, `.jpeg`, `.png`, `.webp`, `.bmp`, `.tif`, `.tiff`
- PDF: `.pdf`
- Excel: `.xlsx`, `.xlsm`, `.xls`, `.csv`
- Word: `.docx` and `.doc`
- Text: `.txt`

Important: `.docx` works directly. Old `.doc` files need LibreOffice installed because Python cannot reliably read old binary Word files directly. For best results, upload `.docx` or PDF.

Routing logic:

- Images go to the Ollama vision model.
- Scanned PDFs are converted page-wise to images and sent to the vision model.
- Text PDFs, Excel, Word, CSV, and TXT are converted into clean text and sent to the text model.

## Install

```bash
cd C:\Users\Amonex\QuickMenuAgent
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Ollama setup

Install/pull models. You can use any good local model, but set the same names in `.env` or environment variables.

```bash
ollama pull llama3.1:8b
ollama pull llava:7b
```

Optional environment setup on Windows PowerShell:

```powershell
$env:OLLAMA_BASE_URL="http://localhost:11434"
$env:OLLAMA_TEXT_MODEL="llama3.1:8b"
$env:OLLAMA_VISION_MODEL="llava:7b"
```

## Run API

```bash
uvicorn backend.main:app --reload
```

Open:
```text
http://127.0.0.1:8000/docs
```

## Run API with Permanent Public Tunnel (100% Free, Uses Local Ollama)

If you want to access your local dashboard using a permanent public URL from other devices (like a phone, tablet, or client computer) completely for free:

```powershell
./run_with_tunnel.ps1
```
This script allows you to choose:
1. **Ngrok**: To expose your dashboard to a free permanent domain (e.g. `https://yourname.ngrok-free.app`). Register on [ngrok.com](https://ngrok.com/) to get your authtoken and static domain.
2. **Serveo**: A zero-install SSH tunnel exposing your dashboard to `https://yoursubdomain.serveo.net`.


Use `POST /extract-menu`:

- `menu_file`: your menu image/PDF/Excel/Word file
- `template_file`: `Bulk_Upload_Sheet_Format.xlsx`

The response gives:

- `download_output_url`: final bulk upload Excel
- `download_review_url`: JSON review report

## Run CLI

```bash
python -m backend.main --input "menu.jpg" --template "templates/Bulk_Upload_Sheet_Format.xlsx" --output "outputs/final_bulk_upload.xlsx" --review "outputs/review.json"
```

## Important output mapping

The writer fills these template columns:

- `Category Name*`
- `Product Name*`
- `Variation`
- `Selling Price*`
- `Listing Price`
- `Master Status*`
- `Menu Status*`
- `Stock Status*`
- `Description`
- `Tax Category`
- `Tax Type`
- `Tax Value`
- `Station`
- `Dietary Tag`
- `Get Weight from Weighing Scale`
- `Image URL 1`

For variations:

```text
Variation = Small#Medium#Large
Selling Price* = 99#149#199
Listing Price = 99#149#199
```

## Review rule

Rows with missing prices, missing names, invalid tags, or low confidence are flagged in the review JSON. Do not upload the bulk file until those rows are checked.
