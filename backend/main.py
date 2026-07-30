from __future__ import annotations

import argparse
import shutil
import uuid
import json
import os
import sys
import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, File, Form, UploadFile, Query, Body, Header
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from dotenv import load_dotenv
load_dotenv()

# Configure system path to resolve local imports cleanly
backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Import core modules
from bulk_writer import write_bulk_upload_excel
from file_extractors import extract_menu_from_file, SUPPORTED_IMAGE_EXTS
from database import (
    init_db, create_draft, add_draft_item, get_draft, 
    get_all_drafts, update_draft_item, get_audit_logs, log_audit, delete_draft,
    save_learned_correction, execute_query
)
from validation import validate_menu, load_validation_lists
from exporter import export_approved_menu, generate_review_report
from ollama_client import OLLAMA_BASE_URL, TEXT_MODEL, REQUEST_TIMEOUT_SECONDS
import requests

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
DEFAULT_TEMPLATE = Path(__file__).resolve().parent / "templates" / "Bulk_Upload_Sheet_Format.xlsx"
FRONTEND_DIR = BASE_DIR / "frontend"

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
FRONTEND_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Menu Ninja Menu Agent by Naman Kshetri", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    import traceback
    error_msg = f"{type(exc).__name__}: {str(exc)}"
    print(f"[Unhandled Exception] {error_msg}\n{traceback.format_exc()}")
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal Server Error",
            "message": error_msg,
            "traceback": traceback.format_exc().splitlines()
        }
    )

@app.on_event("startup")
def startup_event():
    # Initialize SQLite database schema
    init_db()

# Legacy direct endpoint (kept for compatibility)
@app.post("/extract-menu")
async def extract_menu(
    menu_file: UploadFile = File(...),
    template_file: Optional[UploadFile] = File(None),
    default_template: bool = Form(False),
    extraction_engine: str = Form("auto")
):
    run_id = uuid.uuid4().hex[:10]
    input_path = UPLOAD_DIR / f"{run_id}_{menu_file.filename}"
    with input_path.open("wb") as f:
        shutil.copyfileobj(menu_file.file, f)

    if default_template:
        template_path = DEFAULT_TEMPLATE
        if not template_path.exists():
            return JSONResponse(
                status_code=400,
                content={"error": f"Default template not found: {template_path}"},
            )
    else:
        if template_file is None:
            return JSONResponse(
                status_code=400,
                content={"error": "Please upload template_file or set default_template=true."},
            )
        template_path = UPLOAD_DIR / f"{run_id}_{template_file.filename}"
        with template_path.open("wb") as f:
            shutil.copyfileobj(template_file.file, f)

    output_path = OUTPUT_DIR / f"bulk_upload_output_{run_id}.xlsx"
    review_path = OUTPUT_DIR / f"bulk_upload_review_{run_id}.json"

    try:
        extraction = extract_menu_from_file(input_path, engine=extraction_engine)
        result = write_bulk_upload_excel(template_path, extraction, output_path, review_path)
        return {
            "status": "success",
            "items_written": result["total_items_written"],
            "review_required_count": result["review_required_count"],
            "output_file": output_path.name,
            "review_file": review_path.name,
            "download_output_url": f"/download/{output_path.name}",
            "download_review_url": f"/download/{review_path.name}",
            "review_preview": result["review_rows"][:20],
        }
    except Exception as exc:
        return JSONResponse(status_code=500, content={"status": "error", "error": str(exc)})

# ----------------- DRAFT / REVIEW FLOW PRODUCTION API -----------------

@app.get("/api/drafts")
def list_drafts():
    return get_all_drafts()

@app.get("/api/drafts/{draft_id}")
def get_draft_details(draft_id: str):
    draft = get_draft(draft_id)
    if not draft:
        return JSONResponse(status_code=404, content={"error": "Draft not found"})
    
    # Run dynamic validation engine on read to ensure they are calculated fresh
    items = draft.get("items", [])
    validated = validate_menu(items, DEFAULT_TEMPLATE)
    draft["items"] = validated
    return draft

