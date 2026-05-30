import io
import requests
from flask import Flask, jsonify, request, send_file, render_template_string
from flask_cors import CORS
from datetime import datetime, timedelta
from pypdf import PdfWriter, PdfReader

CHURCHSUITE_ACCOUNT = "gracechurchcambridge"
CLIENT_ID = "1tkg9epdqaoyf8kh9q3g"
CLIENT_SECRET = "cs_oauth2_1b39773c-dccf-461b-8d43-6b9c4e18006f"
TOKEN_URL = "https://login.churchsuite.com/oauth2/token"

app = Flask(__name__)
CORS(app, resources={r"/api/import-plan": {"origins": "*"}})

# In-memory store: identifier -> {songs: [...], date: "YYYY-MM-DD"}
_plan_cache = {}


def get_access_token():
    data = {
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": "full_access",
    }
    response = requests.post(TOKEN_URL, data=data)
    response.raise_for_status()
    return response.json()["access_token"]


def auth_headers(token):
    return {"Authorization": "Bearer %s" % token}


def get_next_sunday_minus_one():
    today = datetime.today()
    days_ahead = 6 - today.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    days_ahead -= 1
    return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")


# ── API routes ────────────────────────────────────────────────────────────────

@app.route("/api/plans")
def api_plans():
    token = get_access_token()
    params = {"q": "Sunday", "starts_after": get_next_sunday_minus_one()}
    r = requests.get(
        "https://api.churchsuite.com/v2/planning/plans",
        headers=auth_headers(token),
        params=params,
    )
    r.raise_for_status()
    plans = r.json()["data"]
    # Attach cached status so the UI knows which plans have PDF data
    for p in plans:
        p["has_pdfs"] = p["identifier"] in _plan_cache
    return jsonify(plans)


@app.route("/api/import-plan", methods=["POST"])
def import_plan():
    """Receive plan data POSTed from the bookmarklet."""
    body = request.get_json()
    identifier = body.get("identifier")
    songs = body.get("songs", [])
    date = body.get("date", "")
    app.logger.info("import-plan: identifier=%r date=%r songs=%d", identifier, date, len(songs))
    if not identifier:
        return "Missing identifier", 400
    _plan_cache[identifier] = {"songs": songs, "date": date}
    return jsonify({"ok": True, "redirect": "/plan/%s" % identifier})


@app.route("/api/plan/<identifier>/songs")
def api_plan_songs(identifier):
    entry = _plan_cache.get(identifier)
    if entry is None:
        return jsonify({"error": "not_imported"}), 404
    date = entry.get("date", "")
    app.logger.info("songs endpoint: identifier=%r date=%r", identifier, date)
    return jsonify({"songs": entry["songs"], "date": date})


@app.route("/api/proxy/pdf")
def proxy_pdf():
    url = request.args.get("url")
    if not url:
        return "Missing url", 400
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return send_file(io.BytesIO(r.content), mimetype="application/pdf", as_attachment=False)


@app.route("/api/combine", methods=["POST"])
def combine_pdfs():
    body = request.get_json()
    urls = body.get("urls", [])
    date = body.get("date", "")
    app.logger.info("combine: date=%r urls=%d", date, len(urls))
    if not urls:
        return "No URLs provided", 400

    writer = PdfWriter()
    for url in urls:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        for page in PdfReader(io.BytesIO(r.content)).pages:
            writer.add_page(page)

    filename = ("service-%s.pdf" % date) if date else "service.pdf"

    buf = io.BytesIO()
    writer.write(buf)
    buf.seek(0)
    return send_file(buf, mimetype="application/pdf", as_attachment=True, download_name=filename)


# ── frontend ──────────────────────────────────────────────────────────────────

