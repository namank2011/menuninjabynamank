from __future__ import annotations

import argparse
import shutil
import uuid
import json
import os
import sys
import datetime
import base64
import hmac
import hashlib
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, File, Form, UploadFile, Query, Body, Header, Request, Response
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
    save_learned_correction, execute_query,
    create_pos_company, get_pos_company_by_api_key, get_all_pos_companies,
    delete_pos_company, update_pos_company
)
from validation import validate_menu, load_validation_lists
from exporter import export_approved_menu, generate_review_report, export_pos_menu
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

@app.middleware("http")
async def dynamic_cors_credentials_middleware(request: Request, call_next):
    origin = request.headers.get("origin")
    
    # Handle preflight (OPTIONS) requests
    if request.method == "OPTIONS" and origin:
        response = Response(status_code=204)
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = request.headers.get(
            "Access-Control-Request-Headers", "Content-Type, Authorization, Cookie"
        )
        return response
        
    response = await call_next(request)
    
    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = request.headers.get(
            "Access-Control-Request-Headers", "Content-Type, Authorization, Cookie"
        )
    return response

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


# Helper cryptography functions
import time
from fastapi.security import APIKeyCookie
from fastapi import Security, Depends, HTTPException, Header

SECRET_KEY = os.getenv("AUTH_SECRET_KEY", "super-secret-ninja-key-12345")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")

session_cookie = APIKeyCookie(name="session_token", auto_error=False)