@app.post("/api/drafts")
async def create_new_draft(
    business_name: str = Form(...),
    menu_files: List[UploadFile] = File(...),
    template_file: Optional[UploadFile] = File(None),
    default_template: bool = Form(True),
    tax_category: str = Form("Services"),
    tax_type: str = Form("GST"),
    tax_value: float = Form(5.0),
    master_status: str = Form("Active"),
    menu_status: str = Form("Active"),
    stock_status: str = Form("Active"),
    station: str = Form("Kitchen"),
    preparation_time: str = Form(""),
    default_dietary: str = Form(""),
    direct_approve: bool = Form(False),
    extraction_engine: str = Form("auto"),
    x_gemini_api_key: Optional[str] = Header(None, alias="X-Gemini-API-Key")
):
    # Save template
    run_id = uuid.uuid4().hex[:10]
    if default_template:
        template_path = DEFAULT_TEMPLATE
        if not template_path.exists():
            return JSONResponse(
                status_code=400,
                content={"error": "Default template not found on server templates/ directory."}
            )
    else:
        if not template_file:
            return JSONResponse(
                status_code=400,
                content={"error": "Please provide template_file or select default_template=true."}
            )
        template_path = UPLOAD_DIR / f"{run_id}_{template_file.filename}"
        with template_path.open("wb") as f:
            shutil.copyfileobj(template_file.file, f)

    # Save uploaded files & run extractions
    saved_files = []
    all_extracted_items = []
    
    defaults_dict = {
        "taxCategory": tax_category,
        "taxType": tax_type,
        "taxValue": tax_value,
        "masterStatus": master_status,
        "menuStatus": menu_status,
        "stockStatus": stock_status,
        "station": station,
        "preparationTime": preparation_time,
        "dietaryTag": default_dietary
    }
    errors_encountered = []
    
    # Save all uploaded files to disk first
    saved_paths = []
    for menu_file in menu_files:
        file_path = UPLOAD_DIR / f"{run_id}_{menu_file.filename}"
        with file_path.open("wb") as f:
            shutil.copyfileobj(menu_file.file, f)
            
        file_info = {
            "name": menu_file.filename,
            "path": str(file_path),
            "size": file_path.stat().st_size,
            "engine": extraction_engine,
            "timeSeconds": 0.0
        }
        saved_files.append(file_info)
        saved_paths.append((menu_file, file_path, file_info))

    use_gemini_batch = False
    gemini_key = x_gemini_api_key or os.getenv("GEMINI_API_KEY")
    
    # We can batch together if all files are images and engine supports/favors Gemini
    all_images = all(p[1].suffix.lower() in SUPPORTED_IMAGE_EXTS for p in saved_paths)
    if all_images and len(saved_paths) > 1:
        if extraction_engine == "gemini" or (extraction_engine == "auto" and gemini_key):
            use_gemini_batch = True

    if use_gemini_batch:
        print(f"Batching {len(saved_paths)} uploaded images together for unified Gemini extraction...")
        try:
            import time
            import tempfile
            from file_extractors import _compress_image_for_vision
            from ollama_client import extract_from_images_with_gemini
            
            start_time = time.time()
            temp_paths = []
            temp_dir = Path(tempfile.mkdtemp(prefix="menu_imgs_batch_"))
            for idx, (_, path, _) in enumerate(saved_paths):
                comp_path = temp_dir / f"input_{idx}.jpg"
                _compress_image_for_vision(path, comp_path)
                temp_paths.append(comp_path)
                
            extraction = extract_from_images_with_gemini(temp_paths, api_key=gemini_key)
            execution_seconds = time.time() - start_time
            time_per_file = round(execution_seconds / len(saved_paths), 2)
            
            for _, _, file_info in saved_paths:
                file_info["timeSeconds"] = time_per_file
            print(f"Unified Gemini batch extraction took {execution_seconds:.2f} seconds.")
            
            for page_item in extraction.items:
                variations = []
                for v in page_item.variations:
                    variations.append({
                        "name": v.name,
                        "sellingPrice": v.price,
                        "listingPrice": v.listing_price,
                        "confidence": page_item.confidence
                    })
                    
                mapped_item = {
                    "source": {
                        "fileName": ", ".join(p[0].filename for p in saved_paths),
                        "page": 1,
                        "rawText": page_item.source_text,
                        "confidence": page_item.confidence,
                        "initialCategory": page_item.category or "Uncategorized",
                        "initialProductName": page_item.product_name
                    },
                    "categoryName": page_item.category or "Uncategorized",
                    "productName": page_item.product_name,
                    "variantGroupName": "Portion" if any(n.lower() in ["half", "full"] for n in [v["name"] for v in variations]) else ("Size" if len(variations) > 1 else ""),
                    "variations": variations,
                    "description": page_item.description or "",
                    "dietaryTag": page_item.dietary_tag or default_dietary,
                    "masterStatus": master_status,
                    "menuStatus": menu_status,
                    "stockStatus": stock_status,
                    "itemCode": page_item.item_code or "",
                    "station": page_item.station or station,
                    "preparationTime": page_item.preparation_time or preparation_time,
                    "imageUrl1": page_item.image_url_1 or "",
                    "imageUrl2": "",
                    "imageUrl3": "",
                    "taxCategory": tax_category,
                    "taxType": tax_type,
                    "taxValue": tax_value,
                    "reviewStatus": "Not Reviewed",
                    "approved": True if direct_approve else False
                }
                all_extracted_items.append(mapped_item)
                
            # Clean up saved files
            for _, path, _ in saved_paths:
                if path.exists():
                    try:
                        os.remove(path)
                    except Exception:
                        pass
        except Exception as err:
            print(f"Gemini batch extraction failed: {err}. Falling back to sequential extraction...")
            use_gemini_batch = False

    if not use_gemini_batch:
        import concurrent.futures

        def process_single_file(menu_file, file_path, file_info):
            import time
            start_time = time.time()
            try:
                extraction = extract_menu_from_file(file_path, engine=extraction_engine, api_key=x_gemini_api_key)
                execution_seconds = time.time() - start_time
                return {
                    "success": True,
                    "extraction": extraction,
                    "execution_seconds": execution_seconds,
                    "menu_file": menu_file,
                    "file_path": file_path,
                    "file_info": file_info
                }
            except Exception as err:
                execution_seconds = time.time() - start_time
                return {
                    "success": False,
                    "error": err,
                    "execution_seconds": execution_seconds,
                    "menu_file": menu_file,
                    "file_path": file_path,
                    "file_info": file_info
                }

        # Run extraction in parallel using ThreadPoolExecutor
        max_workers = min(4, len(saved_paths))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_file = {
                executor.submit(process_single_file, menu_file, file_path, file_info): menu_file
                for menu_file, file_path, file_info in saved_paths
            }
            
            for future in concurrent.futures.as_completed(future_to_file):
                res = future.result()
                menu_file = res["menu_file"]
                file_path = res["file_path"]
                file_info = res["file_info"]
                file_info["timeSeconds"] = round(res["execution_seconds"], 2)
                
                if res["success"]:
                    extraction = res["extraction"]
                    print(f"Extraction for {menu_file.filename} took {res['execution_seconds']:.2f} seconds using engine '{extraction_engine}'.")
                    for page_item in extraction.items:
                        # Map variations
                        variations = []
                        for v in page_item.variations:
                            variations.append({
                                "name": v.name,
                                "sellingPrice": v.price,
                                "listingPrice": v.listing_price,
                                "confidence": page_item.confidence
                            })
                            
                        mapped_item = {
                            "source": {
                                "fileName": menu_file.filename,
                                "page": 1,
                                "rawText": page_item.source_text,
                                "confidence": page_item.confidence,
                                "initialCategory": page_item.category or "Uncategorized",
                                "initialProductName": page_item.product_name
                            },
                            "categoryName": page_item.category or "Uncategorized",
                            "productName": page_item.product_name,
                            "variantGroupName": "Portion" if any(n.lower() in ["half", "full"] for n in [v["name"] for v in variations]) else ("Size" if len(variations) > 1 else ""),
                            "variations": variations,
                            "description": page_item.description or "",
                            "dietaryTag": page_item.dietary_tag or default_dietary,
                            "masterStatus": master_status,
                            "menuStatus": menu_status,
                            "stockStatus": stock_status,
                            "itemCode": page_item.item_code or "",
                            "station": page_item.station or station,
                            "preparationTime": page_item.preparation_time or preparation_time,
                            "imageUrl1": page_item.image_url_1 or "",
                            "imageUrl2": "",
                            "imageUrl3": "",
                            "taxCategory": tax_category,
                            "taxType": tax_type,
                            "taxValue": tax_value,
                            "reviewStatus": "Not Reviewed",
                            "approved": True if direct_approve else False
                        }
                        all_extracted_items.append(mapped_item)
                else:
                    err = res["error"]
                    print(f"Error extracting from {menu_file.filename}: {err}")
                    errors_encountered.append(f"{menu_file.filename}: {str(err)}")
                    
                # Clean up temp file
                if file_path.exists():
                    try:
                        os.remove(file_path)
                    except Exception:
                        pass
            
    if not all_extracted_items:
        err_msg = "; ".join(errors_encountered) if errors_encountered else "No menu items were extracted. Please ensure the file contains legible menu contents."
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": f"Extraction failed: {err_msg}"}
        )
            
    # Cross-file deduplication and variation merging
    deduped_items = []
    seen_map = {}
    for item in all_extracted_items:
        cat = item["categoryName"].lower().strip()
        name = item["productName"].lower().strip()
        key = (cat, name)
        if key in seen_map:
            existing = deduped_items[seen_map[key]]
            ev_list = existing["variations"]
            for nv in item["variations"]:
                exists = False
                for ev in ev_list:
                    if ev["name"].lower().strip() == nv["name"].lower().strip() or (ev["sellingPrice"] == nv["sellingPrice"] and ev["name"] == nv["name"]):
                        exists = True
                        break
                if not exists:
                    ev_list.append(nv)
            if len(ev_list) > 1:
                existing["variantGroupName"] = "Portion" if any(n.lower() in ["half", "full"] for n in [v["name"] for v in ev_list]) else "Size"
            if item["description"] and item["description"] not in existing["description"]:
                existing["description"] = (existing["description"] + " / " + item["description"]).strip(" / ")
        else:
            seen_map[key] = len(deduped_items)
            deduped_items.append(item)
            
    all_extracted_items = deduped_items
            
    # Save draft inside database
    draft_id = create_draft(business_name, defaults_dict, saved_files)
    
    # Save template path to metadata
    defaults_dict["templatePath"] = str(template_path)
    
    # Insert items
    for item in all_extracted_items:
        add_draft_item(draft_id, item)
        
    # Get validated menu
    draft = get_draft(draft_id)
    validated_items = validate_menu(draft["items"], template_path)
    
    # Update SQLite records with validation statuses
    for v_item in validated_items:
        update_draft_item(draft_id, v_item["id"], v_item, user="AI Extractor")
        
    if direct_approve:
        import re
        safe_business_name = re.sub(r'[\\/*?:"<>| ]', "_", business_name)
        output_filename = f"{safe_business_name}_Menu_Ninja.xlsx"
        output_path = OUTPUT_DIR / output_filename
        
        # update draft details status to Approved
        import sqlite3
        conn = sqlite3.connect(str(backend_dir.parent / "outputs" / "shopverse_agent.db"))
        cursor = conn.cursor()
        cursor.execute("UPDATE drafts SET status = 'Approved' WHERE id = ?", (draft_id,))
        conn.commit()
        conn.close()
        
        # fetch fresh details with final item status
        draft = get_draft(draft_id)
        export_approved_menu(DEFAULT_TEMPLATE, output_path, draft["items"], business_name)
        
        report_json_name = f"{safe_business_name}_review_report.json"
        report_txt_name = f"{safe_business_name}_review_report.txt"
        report_json_path = OUTPUT_DIR / report_json_name
        report_txt_path = OUTPUT_DIR / report_txt_name
        
        audit_logs = get_audit_logs(draft_id)
        generate_review_report(draft, audit_logs, report_json_path, report_txt_path)
        
        log_audit(draft_id, "EXPORT_MENU", f"Menu approved and Excel file generated: {output_filename}", user="System Direct Approver")
        
        return {
            "status": "success",
            "direct_approved": True,
            "draftId": draft_id,
            "outputFile": output_filename,
            "downloadOutputUrl": f"/download/{output_filename}",
            "downloadReviewReportJsonUrl": f"/download/{report_json_name}",
            "downloadReviewReportTxtUrl": f"/download/{report_txt_name}"
        }

    return {"status": "success", "draftId": draft_id}

