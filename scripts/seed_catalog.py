"""
Dev helper: seed every table in the schema with sample data, bypassing the
API - brands, categories, products, a manual, a promotion, one admin user,
one staff user, one plain customer and one VIP (price-visible) customer.

Usage:
    python -m scripts.seed_catalog
    (run from the project root, with the virtualenv activated and
     DATABASE_URL pointing at your Postgres instance)

Safe to re-run: every row is matched by its natural unique key (name,
email, ...) and skipped if it already exists, so this won't create
duplicates on a second run.

Seeded login credentials (dev only - never use these in production):
    admin@store.dev        / Admin@12345       (all 4 permissions)
    staff@store.dev        / Staff@12345        (price_listing + product_management)
    customer@store.dev     / Customer@12345     (access_permission=False, sees no prices)
    vip.customer@store.dev / VipCustomer@12345  (access_permission=True, sees prices)

    NOTE: ".local" was originally used here but is rejected by pydantic's
    EmailStr (email-validator treats it as a reserved special-use TLD, see
    RFC 6762) - that only shows up once you try to actually log in with a
    seeded account, not in the test suite (which uses .example.com
    addresses), so ".dev" is used instead.
"""
from datetime import datetime, timedelta, timezone

from app.database import SessionLocal
from app.core.security import hash_password
from app.models import Brand, Category, Customer, Manual, Product, Promotion, User

BRANDS = ["Woodpecker", "YouJoy"]