def sign_token(payload: dict) -> str:
    payload_str = json.dumps(payload)
    payload_b64 = base64.urlsafe_b64encode(payload_str.encode('utf-8')).decode('utf-8')
    sig = hmac.new(SECRET_KEY.encode('utf-8'), payload_b64.encode('utf-8'), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"

def verify_token(token: str) -> Optional[dict]:
    try:
        if not token or "." not in token:
            return None
        payload_b64, sig = token.split(".", 1)
        expected_sig = hmac.new(SECRET_KEY.encode('utf-8'), payload_b64.encode('utf-8'), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        payload_str = base64.urlsafe_b64decode(payload_b64.encode('utf-8')).decode('utf-8')
        payload = json.loads(payload_str)
        if "exp" in payload and payload["exp"] < time.time():
            return None
        return payload
    except Exception:
        return None

def get_current_user(
    token_cookie: Optional[str] = Depends(session_cookie),
    authorization: Optional[str] = Header(None),
    x_session_token: Optional[str] = Header(None, alias="X-Session-Token")
):
    token = token_cookie
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
    if not token and x_session_token:
        token = x_session_token
        
    if token:
        token = token.strip('"').strip("'")

    if not token:
        raise HTTPException(status_code=401, detail="Authentication session cookie or token required.")
    
    payload = verify_token(token)
    if not payload or "email" not in payload:
        raise HTTPException(status_code=401, detail="Session expired or invalid token")
        
    email = payload["email"]
    if email.lower() == "namankshetri2@gmail.com":
        return {"email": "namankshetri2@gmail.com", "role": "super_admin", "is_allowed": True}
        
    from database import get_user_by_email
    user = get_user_by_email(email)
    if not user or not user["is_allowed"]:
        raise HTTPException(status_code=403, detail="User account deactivated or access revoked")
        
    return user

def get_super_admin(current_user=Depends(get_current_user)):
    if current_user["role"] != "super_admin":
        raise HTTPException(status_code=403, detail="Super-admin access required")
    return current_user

def require_draft_access(draft, user):
    """Check if user has access to a draft. Super admins can access all drafts.
    Menu operators can only access drafts they created."""
    if user["role"] == "super_admin":
        return True
    draft_owner = (draft.get("createdBy") or "").lower()
    return draft_owner == user["email"].lower()

# Auth endpoints
@app.get("/api/auth/config")
def get_auth_config():
    return {
        "google_client_id": GOOGLE_CLIENT_ID
    }

@app.post("/api/auth/login")
def login(payload: Dict[str, Any] = Body(...)):
    email = payload.get("email", "").strip().lower()
    password = payload.get("password", "")
    
    if not email or not password:
        return JSONResponse(status_code=400, content={"error": "Email and password are required"})
        
    if email == "namankshetri2@gmail.com" and password == "2011@Naman":
        user_info = {"email": "namankshetri2@gmail.com", "role": "super_admin"}
    else:
        from database import get_user_by_email, verify_password
        user = get_user_by_email(email)
        if not user:
            return JSONResponse(status_code=401, content={"error": "Invalid email or password"})
        if not user["is_allowed"]:
            return JSONResponse(status_code=403, content={"error": "Access deactivated by Administrator"})
        if not user["password_hash"] or not verify_password(password, user["password_hash"]):
            return JSONResponse(status_code=401, content={"error": "Invalid email or password"})
        user_info = {"email": user["email"], "role": user["role"]}
        
    token_payload = {
        "email": user_info["email"],
        "role": user_info["role"],
        "exp": time.time() + 86400 * 7
    }
    token = sign_token(token_payload)
    
    response = JSONResponse(content={"status": "success", "user": user_info, "token": token})
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        max_age=86400 * 7,
        samesite="none",
        secure=True
    )
    return response

@app.post("/api/auth/google")
def google_auth(payload: Dict[str, Any] = Body(...)):
    credential = payload.get("credential")
    if not credential:
        return JSONResponse(status_code=400, content={"error": "Missing Google credential token"})
        
    try:
        r = requests.get(f"https://oauth2.googleapis.com/tokeninfo?id_token={credential}", timeout=5)
        if r.status_code != 200:
            return JSONResponse(status_code=401, content={"error": "Invalid Google token (Google API call failed)"})
            
        token_info = r.json()
        email = token_info.get("email", "").strip().lower()
        email_verified = token_info.get("email_verified")
        
        if str(email_verified).lower() not in ("true", "1"):
            return JSONResponse(status_code=401, content={"error": "Google email not verified"})
            
        if email == "namankshetri2@gmail.com":
            user_info = {"email": "namankshetri2@gmail.com", "role": "super_admin"}
        else:
            from database import get_user_by_email
            user = get_user_by_email(email)
            if not user:
                return JSONResponse(status_code=403, content={"error": f"Access denied. '{email}' is not white-listed. Please contact namankshetri2@gmail.com"})
            if not user["is_allowed"]:
                return JSONResponse(status_code=403, content={"error": "Access deactivated by Administrator"})
            user_info = {"email": user["email"], "role": user["role"]}
            
        token_payload = {
            "email": user_info["email"],
            "role": user_info["role"],
            "exp": time.time() + 86400 * 7
        }
        token = sign_token(token_payload)
        
        response = JSONResponse(content={"status": "success", "user": user_info, "token": token})
        response.set_cookie(
            key="session_token",
            value=token,
            httponly=True,
            max_age=86400 * 7,
            samesite="none",
            secure=True
        )
        return response
        
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Error authenticating with Google: {str(e)}"})

@app.post("/api/auth/logout")
def logout():
    response = JSONResponse(content={"status": "success"})
    response.delete_cookie("session_token", samesite="none", secure=True)
    return response

@app.get("/api/auth/me")
def get_current_user_profile(user=Depends(get_current_user)):
    return {"email": user["email"], "role": user["role"]}

# Whitelist Account Management endpoints
@app.get("/api/users")
def get_users_list(admin=Depends(get_super_admin)):
    from database import get_all_users
    return get_all_users()

@app.post("/api/users")
def add_new_user(payload: Dict[str, Any] = Body(...), admin=Depends(get_super_admin)):
    email = payload.get("email", "").strip().lower()
    role = payload.get("role", "user").strip()
    password = payload.get("password")
    
    if not email:
        return JSONResponse(status_code=400, content={"error": "Email is required"})
        
    from database import get_user_by_email, create_user
    existing = get_user_by_email(email)
    if existing:
        return JSONResponse(status_code=400, content={"error": "User with this email already exists"})
        
    create_user(email, role, password)
    return {"status": "success", "message": "User registered successfully"}

@app.post("/api/users/{email}/toggle")
def toggle_user_permission(email: str, payload: Dict[str, Any] = Body(...), admin=Depends(get_super_admin)):
    is_allowed = payload.get("is_allowed", True)
    if email.lower() == "namankshetri2@gmail.com":
        return JSONResponse(status_code=400, content={"error": "Cannot deactivate the super admin"})
        
    from database import update_user_allowed
    update_user_allowed(email, is_allowed)
    return {"status": "success", "message": "User permission updated"}

@app.delete("/api/users/{email}")
def delete_user_account(email: str, admin=Depends(get_super_admin)):
    if email.lower() == "namankshetri2@gmail.com":
        return JSONResponse(status_code=400, content={"error": "Cannot delete the super admin"})
        
    from database import delete_user
    delete_user(email)
    return {"status": "success", "message": "User deleted successfully"}


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
def list_drafts(user=Depends(get_current_user)):
    return get_all_drafts(user_email=user["email"], user_role=user["role"])

@app.get("/api/drafts/{draft_id}")
def get_draft_details(draft_id: str, user=Depends(get_current_user)):
    draft = get_draft(draft_id)
    if not draft:
        return JSONResponse(status_code=404, content={"error": "Draft not found"})
    if not require_draft_access(draft, user):
        return JSONResponse(status_code=403, content={"error": "You do not have access to this draft"})
    
    # Run dynamic validation engine on read to ensure they are calculated fresh
    items = draft.get("items", [])
    validated = validate_menu(items, DEFAULT_TEMPLATE)
    draft["items"] = validated
    return draft

def trigger_webhook_task(webhook_url: str, payload: Dict[str, Any]):
    try:
        import requests
        r = requests.post(webhook_url, json=payload, headers={"Content-Type": "application/json"}, timeout=20)
        print(f"[Webhook] Sent payload to {webhook_url}. Status code: {r.status_code}")
    except Exception as e:
        print(f"[Webhook Error] Failed to POST webhook to {webhook_url}: {str(e)}")


async def execute_extraction_pipeline(
    business_name: str,
    menu_files: List[UploadFile],
    template_file: Optional[UploadFile],
    default_template: bool,
    defaults_dict: Dict[str, Any],
    extraction_engine: str,
    direct_approve: bool,
    x_gemini_api_key: Optional[str],
    created_by: str,
    pos_company_id: Optional[str] = None
) -> Any:
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

    default_dietary = defaults_dict.get("dietaryTag", "")
    master_status = defaults_dict.get("masterStatus", "Active")
    menu_status = defaults_dict.get("menuStatus", "Active")
    stock_status = defaults_dict.get("stockStatus", "Active")
    station = defaults_dict.get("station", "Kitchen")
    preparation_time = defaults_dict.get("preparationTime", "")
    tax_category = defaults_dict.get("taxCategory", "Services")
    tax_type = defaults_dict.get("taxType", "GST")
    tax_value = defaults_dict.get("taxValue", 5.0)

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
            from file_extractors import apply_learned_corrections_to_extraction
            extraction = apply_learned_corrections_to_extraction(extraction)
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
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        def _parallel_extract(menu_f, f_path, f_info):
            import time
            start = time.time()
            try:
                ext = extract_menu_from_file(f_path, engine=extraction_engine, api_key=x_gemini_api_key)
                duration = time.time() - start
                return (menu_f, f_path, f_info, ext, duration, None)
            except Exception as ex:
                duration = time.time() - start
                return (menu_f, f_path, f_info, None, duration, ex)
        
        extraction_results = [None] * len(saved_paths)
        with ThreadPoolExecutor(max_workers=min(len(saved_paths), 6)) as executor:
            futures = {
                executor.submit(_parallel_extract, menu_f, f_path, f_info): idx
                for idx, (menu_f, f_path, f_info) in enumerate(saved_paths)
            }
            for fut in as_completed(futures):
                idx = futures[fut]
                extraction_results[idx] = fut.result()
                
        for res in extraction_results:
            if not res:
                continue
            menu_file, file_path, file_info, extraction, execution_seconds, err = res
            file_info["timeSeconds"] = round(execution_seconds, 2)
            
            try:
                if err is not None:
                    raise err
                if extraction:
                    print(f"Extraction for {menu_file.filename} took {execution_seconds:.2f} seconds using engine '{extraction_engine}'.")
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
            except Exception as err:
                print(f"Error extracting from {menu_file.filename}: {err}")
                errors_encountered.append(f"{menu_file.filename}: {str(err)}")
            finally:
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
            
    # Save draft inside database with ownership tracking
    from database import get_db_connection
    conn = get_db_connection()
    try:
        draft_id = create_draft(business_name, defaults_dict, saved_files, created_by=created_by, pos_company_id=pos_company_id, conn=conn)
        
        # Save template path to metadata
        defaults_dict["templatePath"] = str(template_path)
        
        # Insert items
        for item in all_extracted_items:
            add_draft_item(draft_id, item, conn=conn)
            
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
        
    # Get validated menu
    draft = get_draft(draft_id)
    validated_items = validate_menu(draft["items"], template_path)
    
    # Update SQLite records with validation statuses
    conn = get_db_connection()
    try:
        for v_item in validated_items:
            update_draft_item(draft_id, v_item["id"], v_item, user="AI Extractor", conn=conn)
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
        
    if direct_approve:
        import re
        safe_business_name = re.sub(r'[\\/*?:"<>| ]', "_", business_name)
        
        output_format = "shopverse"
        webhook_url = None
        if pos_company_id:
            from database import execute_query
            rows = execute_query("SELECT company_name, output_format, webhook_url FROM pos_companies WHERE id = ?", (pos_company_id,))
            if rows:
                company_name, output_format, webhook_url = rows[0]
        
        # update draft details status to Approved
        conn = get_db_connection()
        try:
            execute_query("UPDATE drafts SET status = 'Approved' WHERE id = ?", (draft_id,), commit=True, conn=conn)
            conn.commit()
        finally:
            conn.close()
        
        # fetch fresh details with final item status
        draft = get_draft(draft_id)
        
        if output_format in ["urbanpiper", "json"]:
            output_filename = f"{safe_business_name}_{output_format}_Menu.json"
            mime_type = "application/json"
        else:
            output_filename = f"{safe_business_name}_{output_format}_Menu.xlsx"
            mime_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            
        output_path = OUTPUT_DIR / output_filename
        export_pos_menu(output_format, draft["items"], business_name, output_path, template_path)
        
        report_json_name = f"{safe_business_name}_review_report.json"
        report_txt_name = f"{safe_business_name}_review_report.txt"
        report_json_path = OUTPUT_DIR / report_json_name
        report_txt_path = OUTPUT_DIR / report_txt_name
        
        audit_logs = get_audit_logs(draft_id)
        generate_review_report(draft, audit_logs, report_json_path, report_txt_path)
        
        log_audit(draft_id, "EXPORT_MENU", f"Menu approved and output file generated in format '{output_format}': {output_filename}", user="System Direct Approver")
        
        # Read the file to Base64 to return
        import base64
        file_b64 = ""
        try:
            if output_path.exists():
                with open(output_path, "rb") as f:
                    file_b64 = base64.b64encode(f.read()).decode("utf-8")
        except Exception:
            pass

        # Dispatch webhook if configured
        if webhook_url:
            payload = {}
            if output_format in ["urbanpiper", "json"]:
                try:
                    payload = json.loads(output_path.read_text(encoding="utf-8"))
                except Exception:
                    payload = {"items": draft["items"]}
            else:
                payload = {
                    "event": "menu.approved",
                    "business_name": business_name,
                    "draft_id": draft_id,
                    "company_name": pos_company_id,
                    "output_format": output_format,
                    "output_file_name": output_filename,
                    "file_content_base64": file_b64,
                    "items": [it for it in draft["items"] if it.get("approved")]
                }
            
            import threading
            threading.Thread(target=trigger_webhook_task, args=(webhook_url, payload), daemon=True).start()

        # Delete local copy of output files
        for p in [output_path, report_json_path, report_txt_path]:
            if p.exists():
                try:
                    os.remove(p)
                except Exception:
                    pass

        return {
            "status": "success",
            "direct_approved": True,
            "draftId": draft_id,
            "outputFile": output_filename,
            "downloadOutputUrl": f"data:{mime_type};base64,{file_b64}",
            "downloadReviewReportJsonUrl": f"data:application/json;base64,{base64.b64encode(json.dumps(draft['items']).encode('utf-8')).decode('utf-8')}",
            "file_content_base64": file_b64,
            "items": draft["items"]
        }

    return {"status": "success", "draftId": draft_id}


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
    pos_company_id: Optional[str] = Form(None),
    x_gemini_api_key: Optional[str] = Header(None, alias="X-Gemini-API-Key"),
    user=Depends(get_current_user)
):
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
    return await execute_extraction_pipeline(
        business_name=business_name,
        menu_files=menu_files,
        template_file=template_file,
        default_template=default_template,
        defaults_dict=defaults_dict,
        extraction_engine=extraction_engine,
        direct_approve=direct_approve,
        x_gemini_api_key=x_gemini_api_key,
        created_by=user["email"],
        pos_company_id=pos_company_id
    )