@app.put("/api/drafts/{draft_id}")
def update_draft(draft_id: str, data: Dict[str, Any] = Body(...)):
    draft = get_draft(draft_id)
    if not draft:
        return JSONResponse(status_code=404, content={"error": "Draft not found"})
        
    # Update business defaults or metadata if provided
    new_defaults = data.get("defaults", draft.get("defaults"))
    # Save
    import sqlite3
    conn = sqlite3.connect(str(sqlite3.absolute_path if hasattr(sqlite3, 'absolute_path') else backend_dir.parent / "outputs" / "shopverse_agent.db"))
    cursor = conn.cursor()
    cursor.execute("UPDATE drafts SET defaults = ?, business_name = ? WHERE id = ?", (json.dumps(new_defaults), data.get("businessName", draft.get("businessName")), draft_id))
    conn.commit()
    conn.close()

    # Update item list
    incoming_items = data.get("items", [])
    for it in incoming_items:
        update_draft_item(draft_id, it["id"], it, user="Human Reviewer")
        
    log_audit(draft_id, "UPDATE_DRAFT", "Draft changes and review steps saved.")
    return {"status": "success"}

@app.delete("/api/drafts/{draft_id}")
def delete_draft_api(draft_id: str):
    delete_draft(draft_id)
    return {"status": "success"}

