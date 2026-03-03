from fastapi import APIRouter, Request

router = APIRouter(prefix="/products")

MOCK_PRODUCTS = [
    {
        "id": "p-001",
        "name": "Lenovo IdeaPad 3 (Ryzen 5)",
        "price": 24500,
        "location": "Sampaloc, Manila",
        "image": "https://picsum.photos/seed/studket_laptop_1/640/640",
        "description": "Used for one semester. Includes charger and laptop sleeve.",
    },
    {
        "id": "p-002",
        "name": "TI-84 Plus Graphing Calculator",
        "price": 3200,
        "location": "Quezon City",
        "image": "https://picsum.photos/seed/studket_calc_1/640/640",
        "description": "Great condition, all buttons working. Ideal for engineering math.",
    },
    {
        "id": "p-003",
        "name": "IKEA Study Desk 120cm",
        "price": 3800,
        "location": "Makati City",
        "image": "https://picsum.photos/seed/studket_desk_1/640/640",
        "description": "Minimal scratches. Easy to disassemble for pickup.",
    },
    {
        "id": "p-004",
        "name": "Ergonomic Mesh Office Chair",
        "price": 2950,
        "location": "Pasig City",
        "image": "https://picsum.photos/seed/studket_chair_1/640/640",
        "description": "Breathable mesh back with adjustable height and lumbar support.",
    },
    {
        "id": "p-005",
        "name": "Uniqlo BlockTech Jacket (M)",
        "price": 1200,
        "location": "Taguig City",
        "image": "https://picsum.photos/seed/studket_fashion_1/640/640",
        "description": "Lightweight rain jacket. No tears, rarely worn.",
    },
    {
        "id": "p-006",
        "name": "Yonex Badminton Racket Set",
        "price": 1800,
        "location": "Mandaluyong City",
        "image": "https://picsum.photos/seed/studket_sports_1/640/640",
        "description": "Includes two rackets, one shuttle tube, and carrying case.",
    },
    {
        "id": "p-007",
        "name": "The Pragmatic Programmer (2nd Ed.)",
        "price": 850,
        "location": "Diliman, Quezon City",
        "image": "https://picsum.photos/seed/studket_book_1/640/640",
        "description": "Original paperback, clean pages, no highlights.",
    },
    {
        "id": "p-008",
        "name": "Noise-Cancelling Headphones",
        "price": 4100,
        "location": "Manila City",
        "image": "https://picsum.photos/seed/studket_audio_1/640/640",
        "description": "Up to 25 hours battery life. Includes aux cable and pouch.",
    },
    {
        "id": "p-009",
        "name": "Ring Light 12-inch with Tripod",
        "price": 950,
        "location": "Pasay City",
        "image": "https://picsum.photos/seed/studket_beauty_1/640/640",
        "description": "Adjustable brightness and color temperature. USB powered.",
    },
    {
        "id": "p-010",
        "name": "Samsung Galaxy Tab A8",
        "price": 7800,
        "location": "Caloocan City",
        "image": "https://picsum.photos/seed/studket_tablet_1/640/640",
        "description": "64GB model, includes case and tempered glass.",
    },
    {
        "id": "p-011",
        "name": "KitchenAid Blender (1.5L)",
        "price": 2300,
        "location": "San Juan City",
        "image": "https://picsum.photos/seed/studket_home_1/640/640",
        "description": "Perfect for smoothies and sauces. Blade recently replaced.",
    },
    {
        "id": "p-012",
        "name": "Basketball Shoes Size 9",
        "price": 2600,
        "location": "Paranaque City",
        "image": "https://picsum.photos/seed/studket_shoes_1/640/640",
        "description": "Good grip and cushioning. Used for indoor courts only.",
    },
]


@router.get("/")
async def products_list(request: Request):
    return MOCK_PRODUCTS
