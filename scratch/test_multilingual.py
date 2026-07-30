import os
import sys
from pathlib import Path

# Add backend directory to path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from ollama_client import extract_from_text_with_gemini

def test_arabic_hindi_extraction():
    # Load .env file manually
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        with env_path.open("r", encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")

    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        print("GEMINI_API_KEY not found in environment. Skipping API test.")
        return

    # Multilingual Mock Menu Content
    arabic_hindi_menu = """
    قائمة الطعام - Menu
    === الشوربة والبداية (Soup & Starters) ===
    شوربة عدس   15.00 AED
    شوربة طماطم لذيذة مع قطع الخبز المحمص   18.00 AED
    
    === الأطباق الرئيسية (Main Course) ===
    برياني دجاج   35.00 AED
    chicken biryani with basmati rice and spices
    
    منسف لحم   55.00 AED
    original Jordanian Mansaf with meat and jameed
    
    البيتزا (Pizza) - Small: 20 AED, Large: 35 AED
    بيتزا مارغريتا
    
    === पनीर व्यंजन (Paneer Dishes) ===
    पनीर टिक्का मसाला   250 INR
    Spicy grilled cottage cheese in tomato gravy
    """

    print("Sending multilingual menu to Gemini API for test extraction...")
    try:
        extraction = extract_from_text_with_gemini(arabic_hindi_menu, api_key=gemini_key)
        
        print("\n=== EXTRACTION RESULTS ===")
        print(f"Currency detected: {extraction.currency}")
        print(f"Document Notes:")
        for note in extraction.document_notes:
            print(f" - {note}")
            
        print(f"\nExtracted Items ({len(extraction.items)}):")
        for item in extraction.items:
            print(f"\nProduct: {item.product_name}")
            print(f"  Category: {item.category}")
            print(f"  Description: {item.description}")
            print(f"  Dietary Tag: {item.dietary_tag}")
            print(f"  Confidence: {item.confidence}")
            print(f"  Source Text: {item.source_text}")
            print(f"  Variations:")
            for v in item.variations:
                print(f"    - Name: '{v.name}' | Price: {v.price} | Listing Price: {v.listing_price}")

    except Exception as e:
        print(f"Multilingual test failed: {e}")

if __name__ == "__main__":
    test_arabic_hindi_extraction()
