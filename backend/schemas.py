from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator

AllowedDietaryTag = Literal["Veg", "Non-Veg", "Egg", ""]


class Variation(BaseModel):
    name: str = Field(default="", description="Variation name like Small, Medium, Large, Half, Full. Empty for single-price items.")
    price: Optional[float] = Field(default=None, description="Selling price only. No currency symbol.")
    listing_price: Optional[float] = Field(default=None, description="Listing/MRP price only. Same as price if no separate MRP is visible.")


class AddOnGroup(BaseModel):
    category: str = ""
    products: List[str] = Field(default_factory=list)
    rule: Literal["Select Only One", "Select Multiple", "Select Custom", ""] = ""
    required: Literal["Yes", "No", ""] = ""
    min_qty: Optional[int] = None
    max_qty: Optional[int] = None
    chargeable_count: Optional[int] = None


class MenuItem(BaseModel):
    category: str = Field(default="Uncategorized")
    product_name: str
    description: str = ""
    dietary_tag: AllowedDietaryTag = ""
    item_type: str = ""
    tax_category: Literal["Goods", "Services", ""] = "Services"
    tax_type: Literal["GST", "VAT", ""] = "GST"
    tax_value: Optional[float] = 5
    item_code: str = ""
    station: str = "Kitchen"
    preparation_time: str = ""
    image_url_1: str = ""
    variations: List[Variation] = Field(default_factory=list)
    add_on_groups: List[AddOnGroup] = Field(default_factory=list)
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    source_text: str = Field(default="", description="Short visible source line/snippet used for this item.")

    @field_validator("category", mode="before")
    @classmethod
    def clean_category(cls, v: Any) -> str:
        if not v or not str(v).strip():
            return "Uncategorized"
        return str(v).strip()

    @field_validator("product_name", mode="before")
    @classmethod
    def clean_name(cls, v: Any) -> str:
        return str(v or "").strip()

    @field_validator("variations", mode="after")
    @classmethod
    def ensure_variation(cls, v: List[Variation]) -> List[Variation]:
        if not v:
            return [Variation(name="", price=None, listing_price=None)]
        return v


class MenuExtraction(BaseModel):
    currency: str = "INR"
    items: List[MenuItem] = Field(default_factory=list)
    document_notes: List[str] = Field(default_factory=list)


MENU_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "currency": {"type": "string"},
        "document_notes": {"type": "array", "items": {"type": "string"}},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "product_name": {"type": "string"},
                    "description": {"type": "string"},
                    "dietary_tag": {"type": "string", "enum": ["Veg", "Non-Veg", "Egg", ""]},
                    "confidence": {"type": ["number", "null"]},
                    "source_text": {"type": "string"},
                    "variations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "price": {"type": ["number", "null"]},
                                "listing_price": {"type": ["number", "null"]},
                            },
                            "required": ["name", "price"],
                        },
                    },
                },
                "required": [
                    "category", "product_name", "description", "dietary_tag", "variations"
                ],
            },
        },
    },
    "required": ["currency", "items"],
}