@app.get("/api/drafts/{draft_id}/audit")
def get_audit_trail(draft_id: str):
    return get_audit_logs(draft_id)

@app.post("/api/drafts/{draft_id}/generate-descriptions")
def generate_batch_descriptions(
    draft_id: str, 
    payload: Dict[str, Any] = Body(...),
    x_gemini_api_key: Optional[str] = Header(None, alias="X-Gemini-API-Key")
):
    item_ids = payload.get("itemIds", [])
    if not item_ids:
        return {"status": "success", "updated": 0}
        
    draft = get_draft(draft_id)
    if not draft:
        return JSONResponse(status_code=404, content={"error": "Draft not found"})
        
    updated_count = 0
    for item in draft.get("items", []):
        if item["id"] in item_ids and not item.get("description"):
            # Call Gemini/Ollama text model APIs to generate descriptive sentence
            try:
                desc_prompt = f"Write a short, delicious, 1-sentence description (maximum 15 words) for the restaurant dish: '{item['productName']}'. Return ONLY the direct description sentence, do not add introductory phrases or quotes."
                gemini_key = x_gemini_api_key or os.getenv("GEMINI_API_KEY")
                if gemini_key:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={gemini_key}"
                    payload = {
                        "contents": [{
                            "parts": [{"text": desc_prompt}]
                        }]
                    }
                    r = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=30)
                    r.raise_for_status()
                    res_data = r.json()
                    ai_desc = res_data["candidates"][0]["content"]["parts"][0]["text"].strip().replace('"', '')
                else:
                    ollama_payload = {
                        "model": TEXT_MODEL,
                        "prompt": desc_prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.6,
                            "num_predict": 30
                        }
                    }
                    url = f"{OLLAMA_BASE_URL}/api/generate"
                    r = requests.post(url, json=ollama_payload, timeout=REQUEST_TIMEOUT_SECONDS)
                    r.raise_for_status()
                    ai_desc = r.json().get("response", "").strip().replace('"', '')
                
                if ai_desc:
                    item["description"] = ai_desc
                    # Mark review status to refresh validation
                    item["reviewStatus"] = "Review Required" 
                    update_draft_item(draft_id, item["id"], item, user="AI Description Generator")
                    updated_count += 1
            except Exception as err:
                print(f"Description generation failed: {err}")
                
    return {"status": "success", "updated": updated_count}

