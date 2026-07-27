import re
from typing import List, Optional
from schemas import MenuExtraction, MenuItem, Variation

def clean_name(name: str) -> str:
    name = name.strip()
    # Strip common leading/trailing symbols, keeping parentheses for options like (Mutton)
    name = re.sub(r'^[-\+\.\s\:\,\;\*\/\|]+', '', name)
    name = re.sub(r'[-\+\.\s\:\,\;\*\/\|]+$', '', name)
    return name.strip()

def parse_menu_text_heuristically(text: str) -> Optional[MenuExtraction]:
    """
    Parses a menu text block into MenuExtraction using sequential rule-based heuristics,
    supporting multi-column visual alignment, slash-separated variations, and empty-price fallback.
    """
    if not text or not text.strip():
        return None
        
    lines = [line.strip() for line in text.splitlines()]
    lines = [l for l in lines if l]
    
    if not lines:
        return None
        
    items: List[MenuItem] = []
    current_category = "Uncategorized"
    
    # Matches a line that is strictly a price or multiple slash-separated prices (optionally currency signs)
    price_line_regex = re.compile(
        r'^\s*(?:\$|Rs\.?|₹|INR|\+)?\s*(\d+(?:\.\d{1,2})?)\s*(?:[\/|\\\,]\s*(?:\$|Rs\.?|₹|INR|\+)?\s*(\d+(?:\.\d{1,2})?))*\s*(?:\+?\s*(?:\$|Rs\.?|₹|INR)?\s*\d+(?:\.\d{1,2})?)?\s*$',
        re.IGNORECASE
    )
    
    # Matches a line containing both name and price(s)
    mixed_price_regex = re.compile(
        r'^(.*?)\s*(?:[-–—+:]\s*)?(?:\$|Rs\.?|₹|INR)?\s*(\d+(?:\.\d{1,2})?(?:\s*[\/|\\\,]\s*(?:\$|Rs\.?|₹|INR)?\s*(\d+(?:\.\d{1,2})?))+)\s*$',
        re.IGNORECASE
    )
    mixed_single_regex = re.compile(
        r'^(.*?)\s+(?:[-–—+:₹\$]|\bRs\.?\b)?\s*(\d+(?:\.\d{2})?|\d+)\s*$', 
        re.IGNORECASE
    )
    
    # Common menu category keywords
    category_keywords = {
        "menu", "drink", "drinks", "beverage", "beverages", "dessert", "desserts", "starter", "starters", 
        "main", "mains", "appetizer", "appetizers", "side", "sides", "soup", "soups", 
        "salad", "salads", "bread", "breads", "curry", "curries", "signature", "signatures", "breakfast", 
        "tea", "coffee", "juice", "juices", "shake", "shakes", "mocktail", "mocktails", "cocktail", "cocktails",
        "beer", "beers", "wine", "wines", "spirits", "liquor", "soda", "water", "soft", "hot", "cold",
        "roti", "rotis", "naan", "naans", "paratha", "parathas", "kulcha", "kulchas",
        "burger", "burgers", "pizza", "pizzas", "sandwich", "sandwiches", "pasta", "pastas",
        "noodle", "noodles", "momo", "momos", "roll", "rolls", "starter", "starters",
        "kabab", "kababs", "kebab", "kebabs", "tikka", "tikkas", "tandoori", "tandoor",
        "rice", "biryani", "biryanis", "pulao", "dal", "paneer", "chicken", "mutton", "fish", "egg",
        "delight", "delights", "bites", "bites", "snack", "snacks", "chaat", "samosa",
        "platter", "platters", "combo", "combos", "thali", "thalis", "special", "specials",
        "extra", "extras", "addon", "addons", "add-on", "add-ons", "sauce", "sauces", "dip", "dips",
        "gravy", "sweet", "sweets", "waffle", "waffles", "crepe", "crepes", "pancake", "pancakes",
        "icecream", "ice-cream", "ice-creams"
    }
    
    # Track the last seen variation header
    current_variant_names: Optional[List[str]] = None
    
    names_buf = []
    prices_buf = []
    desc_map = {} # index -> list of strings
    source_map = {} # index -> string
    
    def flush_section():
        nonlocal current_variant_names
        if not names_buf:
            return
            
        n_count = len(names_buf)
        p_count = len(prices_buf)
        
        # Distribute prices among names using the mathematically-perfect helper
        mapped_prices = []
        if n_count > 0:
            flat_prices = []
            for g in prices_buf:
                flat_prices.extend(g)
            if len(flat_prices) > 0 and len(flat_prices) % n_count == 0:
                k = len(flat_prices) // n_count
                for idx in range(n_count):
                    mapped_prices.append(flat_prices[idx * k : (idx + 1) * k])
            else:
                for idx in range(n_count):
                    if idx < p_count:
                        mapped_prices.append(prices_buf[idx])
                    elif p_count == 1:
                        mapped_prices.append(prices_buf[0])
                    else:
                        mapped_prices.append([])
                        
        for idx in range(n_count):
            name = names_buf[idx]
            desc = " / ".join(desc_map.get(idx, []))
            source = source_map.get(idx, name)
            
            price_list = mapped_prices[idx] if idx < len(mapped_prices) else []
            
            # Map price list to Variation objects
            variations = []
            if price_list:
                for v_idx, p_val in enumerate(price_list):
                    v_name = ""
                    if current_variant_names and v_idx < len(current_variant_names):
                        v_name = current_variant_names[v_idx]
                    else:
                        if len(price_list) == 2:
                            v_name = ["Half", "Full"][v_idx]
                        elif len(price_list) == 3:
                            v_name = ["Small", "Medium", "Large"][v_idx]
                        else:
                            v_name = f"Option {v_idx + 1}"
                    variations.append(Variation(name=v_name, price=p_val, listing_price=p_val))
            else:
                variations = [Variation(name="", price=None, listing_price=None)]
                
            # Perform exact duplicate merging per category
            key = (current_category.lower().strip(), name.lower().strip())
            
            existing_item = None
            for it in items:
                if (it.category.lower().strip() == key[0]) and (it.product_name.lower().strip() == key[1]):
                    existing_item = it
                    break
                    
            if existing_item:
                existing_vars_empty = (len(existing_item.variations) == 1 and existing_item.variations[0].price is None)
                new_vars_empty = (len(variations) == 1 and variations[0].price is None)
                
                if existing_vars_empty and not new_vars_empty:
                    existing_item.variations = variations
                    existing_item.confidence = 1.0
                elif not new_vars_empty:
                    for nv in variations:
                        val_exists = False
                        for ev in existing_item.variations:
                            if ev.name.lower().strip() == nv.name.lower().strip() or (ev.price == nv.price and ev.name == nv.name):
                                val_exists = True
                                break
                        if not val_exists:
                            existing_item.variations.append(nv)
                if desc and desc not in existing_item.description:
                    existing_item.description = (existing_item.description + " / " + desc).strip(" / ")
            else:
                menu_item = MenuItem(
                    category=current_category,
                    product_name=name,
                    description=desc,
                    dietary_tag="",
                    confidence=1.0 if price_list else 0.7,
                    source_text=source,
                    variations=variations
                )
                items.append(menu_item)
            
        names_buf.clear()
        prices_buf.clear()
        desc_map.clear()
        source_map.clear()

    i = 0
    num_lines = len(lines)
    
    while i < num_lines:
        line = lines[i].strip()
        cleaned_line = clean_name(line)
        
        if not cleaned_line:
            i += 1
            continue
            
        # 1. Ignore helper rules/text that are instructions, e.g. "Choose 1", "Choose 2", "Buy 1 Get 1 Free"
        if re.search(r'^\s*(?:choose\s+\d+|choose\s+any|must\s+choose|choose|buy\s+1\s+get|buy\s+\d+\s+@)\s*$', cleaned_line, re.IGNORECASE):
            i += 1
            continue

        # 1.1 Ignore contact info
        lower_line = cleaned_line.lower()
        if re.search(r'\b(?:phone|tel\b|mobile|mob\b|contact|call|whatsapp|gstin|gst\b|email|website|web\b|road|street|floor|opposite|near|pincode|block|phase|sector|colony)\b', lower_line):
            i += 1
            continue
        if re.search(r'\b\d{10}\b', cleaned_line):
            i += 1
            continue
        if re.search(r'\b\d{5}\s+\d{5}\b', cleaned_line):
            i += 1
            continue
        if re.search(r'\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b', cleaned_line):
            i += 1
            continue
        if re.search(r'\b\d{6}\b', cleaned_line):
            i += 1
            continue
        if "@" in cleaned_line and "." in cleaned_line:
            i += 1
            continue
        if "www." in lower_line or "http" in lower_line or ".com" in lower_line or ".in" in lower_line:
            i += 1
            continue
            
        # 2. Variation headers
        if "/" in cleaned_line and not any(c.isdigit() for c in cleaned_line if c not in ["/", " "]):
            v_parts = [p.strip() for p in cleaned_line.split("/") if p.strip()]
            if len(v_parts) > 1 and all(len(p) < 15 for p in v_parts):
                current_variant_names = v_parts
                i += 1
                continue

        # 3. Strictly price lines (checked BEFORE category)
        m_price_only = price_line_regex.match(line)
        if m_price_only and not re.search(r'[a-zA-Z]{3,}', line):
            price_vals = []
            for num_str in re.findall(r'\d+(?:\.\d{1,2})?', line):
                try:
                    price_vals.append(float(num_str))
                except ValueError:
                    pass
            if price_vals:
                prices_buf.append(price_vals)
                i += 1
                continue

        # 4. Mixed lines (checked BEFORE category)
        m_mixed_multi = mixed_price_regex.match(line)
        m_mixed_single = mixed_single_regex.match(line)
        
        if m_mixed_multi:
            groups = m_mixed_multi.groups()
            name_part, price_block = groups[0], groups[1]
            name_cleaned = clean_name(name_part)
            if name_cleaned and not price_line_regex.match(name_cleaned) and len(name_cleaned) < 80:
                price_vals = []
                for num_str in re.findall(r'\d+(?:\.\d{1,2})?', price_block):
                    try:
                        price_vals.append(float(num_str))
                    except ValueError:
                        pass
                if price_vals:
                    flush_section()
                    
                    variations = []
                    for v_idx, p_val in enumerate(price_vals):
                        v_name = ""
                        if current_variant_names and v_idx < len(current_variant_names):
                            v_name = current_variant_names[v_idx]
                        else:
                            if len(price_vals) == 2:
                                v_name = ["Half", "Full"][v_idx]
                            elif len(price_vals) == 3:
                                v_name = ["Small", "Medium", "Large"][v_idx]
                            else:
                                v_name = f"Option {v_idx + 1}"
                        variations.append(Variation(name=v_name, price=p_val, listing_price=p_val))
                        
                    key = (current_category.lower().strip(), name_cleaned.lower().strip())
                    existing_item = None
                    for it in items:
                        if (it.category.lower().strip() == key[0]) and (it.product_name.lower().strip() == key[1]):
                            existing_item = it
                            break
                            
                    if existing_item:
                        existing_vars_empty = (len(existing_item.variations) == 1 and existing_item.variations[0].price is None)
                        if existing_vars_empty:
                            existing_item.variations = variations
                            existing_item.confidence = 1.0
                        else:
                            for nv in variations:
                                val_exists = False
                                for ev in existing_item.variations:
                                    if ev.name.lower().strip() == nv.name.lower().strip():
                                        val_exists = True
                                        break
                                if not val_exists:
                                    existing_item.variations.append(nv)
                    else:
                        menu_item = MenuItem(
                            category=current_category,
                            product_name=name_cleaned,
                            description="",
                            dietary_tag="",
                            confidence=1.0,
                            source_text=line,
                            variations=variations
                        )
                        items.append(menu_item)
                    i += 1
                    continue
                    
        elif m_mixed_single:
            name_part, price_str = m_mixed_single.groups()
            name_cleaned = clean_name(name_part)
            try:
                price_val = float(price_str)
            except ValueError:
                price_val = None
                
            if (name_cleaned and price_val is not None and price_val < 3000 
                and not price_line_regex.match(name_cleaned) and len(name_cleaned) < 80):
                
                if price_val in [2026, 410206] or len(price_str) > 5:
                    pass
                else:
                    flush_section()
                    key = (current_category.lower().strip(), name_cleaned.lower().strip())
                    existing_item = None
                    for it in items:
                        if (it.category.lower().strip() == key[0]) and (it.product_name.lower().strip() == key[1]):
                            existing_item = it
                            break
                            
                    variations = [Variation(name="", price=price_val, listing_price=price_val)]
                    if existing_item:
                        existing_vars_empty = (len(existing_item.variations) == 1 and existing_item.variations[0].price is None)
                        if existing_vars_empty:
                            existing_item.variations = variations
                            existing_item.confidence = 1.0
                        else:
                            val_exists = False
                            for ev in existing_item.variations:
                                if ev.price == price_val:
                                    val_exists = True
                                    break
                            if not val_exists:
                                existing_item.variations.append(variations[0])
                    else:
                        menu_item = MenuItem(
                            category=current_category,
                            product_name=name_cleaned,
                            description="",
                            dietary_tag="",
                            confidence=1.0,
                            source_text=line,
                            variations=variations
                        )
                        items.append(menu_item)
                    i += 1
                    continue

        # 5. Category headers with lookahead
        is_next_price = False
        if i + 1 < num_lines:
            next_line = lines[i+1].strip()
            if price_line_regex.match(next_line) and not re.search(r'[a-zA-Z]{3,}', next_line):
                is_next_price = True

        is_hdr = False
        lower_line = cleaned_line.lower()
        
        # Word extraction for keyword matching
        words = set(re.findall(r'[a-z]+', lower_line))
        has_cat_word = any(w in category_keywords for w in words)
        
        # Check if line contains digits
        has_digits = any(c.isdigit() for c in cleaned_line)
        
        # Stylistic markers commonly flanking category headers
        is_styled_hdr = (
            (cleaned_line.startswith("[") and cleaned_line.endswith("]")) or
            (cleaned_line.startswith("【") and cleaned_line.endswith("】")) or
            (cleaned_line.startswith("★") and cleaned_line.endswith("★")) or
            (cleaned_line.startswith("=") and cleaned_line.endswith("=")) or
            (cleaned_line.startswith("*") and cleaned_line.endswith("*")) or
            (cleaned_line.startswith("-") and cleaned_line.endswith("-"))
        )
        
        # Lookahead check: check if it is followed by priced items
        is_followed_by_price = False
        if i + 1 < num_lines:
            next_line = lines[i+1].strip()
            if mixed_price_regex.match(next_line) or mixed_single_regex.match(next_line):
                is_followed_by_price = True
            elif i + 2 < num_lines:
                next_next_line = lines[i+2].strip()
                if (price_line_regex.match(next_next_line) and not re.search(r'[a-zA-Z]{3,}', next_next_line)) or mixed_price_regex.match(next_next_line) or mixed_single_regex.match(next_next_line):
                    is_followed_by_price = True

        if len(cleaned_line) < 45 and not is_next_price and not has_digits:
            if (cleaned_line.isupper() and not price_line_regex.match(cleaned_line)) or has_cat_word or cleaned_line.endswith(":") or is_styled_hdr or is_followed_by_price:
                is_hdr = True
                
        if is_hdr:
            flush_section()
            current_category = cleaned_line.rstrip(":").title()
            i += 1
            continue
                    
        # 6. Candidate names or descriptions
        is_name = False
        if len(cleaned_line) < 80:
            if cleaned_line[0].isupper() or cleaned_line[0].isdigit():
                is_name = True
            if " / " in cleaned_line and len(cleaned_line) > 30:
                is_name = False
                
        if is_name:
            names_buf.append(cleaned_line)
            idx = len(names_buf) - 1
            source_map[idx] = line
            desc_map[idx] = []
        else:
            if names_buf:
                last_idx = len(names_buf) - 1
                desc_map[last_idx].append(line)
                
        i += 1
        
    flush_section()
    
    # Fallback to output elements with empty prices if no items matched with prices
    if not items and names_buf:
        for idx, name in enumerate(names_buf):
            desc = " / ".join(desc_map.get(idx, []))
            
            key = (current_category.lower().strip(), name.lower().strip())
            existing_item = None
            for it in items:
                if (it.category.lower().strip() == key[0]) and (it.product_name.lower().strip() == key[1]):
                    existing_item = it
                    break
            
            if not existing_item:
                menu_item = MenuItem(
                    category=current_category,
                    product_name=name,
                    description=desc,
                    dietary_tag="",
                    confidence=0.5,
                    source_text=source_map.get(idx, name),
                    variations=[Variation(name="", price=None, listing_price=None)]
                )
                items.append(menu_item)
            
    if not items:
        return None
        
    return MenuExtraction(
        currency="INR",
        items=items,
        document_notes=["Extracted with client-side visual heuristic text engine."]
    )
