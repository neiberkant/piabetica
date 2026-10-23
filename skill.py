#!/usr/bin/env python3
"""LibreView Claude Skill — unified glucose data for LLM routing.

Handles both LibreView PDF and LibreLinkUP API:
- Current glucose: real-time from LibreLinkUP API
- History/Trends: unified DB with both PDF and API data
- Sync: automated data ingestion from both sources

Six behaviors (invoked via command-line or imported as library):
  install   — Prompt for credentials, write .env, run initial sync
  get_current — Latest reading from LibreLinkUP API
  get_history — Recent readings from unified DB
  get_summary — Daily metrics from unified DB
  get_weekly  — Weekly trends from unified DB
  sync_now    — Trigger full sync from both sources
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Diabetes Specialist System Prompt ────────────────────────────────────────

DIABETOLOGIST_PROMPT = """Eres un endocrinólogo y diabetólogo de primer nivel con más de 20 años de experiencia clínica. Tu especialidad es el manejo integral de la diabetes mellitus tipo 1 y tipo 2, con conocimientos profundos en:

- **Nutrición:** Conteo de carbohidratos, índice glucémico, planes alimentarios (bajo en carbohidratos, mediterráneo, DASH), distribución de macronutrientes, horarios de ingesta, corrección de hipoglucemias con snack rule (15-15 rule), relación insulina-carbohidratos (ICR) y factores de corrección.
- **Ejercicio físico:** Tipos de ejercicio (aeróbico, anaeróbico, HIIT, resistencia), su impacto en la glucemia (hipoglucemia inmediata vs. hiperglucemia por adrenalina/cortisol), timing ideal con respecto a insulina y comidas, protocolos de manejo pre/post-ejercicio.
- **Farmacología:** Insulinas de acción rápida, prolongada, mixtas (aspart, lisina, glulisina, glargina, detemir, degludec), bombas de insulina, sistemas de monitoreo continuo (Libre, Dexcom), sensores CGM, algoritmos de ajuste de dosis.
- **Interpretación CGM:** Time in Range (TIR), Time in Target (70-180 mg/dL), Time Below Range (<70 mg/dL), Time Above Range (>180 mg/dL), HbA1c estimada (GMI), coeficiente de variación, patrones circadianos, tendencias y dirección del glucosa.
- **Educación en autocuidado:** Técnica de inyección, rotación de sitios, manejo de hipoglucemia e hiperglucemia, cetosis/ Ketoacidosis, Sick Day Rules, manejo de estrés y sueño.
- **Prevención de complicaciones:** Macrovasculares (cardíacas, cerebrovasculares, periféricas) y microvasculares (retinopatía, nefropatía, neuropatía).

## Reglas fundamentales