# Every entry below carries a "brand_id" that is a 1-based position in
# BRANDS (1 = Woodpecker, 2 = YouJoy), not a real database id. main()
# resolves it positionally, the same way brand_by_name resolves brand_name
# elsewhere.
PRODUCTS = [
  {"product_name": "LX16-PLUS", "description": "Multi-purpose dental diode laser for soft tissue, perio, endo, implant, surgery, and pain therapy.", "category": "Diode Laser System", "badge": "Item", "price": 3200, "new_price": 3480, "brand_id": 1},
  {"product_name": "KP SmileScan", "description": "High-precision intraoral scanner with full-arch imaging, AI-enhanced accuracy, and seamless lab integration.", "category": "Intraoral Scanner", "badge": "Item", "price": 3372.5, "new_price": 3550, "brand_id": 1},
  {"product_name": "US-II LED", "description": "Multi-function piezo ultrasonic scaler for scaling, perio, endo, implant maintenance, and surgery.", "category": "Piezo Bone Surgery", "badge": "Item", "price": 1425, "new_price": 1500, "brand_id": 1},
  {"product_name": "Woodpecker Implanter", "description": "Precision implant motor for implant placement, surgery, and torque control.", "category": "Dental Implant Device", "badge": "Item", "price": 1235, "new_price": 1300, "brand_id": 1},
  {"product_name": "Surgery-X", "description": "Piezoelectric surgical unit for bone surgery, soft tissue surgery, and implant placement with adjustable power and irrigation.", "category": "Piezo Bone Surgery", "badge": "Item", "price": 2470, "new_price": 2600, "brand_id": 1},
  {"product_name": "3 in 1 Surgic Star", "description": "3-in-1 premium implant and surgical motor with smart presets, touchscreen control, and stable irrigation.", "category": "Dental Implant Device", "badge": "Item", "price": 3705, "new_price": 3900, "brand_id": 1},
  {"product_name": "ES5", "description": "High-performance multi-purpose electric motor for implant, surgical, and endodontic procedures, up to 120,000 rpm.", "category": "Dental Electric Motor", "badge": "Item", "price": 1520, "new_price": 1600, "brand_id": 1},
  {"product_name": "PT-A", "description": "Ultrasonic scaler and air polisher combo with heated water delivery and hands-free touchscreen control.", "category": "Air Polisher System", "badge": "Item", "price": 2470, "new_price": 2600, "brand_id": 1},

  {"product_name": "Endo 3", "description": "High-performance rotary endodontic system for root canal treatment, cleaning, and shaping.", "category": "Endo Activator", "badge": "Item", "price": 171, "new_price": 180, "brand_id": 1},
  {"product_name": "Endo Radar Pro", "description": "Top-tier apex locator with advanced tracking technology for precise root canal measurement.", "category": "Endo Motor", "badge": "Item", "price": 456, "new_price": 480, "brand_id": 1},
  {"product_name": "FI-G", "description": "Gutta percha obturation device with exceptional sealing performance and easy handling.", "category": "Gutta-purcha Obteration System", "badge": "Item", "price": 289.8, "new_price": 305, "brand_id": 1},
  {"product_name": "Motopex", "description": "Compact dental micromotor for precision drilling, root canal treatment, implantology, and crown prep.", "category": "Endo Motor", "badge": "Item", "price": 399, "new_price": 420, "brand_id": 1},
  {"product_name": "FI-P", "description": "Gutta percha obturation device with superior adaptability and long-term reliability.", "category": "Gutta-purcha Obteration System", "badge": "Item", "price": 180.5, "new_price": 190, "brand_id": 1},
  {"product_name": "E-COM+", "description": "Powerful cordless endomotor for canal cleaning and shaping with precise torque control.", "category": "Endo Motor", "badge": "Item", "price": 275.5, "new_price": 290, "brand_id": 1},
  {"product_name": "R1-Plus", "description": "Precision endodontic measuring ruler for gutta percha cutting and working length measurement.", "category": "Gutta-purcha Obteration System", "badge": "Item", "price": 25, "new_price": 29, "brand_id": 1},
  {"product_name": "Endo Pace", "description": "Cordless brushless endomotor with apex locator integration for safe, efficient canal treatment.", "category": "Endo Motor", "badge": "Item", "price": 256.5, "new_price": 270, "brand_id": 1},

  {"product_name": "LED H", "description": "Curing light, light intensity 1000-1800 mW/cm2.", "category": "Curing Light", "badge": "Item", "price": 104.5, "new_price": 110, "brand_id": 1},
  {"product_name": "LED F", "description": "Curing light, light intensity 1600-1800 mW/cm2.", "category": "Curing Light", "badge": "Item", "price": 128.3, "new_price": 135, "brand_id": 1},
  {"product_name": "LED G", "description": "Curing light, light intensity 1000-1200 mW/cm2.", "category": "Curing Light", "badge": "Item", "price": 66.5, "new_price": 70, "brand_id": 1},
  {"product_name": "LED B", "description": "Curing light, light intensity 1000-1700 mW/cm2.", "category": "Curing Light", "badge": "Item", "price": 54.4, "new_price": 68, "brand_id": 1},
  {"product_name": "iLED II", "description": "Curing light, light intensity 2700-3000 mW/cm2.", "category": "Curing Light", "badge": "Item", "price": 152, "new_price": 160, "brand_id": 1},
  {"product_name": "U-Light", "description": "Lightweight curing light with 3 modes and 360° rotation for consistent polymerization.", "category": "Curing Light", "badge": "Item", "price": 171, "new_price": 180, "brand_id": 1},
  {"product_name": "iLED MAX", "description": "Curing light, light intensity 2300-2500 mW/cm2.", "category": "Curing Light", "badge": "Item", "price": 116, "new_price": 120, "brand_id": 1},
  {"product_name": "O-Star", "description": "High-performance curing light with 7 preset modes and 360° use for deep polymerization.", "category": "Curing Light", "badge": "Item", "price": 342, "new_price": 360, "brand_id": 1},
  {"product_name": "iLED Plus", "description": "Curing light, light intensity 2300-2500 mW/cm2.", "category": "Curing Light", "badge": "Item", "price": 95, "new_price": 100, "brand_id": 1},
  {"product_name": "X-Star", "description": "High-performance curing light with 8 preset modes and 360° use for deep polymerization.", "category": "Curing Light", "badge": "Item", "price": 494, "new_price": 520, "brand_id": 1},

  {"product_name": "U600 LED", "description": "Advanced ultrasonic scaler with LED handpiece illumination and 3 modes (scaling, perio, endo).", "category": "Ultrasonic Scaler", "badge": "Item", "price": 332.5, "new_price": 350, "brand_id": 1},
  {"product_name": "UDS-N3 LED", "description": "Built-in ultrasonic scaler with LED illumination and detachable autoclavable handpiece.", "category": "Ultrasonic Scaler", "badge": "Item", "price": 115, "new_price": None, "brand_id": 1},
  {"product_name": "UDS-T LED", "description": "Multi-function ultrasonic scaler for scaling, perio, and endo with LED handpiece.", "category": "Ultrasonic Scaler", "badge": "Item", "price": 247, "new_price": 260, "brand_id": 1},
  {"product_name": "UDS-N6", "description": "Compact built-in scaler for scaling, perio, and endo with auto frequency tracking.", "category": "Ultrasonic Scaler", "badge": "Item", "price": 179, "new_price": None, "brand_id": 1},
  {"product_name": "USD-E LED", "description": "Precision ultrasonic scaler with adjustable power and bright LED illumination.", "category": "Ultrasonic Scaler", "badge": "Item", "price": 152, "new_price": 160, "brand_id": 1},
  {"product_name": "UDS-N3 LED Handpiece", "description": "Replacement handpiece for the UDS-N3 LED ultrasonic scaler.", "category": "Ultrasonic Scaler", "badge": "Item", "price": 69, "new_price": None, "brand_id": 1},
  {"product_name": "UDS-N6 LED Handpiece", "description": "Replacement handpiece for the UDS-N6 LED ultrasonic scaler.", "category": "Ultrasonic Scaler", "badge": "Item", "price": 81, "new_price": None, "brand_id": 1},
  {"product_name": "AP-H", "description": "High-performance dental air polisher for stain removal, whitening, prophylaxis, and implant cleaning.", "category": "Air polisher", "badge": "Item", "price": 179.6, "new_price": 189, "brand_id": 1},

  {"product_name": "i-Sensor H2", "description": "High-resolution digital intraoral sensor for adult imaging with AI-enhanced diagnostics.", "category": "i-Sensor", "badge": "Item", "price": 750.5, "new_price": 790, "brand_id": 1},
  {"product_name": "Smart Ray Pro", "description": "High-speed 3D scanner for quality inspection, reverse engineering, and precision measurement.", "category": "Portable X-ray", "badge": "Item", "price": 665, "new_price": 700, "brand_id": 1},
  {"product_name": "i-Sensor H 1.5", "description": "High-resolution digital intraoral sensor for adult imaging with AI-enhanced diagnostics.", "category": "i-Sensor", "badge": "Item", "price": 655.5, "new_price": 690, "brand_id": 1},
  {"product_name": "AI-Ray", "description": "AI-driven dental imaging system for diagnostics, treatment planning, and radiography.", "category": "Portable X-ray", "badge": "Item", "price": 845.5, "new_price": 890, "brand_id": 1},
  {"product_name": "AI-Ray Pro", "description": "AI positioning X-ray system that automatically detects and aligns the target for consistent imaging.", "category": "Portable X-ray", "badge": "Item", "price": 997.5, "new_price": 1050, "brand_id": 1},
  {"product_name": "Smart Ray", "description": "Portable DC X-ray system for intraoral imaging, endodontic diagnosis, and implant planning.", "category": "Portable X-ray", "badge": "Item", "price": 703, "new_price": 740, "brand_id": 1},

  {"product_name": "Smart Ray Pro Combo H1.5", "description": "Smart Ray Pro + Sensor H1.5 + trolley + radiation apron + laptop bundle.", "category": "Surgical Microscope Combo", "badge": "Item", "price": 1790, "new_price": 1924, "brand_id": 1},
  {"product_name": "Smart Ray Pro Combo H2", "description": "Smart Ray Pro + Sensor H2 + trolley + radiation apron + laptop bundle.", "category": "Surgical Microscope Combo", "badge": "Item", "price": 1890, "new_price": 2024, "brand_id": 1},

  {"product_name": "Smart Ray Combo H1.5", "description": "Smart Ray + Sensor H1.5 + trolley + radiation apron + laptop bundle.", "category": "Woodpecker Combo Set", "badge": "Item", "price": 1850, "new_price": 1964, "brand_id": 1},
  {"product_name": "Smart Ray Combo H2", "description": "Smart Ray + Sensor H2 + trolley + radiation apron + laptop bundle.", "category": "Woodpecker Combo Set", "badge": "Item", "price": 1950, "new_price": 2064, "brand_id": 1},
  {"product_name": "AI Ray Combo H1.5", "description": "AI Ray + Sensor H1.5 + trolley + radiation apron + laptop bundle.", "category": "Woodpecker Combo Set", "badge": "Item", "price": 1890, "new_price": 2114, "brand_id": 1},
  {"product_name": "AI Ray Combo H2", "description": "AI Ray + Sensor H2 + trolley + radiation apron + laptop bundle.", "category": "Woodpecker Combo Set", "badge": "Item", "price": 1990, "new_price": 2214, "brand_id": 1},
  {"product_name": "AI Ray Pro Combo H1.5", "description": "AI Ray Pro + Sensor H1.5 + trolley + radiation apron + laptop bundle.", "category": "Woodpecker Combo Set", "badge": "Item", "price": 2050, "new_price": 2274, "brand_id": 1},
  {"product_name": "AI Ray Pro Combo H2", "description": "AI Ray Pro + Sensor H2 + trolley + radiation apron + laptop bundle.", "category": "Woodpecker Combo Set", "badge": "Item", "price": 2150, "new_price": 2374, "brand_id": 1},

  {"product_name": "AI Ray Pro + KP Cam Pro Combo (All-in-One Chairside Imaging Package)", "description": "AI Ray Pro + i-Sensor H2 + KP Cam Pro + trolley all-in-one chairside imaging bundle, 2-year warranty.", "category": "KP-Oral Camera Combo Set", "badge": "Item", "price": 2790, "new_price": 3044, "brand_id": 1},

  {"product_name": "Lenovo Ideapad (Core i3 Gen 13th, 8GB/128GB)", "description": "Laptop: Core i3 gen 13th, RAM 8GB, SSD 128GB, 15.6-inch FHD IPS.", "category": "Laptop", "badge": "Item", "price": 369, "new_price": 389, "brand_id": 1},
  {"product_name": "Lenovo Ideapad Ryzen 5 (Ryzen5 7000 Series, 8GB/256GB)", "description": "Laptop: Ryzen 5 7000 series, RAM 8GB, SSD 256GB, 15.6-inch FHD.", "category": "Laptop", "badge": "Item", "price": 419, "new_price": 519, "brand_id": 1},
  {"product_name": "Lenovo Ideapad Ryzen 5 (Ryzen5 5000 Series, 8GB/256GB)", "description": "Laptop: Ryzen 5 5000 series, RAM 8GB, SSD 256GB, 15.6-inch FHD IPS.", "category": "Laptop", "badge": "Item", "price": 448, "new_price": 549, "brand_id": 1},
  {"product_name": "Lenovo Ideapad Core i5 (8GB/256GB)", "description": "Laptop: Core i5 gen 11th, RAM 8GB, SSD 256GB, 15.6-inch FHD IPS.", "category": "Laptop", "badge": "Item", "price": 468, "new_price": 549, "brand_id": 1},
  {"product_name": "Lenovo ThinkBook Core i5", "description": "Laptop: Core i5 gen 11th, RAM 16GB, SSD 512GB, 15.6-inch FHD IPS.", "category": "Laptop", "badge": "Item", "price": 548, "new_price": 669, "brand_id": 1},
  {"product_name": "Lenovo Legion Core i7 (RTX 4060)", "description": "Laptop: Core i7 gen 14th, RAM 16GB, 512TB M2 SSD, GPU RTX 4060 8G, 15.6-inch high-performance IPS.", "category": "Laptop", "badge": "Item", "price": 1249, "new_price": 1349, "brand_id": 1},
  {"product_name": "Lenovo Legion Core i7 (RTX 5060)", "description": "Laptop: Core i7 gen 14th, RAM 16GB, 1TB M2 SSD, GPU RTX 5060 8G, 15.6-inch high-performance IPS.", "category": "Laptop", "badge": "Item", "price": 1449, "new_price": 1549, "brand_id": 1},

  {"product_name": "AI-Pex (Master Version)", "description": "Advanced AI-powered apex locator plus pulp testing function for precise root canal measurement.", "category": "Apex Locator", "badge": "Item", "price": 361, "new_price": 380, "brand_id": 1},
  {"product_name": "AI-Pex", "description": "Advanced AI-powered apex locator plus pulp testing function for precise root canal measurement.", "category": "Apex Locator", "badge": "Item", "price": 342, "new_price": 360, "brand_id": 1},
  {"product_name": "Woodpex V", "description": "State-of-the-art apex locator ensuring accurate measurement for precise root canal treatments.", "category": "Apex Locator", "badge": "Item", "price": 133, "new_price": 140, "brand_id": 1},
  {"product_name": "Woodpex X", "description": "Compact, accurate apex locator for root canal length measurement and real-time canal monitoring.", "category": "Apex Locator", "badge": "Item", "price": 180.5, "new_price": 190, "brand_id": 1},
  {"product_name": "Mini-Pex", "description": "Portable, precise apex locator designed for accurate root canal measurement (student kit).", "category": "Student Kit", "badge": "Item", "price": 99, "new_price": 140, "brand_id": 1},

  {"product_name": "Tampered Glass Trolley", "description": "Mobile organized cart with 360° mobility, secure storage, and cable management; includes 2 free trays.", "category": "Trolley", "badge": "Item", "price": 266, "new_price": 280, "brand_id": 1},
  {"product_name": "NW-A01", "description": "Mobile monitor cart with 360° mobility, secure storage, and cable management.", "category": "Trolley", "badge": "Item", "price": 152, "new_price": 160, "brand_id": 1},
  {"product_name": "Trolley 3-Drawer", "description": "Mobile organized cart with 360° mobility and secure storage; includes 2 free trays.", "category": "Trolley", "badge": "Item", "price": 285, "new_price": 300, "brand_id": 1},
  {"product_name": "Trolley T3-4", "description": "Multi-layer mobile dental trolley with 3-tier storage, lockable wheels, medical-grade structure.", "category": "Trolley", "badge": "Item", "price": 123.5, "new_price": 130, "brand_id": 1},
  {"product_name": "Woodpecker Trolley", "description": "Mobile organized cart designed to support Woodpecker devices with 360° mobility.", "category": "Trolley", "badge": "Item", "price": 199.5, "new_price": 210, "brand_id": 1},
  {"product_name": "Trolley T3-2", "description": "Multi-layer mobile dental trolley with 3-tier design, lockable wheels, medical-grade structure.", "category": "Trolley", "badge": "Item", "price": 95, "new_price": 100, "brand_id": 1},
  {"product_name": "Trolley NW-A03", "description": "Compact multi-drawer dental trolley with lockable wheels and flexible storage layout.", "category": "Trolley", "badge": "Item", "price": 161.5, "new_price": 170, "brand_id": 1},

  {"product_name": "Instrument Cart 66x44mm", "description": "Mobile organized cart with 360° mobility and secure storage.", "category": "Trolley", "badge": "Item", "price": 55.1, "new_price": 58, "brand_id": 1},
  {"product_name": "Instrument Cart 600mm", "description": "Mobile organized cart with 360° mobility and secure storage.", "category": "Trolley", "badge": "Item", "price": 75.05, "new_price": 79, "brand_id": 1},
  {"product_name": "Instrument Cart 60x40mm", "description": "Mobile organized cart with 360° mobility and secure storage.", "category": "Trolley", "badge": "Item", "price": 49.4, "new_price": 52, "brand_id": 1},
  {"product_name": "Instrument Cart 500mm", "description": "Mobile organized cart with 360° mobility and secure storage.", "category": "Trolley", "badge": "Item", "price": 69.35, "new_price": 73, "brand_id": 1},
  {"product_name": "Instrument Cart 30x40mm (2-tier)", "description": "Mobile organized cart with 360° mobility and secure storage.", "category": "Trolley", "badge": "Item", "price": 36.1, "new_price": 38, "brand_id": 1},
  {"product_name": "Instrument Cart 40x60mm (3-tier)", "description": "Mobile organized cart with 360° mobility and secure storage.", "category": "Trolley", "badge": "Item", "price": 56.05, "new_price": 59, "brand_id": 1},
  {"product_name": "Instrument Cart 30x40mm (3-tier)", "description": "Mobile organized cart with 360° mobility and secure storage.", "category": "Trolley", "badge": "Item", "price": 45.6, "new_price": 48, "brand_id": 1},

  {"product_name": "KP iSee Pro", "description": "Premium surgical dental microscope with German SCHOTT optics, inclinable binocular tube, and integrated 4K imaging.", "category": "Surgical Microscope", "badge": "Item", "price": 10600, "new_price": None, "brand_id": 1},
  {"product_name": "KP Cam Pro (Metal)", "description": "High-definition 1080P intraoral camera with one-click capture, true color reproduction, and 10 LED illumination.", "category": "Intraoral Camera", "badge": "Item", "price": 940.5, "new_price": 990, "brand_id": 1},
  {"product_name": "KP Cam Pro (Plastic)", "description": "High-definition 1080P intraoral camera with one-click capture, true color reproduction, and 10 LED illumination.", "category": "Intraoral Camera", "badge": "Item", "price": 750.5, "new_price": 790, "brand_id": 1},

  {"product_name": "WJ-45TL (1:5)", "description": "Contra-angle handpiece, gear ratio 1:5.", "category": "Contra-angles", "badge": "Item", "price": 220, "new_price": None, "brand_id": 1},
  {"product_name": "WJ-45L (1:4.2)", "description": "Contra-angle handpiece, gear ratio 1:4.2.", "category": "Contra-angles", "badge": "Item", "price": 365, "new_price": None, "brand_id": 1},
  {"product_name": "WJ-SG45CL (1:3)", "description": "Contra-angle handpiece, gear ratio 1:3.", "category": "Contra-angles", "badge": "Item", "price": 340, "new_price": None, "brand_id": 1},
  {"product_name": "WP-1L (20:1)", "description": "Contra-angle for implant motor, 20:1 reduction ratio, German bearing option available.", "category": "Contra-angles for implant Motor", "badge": "Item", "price": 360, "new_price": None, "brand_id": 1},
  {"product_name": "CA161 (6:1)", "description": "Contra-angle for endo motor, 6:1 reduction ratio.", "category": "Contra-angles Endo Motor", "badge": "Item", "price": 169, "new_price": None, "brand_id": 1},
  {"product_name": "CA001-G", "description": "Contra-angle for endo motor.", "category": "Contra-angles Endo Motor", "badge": "Item", "price": 98, "new_price": None, "brand_id": 1},
  {"product_name": "Contra-angle Endo Motor (Woodpecker Bearing)", "description": "Contra-angle for endo motor with Woodpecker bearing option.", "category": "Contra-angles Endo Motor", "badge": "Item", "price": 210, "new_price": None, "brand_id": 1},

  {"product_name": "AI Ray Pro Set Core i5", "description": "AI Ray Pro set including laptop (Core i5), H2 sensor, radiation apron, trolley, mouse and mouse pad.", "category": "X Ray sensor Package", "badge": "Item", "price": 2250, "new_price": 2514, "brand_id": 1},
  {"product_name": "AI Ray Pro Set Core i3", "description": "AI Ray Pro set including laptop (Core i3), H2 sensor, radiation apron, trolley, mouse and mouse pad.", "category": "X Ray sensor Package", "badge": "Item", "price": 2150, "new_price": 2374, "brand_id": 1},
  {"product_name": "AI Ray Set Core i5", "description": "AI Ray set including laptop (Core i5), H2 sensor, radiation apron, trolley, mouse and mouse pad.", "category": "X Ray sensor Package", "badge": "Item", "price": 2100, "new_price": 2354, "brand_id": 1},
  {"product_name": "AI Ray Set Core i3", "description": "AI Ray set including laptop (Core i3), H2 sensor, radiation apron, trolley, mouse and mouse pad.", "category": "X Ray sensor Package", "badge": "Item", "price": 1990, "new_price": 2214, "brand_id": 1},
  {"product_name": "Smart Ray Pro Set Core i5 (Woodpecker)", "description": "Smart Ray Pro set including laptop (Core i5), H2 sensor, radiation apron, trolley, mouse and mouse pad.", "category": "X Ray sensor Package", "badge": "Item", "price": 2030, "new_price": 2194, "brand_id": 1},
  {"product_name": "Smart Ray Pro Set Core i3 (Woodpecker)", "description": "Smart Ray Pro set including laptop (Core i3), H2 sensor, radiation apron, trolley, mouse and mouse pad.", "category": "X Ray sensor Package", "badge": "Item", "price": 1930, "new_price": 2054, "brand_id": 1},
  {"product_name": "Smart Ray Pro Set Core i5", "description": "Smart Ray Pro set including laptop (Core i5), H2 sensor, radiation apron, trolley, mouse and mouse pad.", "category": "X Ray sensor Package", "badge": "Item", "price": 1990, "new_price": 2154, "brand_id": 1},
  {"product_name": "Smart Ray Pro Set Core i3", "description": "Smart Ray Pro set including laptop (Core i3), H2 sensor, radiation apron, trolley, mouse and mouse pad.", "category": "X Ray sensor Package", "badge": "Item", "price": 1890, "new_price": 2024, "brand_id": 1},

  # --- YouJoy products (extracted from the YouJoy Product Catalog PDF) ---
  {"product_name": "OMNI", "description": "Youjoy Omni Color Shade Detector for fast, accurate shade matching in crown, veneer, and restoration work.", "category": "Color Shade Detector", "badge": "Item", "price": 266, "new_price": 280, "brand_id": 2},
  {"product_name": "SCAN11", "description": "Youjoy Scan11 Intraoral Scanner - fast 3D intraoral scanning with cloud functions for easy data storage and transfer.", "category": "Intraoral Scanner", "badge": "Item", "price": 2950, "new_price": None, "brand_id": 2},

  {"product_name": "BES 22L-CLASS-B-LCD (Autoclave)", "description": "Youjoy BES-22L Class B LCD Autoclave sterilizer with clear LCD display for accurate, safe, and internationally compliant sterilization.", "category": "Autoclave", "badge": "Item", "price": 1580, "new_price": None, "brand_id": 2},
  {"product_name": "BES 23L-CLASS-B-LCD", "description": "Youjoy BES-23L Class B LCD Autoclave sterilizer with clear LCD display for accurate, safe, and internationally compliant sterilization.", "category": "Autoclave", "badge": "Item", "price": 1650, "new_price": None, "brand_id": 2},
  {"product_name": "BES 23L-CLASS-B-LED", "description": "Youjoy BES-23L Class B LED Autoclave sterilizer with LED display for accurate, safe, and internationally compliant sterilization.", "category": "Autoclave", "badge": "Item", "price": 1550, "new_price": None, "brand_id": 2},

  {"product_name": "UC-01", "description": "Youjoy Ultrasonic Cleaner UC-01 for fast, effective, and safe ultrasonic cleaning of dental instruments, jewelry, and medical tools. Capacity: 3 L.", "category": "Ultrasonic Cleaner", "badge": "Item", "price": 179.55, "new_price": 189, "brand_id": 2},
  {"product_name": "CLEAN-02", "description": "Youjoy Ultrasonic Cleaner UC-02, an advanced ultrasonic cleaner for dental instruments, jewelry, and medical tools. Capacity: 6 L.", "category": "Ultrasonic Cleaner", "badge": "Item", "price": 280.25, "new_price": 295, "brand_id": 2},
  {"product_name": "CLEAN-03", "description": "Youjoy Ultrasonic Cleaner UC-03, a high-performance ultrasonic cleaner for dental instruments, jewelry, and medical tools. Capacity: 7.5 L.", "category": "Ultrasonic Cleaner", "badge": "Item", "price": 418, "new_price": 440, "brand_id": 2},

  {"product_name": "R2", "description": "Youjoy Sensor R2, a digital dental sensor for dental X-ray imaging.", "category": "Dental Sensor", "badge": "Item", "price": 655.5, "new_price": 690, "brand_id": 2},

  {"product_name": "DRINK (Water Distiller)", "description": "Youjoy Water Distiller (Model DRINK), a high-efficiency water distiller designed for dental clinics, medical offices, and laboratory use.", "category": "Water Distiller", "badge": "Item", "price": 114, "new_price": 120, "brand_id": 2},

  {"product_name": "YOUJOY98P", "description": "Youjoy 98P Portable Dental X-Ray for diagnosis, implant, and endo work, offering clear imaging and precise, easy-to-use portable operation. Tube voltage 70KV+-10%, tube current 2mA+-20%, focus 0.4mm, exposure time range 0.04s-2.0s, machine weight 1.9kg.", "category": "Portable X-ray", "badge": "Item", "price": 560.5, "new_price": 590, "brand_id": 2},
  {"product_name": "SUPER05", "description": "Youjoy Super05 Portable X-Ray, a high-performance portable dental X-ray for diagnosis, endo, and implant planning. Tube voltage 70KV+-10%, tube current 2mA+-20%, focus 0.4mm, exposure time range 0.04s-2.0s, machine weight 1.7kg.", "category": "Portable X-ray", "badge": "Item", "price": 655.5, "new_price": 690, "brand_id": 2},

  {"product_name": "SEAL120", "description": "Youjoy SEAL120 Sealing Machine, a reliable sterilization pouch sealing machine designed for dental and medical clinics.", "category": "Sealing Machine", "badge": "Item", "price": 152, "new_price": 160, "brand_id": 2},
  {"product_name": "SEAL120 WITH RACK", "description": "Youjoy SEAL120 with rack Sealing Machine, a reliable sterilization pouch sealing machine designed for dental and medical clinics, including a rack.", "category": "Sealing Machine", "badge": "Item", "price": 170.05, "new_price": 179, "brand_id": 2},

  {"product_name": "GIANT30", "description": "Runyes Giant-30 compact oil-free dental 100% dry air supply system designed for stable and hygienic airflow. Capacity 25 L, 1400 RPM, 0.6 rated power (KVA), supports 1 chair.", "category": "Air Compressor", "badge": "Item", "price": 522.5, "new_price": 550, "brand_id": 2},
  {"product_name": "GIANT35", "description": "Runyes Giant-35 Air Compressor, a powerful dual-pump oil-free dental 100% dry air system for 1-2 chairs. Capacity 50 L, 1400 RPM, 1.2 rated power (KVA), supports 2 chairs.", "category": "Air Compressor", "badge": "Item", "price": 845.5, "new_price": 890, "brand_id": 2},
  {"product_name": "GIANT40", "description": "Runyes Giant-40 Air Compressor, a high-capacity oil-free dental 100% dry air system for up to 3 chairs. Capacity 60 L, 1400 RPM, 1.54 rated power (KVA), supports 3 chairs.", "category": "Air Compressor", "badge": "Item", "price": 997.5, "new_price": 1050, "brand_id": 2},
  {"product_name": "GIANT45", "description": "Runyes Giant-45 Air Compressor, a high-output oil-free dental 100% dry air system built for busy clinics. Capacity 70 L, 1400 RPM, 2.2 rated power (KVA), supports 5 chairs.", "category": "Air Compressor", "badge": "Item", "price": 1282.5, "new_price": 1350, "brand_id": 2},
  {"product_name": "GIANT50", "description": "Runyes Giant-50 powerhouse oil-free 100% dry air compressor built for busy dental clinics. Capacity 135 L, 1400 RPM, 3.1 rated power (KVA), supports 8 chairs.", "category": "Air Compressor", "badge": "Item", "price": 2185, "new_price": 2300, "brand_id": 2},
  {"product_name": "GIANT55", "description": "Runyes Giant-55 powerhouse oil-free 100% dry air compressor built for busy dental clinics. Capacity 210 L, 1400 RPM, 4.7 rated power (KVA), supports 12 chairs.", "category": "Air Compressor", "badge": "Item", "price": 2755, "new_price": 2900, "brand_id": 2},

  {"product_name": "MOBILE CART TR/7A", "description": "TR-7A All-In-One Mobile Dental Cart, a sleek and versatile mobile workstation for digital dentistry with adjustable height, 3-layer tray design, 360-degree lockable wheels, and a built-in shelf for a laptop or scanner.", "category": "Mobile Cart", "badge": "Item", "price": 266, "new_price": 280, "brand_id": 2},
  {"product_name": "TR/7A WORKSTATION CART", "description": "TR-7A All-In-One Mobile Dental Cart, a sleek and versatile mobile workstation for digital dentistry with adjustable height, 3-layer tray design, 360-degree lockable wheels, and a dedicated VESA-compatible monitor mount for a screen or digital scanner.", "category": "Mobile Cart", "badge": "Item", "price": 370.5, "new_price": 390, "brand_id": 2},
]