# ----------------- POS PARTNER WEBHOOKS & API KEY MANAGEMENT (ADMIN ONLY) -----------------
@app.get("/api/admin/pos-companies")
def list_pos_companies(admin=Depends(get_super_admin)):
    return get_all_pos_companies()


@app.post("/api/admin/pos-companies")
def register_pos_company(payload: Dict[str, Any] = Body(...), admin=Depends(get_super_admin)):
    company_name = payload.get("company_name", "").strip()
    output_format = payload.get("output_format", "shopverse").strip().lower()
    webhook_url = payload.get("webhook_url", "").strip() or None
    api_key = payload.get("api_key", "").strip() or None
    
    if not company_name:
        return JSONResponse(status_code=400, content={"error": "company_name is required"})
    if output_format not in ["shopverse", "petpooja", "urbanpiper", "slickpos", "json"]:
        return JSONResponse(status_code=400, content={"error": "Unsupported output_format"})
        
    try:
        new_company = create_pos_company(company_name, output_format, webhook_url, api_key)
        return {"status": "success", "pos_company": new_company}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Failed to register POS company: {str(e)}"})


@app.put("/api/admin/pos-companies/{company_id}")
def update_pos_company_details(company_id: str, payload: Dict[str, Any] = Body(...), admin=Depends(get_super_admin)):
    try:
        update_pos_company(company_id, payload)
        return {"status": "success", "message": "POS company updated successfully"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Failed to update POS company: {str(e)}"})


@app.delete("/api/admin/pos-companies/{company_id}")
def delete_pos_company_integration(company_id: str, admin=Depends(get_super_admin)):
    try:
        delete_pos_company(company_id)
        return {"status": "success", "message": "POS company deleted successfully"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Failed to delete POS company: {str(e)}"})


# ----------------- UNIFIED POS API INTEGRATION ENDPOINT -----------------
@app.post("/api/v1/pos/extract")
async def pos_extract_menu(
    business_name: str = Form(...),
    menu_files: List[UploadFile] = File(...),
    api_key: Optional[str] = Query(None),
    x_pos_api_key: Optional[str] = Header(None, alias="X-POS-API-Key"),
    extraction_engine: str = Form("auto"),
    direct_approve: bool = Form(True),
    tax_category: str = Form("Services"),
    tax_type: str = Form("GST"),
    tax_value: float = Form(5.0),
    master_status: str = Form("Active"),
    default_dietary: str = Form(""),
    station: str = Form("Kitchen"),
    preparation_time: str = Form(""),
    x_gemini_api_key: Optional[str] = Header(None, alias="X-Gemini-API-Key")
):
    key = x_pos_api_key or api_key
    if not key:
        return JSONResponse(status_code=401, content={"error": "API Key is required via header 'X-POS-API-Key' or parameter 'api_key'"})
        
    pos_company = get_pos_company_by_api_key(key)
    if not pos_company:
        return JSONResponse(status_code=401, content={"error": "Invalid or inactive POS API Key"})

    defaults_dict = {
        "taxCategory": tax_category,
        "taxType": tax_type,
        "taxValue": tax_value,
        "masterStatus": master_status,
        "menuStatus": "Active",
        "stockStatus": "Active",
        "station": station,
        "preparationTime": preparation_time,
        "dietaryTag": default_dietary
    }
    
    # Delegate to core execution pipeline
    return await execute_extraction_pipeline(
        business_name=business_name,
        menu_files=menu_files,
        template_file=None,
        default_template=True,
        defaults_dict=defaults_dict,
        extraction_engine=extraction_engine,
        direct_approve=direct_approve,
        x_gemini_api_key=x_gemini_api_key,
        created_by=f"POS: {pos_company['company_name']}",
        pos_company_id=pos_company["id"]
    )

def _is_item_changed(old: Dict[str, Any], new: Dict[str, Any]) -> bool:
    fields = [
        ("categoryName", "Uncategorized"),
        ("productName", ""),
        ("variantGroupName", ""),
        ("description", ""),
        ("dietaryTag", ""),
        ("masterStatus", "Active"),
        ("menuStatus", "Active"),
        ("stockStatus", "Active"),
        ("itemCode", ""),
        ("station", "Kitchen"),
        ("preparationTime", ""),
        ("imageUrl1", ""),
        ("imageUrl2", ""),
        ("imageUrl3", ""),
        ("taxCategory", "Services"),
        ("taxType", "GST"),
        ("reviewStatus", "Not Reviewed"),
    ]
    for field, default in fields:
        if old.get(field, default) != new.get(field, default):
            return True
            
    # Check tax value (float comparison)
    old_tax = old.get("taxValue")
    new_tax = new.get("taxValue")
    if old_tax is None: old_tax = 5.0
    if new_tax is None: new_tax = 5.0
    try:
        if abs(float(old_tax) - float(new_tax)) > 0.0001:
            return True
    except (ValueError, TypeError):
        if old_tax != new_tax:
            return True
        
    # Check approved (bool comparison)
    if bool(old.get("approved")) != bool(new.get("approved")):
        return True
        
    # Check variations
    old_vars = old.get("variations", [])
    new_vars = new.get("variations", [])
    if len(old_vars) != len(new_vars):
        return True
    for ov, nv in zip(old_vars, new_vars):
        if ov.get("name") != nv.get("name"):
            return True
        o_price = ov.get("price") or ov.get("sellingPrice")
        n_price = nv.get("price") or nv.get("sellingPrice")
        try:
            if abs(float(o_price or 0) - float(n_price or 0)) > 0.0001:
                return True
        except (ValueError, TypeError):
            if o_price != n_price:
                return True
        o_lp = ov.get("listing_price") or ov.get("listingPrice")
        n_lp = nv.get("listing_price") or nv.get("listingPrice")
        try:
            if abs(float(o_lp or 0) - float(n_lp or 0)) > 0.0001:
                return True
        except (ValueError, TypeError):
            if o_lp != n_lp:
                return True
            
    # Check source metadata
    old_src = old.get("source", {})
    new_src = new.get("source", {})
    if old_src.get("initialProductName") != new_src.get("initialProductName") or \
       old_src.get("initialCategory") != new_src.get("initialCategory") or \
       old_src.get("fileName") != new_src.get("fileName") or \
       old_src.get("rawText") != new_src.get("rawText"):
        return True
        
    return False

@app.put("/api/drafts/{draft_id}")
def update_draft(draft_id: str, data: Dict[str, Any] = Body(...), user=Depends(get_current_user)):
    draft = get_draft(draft_id)
    if not draft:
        return JSONResponse(status_code=404, content={"error": "Draft not found"})
    if not require_draft_access(draft, user):
        return JSONResponse(status_code=403, content={"error": "You do not have access to this draft"})
        
    # Update business defaults or metadata if provided
    new_defaults = data.get("defaults", draft.get("defaults"))
    # Save
    from database import get_db_connection
    conn = get_db_connection()
    try:
        execute_query("UPDATE drafts SET defaults = ?, business_name = ? WHERE id = ?", 
                      (json.dumps(new_defaults), data.get("businessName", draft.get("businessName")), draft_id), 
                      commit=True, conn=conn)

        # Update item list by comparing changes
        incoming_items = data.get("items", [])
        existing_items_map = {it["id"]: it for it in draft.get("items", [])}
        updated_any = False
        
        for it in incoming_items:
            old_it = existing_items_map.get(it["id"])
            if not old_it or _is_item_changed(old_it, it):
                update_draft_item(draft_id, it["id"], it, user=user.get("email", "Human Reviewer"), conn=conn)
                updated_any = True
            
        if updated_any:
            log_audit(draft_id, "UPDATE_DRAFT", "Draft changes and review steps saved.", user=user.get("email", "Human Reviewer"), conn=conn)
            
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

    # Return the validated draft details directly so the client doesn't need to perform a separate GET fetch!
    updated_draft = get_draft(draft_id)
    items = updated_draft.get("items", [])
    validated = validate_menu(items, DEFAULT_TEMPLATE)
    updated_draft["items"] = validated
    return updated_draft

@app.delete("/api/drafts/{draft_id}")
def delete_draft_api(draft_id: str, user=Depends(get_current_user)):
    draft = get_draft(draft_id)
    if not draft:
        return JSONResponse(status_code=404, content={"error": "Draft not found"})
    if not require_draft_access(draft, user):
        return JSONResponse(status_code=403, content={"error": "You do not have access to this draft"})
    delete_draft(draft_id)
    return {"status": "success"}

@app.get("/api/drafts/{draft_id}/audit")
def get_audit_trail(draft_id: str, user=Depends(get_current_user)):
    draft = get_draft(draft_id)
    if not draft:
        return JSONResponse(status_code=404, content={"error": "Draft not found"})
    if not require_draft_access(draft, user):
        return JSONResponse(status_code=403, content={"error": "You do not have access to this draft"})
    return get_audit_logs(draft_id)

@app.post("/api/drafts/{draft_id}/generate-descriptions")
def generate_batch_descriptions(
    draft_id: str, 
    payload: Dict[str, Any] = Body(...),
    x_gemini_api_key: Optional[str] = Header(None, alias="X-Gemini-API-Key"),
    user=Depends(get_current_user)
):
    item_ids = payload.get("itemIds", [])
    if not item_ids:
        return {"status": "success", "updated": 0}
        
    draft = get_draft(draft_id)
    if not draft:
        return JSONResponse(status_code=404, content={"error": "Draft not found"})
    if not require_draft_access(draft, user):
        return JSONResponse(status_code=403, content={"error": "You do not have access to this draft"})
        
    overwrite = payload.get("overwrite", False)
    
    items_to_generate = []
    for item in draft.get("items", []):
        if item["id"] in item_ids:
            if not item.get("description") or overwrite:
                items_to_generate.append(item)
                
    if not items_to_generate:
        return {"status": "success", "updated": 0}
        
    gemini_key = x_gemini_api_key or os.getenv("GEMINI_API_KEY")
    descriptions_map = {}
    
    # 1. Try batch extraction via Gemini if key is active
    if gemini_key:
        chunk_size = 20
        for i in range(0, len(items_to_generate), chunk_size):
            chunk = items_to_generate[i:i+chunk_size]
            try:
                prompt = (
                    "Write a short, delicious, 1-sentence description (maximum 15 words) for each "
                    "of the following restaurant dishes. Entice the customer, focus on flavor. "
                    "Return a JSON object mapping each dish ID to its generated description as specified in the schema.\n\nDishes:\n"
                )
                for it in chunk:
                    prompt += f"- ID: {it['id']}, Name: {it['productName']}\n"
                    
                schema = {
                    "type": "object",
                    "properties": {
                        "descriptions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "description": {"type": "string"}
                                },
                                "required": ["id", "description"]
                            }
                        }
                    },
                    "required": ["descriptions"]
                }
                
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={gemini_key}"
                post_payload = {
                    "contents": [{
                        "parts": [{"text": prompt}]
                    }],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "responseSchema": schema,
                        "temperature": 0.5
                    }
                }
                
                from ollama_client import _post_to_gemini
                r = _post_to_gemini(url, post_payload, {"Content-Type": "application/json"}, timeout=60)
                r.raise_for_status()
                res_data = r.json()
                raw_text = res_data["candidates"][0]["content"]["parts"][0]["text"].strip()
                parsed = json.loads(raw_text)
                for desc_item in parsed.get("descriptions", []):
                    if desc_item.get("id") and desc_item.get("description"):
                        descriptions_map[desc_item["id"]] = desc_item["description"].replace('"', '')
            except Exception as e:
                print(f"Gemini batch description generation failed for chunk: {e}. Falling back to concurrent sequential generation.")
                
    # 2. Concurrently generate any remaining descriptions using ThreadPoolExecutor
    remaining_items = [it for it in items_to_generate if it["id"] not in descriptions_map]
    if remaining_items:
        from concurrent.futures import ThreadPoolExecutor
        
        def _get_single_desc(item):
            desc_prompt = f"Write a short, delicious, 1-sentence description (maximum 15 words) for the restaurant dish: '{item['productName']}'. Return ONLY the direct description sentence, do not add introductory phrases or quotes."
            try:
                if gemini_key:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={gemini_key}"
                    headers = {"Content-Type": "application/json"}
                    post_payload = {
                        "contents": [{
                            "parts": [{"text": desc_prompt}]
                        }]
                    }
                    r = requests.post(url, json=post_payload, headers=headers, timeout=20)
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
                    r = requests.post(url, json=ollama_payload, timeout=20)
                    r.raise_for_status()
                    ai_desc = r.json().get("response", "").strip().replace('"', '')
                return item["id"], ai_desc
            except Exception as ex:
                print(f"Failed description generation for {item['productName']}: {ex}")
                return item["id"], None
                
        # Limit workers to 5 to avoid overloading local Ollama or hitting Gemini rate limits
        with ThreadPoolExecutor(max_workers=5) as executor:
            thread_results = executor.map(_get_single_desc, remaining_items)
            for item_id, ai_desc in thread_results:
                if ai_desc:
                    descriptions_map[item_id] = ai_desc
                    
    # 3. Write updates database-wise in a single connection transaction
    updated_count = 0
    from database import get_db_connection
    conn = get_db_connection()
    try:
        for item in draft.get("items", []):
            if item["id"] in descriptions_map:
                item["description"] = descriptions_map[item["id"]]
                item["reviewStatus"] = "Review Required"
                update_draft_item(draft_id, item["id"], item, user="AI Description Generator", conn=conn)
                updated_count += 1
        conn.commit()
    except Exception as exc:
        conn.rollback()
        raise exc
    finally:
        conn.close()
        
    return {"status": "success", "updated": updated_count}