@app.post("/api/drafts/{draft_id}/approve")
def approve_and_export_menu(draft_id: str, payload: Dict[str, Any] = Body(...)):
    approved_agreement = payload.get("approvedAgreement", False)
    if not approved_agreement:
        return JSONResponse(status_code=400, content={"error": "Final approval agreement checkbox must be checked."})
        
    draft = get_draft(draft_id)
    if not draft:
        return JSONResponse(status_code=404, content={"error": "Draft not found"})
        
    # Re-run final validation
    validated_items = validate_menu(draft["items"], DEFAULT_TEMPLATE)
    
    # Are there any blocking errors? Only approved items can be checked.
    # The checkbox agreement means the reviewer validates the items.
    approved_items = [it for it in validated_items if it.get("approved")]
    
    # Check if there are blocking errors on any of the approved items
    for it in approved_items:
        blocking_errors = [e for e in it.get("validationErrors", []) if e["type"] == "Blocking Error"]
        if blocking_errors:
            return JSONResponse(
                status_code=400, 
                content={"error": f"Product '{it['productName']}' has blocking errors and cannot be exported: {[e['message'] for e in blocking_errors]}"}
            )

    # Save learned corrections to database memory for online feedback loop
    for it in approved_items:
        source_meta = it.get("source", {})
        initial_name = source_meta.get("initialProductName")
        final_name = it.get("productName")
        if initial_name and final_name and initial_name != final_name:
            save_learned_correction("product_name", initial_name, final_name)
            
        initial_cat = source_meta.get("initialCategory")
        final_cat = it.get("categoryName")
        if initial_cat and final_cat and initial_cat != final_cat:
            save_learned_correction("category", initial_cat, final_cat)
            
    # Export excel using the dynamic template
    import re
    safe_business_name = re.sub(r'[\\/*?:"<>| ]', "_", draft['businessName'])
    output_filename = f"{safe_business_name}_Menu_Ninja.xlsx"
    output_path = OUTPUT_DIR / output_filename
    
    export_approved_menu(DEFAULT_TEMPLATE, output_path, validated_items, draft["businessName"])
    
    # Generate Review Report
    report_json_name = f"{safe_business_name}_review_report.json"
    report_txt_name = f"{safe_business_name}_review_report.txt"
    report_json_path = OUTPUT_DIR / report_json_name
    report_txt_path = OUTPUT_DIR / report_txt_name
    
    audit_logs = get_audit_logs(draft_id)
    generate_review_report(draft, audit_logs, report_json_path, report_txt_path)
    
    # Read files to return as Base64 data URIs so we don't persist them on the server
    import base64
    xlsx_b64 = ""
    json_b64 = ""
    txt_b64 = ""
    
    try:
        if output_path.exists():
            with open(output_path, "rb") as f:
                xlsx_b64 = base64.b64encode(f.read()).decode("utf-8")
        if report_json_path.exists():
            with open(report_json_path, "rb") as f:
                json_b64 = base64.b64encode(f.read()).decode("utf-8")
        if report_txt_path.exists():
            with open(report_txt_path, "rb") as f:
                txt_b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        print(f"Error reading generated files for memory-routing: {e}")
    finally:
        # Delete output files to avoid cloud hosting disk usage
        for p in [output_path, report_json_path, report_txt_path]:
            if p.exists():
                try:
                    os.remove(p)
                except Exception:
                    pass

    # Update Draft status to Approved
    execute_query("UPDATE drafts SET status = 'Approved' WHERE id = ?", (draft_id,), commit=True)
    
    log_audit(draft_id, "EXPORT_MENU", f"Menu approved and Excel file generated: {output_filename}")
    
    return {
        "status": "success",
        "outputFile": output_filename,
        "reviewReportJson": report_json_name,
        "reviewReportTxt": report_txt_name,
        "downloadOutputUrl": f"data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,{xlsx_b64}",
        "downloadReviewReportJsonUrl": f"data:application/json;base64,{json_b64}",
        "downloadReviewReportTxtUrl": f"data:text/plain;base64,{txt_b64}"
    }

