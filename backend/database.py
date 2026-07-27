import os
import sqlite3
import json
import uuid
import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

DB_FILE = Path(__file__).resolve().parent.parent / "outputs" / "shopverse_agent.db"
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    if DATABASE_URL and ("postgresql" in DATABASE_URL or "postgres" in DATABASE_URL):
        try:
            import psycopg2
            # Set a 5-second timeout on connections to prevent hanging on startup
            return psycopg2.connect(DATABASE_URL, connect_timeout=5)
        except Exception as e:
            print(f"[Error] Failed to connect to PostgreSQL: {e}. Falling back to SQLite.")
    
    try:
        DB_FILE.parent.mkdir(exist_ok=True)
    except Exception:
        pass
    return sqlite3.connect(str(DB_FILE))

def init_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
    except Exception as e:
        print(f"[Critical Error] Failed to establish initial database connection: {e}. Falling back to SQLite.")
        try:
            conn = sqlite3.connect("shopverse_agent.db")
            cursor = conn.cursor()
        except Exception as ex:
            print(f"[Fatal] Backup SQLite also failed: {ex}")
            return

    is_postgres = False
    try:
        # Check if the connection is postgres
        is_postgres = hasattr(conn, 'closed') and not hasattr(conn, 'row_factory')
    except Exception:
        pass

    def run_ddl(sql: str):
        try:
            if is_postgres:
                sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
                sql = sql.replace("REAL", "DOUBLE PRECISION")
            cursor.execute(sql)
        except Exception as ddl_err:
            print(f"[DDL Error] Failed executing statement: {sql[:100]}... Error: {ddl_err}")
            # Try to roll back to keep connection active
            try:
                conn.rollback()
            except Exception:
                pass
        
    # Drafts Table
    run_ddl("""
    CREATE TABLE IF NOT EXISTS drafts (
        id TEXT PRIMARY KEY,
        business_name TEXT,
        created_at TEXT,
        updated_at TEXT,
        defaults TEXT,  -- JSON string of default values
        files TEXT,     -- JSON list of uploaded file metadata
        status TEXT     -- 'Draft', 'Approved'
    )
    """)
    
    # Draft Items Table
    run_ddl("""
    CREATE TABLE IF NOT EXISTS draft_items (
        id TEXT PRIMARY KEY,
        draft_id TEXT,
        source TEXT,          -- JSON string of source info {fileName, page, rawText, confidence}
        category_name TEXT,
        product_name TEXT,
        variant_group_name TEXT,
        variations TEXT,       -- JSON list of variations [{name, sellingPrice, listingPrice, confidence}]
        description TEXT,
        dietary_tag TEXT,
        master_status TEXT,
        menu_status TEXT,
        stock_status TEXT,
        item_code TEXT,
        station TEXT,
        preparation_time TEXT,
        image_url_1 TEXT,
        image_url_2 TEXT,
        image_url_3 TEXT,
        tax_category TEXT,
        tax_type TEXT,
        tax_value REAL,
        review_status TEXT,   -- 'Not Reviewed', 'Review Required', 'Reviewed', 'Approved', 'Blocked'
        approved INTEGER,     -- 0 or 1
        FOREIGN KEY(draft_id) REFERENCES drafts(id) ON DELETE CASCADE
    )
    """)
    
    # Audit Log Table
    run_ddl("""
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        draft_id TEXT,
        timestamp TEXT,
        action TEXT,
        details TEXT,
        "user" TEXT,
        FOREIGN KEY(draft_id) REFERENCES drafts(id) ON DELETE CASCADE
    )
    """)
    
    # Learning Memory Table
    run_ddl("""
    CREATE TABLE IF NOT EXISTS learning_memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_type TEXT,
        original_val TEXT,
        corrected_val TEXT,
        frequency INTEGER DEFAULT 1,
        UNIQUE(entry_type, original_val)
    )
    """)
    
    conn.commit()
    conn.close()

def execute_query(query: str, params: tuple = (), commit: bool = False) -> List[tuple]:
    conn = get_db_connection()
    is_postgres = False
    try:
        is_postgres = "psycopg2" in str(type(conn))
    except Exception:
        pass
    
    if is_postgres:
        query = query.replace("?", "%s")
        
    cursor = conn.cursor()
    cursor.execute(query, params)
    result = []
    if not commit:
        result = cursor.fetchall()
    else:
        conn.commit()
    conn.close()
    return result