BOOKMARKLET_JS = """(function(){
  var el=document.querySelector('[x-data]');
  var plan=el&&el._x_dataStack&&el._x_dataStack[0]&&el._x_dataStack[0].store&&el._x_dataStack[0].store.plan;
  if(!plan||!plan.items){alert('Not a ChurchSuite plan page, or page not fully loaded yet.');return;}
  var identifier=plan.identifier;
  var dt=plan.dateTimeStart;var date=dt?JSON.stringify(dt).slice(1,11):'';
  var songs=plan.items.filter(function(i){return i.song;}).map(function(i){
    return {name:i.name,pdfs:Object.values(i.userFiles||{}).filter(function(f){return f.extension==='pdf';}).map(function(f){return {name:f.name,url:f.url};})};
  });
  fetch('http://127.0.0.1:5000/api/import-plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({identifier:identifier,date:date,songs:songs})})
  .then(function(r){return r.json();})
  .then(function(d){window.location='http://127.0.0.1:5000'+d.redirect;})
  .catch(function(e){alert('Error sending to Service Tools: '+e.message);});
})();"""

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Service Tools</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f5f5f7; color: #1d1d1f; min-height: 100vh; }

  header { background: #1d1d1f; color: #fff; padding: 16px 24px;
           display: flex; align-items: center; gap: 16px; }
  header h1 { font-size: 1.2rem; font-weight: 600; }
  #breadcrumb { font-size: 0.9rem; color: #aaa; }
  #breadcrumb a { color: #aaa; text-decoration: none; cursor: pointer; }
  #breadcrumb a:hover { color: #fff; }

  .container { max-width: 900px; margin: 0 auto; padding: 32px 24px 100px;
               transition: margin-right .3s ease; }
  .container.preview-open { margin-right: 50%; max-width: none; }

  .card { background: #fff; border-radius: 12px; padding: 20px 24px;
          margin-bottom: 12px; box-shadow: 0 1px 4px rgba(0,0,0,.08);
          cursor: pointer; transition: box-shadow .15s; display: flex;
          align-items: center; justify-content: space-between; }
  .card:hover { box-shadow: 0 4px 16px rgba(0,0,0,.12); }
  .card-title { font-size: 1rem; font-weight: 600; }
  .card-sub { font-size: 0.85rem; color: #666; margin-top: 4px; }
  .badge { font-size: 0.75rem; padding: 4px 10px; border-radius: 20px; font-weight: 500; }
  .badge-ready { background: #d1fae5; color: #065f46; }
  .badge-pending { background: #fef3c7; color: #92400e; }

  /* setup banner */
  .setup-banner { background: #fff; border-radius: 12px; padding: 24px;
                  margin-bottom: 24px; box-shadow: 0 1px 4px rgba(0,0,0,.08); }
  .setup-banner h2 { font-size: 1rem; font-weight: 600; margin-bottom: 12px; }
  .setup-banner p { font-size: 0.9rem; color: #444; margin-bottom: 16px; line-height: 1.5; }
  .bookmarklet-btn { display: inline-block; background: #007aff; color: #fff;
                     padding: 10px 18px; border-radius: 8px; text-decoration: none;
                     font-weight: 600; font-size: 0.9rem; cursor: grab; }
  .bookmarklet-btn:hover { background: #0060df; }
  .setup-steps { font-size: 0.85rem; color: #555; margin-top: 12px;
                 padding-left: 20px; line-height: 2; }

  /* plan pending state */
  .pending-box { background: #fff; border-radius: 12px; padding: 32px 24px;
                 text-align: center; box-shadow: 0 1px 4px rgba(0,0,0,.08); }
  .pending-box h2 { font-size: 1.1rem; margin-bottom: 12px; }
  .pending-box p { color: #555; font-size: 0.9rem; margin-bottom: 20px; line-height: 1.6; }
  .open-btn { display: inline-block; background: #1d1d1f; color: #fff;
              padding: 10px 20px; border-radius: 8px; text-decoration: none;
              font-weight: 600; font-size: 0.9rem; margin-right: 8px; }
  .open-btn:hover { background: #333; }

  /* song list */
  .song { background: #fff; border-radius: 12px; margin-bottom: 16px;
          box-shadow: 0 1px 4px rgba(0,0,0,.08); overflow: hidden; }
  .song-header { display: flex; align-items: center; gap: 12px;
                 padding: 16px 20px; background: #fafafa;
                 border-bottom: 1px solid #e8e8e8; }
  .song-num { font-size: 0.8rem; color: #999; min-width: 20px; }
  .song-name { font-weight: 600; flex: 1; }
  .pdf-list { padding: 12px 20px; display: flex; flex-direction: column; gap: 8px; }
  .pdf-row { display: flex; align-items: center; gap: 10px; }
  .pdf-row input[type=checkbox] { width: 18px; height: 18px; cursor: pointer; accent-color: #007aff; }
  .pdf-link { font-size: 0.9rem; color: #007aff; cursor: pointer; flex: 1;
              text-decoration: underline; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .no-pdf { font-size: 0.85rem; color: #999; padding: 12px 20px; }

  /* PDF preview panel */
  #preview-panel { position: fixed; top: 0; right: -50%; width: 48%; height: 100vh;
                   background: #fff; box-shadow: -4px 0 20px rgba(0,0,0,.15);
                   transition: right .3s ease; z-index: 100; display: flex; flex-direction: column; }
  #preview-panel.open { right: 0; }
  #preview-header { padding: 16px 20px; display: flex; align-items: center;
                    border-bottom: 1px solid #e8e8e8; gap: 12px; }
  #preview-title { flex: 1; font-weight: 600; font-size: 0.95rem;
                   white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  #preview-close { background: none; border: none; font-size: 1.4rem;
                   cursor: pointer; color: #666; line-height: 1; }
  #preview-frame { flex: 1; border: none; }

  /* bottom bar */
  #action-bar { position: fixed; bottom: 0; left: 0; right: 0;
                background: #fff; border-top: 1px solid #e8e8e8;
                padding: 14px 24px; display: flex; align-items: center;
                gap: 16px; transform: translateY(100%);
                transition: transform .25s, right .3s ease; }
  #action-bar.visible { transform: translateY(0); }
  #action-bar.preview-open { right: 50%; }
  #selected-count { font-size: 0.9rem; color: #666; }
  #download-btn { margin-left: auto; background: #007aff; color: #fff;
                  border: none; padding: 10px 22px; border-radius: 8px;
                  font-size: 0.95rem; font-weight: 600; cursor: pointer; }
  #download-btn:hover { background: #0060df; }
  #download-btn:disabled { background: #aaa; cursor: default; }

  .spinner { display: inline-block; width: 16px; height: 16px;
             border: 2px solid rgba(255,255,255,.4); border-top-color: #fff;
             border-radius: 50%; animation: spin .7s linear infinite; vertical-align: middle; }
  @keyframes spin { to { transform: rotate(360deg); } }
  #dl-status { font-size: 0.85rem; color: #666; }
</style>
</head>
<body>

<header>
  <div>
    <h1>Service Tools</h1>
    <div id="breadcrumb"></div>
  </div>
</header>

<div class="container" id="main"></div>

<div id="preview-panel">
  <div id="preview-header">
    <span id="preview-title"></span>
    <button id="preview-close" onclick="closePreview()">&#x2715;</button>
  </div>
  <iframe id="preview-frame" src=""></iframe>
</div>

<div id="action-bar">
  <span id="selected-count"></span>
  <span id="dl-status"></span>
  <button id="download-btn" onclick="downloadCombined()">Download Combined PDF</button>
</div>

<script>
const ACCOUNT = "__ACCOUNT__";
let selectedPdfs = [];

// ── plan list ─────────────────────────────────────────────────────────────────
async function loadPlans() {
  const main = document.getElementById('main');
  main.innerHTML = '<p style="color:#999">Loading plans…</p>';
  setBreadcrumb([]);
  selectedPdfs = [];
  updateActionBar();

  const res = await fetch('/api/plans');
  const plans = await res.json();

  if (!plans.length) { main.innerHTML = '<p>No upcoming plans found.</p>'; return; }

  const bookmarkletHref = __BOOKMARKLET_HREF__;

  main.innerHTML = `
    <div class="setup-banner">
      <h2>First-time setup</h2>
      <p>Drag this button to your bookmarks bar. When you open a ChurchSuite plan page, click it to send the PDF data to this app.</p>
      <a class="bookmarklet-btn" href="${bookmarkletHref}">&#128278; Import Plan PDFs</a>
      <ol class="setup-steps">
        <li>Drag the button above to your bookmarks bar</li>
        <li>Click a plan below — it will open in a new tab</li>
        <li>Once loaded, click the bookmarklet — you'll be brought back here</li>
      </ol>
    </div>` +
    plans.map(p => `
      <div class="card" onclick="openPlan('${p.id}', '${esc(p.identifier)}', ${JSON.stringify(p.name).replace(/"/g,'&quot;')}, ${p.has_pdfs}, '${esc(p.date || '')}')">
        <div>
          <div class="card-title">${esc(p.name)}</div>
          <div class="card-sub">${esc(p.date || '')}</div>
        </div>
        <span class="badge ${p.has_pdfs ? 'badge-ready' : 'badge-pending'}">${p.has_pdfs ? 'Ready' : 'Needs import'}</span>
      </div>`).join('');
}

function openPlan(planId, identifier, planName, hasPdfs, date) {
  currentDate = date || '';
  if (hasPdfs) {
    showPlan(identifier, planName);
  } else {
    showPendingState(identifier, planName);
  }
}

// ── pending state: prompt user to use bookmarklet ────────────────────────────
function showPendingState(identifier, planName) {
  const main = document.getElementById('main');
  const csUrl = `https://${ACCOUNT}.churchsuite.com/-/plans/${identifier}`;
  setBreadcrumb([{label: 'Plans', action: loadPlans}, {label: planName}]);
  main.innerHTML = `
    <div class="pending-box">
      <h2>${esc(planName)}</h2>
      <p>Open the ChurchSuite plan page, then click the <strong>Import Plan PDFs</strong> bookmarklet.<br>
         You'll be brought back here with all the PDFs loaded.</p>
      <a class="open-btn" href="${csUrl}" target="_blank">Open in ChurchSuite &#8599;</a>
    </div>`;

  // Poll until data arrives
  const poll = setInterval(async () => {
    const r = await fetch('/api/plan/' + identifier + '/songs');
    if (r.ok) {
      clearInterval(poll);
      showPlan(identifier, planName);
    }
  }, 1500);
}

// ── song list ─────────────────────────────────────────────────────────────────
async function showPlan(identifier, planName) {
  currentIdentifier = identifier;
  const main = document.getElementById('main');
  main.innerHTML = '<p style="color:#999">Loading songs…</p>';
  setBreadcrumb([{label: 'Plans', action: loadPlans}, {label: planName}]);
  selectedPdfs = [];
  updateActionBar();

  const res = await fetch('/api/plan/' + identifier + '/songs');
  if (!res.ok) { showPendingState(identifier, planName); return; }
  const data = await res.json();
  const songs = data.songs;
  if (!currentDate && data.date) currentDate = data.date;

  if (!songs.length) { main.innerHTML = '<p>No songs found in this plan.</p>'; return; }

  main.innerHTML = songs.map((s, i) => {
    const pdfRows = s.pdfs && s.pdfs.length
      ? s.pdfs.map((p, pi) => `
          <div class="pdf-row">
            <input type="checkbox" id="cb_${i}_${pi}"
              data-name="${esc(p.name)}" data-url="${esc(p.url)}" data-order="${i * 100 + pi}"
              onchange="onCheckboxChange()">
            <span class="pdf-link" onclick="previewPdf('${esc(p.url)}', '${esc(p.name)}')">${esc(p.name)}</span>
          </div>`).join('')
      : `<div class="no-pdf">No PDFs attached</div>`;

    return `
      <div class="song">
        <div class="song-header">
          <span class="song-num">${i + 1}</span>
          <span class="song-name">${esc(s.name)}</span>
        </div>
        <div class="pdf-list">${pdfRows}</div>
      </div>`;
  }).join('');
}

// ── plan page route ───────────────────────────────────────────────────────────
// Handles /plan/<identifier> by loading the plan directly
let currentIdentifier = '';
let currentDate = '';
function routeFromUrl() {
  const m = window.location.pathname.match(/^\\/plan\\/([^/]+)/);
  if (m) {
    currentIdentifier = m[1];
    showPlan(currentIdentifier, 'Plan');
  } else {
    loadPlans();
  }
}

// ── checkboxes / action bar ───────────────────────────────────────────────────
function onCheckboxChange() {
  const checked = [...document.querySelectorAll('input[type=checkbox]:checked')];
  checked.sort((a, b) => +a.dataset.order - +b.dataset.order);
  selectedPdfs = checked.map(cb => ({name: cb.dataset.name, url: cb.dataset.url}));
  updateActionBar();
}

function updateActionBar() {
  const bar = document.getElementById('action-bar');
  const count = document.getElementById('selected-count');
  if (selectedPdfs.length) {
    bar.classList.add('visible');
    count.textContent = selectedPdfs.length + ' PDF' + (selectedPdfs.length > 1 ? 's' : '') + ' selected';
  } else {
    bar.classList.remove('visible');
  }
  document.getElementById('dl-status').textContent = '';
}

// ── PDF preview ───────────────────────────────────────────────────────────────
function previewPdf(url, name) {
  document.getElementById('preview-title').textContent = name;
  document.getElementById('preview-frame').src = '/api/proxy/pdf?url=' + encodeURIComponent(url);
  document.getElementById('preview-panel').classList.add('open');
  document.getElementById('main').classList.add('preview-open');
  document.getElementById('action-bar').classList.add('preview-open');
}

function closePreview() {
  document.getElementById('preview-panel').classList.remove('open');
  document.getElementById('preview-frame').src = '';
  document.getElementById('main').classList.remove('preview-open');
  document.getElementById('action-bar').classList.remove('preview-open');
}

// ── download ──────────────────────────────────────────────────────────────────
async function downloadCombined() {
  const btn = document.getElementById('download-btn');
  const status = document.getElementById('dl-status');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Combining…';
  status.textContent = '';

  try {
    const res = await fetch('/api/combine', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({urls: selectedPdfs.map(p => p.url), date: currentDate}),
    });
    if (!res.ok) throw new Error(await res.text());
    const disposition = res.headers.get('Content-Disposition') || '';
    const filenamePart = disposition.split('filename=')[1] || '';
    const filename = filenamePart ? filenamePart.split(';')[0].trim().split('"').join('') : 'service.pdf';
    const blob = await res.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    status.textContent = 'Downloaded!';
  } catch (e) {
    status.textContent = 'Error: ' + e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Download Combined PDF';
  }
}

// ── helpers ───────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function setBreadcrumb(parts) {
  const bc = document.getElementById('breadcrumb');
  if (!parts.length) { bc.textContent = ''; return; }
  bc.innerHTML = parts.map((p, i) =>
    i < parts.length - 1
      ? `<a onclick="${p.action.name}()">${esc(p.label)}</a> › `
      : esc(p.label)
  ).join('');
}

routeFromUrl();
</script>
</body>
</html>"""


@app.route("/")
@app.route("/plan/<identifier>")
def index(identifier=None):
    import json
    bookmarklet_href = "javascript:" + BOOKMARKLET_JS.replace("\n", " ")
    # Inject values via simple token replacement, avoiding % formatting issues with CSS
    html = HTML.replace("__ACCOUNT__", CHURCHSUITE_ACCOUNT)
    html = html.replace("__BOOKMARKLET_HREF__", json.dumps(bookmarklet_href))
    return html


if __name__ == "__main__":
    app.run(debug=True, port=5000)