1. **PROHIBIDO INVENTAR:** Todos los valores de glucosa, carbohidratos, insulina y notas deben provenir EXCLUSIVAMENTE de los datos devueltos por las funciones de la skill. Nunca inventes un valor que no esté en la base de datos.
2. Si no hay datos para una fecha o métrica, dilo explícitamente: "No hay datos disponibles para esa fecha."
3. Cruza siempre los datos del CGM (glucosa en tiempo real) con los datos del PDF (carbohidratos, insulina, notas) para dar una interpretación completa.
4. Cuando analices tendencias, referencia el TIR (Time in Range), GMI (Glucose Management Indicator), y patrones observables.
5. Sugiere ajustes basándote en patrones concretos: hipoglucemias repetidas a la misma hora, hiperglucemias post-prandiales, etc.
6. Sé claro, conciso y orientado a la acción. El paciente necesita saber qué hacer, no solo qué pasó.
7. Si un valor está fuera de rango, indica si es urgente (<54 mg/dL o >250 mg/dL con cetosis).
8. Siempre considera la relación entre carbohidratos ingeridos, insulina administrada, y la respuesta glucémica observada."""

# ── Library API ──────────────────────────────────────────────────────────────


def get_current_glucose() -> dict:
    """Return current glucose from API (with DB fallback)."""
    from queries import get_current_glucose as _get
    return _get()


def get_glucose_history(days: int = 7) -> dict:
    """Return recent glucose history with TIR/GMI."""
    from queries import get_glucose_history as _get
    return _get(days)


def get_daily_summary(date_str: str | None = None) -> dict:
    """Return daily metrics for a specific date."""
    from queries import get_daily_summary as _get
    return _get(date_str)


def get_weekly_trend(weeks: int = 4) -> dict:
    """Return weekly aggregated trend."""
    from queries import get_weekly_trend as _get
    return _get(weeks)


def get_sensor_status() -> dict:
    """Return sensor coverage and status info."""
    from queries import get_sensor_status as _get
    return _get()


def sync_all() -> dict:
    """Run sync from both LibreLinkUP API and LibreView PDF.

    Returns summary of records inserted per source.
    """
    results = {}

    # LibreLinkUP API sync
    try:
        from librelinkup.sync import LibreLinkSync
        from config import get_api_config
        cfg = get_api_config()
        if cfg.get("email") and cfg.get("password"):
            syncer = LibreLinkSync(
                email=cfg["email"],
                password=cfg["password"],
                region=cfg.get("region", "latam"),
            )
            syncer.run()
            results["librelinkup"] = "success"
            logger.info("LibreLinkUP sync complete")
        else:
            results["librelinkup"] = "skipped (no credentials)"
    except Exception as exc:
        results["librelinkup"] = f"error: {exc}"
        logger.error(f"LibreLinkUP sync failed: {exc}")

    # LibreView PDF sync
    try:
        import sync as libsync
        libsync._do_sync()
        results["libreview"] = "success"
        logger.info("LibreView PDF sync complete")
    except Exception as exc:
        results["libreview"] = f"error: {exc}"
        logger.error(f"LibreView PDF sync failed: {exc}")

    return results


def get_system_prompt() -> str:
    """Return the diabetes specialist system prompt for LLM routing."""
    return DIABETOLOGIST_PROMPT


def install_credentials() -> dict:
    """Interactive credential setup."""
    from config import configure_api, configure_pdf

    print("\n=== LibreView Patient Portal ===")
    email = input("Email: ").strip() or "tu_email_libreview@hotmail.com"
    password = input("Password: ").strip()
    if not password:
        password = input("Password (repeat): ").strip()

    print("\n=== LibreLinkUP Caregiver API ===")
    email_up = input("Email: ").strip() or "tu_email_libreviewup@gmail.com"
    password_up = input("Password: ").strip()
    if not password_up:
        password_up = input("Password (repeat): ").strip()
    region = input("Region [latam]: ").strip() or "latam"

    configure_pdf(email, password)
    configure_api(email_up, password_up, region)

    return {
        "libreview_configured": True,
        "librelinkup_configured": True,
        "message": "Credentials saved. Run 'sync_now' to fetch data.",
    }


# ── PDF Download Functions ───────────────────────────────────────────────────


def _get_pdf_credentials(email=None, password=None):
    """Load credentials from config or use provided values."""
    from config import get_pdf_config
    if email and password:
        return {"email": email, "password": password}
    return get_pdf_config()


def _get_pdf_dir():
    """Get the PDF download directory."""
    from config import get_libreview_pdf_dir
    return get_libreview_pdf_dir()


def download_pdf_mcp(page_id: int, email: str | None = None, password: str | None = None) -> dict:
    """Download LibreView glucose report PDF via MCP Chrome DevTools.

    Uses dynamic UID discovery via snapshot — never hardcodes UIDs.
    Must be called from an environment with MCP Chrome DevTools available.

    Args:
        page_id: The MCP pageId (tab identifier) of the browser tab to use.
        email: Optional email override (defaults to config).
        password: Optional password override (defaults to config).

    Returns:
        dict with status, pdf_path, and message.
    """
    import subprocess
    from pathlib import Path
    from config import is_pdf_configured

    if not is_pdf_configured():
        return {"status": "error", "message": "LibreView credentials not configured. Run 'install' first."}

    creds = _get_pdf_credentials(email, password)
    email = creds["email"]
    password = creds["password"]
    pdf_dir = Path(_get_pdf_dir())
    pdf_dir.mkdir(parents=True, exist_ok=True)
    patient_id = "939ef612-41d9-11ed-8cda-0242ac110005"

    def mcp_call(method: str, params: dict) -> dict:
        """Call an MCP Chrome DevTools tool."""
        try:
            result = subprocess.run(
                ["mcp__plugin_ecc_chrome-devtools__" + method, json.dumps(params)],
                capture_output=True, text=True, timeout=30
            )
            return json.loads(result.stdout) if result.stdout else {}
        except Exception as exc:
            logger.warning(f"MCP call {method} failed: {exc}")
            return {}

    def find_element(snapshot_lines: list[str], text_match: str, element_type: str | None = None) -> str | None:
        """Find a UID in snapshot lines by matching element text."""
        text_match = text_match.lower()
        for line in snapshot_lines:
            if text_match in line.lower():
                parts = line.split()
                for part in parts:
                    if part.startswith("uid="):
                        uid = part.split("=", 1)[1]
                        return uid
        return None

    def parse_snapshot_result(result: dict) -> list[str]:
        """Extract snapshot text lines from MCP result."""
        if isinstance(result, str):
            return result.splitlines()
        if isinstance(result, dict):
            content = result.get("result", result.get("content", ""))
            if isinstance(content, str):
                return content.splitlines()
        return []

    # Step 1: Navigate to LibreView
    try:
        mcp_call("navigate_page", {"pageId": page_id, "type": "url", "url": "https://www.libreview.com/"})
    except Exception:
        return {"status": "error", "message": f"Could not navigate to LibreView (page_id={page_id}). Is Chrome open with MCP extension?"}

    # Step 2-4: Country selector + credentials + login
    try:
        snap = mcp_call("take_snapshot", {"pageId": page_id})
        lines = parse_snapshot_result(snap)

        country_uid = find_element(lines, "argentina")
        if country_uid:
            mcp_call("fill_form", {"pageId": page_id, "elements": [
                {"uid": country_uid, "value": "Argentina"},
                {"uid": find_element(lines, "true") or country_uid, "value": "true"},
            ]})

        email_uid = find_element(lines, "email")
        password_uid = find_element(lines, "contrasena") or find_element(lines, "password")
        if email_uid and password_uid:
            mcp_call("fill_form", {"pageId": page_id, "elements": [
                {"uid": email_uid, "value": email},
                {"uid": password_uid, "value": password},
            ]})

        login_uid = find_element(lines, "iniciar sesion") or find_element(lines, "login") or find_element(lines, "ingresar")
        if login_uid:
            mcp_call("click", {"pageId": page_id, "uid": login_uid})
            mcp_call("wait_for", {"pageId": page_id, "text": ["Glucosa", "dashboard", "Maria", "Acosta"], "timeout": 15000})
        else:
            logger.warning("Login button not found — checking if already logged in")

    except Exception as exc:
        logger.error(f"Login flow failed: {exc}")

    # Step 5: Navigate to reports page
    try:
        mcp_call("navigate_page", {
            "pageId": page_id,
            "type": "url",
            "url": f"https://www.libreview.com/reports/{patient_id}"
        })
        mcp_call("wait_for", {"pageId": page_id, "text": ["Informes", "reports", "Imprimir", "Guardar"], "timeout": 15000})
    except Exception as exc:
        return {"status": "error", "message": f"Could not navigate to reports page: {exc}"}

    # Step 6: Click PDF download button
    try:
        snap = mcp_call("take_snapshot", {"pageId": page_id})
        lines = parse_snapshot_result(snap)

        pdf_btn = (find_element(lines, "imprimir") or
                   find_element(lines, "guardar pdf") or
                   find_element(lines, "download") or
                   find_element(lines, "pdf"))

        if pdf_btn:
            mcp_call("click", {"pageId": page_id, "uid": pdf_btn})
            mcp_call("wait_for", {"pageId": page_id, "text": ["PDF", "Descargando", "Generando"], "timeout": 15000})
        else:
            logger.warning("PDF download button not found in snapshot")
    except Exception as exc:
        return {"status": "error", "message": f"PDF download failed: {exc}"}

    # Step 7: Find the downloaded PDF in Downloads
    import time
    time.sleep(5)

    try:
        import subprocess
        result = subprocess.run(
            ["find", str(Path.home() / "Descargas"), str(Path.home() / "Downloads"),
             "-maxdepth", "2", "-name", "*.pdf",
             "-mmin", "-5", "-exec", "ls", "-t1", "{}", ";"],
            capture_output=True, text=True, timeout=10
        )
        pdfs = [p.strip() for p in result.stdout.strip().split("\n") if p.strip()]
        lv_pdfs = [p for p in pdfs if "Acosta" in p or "Maria" in p or "LV_" in p]

        if lv_pdfs:
            pdf_path = lv_pdfs[0]
            dest = pdf_dir / Path(pdf_path).name
            Path(pdf_path).rename(dest)
            return {
                "status": "success",
                "pdf_path": str(dest),
                "message": f"PDF downloaded to {dest}"
            }
    except Exception as exc:
        logger.warning(f"PDF file discovery failed: {exc}")

    return {"status": "success", "message": "PDF download initiated — check Downloads folder manually"}


def download_pdf_playwright(email: str | None = None, password: str | None = None) -> dict:
    """Download LibreView glucose report PDF via Playwright headless browser.

    No interactive browser needed — runs entirely headless.

    Args:
        email: Optional email override (defaults to config).
        password: Optional password override (defaults to config).

    Returns:
        dict with status, pdf_path, and message.
    """
    from pathlib import Path
    from config import get_pdf_config, is_pdf_configured, get_libreview_pdf_dir
    import threading

    if not is_pdf_configured():
        return {"status": "error", "message": "LibreView credentials not configured. Run 'install' first."}

    creds = get_pdf_config()
    email = email or creds["email"]
    password = password or creds["password"]
    pdf_dir = Path(get_libreview_pdf_dir())
    pdf_dir.mkdir(parents=True, exist_ok=True)
    patient_id = "939ef612-41d9-11ed-8cda-0242ac110005"

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"status": "error", "message": "playwright not installed. Run: pip install playwright && playwright install chromium"}

    result = {"status": "error", "pdf_path": None, "message": ""}
    import time as _time
    start_time = _time.time()

    _log = []  # Event log for diagnostics
    _pdf_found = {"done": False, "path": None}  # Set by on_response callback

    def on_response(response):
        """Detect the S3 PDF response and download it directly."""
        try:
            ct = (response.headers.get("content-type") or "").lower()
            url = response.url
            print(f"[LibreView PDF] RESP: url={url[:100]} ct={ct}")
            if "application/pdf" in ct and "s3" in url.lower():
                print(f"[LibreView PDF] S3 CAPTURED! Downloading directly...")
                try:
                    import urllib.request
                    dest = pdf_dir / f"LibreView_Report_{_time.strftime('%Y%m%d_%H%M%S')}.pdf"
                    urllib.request.urlretrieve(url, str(dest))
                    _pdf_found["path"] = str(dest)
                    _pdf_found["done"] = True
                    print(f"[LibreView PDF] PDF SAVED: {dest}")
                except Exception as e:
                    _log.append(f"S3 download error: {e}")
                    print(f"[LibreView PDF] S3 download error: {e}")
        except Exception:
            _log.append(f"RESP callback error")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            # Step 1: Navigate to LibreView
            page.goto("https://www.libreview.com/", timeout=15000)
            _time.sleep(1)

            # Accept cookie banner if present
            agree = page.query_selector('button:has-text("AGREE")')
            if agree:
                agree.click()
                _time.sleep(1)

            # Step 2: Country/region selector (exact IDs from the working page)
            try:
                page.select_option("#country-select", "Argentina")
                _time.sleep(0.5)
                page.click("button:has-text('Enviar')", timeout=10000)
                _time.sleep(5)
            except Exception:
                pass  # selector already handled or not present

            # Step 3: Fill credentials (uses fixed IDs on LibreView login form)
            try:
                page.fill("#loginForm-email-input", email)
            except Exception:
                result["message"] = "Could not fill email field"
                return result

            try:
                page.fill("#loginForm-password-input", password)
            except Exception:
                result["message"] = "Could not fill password field"
                return result

            # Step 4: Click login
            try:
                page.click('button:has-text("Iniciar sesión")', timeout=10000)
                login_clicked = True
            except Exception:
                try:
                    page.click('button:has-text("Login")', timeout=5000)
                    login_clicked = True
                except Exception:
                    login_clicked = False

            if not login_clicked:
                result["message"] = "Could not find or click login button"
                return result

            _time.sleep(3)

            # Step 5: Navigate to reports
            reports_url = f"https://www.libreview.com/reports/{patient_id}"
            page.goto(reports_url, timeout=15000)
            _time.sleep(3)

            # Step 6: Find the PDF download button (click in Step 7 after listener attached)
            pdf_selectors = [
                'button:has-text("Imprimir")',
                'button:has-text("Guardar PDF")',
                'button:has-text("Print")',
                'button:has-text("Download")',
                'a:has-text("PDF")',
            ]
            pdf_button = None
            for sel in pdf_selectors:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible():
                        pdf_button = btn
                        break
                except Exception:
                    continue

            if pdf_button is None:
                result["message"] = "Could not find PDF download button"
                return result

            # Step 7: Intercept the PDF response from LibreView's server-side generation.
            #
            # The "Imprimir" button submits a form POST to the server. The server
            # generates the PDF and returns it as a document response
            # (Content-Type: application/pdf) from an S3 URL. Playwright's
            # wait_for_download() does NOT catch document responses.
            pdf_button.click()
            page.on("response", on_response)

            # Wait for server-side PDF generation (can take 30-120s).
            # Use page.evaluate with Promise-based setTimeout to yield to the
            # event loop between polls. This is critical: regular
            # time.sleep() blocks the event loop and prevents on_response
            # from ever running.
            max_wait = 180
            deadline = _time.time() + max_wait

            while _time.time() < deadline:
                if _pdf_found["done"]:
                    result["status"] = "success"
                    result["pdf_path"] = _pdf_found["path"]
                    result["message"] = f"PDF downloaded ({_time.time() - start_time:.0f}s)"
                    print(f"[LibreView PDF] Success: {result['pdf_path']}")
                    break
                # Yield to event loop: wait 2s using JS Promise
                page.evaluate("() => new Promise(r => setTimeout(r, 2000))")

            else:
                result["message"] = f"PDF not captured after {max_wait}s — LibreView server response timeout"
                if _log:
                    result["message"] += f". Last events: {'; '.join(_log[-5:])}"

        finally:
            browser.close()


    return result


# ── CLI entry points ─────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="LibreView Claude Skill")
    parser.add_argument(
        "command",
        choices=["install", "get_current", "get_history", "get_summary",
                 "get_weekly", "get_sensor", "sync_now", "status", "prompt",
                 "download_pdf"],
        help="Skill command to execute",
    )
    parser.add_argument("--days", type=int, default=7, help="Days for history/trend")
    parser.add_argument("--date", help="Target date for summary (YYYY-MM-DD)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--method", choices=["mcp", "playwright"],
                        help="Download method: mcp (Chrome DevTools) or playwright (headless)")
    parser.add_argument("--page_id", type=int, default=1,
                        help="MCP pageId for --method mcp (default: 1)")
    parser.add_argument("--email", help="Email override for download_pdf")
    parser.add_argument("--password", help="Password override for download_pdf")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    commands = {
        "install": lambda: install_credentials(),
        "get_current": lambda: get_current_glucose(),
        "get_history": lambda: get_glucose_history(args.days),
        "get_summary": lambda: get_daily_summary(args.date),
        "get_weekly": lambda: get_weekly_trend(args.days),
        "get_sensor": lambda: get_sensor_status(),
        "sync_now": lambda: sync_all(),
        "status": lambda: _get_status_cli(),
        "prompt": lambda: get_system_prompt(),
        "download_pdf": lambda: (
            download_pdf_mcp(args.page_id, args.email, args.password)
            if args.method == "mcp" else
            download_pdf_playwright(args.email, args.password)
            if args.method == "playwright" else
            {"status": "error", "message": "Specify --method mcp or --method playwright"}
        ),
    }

    result = commands[args.command]()

    if args.json or args.command in ("sync_now", "status", "prompt"):
        print(json.dumps(result, indent=2, default=str))
    else:
        _format_human(result)


def _get_status_cli() -> dict:
    """Return configuration and data status."""
    from config import get_status
    from queries import get_glucose_history
    status = get_status()

    # Add data summary
    try:
        hist = get_glucose_history(7)
        status["recent_readings"] = hist.get("days_returned", 0)
    except Exception:
        status["recent_readings"] = 0

    return status


def _format_human(data: dict) -> None:
    """Format output for human consumption (not JSON)."""
    if "value_mg_dl" in data:
        print(f"\nCurrent Glucose: {data['value_mg_dl']} mg/dL")
        if data.get("trend_arrow"):
            print(f"Trend: {data['trend_arrow']}")
        if data.get("minutes_ago") is not None:
            print(f"Timestamp: {data['minutes_ago']} min ago")
        print(f"Source: {data.get('source', 'unknown')}")
    elif "daily_metrics" in data:
        print(f"\n{data['days_returned']} days of history:")
        for day in data["daily_metrics"]:
            avg = day.get("avg_glucose", "N/A")
            tir = day.get("tir_pct", "N/A")
            print(f"  {day['date']}: avg={avg} mg/dL  TIR={tir}%")
    elif "weeks_data" in data:
        print(f"\n{data['weeks_covered']} weeks covered:")
        for wk in data["weeks_data"]:
            print(f"  {wk['week']}: avg={wk.get('avg_glucose', 'N/A')} mg/dL")
    else:
        print(json.dumps(data, indent=2, default=str))


if __name__ == "__main__":
    main()