# category_id is a real FK now (Product.category_id -> categories.id), so
# categories are seeded up front just like BRANDS/brand_id above, instead of
# being created lazily/implicitly while looping over PRODUCTS. Derived from
# PRODUCTS (order-preserving, de-duplicated) rather than hand-maintained
# since there are dozens of them and hand-syncing would drift.
CATEGORIES = list(dict.fromkeys(item["category"] for item in PRODUCTS if item.get("category")))

USERS = [
    {
        "user_name": "Admin User",
        "email": "admin@store.dev",
        "password": "Admin@12345",
        "role_title": "Admin",
        "user_management": True,
        "price_listing": True,
        "product_management": True,
        "customer_management": True,
    },
    {
        "user_name": "Staff User",
        "email": "staff@store.dev",
        "password": "Staff@12345",
        "role_title": "Staff",
        "user_management": False,
        "price_listing": True,
        "product_management": True,
        "customer_management": False,
    },
]

CUSTOMERS = [
    {
        "customer_name": "Sample Customer",
        "email": "customer@store.dev",
        "password": "Customer@12345",
        "access_permission": False,
    },
    {
        "customer_name": "VIP Customer",
        "email": "vip.customer@store.dev",
        "password": "VipCustomer@12345",
        "access_permission": True,
    },
]

