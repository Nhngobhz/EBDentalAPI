"""
Decode a KHQR code and print the fields that matter for configuring payments.

Its main job: pull the **payee routing fields** out of a static KHQR (the one your
bank's app shows on its "my QR / receive money" screen) so they can go into `.env`
as BAKONG_ACCOUNT_ID / BAKONG_ACCOUNT_INFORMATION / BAKONG_ACQUIRING_BANK. None of
them are printed on the QR card itself - they're encoded inside the payload, in EMV
tag 29 (individual) or 30 (merchant), sub-fields 00/01/02.

Usage - either from a saved photo/screenshot of the QR:

    python scripts/decode_khqr.py "C:/path/to/aba-qr.png"

or from the raw payload string, if you already decoded it with a phone/online
QR reader:

    python scripts/decode_khqr.py "00020101021129..."

Image mode needs `pip install pyzbar` (a dev-only convenience, deliberately NOT
in requirements.txt - the app itself never reads QR codes, it only writes them).
Payload mode is pure stdlib and always works.

See app/services/khqr.py for the writing side / the tag meanings.
"""
import sys
from datetime import datetime
from pathlib import Path

# Tag 99 sub-fields, both epoch milliseconds.
_TIMESTAMPS = {"00": "created", "01": "expires"}

# Human-readable names for the top-level EMV tags a KHQR actually uses.
_TAG_NAMES = {
    "00": "Payload format indicator",
    "01": "Point of initiation (11=static, 12=dynamic)",
    "29": "Merchant account info (individual)",
    "30": "Merchant account info (merchant)",
    "52": "Merchant category code",
    "53": "Transaction currency (840=USD, 116=KHR)",
    "54": "Transaction amount",
    "58": "Country code",
    "59": "Merchant name",
    "60": "Merchant city",
    "62": "Additional data",
    "99": "KHQR timestamp",
    "63": "CRC",
}


def parse_tlv(payload: str) -> list[tuple[str, str]]:
    """Splits an EMV payload into (tag, value) pairs. Every field is
    2-digit tag + 2-digit length + value, so this is a straight walk."""
    fields = []
    i = 0
    while i + 4 <= len(payload):
        tag = payload[i:i + 2]
        try:
            length = int(payload[i + 2:i + 4])
        except ValueError:
            print(f"  ! malformed length at offset {i} - stopping", file=sys.stderr)
            break
        value = payload[i + 4:i + 4 + length]
        fields.append((tag, value))
        i += 4 + length
    return fields


def crc16_ccitt(data: str) -> str:
    crc = 0xFFFF
    for byte in data.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return f"{crc:04X}"


def read_qr_image(path: Path) -> str:
    try:
        from PIL import Image
        from pyzbar.pyzbar import decode
    except ImportError:
        sys.exit(
            "Reading a QR image needs Pillow + pyzbar:\n"
            "    pip install pyzbar\n"
            "Or decode the QR with any phone/online reader and pass the text instead."
        )
    results = decode(Image.open(path))
    if not results:
        sys.exit(
            f"No QR code found in {path}.\n"
            "Try a sharper/larger crop of just the QR square."
        )
    return results[0].data.decode("utf-8")


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)

    arg = sys.argv[1]
    candidate = Path(arg)
    payload = read_qr_image(candidate) if candidate.exists() else arg

    print(f"\nPayload ({len(payload)} chars):\n{payload}\n")

    fields = parse_tlv(payload)
    # Tag 29/30 sub-fields 00/01/02 map one-to-one onto the .env settings that
    # app/services/khqr.py builds a spec-shaped KHQR from.
    _SUB_SETTING = {
        "00": "BAKONG_ACCOUNT_ID",
        "01": "BAKONG_ACCOUNT_INFORMATION",
        "02": "BAKONG_ACQUIRING_BANK",
    }
    payee: dict[str, str] = {}
    for tag, value in fields:
        label = _TAG_NAMES.get(tag, "")
        print(f"  {tag}  {label:<45} {value}")
        # 29/30 nest their own TLVs - that's where the payee routing lives.
        if tag in ("29", "30"):
            for sub_tag, sub_value in parse_tlv(value):
                setting = _SUB_SETTING.get(sub_tag, "")
                marker = f"  <-- {setting}" if setting else ""
                print(f"        {sub_tag}  {'sub-field':<41} {sub_value}{marker}")
                if setting and setting not in payee:
                    payee[setting] = sub_value
        elif tag == "99":
            # sub-00 creation, sub-01 expiry, both epoch ms.
            for sub_tag, sub_value in parse_tlv(value):
                when = _TIMESTAMPS.get(sub_tag, "sub-field")
                readable = ""
                if sub_value.isdigit():
                    stamp = datetime.fromtimestamp(int(sub_value) / 1000)
                    readable = f"  ({stamp:%Y-%m-%d %H:%M:%S})"
                print(f"        {sub_tag}  {when:<41} {sub_value}{readable}")
        elif tag.isdigit() and 26 <= int(tag) <= 51:
            print(f"        {'':<45} (proprietary, not part of the KHQR spec)")

    # CRC covers everything up to and including the "6304" tag+length prefix.
    if len(payload) >= 4:
        expected = crc16_ccitt(payload[:-4])
        actual = payload[-4:]
        print(f"\n  CRC: {actual} ({'valid' if expected == actual else f'INVALID, expected {expected}'})")

    if payee:
        print("\n" + "=" * 60)
        print("Put these in store-api/.env:\n")
        for setting in _SUB_SETTING.values():
            print(f"    {setting}={payee.get(setting, '')}")
        print("=" * 60)
    else:
        print("\nNo account ID found (no tag 29/30 sub-field 00) - is this a KHQR?")


if __name__ == "__main__":
    main()
