"""Menu rendering (Task 4 replaces this stub)."""
TEMPLATES = [
    {"id": "dark-classic", "name_en": "Dark classic", "name_ar": "كلاسيكي داكن", "version": "1",
     "aspects": ["16:9", "9:16"], "kinds": ["board", "category", "promo"]},
    {"id": "cream-cafe", "name_en": "Cream café", "name_ar": "كافيه كريمي", "version": "1",
     "aspects": ["16:9", "9:16"], "kinds": ["board", "category", "promo"]},
    {"id": "luxe", "name_en": "Luxe", "name_ar": "فاخر", "version": "1",
     "aspects": ["16:9", "9:16"], "kinds": ["board", "category", "promo"]},
]


def list_templates() -> list[dict]:
    return TEMPLATES