@app.post("/api/drafts/{draft_id}/approve")
def approve_and_export_menu(draft_id: str, payload: Dict[str, Any] = Body(...), user=Depends(get_current_user)):
    approved_agreement = payload.get("approvedAgreement", False)
    if not approved_agreement:
        return JSONResponse(status_code=400, content={"error": "Final approval agreement checkbox must be checked."})
        
    draft = get_draft(draft_id)
    if not draft:
        return JSONResponse(status_code=404, content={"error": "Draft not found"})
    if not require_draft_access(draft, user):
        return JSONResponse(status_code=403, content={"error": "You do not have access to this draft"})
        
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
        final_cat = it.get("categoryName")
        final_diet = it.get("dietaryTag")
        
        if initial_name and final_name:
            # Save product name spelling correction if modified
            if initial_name.strip() != final_name.strip():
                save_learned_correction("product_name", initial_name, final_name)
            
            # Save product category classification
            if final_cat:
                save_learned_correction("product_category", final_name, final_cat)
                
            # Save product dietary tag
            if final_diet:
                save_learned_correction("product_dietary", final_name, final_diet)
            
        # Legacy
        initial_cat = source_meta.get("initialCategory")
        if initial_cat and final_cat and initial_cat.strip() != final_cat.strip():
            save_learned_correction("category", initial_cat, final_cat)
            
    # Determine output format and webhook URL for POS companies
    output_format = "shopverse"
    webhook_url = None
    pos_company_id = draft.get("posCompanyId")
    if pos_company_id:
        from database import execute_query
        rows = execute_query("SELECT company_name, output_format, webhook_url FROM pos_companies WHERE id = ?", (pos_company_id,))
        if rows:
            company_name, output_format, webhook_url = rows[0]

    # Export using the dynamic POS exporter
    import re
    safe_business_name = re.sub(r'[\\/*?:"<>| ]', "_", draft['businessName'])
    
    if output_format in ["urbanpiper", "json"]:
        output_filename = f"{safe_business_name}_{output_format}_Menu.json"
        mime_type = "application/json"
    else:
        output_filename = f"{safe_business_name}_{output_format}_Menu.xlsx"
        mime_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        
    output_path = OUTPUT_DIR / output_filename
    export_pos_menu(output_format, validated_items, draft["businessName"], output_path)
    
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

    # Dispatch webhooks if registered
    if webhook_url:
        payload = {}
        if output_format in ["urbanpiper", "json"]:
            try:
                import json
                payload = json.loads(output_path.read_text(encoding="utf-8"))
            except Exception:
                payload = {"items": validated_items}
        else:
            # Excel
            payload = {
                "event": "menu.approved",
                "business_name": draft["businessName"],
                "draft_id": draft_id,
                "company_name": pos_company_id,
                "output_format": output_format,
                "output_file_name": output_filename,
                "file_content_base64": xlsx_b64,
                "items": [it for it in validated_items if it.get("approved")]
            }
        
        import threading
        threading.Thread(target=trigger_webhook_task, args=(webhook_url, payload), daemon=True).start()

    # Delete output files to avoid cloud hosting disk usage
    for p in [output_path, report_json_path, report_txt_path]:
        if p.exists():
            try:
                os.remove(p)
            except Exception:
                pass

    # Update Draft status to Approved
    execute_query("UPDATE drafts SET status = 'Approved' WHERE id = ?", (draft_id,), commit=True)
    
    log_audit(draft_id, "EXPORT_MENU", f"Menu approved and output file generated in format '{output_format}': {output_filename}")
    
    return {
        "status": "success",
        "outputFile": output_filename,
        "reviewReportJson": report_json_name,
        "reviewReportTxt": report_txt_name,
        "downloadOutputUrl": f"data:{mime_type};base64,{xlsx_b64}",
        "downloadReviewReportJsonUrl": f"data:application/json;base64,{json_b64}",
        "downloadReviewReportTxtUrl": f"data:text/plain;base64,{txt_b64}"
    }

@app.get("/api/diagnostics")
def get_diagnostics(admin=Depends(get_super_admin)):
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
def download_file(filename: str, user=Depends(get_current_user)):
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
