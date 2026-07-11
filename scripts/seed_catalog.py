"""
Dev helper: insert a handful of sample brands/products, bypassing the API.

Usage:
    python -m scripts.seed_catalog
    (run from the project root, with the virtualenv activated and
     DATABASE_URL pointing at your Postgres instance)

Safe to re-run: brands/products are matched by name and skipped if they
already exist, so this won't create duplicates on a second run.
"""
from app.database import SessionLocal
from app.models import Brand, Category, Product

BRANDS = ["Woodpecker", "YouJoy"]

# Every entry below carries "brand_id": 1 - a 1-based position in BRANDS
# (all of them are Woodpecker), not a real database id. main() resolves it
# positionally, the same way brand_by_name resolves brand_name elsewhere.
PRODUCTS = [
  {"product_name": "LX16-PLUS", "description": "Multi-purpose dental diode laser for soft tissue, perio, endo, implant, surgery, and pain therapy.", "category": "Diode Laser System", "badge": "SUPER SALE", "price": 3200, "new_price": 3480, "brand_id": 1},
  {"product_name": "KP SmileScan", "description": "High-precision intraoral scanner with full-arch imaging, AI-enhanced accuracy, and seamless lab integration.", "category": "Intraoral Scanner", "badge": "SUPER SALE", "price": 3372.5, "new_price": 3550, "brand_id": 1},
  {"product_name": "US-II LED", "description": "Multi-function piezo ultrasonic scaler for scaling, perio, endo, implant maintenance, and surgery.", "category": "Piezo Bone Surgery", "badge": "SUPER SALE", "price": 1425, "new_price": 1500, "brand_id": 1},
  {"product_name": "Woodpecker Implanter", "description": "Precision implant motor for implant placement, surgery, and torque control.", "category": "Dental Implant Device", "badge": "SUPER SALE", "price": 1235, "new_price": 1300, "brand_id": 1},
  {"product_name": "Surgery-X", "description": "Piezoelectric surgical unit for bone surgery, soft tissue surgery, and implant placement with adjustable power and irrigation.", "category": "Piezo Bone Surgery", "badge": "SUPER SALE", "price": 2470, "new_price": 2600, "brand_id": 1},
  {"product_name": "3 in 1 Surgic Star", "description": "3-in-1 premium implant and surgical motor with smart presets, touchscreen control, and stable irrigation.", "category": "Dental Implant Device", "badge": "SUPER SALE", "price": 3705, "new_price": 3900, "brand_id": 1},
  {"product_name": "ES5", "description": "High-performance multi-purpose electric motor for implant, surgical, and endodontic procedures, up to 120,000 rpm.", "category": "Dental Electric Motor", "badge": "SUPER SALE", "price": 1520, "new_price": 1600, "brand_id": 1},
  {"product_name": "PT-A", "description": "Ultrasonic scaler and air polisher combo with heated water delivery and hands-free touchscreen control.", "category": "Air Polisher System", "badge": "SUPER SALE", "price": 2470, "new_price": 2600, "brand_id": 1},

  {"product_name": "Endo 3", "description": "High-performance rotary endodontic system for root canal treatment, cleaning, and shaping.", "category": "Endo Activator", "badge": "SUPER SALE", "price": 171, "new_price": 180, "brand_id": 1},
  {"product_name": "Endo Radar Pro", "description": "Top-tier apex locator with advanced tracking technology for precise root canal measurement.", "category": "Endo Motor", "badge": "SUPER SALE", "price": 456, "new_price": 480, "brand_id": 1},
  {"product_name": "FI-G", "description": "Gutta percha obturation device with exceptional sealing performance and easy handling.", "category": "Gutta-purcha Obteration System", "badge": "SUPER SALE", "price": 289.8, "new_price": 305, "brand_id": 1},
  {"product_name": "Motopex", "description": "Compact dental micromotor for precision drilling, root canal treatment, implantology, and crown prep.", "category": "Endo Motor", "badge": "SUPER SALE", "price": 399, "new_price": 420, "brand_id": 1},
  {"product_name": "FI-P", "description": "Gutta percha obturation device with superior adaptability and long-term reliability.", "category": "Gutta-purcha Obteration System", "badge": "SUPER SALE", "price": 180.5, "new_price": 190, "brand_id": 1},
  {"product_name": "E-COM+", "description": "Powerful cordless endomotor for canal cleaning and shaping with precise torque control.", "category": "Endo Motor", "badge": "SUPER SALE", "price": 275.5, "new_price": 290, "brand_id": 1},
  {"product_name": "R1-Plus", "description": "Precision endodontic measuring ruler for gutta percha cutting and working length measurement.", "category": "Gutta-purcha Obteration System", "badge": "SUPER SALE", "price": 25, "new_price": 29, "brand_id": 1},
  {"product_name": "Endo Pace", "description": "Cordless brushless endomotor with apex locator integration for safe, efficient canal treatment.", "category": "Endo Motor", "badge": "SUPER SALE", "price": 256.5, "new_price": 270, "brand_id": 1},

  {"product_name": "LED H", "description": "Curing light, light intensity 1000-1800 mW/cm2.", "category": "Curing Light", "badge": "SUPER SALE", "price": 104.5, "new_price": 110, "brand_id": 1},
  {"product_name": "LED F", "description": "Curing light, light intensity 1600-1800 mW/cm2.", "category": "Curing Light", "badge": "SUPER SALE", "price": 128.3, "new_price": 135, "brand_id": 1},
  {"product_name": "LED G", "description": "Curing light, light intensity 1000-1200 mW/cm2.", "category": "Curing Light", "badge": "SUPER SALE", "price": 66.5, "new_price": 70, "brand_id": 1},
  {"product_name": "LED B", "description": "Curing light, light intensity 1000-1700 mW/cm2.", "category": "Curing Light", "badge": "SUPER SALE", "price": 54.4, "new_price": 68, "brand_id": 1},
  {"product_name": "iLED II", "description": "Curing light, light intensity 2700-3000 mW/cm2.", "category": "Curing Light", "badge": "SUPER SALE", "price": 152, "new_price": 160, "brand_id": 1},
  {"product_name": "U-Light", "description": "Lightweight curing light with 3 modes and 360° rotation for consistent polymerization.", "category": "Curing Light", "badge": "SUPER SALE", "price": 171, "new_price": 180, "brand_id": 1},
  {"product_name": "iLED MAX", "description": "Curing light, light intensity 2300-2500 mW/cm2.", "category": "Curing Light", "badge": "SUPER SALE", "price": 116, "new_price": 120, "brand_id": 1},
  {"product_name": "O-Star", "description": "High-performance curing light with 7 preset modes and 360° use for deep polymerization.", "category": "Curing Light", "badge": "SUPER SALE", "price": 342, "new_price": 360, "brand_id": 1},
  {"product_name": "iLED Plus", "description": "Curing light, light intensity 2300-2500 mW/cm2.", "category": "Curing Light", "badge": "SUPER SALE", "price": 95, "new_price": 100, "brand_id": 1},
  {"product_name": "X-Star", "description": "High-performance curing light with 8 preset modes and 360° use for deep polymerization.", "category": "Curing Light", "badge": "SUPER SALE", "price": 494, "new_price": 520, "brand_id": 1},

  {"product_name": "U600 LED", "description": "Advanced ultrasonic scaler with LED handpiece illumination and 3 modes (scaling, perio, endo).", "category": "Ultrasonic Scaler", "badge": "SUPER SALE", "price": 332.5, "new_price": 350, "brand_id": 1},
  {"product_name": "UDS-N3 LED", "description": "Built-in ultrasonic scaler with LED illumination and detachable autoclavable handpiece.", "category": "Ultrasonic Scaler", "badge": "SUPER SALE", "price": 115, "new_price": None, "brand_id": 1},
  {"product_name": "UDS-T LED", "description": "Multi-function ultrasonic scaler for scaling, perio, and endo with LED handpiece.", "category": "Ultrasonic Scaler", "badge": "SUPER SALE", "price": 247, "new_price": 260, "brand_id": 1},
  {"product_name": "UDS-N6", "description": "Compact built-in scaler for scaling, perio, and endo with auto frequency tracking.", "category": "Ultrasonic Scaler", "badge": "SUPER SALE", "price": 179, "new_price": None, "brand_id": 1},
  {"product_name": "USD-E LED", "description": "Precision ultrasonic scaler with adjustable power and bright LED illumination.", "category": "Ultrasonic Scaler", "badge": "SUPER SALE", "price": 152, "new_price": 160, "brand_id": 1},
  {"product_name": "UDS-N3 LED Handpiece", "description": "Replacement handpiece for the UDS-N3 LED ultrasonic scaler.", "category": "Ultrasonic Scaler", "badge": "SUPER SALE", "price": 69, "new_price": None, "brand_id": 1},
  {"product_name": "UDS-N6 LED Handpiece", "description": "Replacement handpiece for the UDS-N6 LED ultrasonic scaler.", "category": "Ultrasonic Scaler", "badge": "SUPER SALE", "price": 81, "new_price": None, "brand_id": 1},
  {"product_name": "AP-H", "description": "High-performance dental air polisher for stain removal, whitening, prophylaxis, and implant cleaning.", "category": "Air polisher", "badge": "SUPER SALE", "price": 179.6, "new_price": 189, "brand_id": 1},

  {"product_name": "i-Sensor H2", "description": "High-resolution digital intraoral sensor for adult imaging with AI-enhanced diagnostics.", "category": "i-Sensor", "badge": "SUPER SALE", "price": 750.5, "new_price": 790, "brand_id": 1},
  {"product_name": "Smart Ray Pro", "description": "High-speed 3D scanner for quality inspection, reverse engineering, and precision measurement.", "category": "Portable X-ray", "badge": "SUPER SALE", "price": 665, "new_price": 700, "brand_id": 1},
  {"product_name": "i-Sensor H 1.5", "description": "High-resolution digital intraoral sensor for adult imaging with AI-enhanced diagnostics.", "category": "i-Sensor", "badge": "SUPER SALE", "price": 655.5, "new_price": 690, "brand_id": 1},
  {"product_name": "AI-Ray", "description": "AI-driven dental imaging system for diagnostics, treatment planning, and radiography.", "category": "Portable X-ray", "badge": "SUPER SALE", "price": 845.5, "new_price": 890, "brand_id": 1},
  {"product_name": "AI-Ray Pro", "description": "AI positioning X-ray system that automatically detects and aligns the target for consistent imaging.", "category": "Portable X-ray", "badge": "SUPER SALE", "price": 997.5, "new_price": 1050, "brand_id": 1},
  {"product_name": "Smart Ray", "description": "Portable DC X-ray system for intraoral imaging, endodontic diagnosis, and implant planning.", "category": "Portable X-ray", "badge": "SUPER SALE", "price": 703, "new_price": 740, "brand_id": 1},

  {"product_name": "Smart Ray Pro Combo H1.5", "description": "Smart Ray Pro + Sensor H1.5 + trolley + radiation apron + laptop bundle.", "category": "Surgical Microscope Combo", "badge": "SUPER SALE", "price": 1790, "new_price": 1924, "brand_id": 1},
  {"product_name": "Smart Ray Pro Combo H2", "description": "Smart Ray Pro + Sensor H2 + trolley + radiation apron + laptop bundle.", "category": "Surgical Microscope Combo", "badge": "SUPER SALE", "price": 1890, "new_price": 2024, "brand_id": 1},

  {"product_name": "Smart Ray Combo H1.5", "description": "Smart Ray + Sensor H1.5 + trolley + radiation apron + laptop bundle.", "category": "Woodpecker Combo Set", "badge": "SUPER SALE", "price": 1850, "new_price": 1964, "brand_id": 1},
  {"product_name": "Smart Ray Combo H2", "description": "Smart Ray + Sensor H2 + trolley + radiation apron + laptop bundle.", "category": "Woodpecker Combo Set", "badge": "SUPER SALE", "price": 1950, "new_price": 2064, "brand_id": 1},
  {"product_name": "AI Ray Combo H1.5", "description": "AI Ray + Sensor H1.5 + trolley + radiation apron + laptop bundle.", "category": "Woodpecker Combo Set", "badge": "SUPER SALE", "price": 1890, "new_price": 2114, "brand_id": 1},
  {"product_name": "AI Ray Combo H2", "description": "AI Ray + Sensor H2 + trolley + radiation apron + laptop bundle.", "category": "Woodpecker Combo Set", "badge": "SUPER SALE", "price": 1990, "new_price": 2214, "brand_id": 1},
  {"product_name": "AI Ray Pro Combo H1.5", "description": "AI Ray Pro + Sensor H1.5 + trolley + radiation apron + laptop bundle.", "category": "Woodpecker Combo Set", "badge": "SUPER SALE", "price": 2050, "new_price": 2274, "brand_id": 1},
  {"product_name": "AI Ray Pro Combo H2", "description": "AI Ray Pro + Sensor H2 + trolley + radiation apron + laptop bundle.", "category": "Woodpecker Combo Set", "badge": "SUPER SALE", "price": 2150, "new_price": 2374, "brand_id": 1},

  {"product_name": "AI Ray Pro + KP Cam Pro Combo (All-in-One Chairside Imaging Package)", "description": "AI Ray Pro + i-Sensor H2 + KP Cam Pro + trolley all-in-one chairside imaging bundle, 2-year warranty.", "category": "KP-Oral Camera Combo Set", "badge": "SUPER SALE", "price": 2790, "new_price": 3044, "brand_id": 1},

  {"product_name": "Lenovo Ideapad (Core i3 Gen 13th, 8GB/128GB)", "description": "Laptop: Core i3 gen 13th, RAM 8GB, SSD 128GB, 15.6-inch FHD IPS.", "category": "Laptop", "badge": "SUPER SALE", "price": 369, "new_price": 389, "brand_id": 1},
  {"product_name": "Lenovo Ideapad Ryzen 5 (Ryzen5 7000 Series, 8GB/256GB)", "description": "Laptop: Ryzen 5 7000 series, RAM 8GB, SSD 256GB, 15.6-inch FHD.", "category": "Laptop", "badge": "SUPER SALE", "price": 419, "new_price": 519, "brand_id": 1},
  {"product_name": "Lenovo Ideapad Ryzen 5 (Ryzen5 5000 Series, 8GB/256GB)", "description": "Laptop: Ryzen 5 5000 series, RAM 8GB, SSD 256GB, 15.6-inch FHD IPS.", "category": "Laptop", "badge": "SUPER SALE", "price": 448, "new_price": 549, "brand_id": 1},
  {"product_name": "Lenovo Ideapad Core i5 (8GB/256GB)", "description": "Laptop: Core i5 gen 11th, RAM 8GB, SSD 256GB, 15.6-inch FHD IPS.", "category": "Laptop", "badge": "SUPER SALE", "price": 468, "new_price": 549, "brand_id": 1},
  {"product_name": "Lenovo ThinkBook Core i5", "description": "Laptop: Core i5 gen 11th, RAM 16GB, SSD 512GB, 15.6-inch FHD IPS.", "category": "Laptop", "badge": "SUPER SALE", "price": 548, "new_price": 669, "brand_id": 1},
  {"product_name": "Lenovo Legion Core i7 (RTX 4060)", "description": "Laptop: Core i7 gen 14th, RAM 16GB, 512TB M2 SSD, GPU RTX 4060 8G, 15.6-inch high-performance IPS.", "category": "Laptop", "badge": "SUPER SALE", "price": 1249, "new_price": 1349, "brand_id": 1},
  {"product_name": "Lenovo Legion Core i7 (RTX 5060)", "description": "Laptop: Core i7 gen 14th, RAM 16GB, 1TB M2 SSD, GPU RTX 5060 8G, 15.6-inch high-performance IPS.", "category": "Laptop", "badge": "SUPER SALE", "price": 1449, "new_price": 1549, "brand_id": 1},

  {"product_name": "AI-Pex (Master Version)", "description": "Advanced AI-powered apex locator plus pulp testing function for precise root canal measurement.", "category": "Apex Locator", "badge": "SUPER SALE", "price": 361, "new_price": 380, "brand_id": 1},
  {"product_name": "AI-Pex", "description": "Advanced AI-powered apex locator plus pulp testing function for precise root canal measurement.", "category": "Apex Locator", "badge": "SUPER SALE", "price": 342, "new_price": 360, "brand_id": 1},
  {"product_name": "Woodpex V", "description": "State-of-the-art apex locator ensuring accurate measurement for precise root canal treatments.", "category": "Apex Locator", "badge": "SUPER SALE", "price": 133, "new_price": 140, "brand_id": 1},
  {"product_name": "Woodpex X", "description": "Compact, accurate apex locator for root canal length measurement and real-time canal monitoring.", "category": "Apex Locator", "badge": "SUPER SALE", "price": 180.5, "new_price": 190, "brand_id": 1},
  {"product_name": "Mini-Pex", "description": "Portable, precise apex locator designed for accurate root canal measurement (student kit).", "category": "Student Kit", "badge": "SUPER SALE", "price": 99, "new_price": 140, "brand_id": 1},

  {"product_name": "Tampered Glass Trolley", "description": "Mobile organized cart with 360° mobility, secure storage, and cable management; includes 2 free trays.", "category": "Trolley", "badge": "SUPER SALE", "price": 266, "new_price": 280, "brand_id": 1},
  {"product_name": "NW-A01", "description": "Mobile monitor cart with 360° mobility, secure storage, and cable management.", "category": "Trolley", "badge": "SUPER SALE", "price": 152, "new_price": 160, "brand_id": 1},
  {"product_name": "Trolley 3-Drawer", "description": "Mobile organized cart with 360° mobility and secure storage; includes 2 free trays.", "category": "Trolley", "badge": "SUPER SALE", "price": 285, "new_price": 300, "brand_id": 1},
  {"product_name": "Trolley T3-4", "description": "Multi-layer mobile dental trolley with 3-tier storage, lockable wheels, medical-grade structure.", "category": "Trolley", "badge": "SUPER SALE", "price": 123.5, "new_price": 130, "brand_id": 1},
  {"product_name": "Woodpecker Trolley", "description": "Mobile organized cart designed to support Woodpecker devices with 360° mobility.", "category": "Trolley", "badge": "SUPER SALE", "price": 199.5, "new_price": 210, "brand_id": 1},
  {"product_name": "Trolley T3-2", "description": "Multi-layer mobile dental trolley with 3-tier design, lockable wheels, medical-grade structure.", "category": "Trolley", "badge": "SUPER SALE", "price": 95, "new_price": 100, "brand_id": 1},
  {"product_name": "Trolley NW-A03", "description": "Compact multi-drawer dental trolley with lockable wheels and flexible storage layout.", "category": "Trolley", "badge": "SUPER SALE", "price": 161.5, "new_price": 170, "brand_id": 1},

  {"product_name": "Instrument Cart 66x44mm", "description": "Mobile organized cart with 360° mobility and secure storage.", "category": "Trolley", "badge": "SUPER SALE", "price": 55.1, "new_price": 58, "brand_id": 1},
  {"product_name": "Instrument Cart 600mm", "description": "Mobile organized cart with 360° mobility and secure storage.", "category": "Trolley", "badge": "SUPER SALE", "price": 75.05, "new_price": 79, "brand_id": 1},
  {"product_name": "Instrument Cart 60x40mm", "description": "Mobile organized cart with 360° mobility and secure storage.", "category": "Trolley", "badge": "SUPER SALE", "price": 49.4, "new_price": 52, "brand_id": 1},
  {"product_name": "Instrument Cart 500mm", "description": "Mobile organized cart with 360° mobility and secure storage.", "category": "Trolley", "badge": "SUPER SALE", "price": 69.35, "new_price": 73, "brand_id": 1},
  {"product_name": "Instrument Cart 30x40mm (2-tier)", "description": "Mobile organized cart with 360° mobility and secure storage.", "category": "Trolley", "badge": "SUPER SALE", "price": 36.1, "new_price": 38, "brand_id": 1},
  {"product_name": "Instrument Cart 40x60mm (3-tier)", "description": "Mobile organized cart with 360° mobility and secure storage.", "category": "Trolley", "badge": "SUPER SALE", "price": 56.05, "new_price": 59, "brand_id": 1},
  {"product_name": "Instrument Cart 30x40mm (3-tier)", "description": "Mobile organized cart with 360° mobility and secure storage.", "category": "Trolley", "badge": "SUPER SALE", "price": 45.6, "new_price": 48, "brand_id": 1},

  {"product_name": "KP iSee Pro", "description": "Premium surgical dental microscope with German SCHOTT optics, inclinable binocular tube, and integrated 4K imaging.", "category": "Surgical Microscope", "badge": "SUPER SALE", "price": 10600, "new_price": None, "brand_id": 1},
  {"product_name": "KP Cam Pro (Metal)", "description": "High-definition 1080P intraoral camera with one-click capture, true color reproduction, and 10 LED illumination.", "category": "Intraoral Camera", "badge": "SUPER SALE", "price": 940.5, "new_price": 990, "brand_id": 1},
  {"product_name": "KP Cam Pro (Plastic)", "description": "High-definition 1080P intraoral camera with one-click capture, true color reproduction, and 10 LED illumination.", "category": "Intraoral Camera", "badge": "SUPER SALE", "price": 750.5, "new_price": 790, "brand_id": 1},

  {"product_name": "WJ-45TL (1:5)", "description": "Contra-angle handpiece, gear ratio 1:5.", "category": "Contra-angles", "badge": "SUPER SALE", "price": 220, "new_price": None, "brand_id": 1},
  {"product_name": "WJ-45L (1:4.2)", "description": "Contra-angle handpiece, gear ratio 1:4.2.", "category": "Contra-angles", "badge": "SUPER SALE", "price": 365, "new_price": None, "brand_id": 1},
  {"product_name": "WJ-SG45CL (1:3)", "description": "Contra-angle handpiece, gear ratio 1:3.", "category": "Contra-angles", "badge": "SUPER SALE", "price": 340, "new_price": None, "brand_id": 1},
  {"product_name": "WP-1L (20:1)", "description": "Contra-angle for implant motor, 20:1 reduction ratio, German bearing option available.", "category": "Contra-angles for implant Motor", "badge": "SUPER SALE", "price": 360, "new_price": None, "brand_id": 1},
  {"product_name": "CA161 (6:1)", "description": "Contra-angle for endo motor, 6:1 reduction ratio.", "category": "Contra-angles Endo Motor", "badge": "SUPER SALE", "price": 169, "new_price": None, "brand_id": 1},
  {"product_name": "CA001-G", "description": "Contra-angle for endo motor.", "category": "Contra-angles Endo Motor", "badge": "SUPER SALE", "price": 98, "new_price": None, "brand_id": 1},
  {"product_name": "Contra-angle Endo Motor (Woodpecker Bearing)", "description": "Contra-angle for endo motor with Woodpecker bearing option.", "category": "Contra-angles Endo Motor", "badge": "SUPER SALE", "price": 210, "new_price": None, "brand_id": 1},

  {"product_name": "AI Ray Pro Set Core i5", "description": "AI Ray Pro set including laptop (Core i5), H2 sensor, radiation apron, trolley, mouse and mouse pad.", "category": "X Ray sensor Package", "badge": "SUPER SALE", "price": 2250, "new_price": 2514, "brand_id": 1},
  {"product_name": "AI Ray Pro Set Core i3", "description": "AI Ray Pro set including laptop (Core i3), H2 sensor, radiation apron, trolley, mouse and mouse pad.", "category": "X Ray sensor Package", "badge": "SUPER SALE", "price": 2150, "new_price": 2374, "brand_id": 1},
  {"product_name": "AI Ray Set Core i5", "description": "AI Ray set including laptop (Core i5), H2 sensor, radiation apron, trolley, mouse and mouse pad.", "category": "X Ray sensor Package", "badge": "SUPER SALE", "price": 2100, "new_price": 2354, "brand_id": 1},
  {"product_name": "AI Ray Set Core i3", "description": "AI Ray set including laptop (Core i3), H2 sensor, radiation apron, trolley, mouse and mouse pad.", "category": "X Ray sensor Package", "badge": "SUPER SALE", "price": 1990, "new_price": 2214, "brand_id": 1},
  {"product_name": "Smart Ray Pro Set Core i5 (Woodpecker)", "description": "Smart Ray Pro set including laptop (Core i5), H2 sensor, radiation apron, trolley, mouse and mouse pad.", "category": "X Ray sensor Package", "badge": "SUPER SALE", "price": 2030, "new_price": 2194, "brand_id": 1},
  {"product_name": "Smart Ray Pro Set Core i3 (Woodpecker)", "description": "Smart Ray Pro set including laptop (Core i3), H2 sensor, radiation apron, trolley, mouse and mouse pad.", "category": "X Ray sensor Package", "badge": "SUPER SALE", "price": 1930, "new_price": 2054, "brand_id": 1},
  {"product_name": "Smart Ray Pro Set Core i5", "description": "Smart Ray Pro set including laptop (Core i5), H2 sensor, radiation apron, trolley, mouse and mouse pad.", "category": "X Ray sensor Package", "badge": "SUPER SALE", "price": 1990, "new_price": 2154, "brand_id": 1},
  {"product_name": "Smart Ray Pro Set Core i3", "description": "Smart Ray Pro set including laptop (Core i3), H2 sensor, radiation apron, trolley, mouse and mouse pad.", "category": "X Ray sensor Package", "badge": "SUPER SALE", "price": 1890, "new_price": 2024, "brand_id": 1}
]


