"""
Small inline HTML page returned directly to the browser for links a human
opens by clicking an email (email verification) rather than calling from
code - so a plain JSON body isn't useful there.
"""
from app.config import settings

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{title}</title>
<style>
  body {{
    margin: 0;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background-color: #f4f5f7;
    font-family: 'Segoe UI', Helvetica, Arial, sans-serif;
  }}
  .card {{
    background-color: #ffffff;
    border-radius: 12px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
    padding: 40px 32px;
    max-width: 380px;
    text-align: center;
  }}
  .icon {{ font-size: 40px; margin-bottom: 12px; }}
  h1 {{ margin: 0 0 8px; font-size: 20px; color: #1a1a1a; }}
  p {{ margin: 0; font-size: 14px; line-height: 1.5; color: #6b6b6b; }}
  .footnote {{ margin-top: 20px; font-size: 12px; color: #a0a0a0; }}
  button {{
    margin-top: 20px;
    border: none;
    background-color: #0f6e5f;
    color: #ffffff;
    font-size: 14px;
    font-weight: 600;
    padding: 10px 20px;
    border-radius: 8px;
    cursor: pointer;
  }}
</style>
</head>
<body>
  <div class="card">
    <div class="icon">{icon}</div>
    <h1>{heading}</h1>
    <p>{message}</p>
    <p class="footnote">This window will close automatically...</p>
    <button onclick="window.close()">Close window</button>
  </div>
  <script>
    setTimeout(function () {{ window.close(); }}, {close_after_ms});
  </script>
</body>
</html>
"""


def render_status_page(
    *, success: bool, heading: str, message: str, close_after_ms: int = 4000
) -> str:
    return _PAGE.format(
        title=settings.APP_NAME,
        icon="✅" if success else "⚠️",
        heading=heading,
        message=message,
        close_after_ms=close_after_ms,
    )