@app.get("/api/diagnostics")
def get_diagnostics():
    from verify_pipeline import run_diagnostics
    report = run_diagnostics(verbose=False)
    return report

@app.get("/api/health")
def get_health():
    from verify_pipeline import run_diagnostics
    report = run_diagnostics(verbose=False)
    return {
        "status": report["status"],
        "timestamp": report["timestamp"],
        "database": report["steps"].get("database", {}).get("writeable", False),
        "ollama_online": report["steps"].get("ollama", {}).get("online", False)
    }

@app.get("/download/{filename}")
def download_file(filename: str):
    file_path = OUTPUT_DIR / filename
    if not file_path.exists():
        return JSONResponse(status_code=404, content={"error": "File not found"})
    return FileResponse(str(file_path), filename=filename)

# Serve Frontend SPA
@app.get("/")
def index():
    index_file = FRONTEND_DIR / "index.html"
    if not index_file.exists():
        return {"message": "Menu Ninja Frontend files not found. Creating interface structure..."}
    return FileResponse(str(index_file))

# Mount static frontend directory
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR / "static")), name="static")

def run_cli(input_file: str, template: str, output: str, review: Optional[str] = None):
    extraction = extract_menu_from_file(input_file)
    result = write_bulk_upload_excel(template, extraction, output, review)
    print("Done")
    print(f"Items written: {result['total_items_written']}")
    print(f"Review required: {result['review_required_count']}")
    print(f"Output: {result['output_xlsx']}")
    if review:
        print(f"Review JSON: {review}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract menu and create Menu Ninja bulk upload XLSX.")
    parser.add_argument("--input", required=True, help="Menu file path: image/pdf/docx/xlsx/csv/txt")
    parser.add_argument("--template", required=True, help="Bulk upload template XLSX path")
    parser.add_argument("--output", required=True, help="Output XLSX path")
    parser.add_argument("--review", default=None, help="Optional review JSON path")
    args = parser.parse_args()
    run_cli(args.input, args.template, args.output, args.review)