# Attached to a product seeded above.
MANUALS = [
    {
        "product_name": "LX16-PLUS",
        "description": "User manual and quick-start guide for the LX16-PLUS diode laser.",
        "pdf": None,
    },
]

PROMOTIONS = [
    {
        "promotion_name": "Summer Dental Sale",
        "description": "Seasonal discount bundle across select diode laser and scanner equipment.",
        "price": 2999,
        "old_price": 3480,
        "start_date": datetime.now(timezone.utc),
        "end_date": datetime.now(timezone.utc) + timedelta(days=30),
    },
]


def seed_brands(db) -> dict[str, Brand]:
    brand_by_name = {}
    for name in BRANDS:
        brand = db.query(Brand).filter(Brand.brand_name == name).first()
        if brand:
            print(f"Brand '{name}' already exists (id={brand.id}), skipping.")
        else:
            brand = Brand(brand_name=name)
            db.add(brand)
            db.flush()
            print(f"Created brand '{name}' (id={brand.id}).")
        brand_by_name[name] = brand
    return brand_by_name


def seed_categories(db) -> dict[str, Category]:
    category_by_name = {}
    for name in CATEGORIES:
        category = db.query(Category).filter(Category.category_name == name).first()
        if category:
            print(f"Category '{name}' already exists (id={category.id}), skipping.")
        else:
            category = Category(category_name=name)
            db.add(category)
            db.flush()
            print(f"Created category '{name}' (id={category.id}).")
        category_by_name[name] = category
    return category_by_name


