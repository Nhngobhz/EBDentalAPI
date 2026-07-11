"""
Small inline HTML pages returned directly to the browser for links a human
opens by clicking an email (email verification, password reset) rather than
calling from code - so a plain JSON body isn't useful there.
"""
from app.config import settings

_ACCENT_START = "#095799"
_ACCENT_END = "#45d3f7"
_ACCENT_GRADIENT = f"linear-gradient(135deg, {_ACCENT_START}, {_ACCENT_END})"

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
    background: {accent_gradient};
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
        accent_gradient=_ACCENT_GRADIENT,
    )


_RESET_FORM_PAGE = """<!DOCTYPE html>
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
    width: 100%;
    text-align: center;
    box-sizing: border-box;
  }}
  .icon {{ font-size: 40px; margin-bottom: 12px; }}
  h1 {{ margin: 0 0 8px; font-size: 20px; color: #1a1a1a; }}
  p {{ margin: 0 0 24px; font-size: 14px; line-height: 1.5; color: #6b6b6b; }}
  label {{
    display: block;
    text-align: left;
    font-size: 13px;
    font-weight: 600;
    color: #4a4a4a;
    margin: 16px 0 6px;
  }}
  input[type="password"] {{
    width: 100%;
    padding: 10px 12px;
    font-size: 14px;
    border: 1px solid #d8dbe0;
    border-radius: 8px;
    box-sizing: border-box;
  }}
  input[type="password"]:focus {{
    outline: none;
    border-color: {accent_start};
  }}
  .error {{
    margin-top: 16px;
    font-size: 13px;
    color: #c0392b;
    display: none;
  }}
  button {{
    margin-top: 24px;
    width: 100%;
    border: none;
    background: {accent_gradient};
    color: #ffffff;
    font-size: 15px;
    font-weight: 600;
    padding: 12px 20px;
    border-radius: 8px;
    cursor: pointer;
  }}
  button:disabled {{ opacity: 0.6; cursor: default; }}
</style>
</head>
<body>
  <div class="card" id="card">
    <div class="icon">🔒</div>
    <h1>Reset your password</h1>
    <p>Choose a new password for your {app_name} account.</p>
    <form id="reset-form">
      <label for="new_password">New password</label>
      <input type="password" id="new_password" minlength="8" maxlength="72" required />
      <label for="confirm_password">Confirm password</label>
      <input type="password" id="confirm_password" minlength="8" maxlength="72" required />
      <p class="error" id="error"></p>
      <button type="submit" id="submit-btn">Reset password</button>
    </form>
  </div>
  <script>
    var form = document.getElementById('reset-form');
    var errorEl = document.getElementById('error');
    var submitBtn = document.getElementById('submit-btn');
    form.addEventListener('submit', function (event) {{
      event.preventDefault();
      var newPassword = document.getElementById('new_password').value;
      var confirmPassword = document.getElementById('confirm_password').value;
      errorEl.style.display = 'none';
      if (newPassword !== confirmPassword) {{
        errorEl.textContent = 'Passwords do not match.';
        errorEl.style.display = 'block';
        return;
      }}
      submitBtn.disabled = true;
      submitBtn.textContent = 'Resetting...';
      fetch({submit_url_json}, {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ token: {token_json}, new_password: newPassword }})
      }})
        .then(function (res) {{
          return res.json().then(function (data) {{ return {{ ok: res.ok, data: data }}; }});
        }})
        .then(function (result) {{
          if (result.ok) {{
            document.getElementById('card').innerHTML =
              '<div class="icon">✅</div><h1>Password reset</h1>' +
              '<p>' + result.data.detail + '</p>';
          }} else {{
            errorEl.textContent = result.data.detail || 'Something went wrong.';
            errorEl.style.display = 'block';
            submitBtn.disabled = false;
            submitBtn.textContent = 'Reset password';
          }}
        }})
        .catch(function () {{
          errorEl.textContent = 'Network error. Please try again.';
          errorEl.style.display = 'block';
          submitBtn.disabled = false;
          submitBtn.textContent = 'Reset password';
        }});
    }});
  </script>
</body>
</html>
"""


def render_reset_password_form(*, token: str, submit_url: str) -> str:
    import json

    return _RESET_FORM_PAGE.format(
        title=settings.APP_NAME,
        app_name=settings.APP_NAME,
        submit_url_json=json.dumps(submit_url),
        token_json=json.dumps(token),
        accent_gradient=_ACCENT_GRADIENT,
        accent_start=_ACCENT_START,
    )