def main() -> None:
    db = SessionLocal()
    try:
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
        brands_by_position = [brand_by_name[name] for name in BRANDS]

        category_by_name = {}

        for item in PRODUCTS:
            existing = db.query(Product).filter(Product.product_name == item["product_name"]).first()
            if existing:
                print(f"Product '{item['product_name']}' already exists (id={existing.id}), skipping.")
                continue

            category_id = None
            category_name = item.get("category")
            if category_name:
                category = category_by_name.get(category_name)
                if category is None:
                    category = db.query(Category).filter(Category.category_name == category_name).first()
                    if category:
                        print(f"Category '{category_name}' already exists (id={category.id}), skipping.")
                    else:
                        category = Category(category_name=category_name)
                        db.add(category)
                        db.flush()
                        print(f"Created category '{category_name}' (id={category.id}).")
                    category_by_name[category_name] = category
                category_id = category.id

            product = Product(
                product_name=item["product_name"],
                description=item.get("description"),
                price=item["price"],
                old_price=item.get("new_price"),
                brand_id=brands_by_position[item["brand_id"] - 1].id,
                category_id=category_id,
                badge=item.get("badge"),
            )
            db.add(product)
            db.flush()
            print(f"Created product '{product.product_name}' (id={product.id}).")

        db.commit()
        print("Done.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