def _discount_percent(price: float, new_price: float | None) -> int:
    """new_price (the pre-discount figure) vs. price, as a percentage off -
    matches the Product.discount format (integer 0-100)."""
    if new_price is None or new_price <= price:
        return 0
    return round((new_price - price) / new_price * 100)


def seed_products(db, brand_by_name: dict[str, Brand], category_by_name: dict[str, Category]) -> None:
    brands_by_position = [brand_by_name[name] for name in BRANDS]

    for item in PRODUCTS:
        existing = db.query(Product).filter(Product.product_name == item["product_name"]).first()
        if existing:
            print(f"Product '{item['product_name']}' already exists (id={existing.id}), skipping.")
            continue

        category = category_by_name.get(item.get("category"))

        product = Product(
            product_name=item["product_name"],
            description=item.get("description"),
            price=item["price"],
            discount=_discount_percent(item["price"], item.get("new_price")),
            brand_id=brands_by_position[item["brand_id"] - 1].id,
            category_id=category.id if category else None,
            badge=item.get("badge"),
        )
        db.add(product)
        db.flush()
        print(f"Created product '{product.product_name}' (id={product.id}).")


def seed_users(db) -> None:
    for item in USERS:
        existing = db.query(User).filter(User.email == item["email"]).first()
        if existing:
            print(f"User '{item['email']}' already exists (id={existing.id}), skipping.")
            continue

        user = User(
            user_name=item["user_name"],
            email=item["email"],
            hashed_password=hash_password(item["password"]),
            role_title=item["role_title"],
            is_active=True,
            is_verified=True,
            user_management=item["user_management"],
            price_listing=item["price_listing"],
            product_management=item["product_management"],
            customer_management=item["customer_management"],
        )
        db.add(user)
        db.flush()
        print(f"Created user '{user.email}' (id={user.id}, role={user.role_title}).")