def create_draft(business_name: str, defaults: Dict[str, Any], files: List[Dict[str, Any]]) -> str:
    draft_id = uuid.uuid4().hex[:12]
    now = datetime.datetime.now().isoformat()
    execute_query(
        "INSERT INTO drafts (id, business_name, created_at, updated_at, defaults, files, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (draft_id, business_name, now, now, json.dumps(defaults), json.dumps(files), "Draft"),
        commit=True
    )
    log_audit(draft_id, "CREATE_DRAFT", f"Draft created for {business_name}")
    return draft_id

def log_audit(draft_id: str, action: str, details: str, user: str = "Human Reviewer"):
    now = datetime.datetime.now().isoformat()
    execute_query(
        'INSERT INTO audit_logs (draft_id, timestamp, action, details, "user") VALUES (?, ?, ?, ?, ?)',
        (draft_id, now, action, details, user),
        commit=True
    )

def add_draft_item(draft_id: str, item: Dict[str, Any]) -> str:
    item_id = item.get("id") or uuid.uuid4().hex[:12]
    execute_query("""
        INSERT INTO draft_items (
            id, draft_id, source, category_name, product_name, variant_group_name, variations, 
            description, dietary_tag, master_status, menu_status, stock_status, 
            item_code, station, preparation_time, image_url_1, image_url_2, image_url_3, 
            tax_category, tax_type, tax_value, review_status, approved
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        item_id,
        draft_id,
        json.dumps(item.get("source", {})),
        item.get("categoryName", "Uncategorized"),
        item.get("productName", ""),
        item.get("variantGroupName", ""),
        json.dumps(item.get("variations", [])),
        item.get("description", ""),
        item.get("dietaryTag", ""),
        item.get("masterStatus", "Active"),
        item.get("menuStatus", "Active"),
        item.get("stockStatus", "Active"),
        item.get("itemCode", ""),
        item.get("station", "Kitchen"),
        item.get("preparationTime", ""),
        item.get("imageUrl1", ""),
        item.get("imageUrl2", ""),
        item.get("imageUrl3", ""),
        item.get("taxCategory", "Services"),
        item.get("taxType", "GST"),
        item.get("taxValue", 5.0) if item.get("taxValue") is not None else 5.0,
        item.get("reviewStatus", "Not Reviewed"),
        1 if item.get("approved") else 0
    ), commit=True)
    return item_id

def update_draft_item(draft_id: str, item_id: str, item: Dict[str, Any], user: str = "Human Reviewer"):
    # Grab the old values for audit logging
    old_rows = execute_query("SELECT category_name, product_name, variations, review_status, approved FROM draft_items WHERE id = ? AND draft_id = ?", (item_id, draft_id))
    
    execute_query("""
        UPDATE draft_items SET
            category_name = ?, product_name = ?, variant_group_name = ?, variations = ?, 
            description = ?, dietary_tag = ?, master_status = ?, menu_status = ?, stock_status = ?, 
            item_code = ?, station = ?, preparation_time = ?, image_url_1 = ?, image_url_2 = ?, image_url_3 = ?, 
            tax_category = ?, tax_type = ?, tax_value = ?, review_status = ?, approved = ?
        WHERE id = ? AND draft_id = ?
    """, (
        item.get("categoryName", "Uncategorized"),
        item.get("productName", ""),
        item.get("variantGroupName", ""),
        json.dumps(item.get("variations", [])),
        item.get("description", ""),
        item.get("dietaryTag", ""),
        item.get("masterStatus", "Active"),
        item.get("menuStatus", "Active"),
        item.get("stockStatus", "Active"),
        item.get("itemCode", ""),
        item.get("station", "Kitchen"),
        item.get("preparationTime", ""),
        item.get("imageUrl1", ""),
        item.get("imageUrl2", ""),
        item.get("imageUrl3", ""),
        item.get("taxCategory", "Services"),
        item.get("taxType", "GST"),
        item.get("taxValue", 5.0) if item.get("taxValue") is not None else 5.0,
        item.get("reviewStatus", "Not Reviewed"),
        1 if item.get("approved") else 0,
        item_id,
        draft_id
    ), commit=True)
    
    # Audit log if changed
    if old_rows:
        old_cat, old_name, old_vars_str, old_rev_status, old_appr = old_rows[0]
        new_cat = item.get("categoryName", "")
        new_name = item.get("productName", "")
        new_rev_status = item.get("reviewStatus", "")
        new_appr = 1 if item.get("approved") else 0
        
        changes = []
        if old_cat != new_cat:
            changes.append(f"category: {old_cat} -> {new_cat}")
        if old_name != new_name:
            changes.append(f"name: {old_name} -> {new_name}")
        if old_rev_status != new_rev_status:
            changes.append(f"status: {old_rev_status} -> {new_rev_status}")
        if old_appr != new_appr:
            changes.append(f"approved: {bool(old_appr)} -> {bool(new_appr)}")
            
        if changes:
            log_audit(draft_id, "UPDATE_ITEM", f"Updated product '{new_name}' ({item_id}): " + ", ".join(changes), user)

def get_draft(draft_id: str) -> Optional[Dict[str, Any]]:
    rows = execute_query("SELECT id, business_name, created_at, updated_at, defaults, files, status FROM drafts WHERE id = ?", (draft_id,))
    if not rows:
        return None
    
    d_id, bus_name, created, updated, defaults, files, status = rows[0]
    
    # Get items
    item_rows = execute_query("""
        SELECT id, source, category_name, product_name, variant_group_name, variations, 
               description, dietary_tag, master_status, menu_status, stock_status, 
               item_code, station, preparation_time, image_url_1, image_url_2, image_url_3, 
               tax_category, tax_type, tax_value, review_status, approved
        FROM draft_items WHERE draft_id = ?
    """, (draft_id,))
    
    items = []
    for r in item_rows:
        items.append({
            "id": r[0],
            "source": json.loads(r[1] or "{}"),
            "categoryName": r[2],
            "productName": r[3],
            "variantGroupName": r[4],
            "variations": json.loads(r[5] or "[]"),
            "description": r[6],
            "dietaryTag": r[7],
            "masterStatus": r[8],
            "menuStatus": r[9],
            "stockStatus": r[10],
            "itemCode": r[11],
            "station": r[12],
            "preparationTime": r[13],
            "imageUrl1": r[14],
            "imageUrl2": r[15],
            "imageUrl3": r[16],
            "taxCategory": r[17],
            "taxType": r[18],
            "taxValue": r[19],
            "reviewStatus": r[20],
            "approved": bool(r[21])
        })
        
    return {
        "id": d_id,
        "businessName": bus_name,
        "createdAt": created,
        "updatedAt": updated,
        "defaults": json.loads(defaults or "{}"),
        "files": json.loads(files or "[]"),
        "status": status,
        "items": items
    }

def get_all_drafts() -> List[Dict[str, Any]]:
    rows = execute_query("SELECT id, business_name, created_at, updated_at, status FROM drafts ORDER BY updated_at DESC")
    return [{
        "id": r[0],
        "businessName": r[1],
        "createdAt": r[2],
        "updatedAt": r[3],
        "status": r[4]
    } for r in rows]

def delete_draft(draft_id: str):
    execute_query("DELETE FROM drafts WHERE id = ?", (draft_id,), commit=True)
    execute_query("DELETE FROM draft_items WHERE draft_id = ?", (draft_id,), commit=True)
    execute_query("DELETE FROM audit_logs WHERE draft_id = ?", (draft_id,), commit=True)

def get_audit_logs(draft_id: str) -> List[Dict[str, Any]]:
    rows = execute_query('SELECT timestamp, action, details, "user" FROM audit_logs WHERE draft_id = ? ORDER BY timestamp DESC', (draft_id,))
    return [{
        "timestamp": r[0],
        "action": r[1],
        "details": r[2],
        "user": r[3]
    } for r in rows]

def save_learned_correction(entry_type: str, original_val: str, corrected_val: str):
    if not original_val or not corrected_val:
        return
    original_val = original_val.strip()
    corrected_val = corrected_val.strip()
    if original_val == corrected_val or original_val.lower() == corrected_val.lower():
        return
        
    try:
        execute_query("""
            INSERT INTO learning_memory (entry_type, original_val, corrected_val, frequency)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(entry_type, original_val) DO UPDATE SET
                corrected_val = EXCLUDED.corrected_val,
                frequency = learning_memory.frequency + 1
        """, (entry_type, original_val, corrected_val), commit=True)
    except Exception as e:
        print(f"Error saving learning memory item: {e}")

def get_learned_corrections(entry_type: str) -> Dict[str, str]:
    corrections = {}
    try:
        rows = execute_query("SELECT original_val, corrected_val FROM learning_memory WHERE entry_type = ?", (entry_type,))
        for orig, corr in rows:
            corrections[orig.lower().strip()] = corr
    except Exception as e:
        print(f"Error reading learning memory: {e}")
    return corrections
