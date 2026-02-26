"""
TruthSeeker Web Server
======================
Run with:  python server.py
Then open: http://localhost:5173
"""
import itertools
import json
import os
import random
import re
import sys
import time
import webbrowser
from datetime import datetime
from threading import Thread
from urllib.parse import urlparse, unquote

import requests as req_lib
from flask import Flask, Response, jsonify, render_template, request, send_file

app = Flask(__name__)

# ── User-Agent pool ───────────────────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36 OPR/105.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 Chrome/118.0.0.0 Safari/537.36",
]

GATE_RE = re.compile(
    r'agree|i agree|verify|accept|confirm|continue|certify|proceed|enter', re.I)

HTML_CTYPES = ('text/html', 'text/plain', 'text/xml', 'application/xhtml+xml')


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def _log(msg: str) -> str:
    return _sse({"type": "log", "msg": msg})


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/parse", methods=["POST"])
def parse():
    url = (request.json or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    parsed = urlparse(url)
    fname  = unquote(parsed.path.rstrip("/").split("/")[-1])
    if "." not in fname:
        return jsonify({"error": "Filename has no extension"}), 400

    name_no_ext = fname.rsplit(".", 1)[0]
    m = re.match(r"^(.*?)(\d+)$", name_no_ext)
    if not m:
        return jsonify({"error": "No numeric suffix found in filename"}), 400

    prefix    = m.group(1)
    num_str   = m.group(2)
    num_width = len(num_str)
    base_num  = int(num_str)
    base_path = "/".join(parsed.path.split("/")[:-1]) + "/"
    base_url  = f"{parsed.scheme}://{parsed.netloc}{base_path}"

    return jsonify({
        "prefix":    prefix,
        "num_width": num_width,
        "base_num":  base_num,
        "next_num":  base_num + 1,
        "base_url":  base_url,
    })


@app.route("/scan")
def scan():
    base_url  = request.args.get("base_url", "")
    prefix    = request.args.get("prefix", "")
    num_width = int(request.args.get("num_width", 8))
    base_num  = int(request.args.get("base_num", 0))
    start_num = int(request.args.get("start_num", base_num))
    max_n     = int(request.args.get("max_n", 500))
    max_mis   = int(request.args.get("max_mis", 50))
    delay_min = float(request.args.get("delay_min", 3))
    delay_max = float(request.args.get("delay_max", 7))
    exts      = request.args.getlist("exts") or [".mp4", ".mov"]
    cookie_str = request.args.get("cookie", "").strip()

    def generate():
        session     = req_lib.Session()
        # Ensure the scanner starts with the exact same UA as Playwright
        session.headers.update({"User-Agent": USER_AGENTS[0]})
        agent_cycle = itertools.cycle(USER_AGENTS)

        # ── Authentication ────────────────────────────────────────────────────
        if cookie_str:
            domain = urlparse(base_url).netloc
            count  = 0
            for pair in cookie_str.split(";"):
                pair = pair.strip()
                if "=" in pair:
                    n, _, v = pair.partition("=")
                    session.cookies.set(n.strip(), v.strip(), domain=domain)
                    count += 1
            yield _log(f"✔ {count} browser cookie(s) injected.")
        else:
            # Playwright — auto-click the age gate
            seed_url = (f"{base_url}{prefix}"
                        f"{str(base_num).zfill(num_width)}{exts[0]}")
            yield _log("🌐 Opening browser to handle age gate…")
            try:
                from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=False)
                    ctx     = browser.new_context(user_agent=USER_AGENTS[0])
                    page    = ctx.new_page()
                    page.goto(seed_url, timeout=20_000)
                    page.wait_for_load_state("domcontentloaded", timeout=10_000)

                    # ── Try to click the gate button ──────────────────────────
                    clicked = False

                    # ── DOJ Specific Two-Step Verification ────────────────────
                    if "justice.gov" in seed_url:
                        try:
                            # Step 1: "I am not a robot"
                            bot_btn = page.wait_for_selector('input.usa-button[value="I am not a robot"]', timeout=5000)
                            if bot_btn:
                                bot_btn.click()
                                yield _log("✔ Bot verification clicked.")
                            
                            # Step 2: "Are you 18 years of age or older?" -> Yes
                            age_btn = page.wait_for_selector('button#age-button-yes', timeout=5000)
                            if age_btn:
                                age_btn.click()
                                yield _log("✔ Age confirmation clicked.")
                                try:
                                    # DOJ redirects to the file, which may never reach networkidle
                                    page.wait_for_load_state("networkidle", timeout=5000)
                                except Exception:
                                    pass 
                                clicked = True
                        except Exception as e:
                            yield _log(f"⚠ DOJ-specific gate failed: {e}")

                    # Generic Fallback
                    if not clicked:
                        for selector in [
                            "button", "input[type=submit]", "input[type=button]",
                            "a.btn", "a[href]", "[role=button]"
                        ]:
                            if clicked:
                                break
                            try:
                                for el in page.locator(selector).all():
                                    label = (el.get_attribute("value") or
                                             el.inner_text(timeout=500) or "").strip()
                                    if GATE_RE.search(label):
                                        el.click(timeout=3000)
                                        page.wait_for_load_state("networkidle",
                                                                 timeout=8000)
                                        clicked = True
                                        break
                            except Exception:
                                pass

                    if clicked:
                        yield _log("✔ Age gate button clicked.")
                    else:
                        yield _log("⚠ No gate button found — using page cookies.")

                    # Faithfully mirror all cookie metadata
                    for c in ctx.cookies():
                        session.cookies.set(
                            c["name"], c["value"],
                            domain=c.get("domain"),
                            path=c.get("path")
                        )
                    browser.close()
                    yield _log("🔒 Session cookies (with domain/path) imported. Browser closed.")

            except ImportError:
                yield _log("⚠ Playwright not installed — scanning without gate bypass.")
            except Exception as ex:
                yield _log(f"⚠ Browser error: {ex} — continuing anyway.")

        # ── Range preview ─────────────────────────────────────────────────────
        first_url = (f"{base_url}{prefix}"
                     f"{str(start_num).zfill(num_width)}{exts[0]}")
        last_url  = (f"{base_url}{prefix}"
                     f"{str(start_num + max_n - 1).zfill(num_width)}{exts[-1]}")
        yield _sse({"type": "range", "first": first_url, "last": last_url})

        # ── Scan loop ─────────────────────────────────────────────────────────
        found       = 0
        consecutive = 0

        for i in range(max_n):
            cur_num     = start_num + i
            num_str_pad = str(cur_num).zfill(num_width)
            hit_this    = False

            for ext in exts:
                url   = f"{base_url}{prefix}{num_str_pad}{ext}"
                agent = next(agent_cycle)
                session.headers.update({"User-Agent": agent})

                wait = round(random.uniform(delay_min, delay_max), 1)
                yield _sse({
                    "type": "checking",
                    "url":   url,
                    "wait":  wait,
                    "found": found,
                    "i":     i,
                    "total": max_n,
                })

                for _ in range(int(wait * 10)):
                    time.sleep(0.1)

                try:
                    r  = session.head(url, timeout=10, allow_redirects=True)
                    ct = r.headers.get("Content-Type", "").lower().split(";")[0].strip()
                    cl = r.headers.get("Content-Length", None)

                    if r.status_code in (200, 206):
                        if ct in HTML_CTYPES:
                            pass  # soft-404
                        elif cl is not None and int(cl) < 5_000:
                            pass  # too small
                        else:
                            found    += 1
                            hit_this  = True
                            yield _sse({"type": "hit", "url": url, "found": found})
                except Exception:
                    pass

            if hit_this:
                consecutive = 0
            else:
                consecutive += 1
                if consecutive >= max_mis:
                    yield _sse({"type": "stopped",
                                "reason": f"{max_mis} consecutive misses"})
                    break

        yield _sse({"type": "done", "found": found})

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/export/pdf", methods=["POST"])
def export_pdf():
    data      = request.json or {}
    urls      = data.get("urls", [])
    base_info = data.get("base", "")

    try:
        from fpdf import FPDF
    except ImportError:
        return jsonify({"error": "fpdf2 not installed"}), 500

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(233, 69, 96)
    pdf.cell(0, 12, "TruthSeeker — Valid Video URLs",
             new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 7,
             f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  "
             f"Base: {base_info}  |  {len(urls)} URL(s) — 404s excluded",
             new_x="LMARGIN", new_y="NEXT")

    pdf.ln(2)
    pdf.set_draw_color(233, 69, 96)
    pdf.set_line_width(0.5)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(5)

    pdf.set_font("Courier", "", 8)
    pdf.set_text_color(0, 80, 180)
    for url in urls:
        pdf.cell(0, 6, url, new_x="LMARGIN", new_y="NEXT", link=url)

    path = os.path.join(os.path.dirname(__file__),
                        f"TruthSeeker_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf")
    pdf.output(path)
    return send_file(path, as_attachment=True)


# ── Launch ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = 5173
    print(f"\n  TruthSeeker running at  http://localhost:{port}\n")
    Thread(target=lambda: (time.sleep(1.2),
                           webbrowser.open(f"http://localhost:{port}")),
           daemon=True).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