def seed_customers(db) -> None:
    for item in CUSTOMERS:
        existing = db.query(Customer).filter(Customer.email == item["email"]).first()
        if existing:
            print(f"Customer '{item['email']}' already exists (id={existing.id}), skipping.")
            continue

        customer = Customer(
            customer_name=item["customer_name"],
            email=item["email"],
            hashed_password=hash_password(item["password"]),
            access_permission=item["access_permission"],
            is_active=True,
            is_verified=True,
        )
        db.add(customer)
        db.flush()
        print(f"Created customer '{customer.email}' (id={customer.id}, vip={customer.access_permission}).")


def seed_manuals(db) -> None:
    for item in MANUALS:
        product = db.query(Product).filter(Product.product_name == item["product_name"]).first()
        if not product:
            print(f"Product '{item['product_name']}' not found, skipping manual.")
            continue

        existing = (
            db.query(Manual)
            .filter(Manual.product_id == product.id, Manual.description == item["description"])
            .first()
        )
        if existing:
            print(f"Manual for '{item['product_name']}' already exists (id={existing.id}), skipping.")
            continue

        manual = Manual(
            product_id=product.id,
            description=item["description"],
            pdf=item.get("pdf"),
        )
        db.add(manual)
        db.flush()
        print(f"Created manual for '{item['product_name']}' (id={manual.id}).")


def seed_promotions(db) -> None:
    for item in PROMOTIONS:
        existing = db.query(Promotion).filter(Promotion.promotion_name == item["promotion_name"]).first()
        if existing:
            print(f"Promotion '{item['promotion_name']}' already exists (id={existing.id}), skipping.")
            continue

        promotion = Promotion(
            promotion_name=item["promotion_name"],
            description=item.get("description"),
            price=item["price"],
            old_price=item.get("old_price"),
            start_date=item["start_date"],
            end_date=item["end_date"],
        )
        db.add(promotion)
        db.flush()
        print(f"Created promotion '{promotion.promotion_name}' (id={promotion.id}).")


def main() -> None:
    db = SessionLocal()
    try:
        brand_by_name = seed_brands(db)
        category_by_name = seed_categories(db)
        seed_products(db, brand_by_name, category_by_name)
        seed_users(db)
        seed_customers(db)
        seed_manuals(db)
        seed_promotions(db)
        db.commit()
        print("Done.")
    finally:
        db.close()


if __name__ == "__main__":
    main()