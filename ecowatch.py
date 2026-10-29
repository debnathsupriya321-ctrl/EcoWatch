#!/usr/bin/env python3
"""Small standard-library RSS monitor. Run from this directory."""
import argparse
import email.utils
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
SOURCES_PATH = BASE / "sources.json"
STATE_PATH = BASE / "state.json"
LAST_REPORT_PATH = BASE / "last_report.json"
REPORT_PATH = BASE / "report.md"
DASHBOARD_PATH = BASE / "index.html"

CATEGORY_RULES = {
    "pollution": ("pollution", "pollutant", "contaminat", "toxic", "chemical", "spill", "emission", "air quality", "water quality", "smog", "sewage", "pesticide"),
    "biodiversity": ("biodiversity", "wildlife", "species", "habitat", "extinction", "endangered", "ecosystem", "conservation", "forest", "coral", "invasive"),
    "waste management": ("waste", "recycl", "plastic", "landfill", "dumping", "litter", "compost", "garbage", "trash", "circular economy"),
}
HIGH_WORDS = ("toxic", "spill", "leak", "contaminat", "die-off", "mass death", "extinction", "illegal dumping", "hazardous", "emergency", "outbreak")
MEDIUM_WORDS = ("pollution", "pollutant", "chemical", "pesticide", "endangered", "habitat loss", "plastic", "waste", "fire", "decline", "threat")


def clean_text(value):
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value or "")
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _child_text(element, names):
    wanted = set(names)
    for child in element:
        local = child.tag.rsplit("}", 1)[-1].lower()
        if local in wanted:
            if local == "encoded":
                return "".join(child.itertext())
            return "".join(child.itertext())
    return ""


def parse_feed(xml_bytes, source_name):
    root = ET.fromstring(xml_bytes)
    entries = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() in ("item", "entry")]
    result = []
    for entry in entries:
        title = clean_text(_child_text(entry, ("title",))) or "Untitled"
        link = ""
        for node in entry:
            if node.tag.rsplit("}", 1)[-1].lower() == "link":
                link = node.attrib.get("href", "") or clean_text("".join(node.itertext()))
                if node.attrib.get("rel", "alternate") == "alternate" or not link:
                    break
        description = clean_text(_child_text(entry, ("description", "summary", "content", "encoded")))
        date = clean_text(_child_text(entry, ("pubdate", "published", "updated", "date"))) or "Date not provided"
        guid = clean_text(_child_text(entry, ("guid", "id")))
        stable_id = guid or link or (title + "|" + date)
        result.append({"id": stable_id, "source": source_name, "title": title, "date": date, "link": link, "description": description})
    return result


def classify_item(item):
    text = (item.get("title", "") + " " + item.get("description", "")).lower()
    scores = {category: sum(1 for word in words if word in text) for category, words in CATEGORY_RULES.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else "environment"


def assess_concern(item):
    text = (item.get("title", "") + " " + item.get("description", "")).lower()
    matched_high = [word for word in HIGH_WORDS if word in text]
    matched_medium = [word for word in MEDIUM_WORDS if word in text]
    matched = list(dict.fromkeys(matched_high + matched_medium))
    if matched_high:
        severity = "high"
    elif matched_medium:
        severity = "medium"
    else:
        severity = "low"
    return {"severity": severity, "matched_keywords": matched}


def extractive_summary(description, max_sentences=2):
    text = clean_text(description)
    if not text:
        return "No description supplied by the feed."
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(sentences[:max_sentences]).strip()


def only_new_items(items, state_path=STATE_PATH):
    try:
        state = json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {"seen": []}
    seen = set(state.get("seen", []))
    new_items = [item for item in items if item.get("id") not in seen]
    seen.update(item.get("id") for item in items if item.get("id"))
    Path(state_path).write_text(json.dumps({"seen": sorted(seen), "updated_at": datetime.now(timezone.utc).isoformat()}, indent=2) + "\n", encoding="utf-8")
    return new_items


def reset_state(state_path=STATE_PATH):
    Path(state_path).unlink(missing_ok=True)


def update_last_report(last_report_path, new_items, source_status, checked_at, new_count):
    path = Path(last_report_path)
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        previous = {"items": [], "source_status": []}
    if new_items:
        previous["items"] = new_items
    if not previous.get("items"):
        previous["items"] = []
    previous["source_status"] = source_status
    previous["last_checked"] = checked_at
    previous["new_this_run"] = new_count
    path.write_text(json.dumps(previous, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return previous


def recommendations(category, severity):
    category_actions = {
        "pollution": ["Consider checking local air- or water-quality monitoring data relevant to the location.", "Consider verifying details with the cited primary source before sharing or acting."],
        "biodiversity": ["Consider checking the cited study or conservation authority for location and species details.", "Consider contacting a relevant local conservation NGO or wildlife authority for context."],
        "waste management": ["Consider checking local waste collection, recycling, or hazardous-waste guidance.", "Consider verifying the reported site or policy with the responsible municipal authority."],
        "environment": ["Consider opening the cited source and checking its primary evidence and location.", "Consider seeking context from a relevant local environmental authority or NGO."],
    }
    actions = list(category_actions.get(category, category_actions["environment"]))
    if severity == "high":
        actions.insert(0, "If this appears to describe an immediate local hazard, consider contacting the appropriate local emergency or environmental authority; this report does not verify an incident.")
    elif severity == "medium":
        actions.append("Consider monitoring the primary source for updates; the concern flag is a keyword-based prompt, not a verified assessment.")
    return actions


def enrich(items):
    for item in items:
        item["category"] = classify_item(item)
        concern = assess_concern(item)
        item["severity"] = concern["severity"]
        item["matched_keywords"] = concern["matched_keywords"]
        item["summary"] = extractive_summary(item.get("description", ""))
        item["actions"] = recommendations(item["category"], item["severity"])
    return items


def fetch_source(source, timeout=15):
    request = urllib.request.Request(source["url"], headers={"User-Agent": "EcoWatch/1.0 (standard-library RSS monitor)", "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(5_000_000)
    return parse_feed(data, source["name"])


def _esc(value):
    return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def write_outputs(items, source_status, generated_at, last_checked, new_this_run):
    lines = ["# EcoWatch — Environmental Monitoring Report", "", f"Generated from latest run with new items: {generated_at}", f"Last checked: {last_checked}, new this run: {new_this_run}", "", "> Keyword-based monitoring only. Concerns are prompts for verification, not verified environmental findings.", "", f"Items in latest non-empty report: **{len(items)}**", "", "## Source checks", ""]
    for status in source_status:
        lines.append(f"- **{status['name']}** — {status['status']}{(': ' + status['detail']) if status.get('detail') else ''}")
    categories = ("pollution", "biodiversity", "waste management", "environment")
    lines.extend(["", "## Developments from latest run with new items", ""])
    if not items:
        lines.append("No run with new items has been recorded yet.")
    for category in categories:
        category_items = [item for item in items if item.get("category") == category]
        if not category_items:
            continue
        lines.extend([f"### {category.title()}", ""])
        for item in category_items:
            link = f" [{_esc(item['link'])}]({item['link']})" if item.get("link") else " (no link provided)"
            lines.extend([f"#### {_esc(item['title'])}", "", f"- **Source:** {_esc(item['source'])}", f"- **Date:** {_esc(item['date'])}", f"- **Link:**{link}", f"- **Potential concern:** {_esc(item['severity'])}" + (f" — keyword matches: {_esc(', '.join(item['matched_keywords']))}" if item["matched_keywords"] else " — no severity keywords matched"), "", f"**Extractive summary:** {item['summary']}", "", "**Suggested follow-up:**"])
            lines.extend(f"- {action}" for action in item["actions"])
            lines.append("")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    data = json.dumps({"items": items, "source_status": source_status, "generated_at": generated_at, "last_checked": last_checked, "new_this_run": new_this_run}, ensure_ascii=False).replace("</", "<\\/")
    page = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>EcoWatch dashboard</title><style>
:root{color-scheme:light;--ink:#173326;--muted:#587064;--green:#246447;--paper:#f4f8f3;--line:#d5e3d6}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 system-ui,sans-serif}header{background:#173f2e;color:white;padding:1.4rem max(1rem,calc((100vw - 960px)/2))}header p{margin:.4rem 0}main{max-width:960px;margin:1.5rem auto;padding:0 1rem}.notice,.card,.sources{background:white;border:1px solid var(--line);border-radius:12px;padding:1rem;margin-bottom:1rem}.notice{border-left:5px solid #d49b32}.meta{color:var(--muted);font-size:.93rem}.pill{display:inline-block;background:#e8f2e8;color:#20543a;padding:.15rem .6rem;border-radius:99px;margin:.15rem}.severity{font-weight:700;text-transform:uppercase}.severity.high{color:#a42c20}.severity.medium{color:#855900}.severity.low{color:#38664c}a{color:#176344;overflow-wrap:anywhere}h1{margin:0}h2{margin-top:1.6rem}ul{padding-left:1.3rem}footer{color:var(--muted);font-size:.9rem}@media(min-width:760px){.grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem}}
</style></head><body><header><h1>EcoWatch</h1><p>Environmental news monitoring · generated <span id="time"></span></p></header><main><div class="notice">Potential concerns and follow-ups are rule-based suggestions, not verified findings.</div><div id="app"></div><footer>Static offline dashboard. Source links require an internet connection.</footer></main><script>const DATA=__DATA__;document.getElementById('time').textContent=`Last checked: ${DATA.last_checked}, new this run: ${DATA.new_this_run}`;const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const link=(u)=>/^https?:\/\//i.test(u||'')?`<a href="${esc(u)}" rel="noopener noreferrer">${esc(u)}</a>`:'No link provided';let h=`<section class="sources"><h2>Source checks</h2><ul>${DATA.source_status.map(s=>`<li><b>${esc(s.name)}</b> — ${esc(s.status)}${s.detail?`: ${esc(s.detail)}`:''}</li>`).join('')}</ul></section><h2>Latest run with new items (${DATA.items.length})</h2>`;const categories=['pollution','biodiversity','waste management','environment'];if(!DATA.items.length)h+='<p>No run with new items has been recorded yet.</p>';for(const cat of categories){const items=DATA.items.filter(i=>i.category===cat);if(!items.length)continue;h+=`<h2>${esc(cat)}</h2><div class="grid">`;h+=items.map(i=>`<article class="card"><h3>${esc(i.title)}</h3><p class="meta"><b>${esc(i.source)}</b> · ${esc(i.date)}<br>${link(i.link)}</p><span class="pill">${esc(i.category)}</span> <span class="severity ${esc(i.severity)}">Potential concern: ${esc(i.severity)}</span><p>${esc(i.summary)}</p><p><b>Matched keywords:</b> ${esc(i.matched_keywords.join(', ')||'none')}</p><b>Suggested follow-up</b><ul>${i.actions.map(a=>`<li>${esc(a)}</li>`).join('')}</ul></article>`).join('');h+='</div>';}document.getElementById('app').innerHTML=h;</script></body></html>'''.replace("__DATA__", data)
    DASHBOARD_PATH.write_text(page, encoding="utf-8")


def run(sources_path=SOURCES_PATH, state_path=STATE_PATH, timeout=15, last_report_path=LAST_REPORT_PATH):
    global STATE_PATH
    STATE_PATH = Path(state_path)
    sources = json.loads(Path(sources_path).read_text(encoding="utf-8"))
    all_items, statuses = [], []
    for source in sources:
        try:
            items = fetch_source(source, timeout=timeout)
            all_items.extend(items)
            statuses.append({"name": source["name"], "status": f"OK ({len(items)} items)"})
        except Exception as exc:
            statuses.append({"name": source.get("name", source.get("url", "Unknown source")), "status": "FAILED", "detail": f"{type(exc).__name__}: {str(exc)[:180]}"})
    new_items = only_new_items(all_items, STATE_PATH)
    enriched = enrich(new_items)
    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    last_report = update_last_report(last_report_path, enriched, statuses, checked_at, len(enriched))
    retained_items = last_report["items"]
    generated_at = last_report.get("generated_at", checked_at)
    if enriched:
        generated_at = checked_at
        last_report["generated_at"] = generated_at
        Path(last_report_path).write_text(json.dumps(last_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_outputs(retained_items, last_report["source_status"], generated_at, last_report["last_checked"], last_report["new_this_run"])
    return {"items_fetched": len(all_items), "new_items": len(enriched), "source_status": statuses, "items": retained_items, "new_items_this_run": enriched}


def main():
    parser = argparse.ArgumentParser(description="Fetch public environmental RSS feeds and report new items.")
    parser.add_argument("--timeout", type=int, default=15, help="per-source network timeout in seconds")
    parser.add_argument("--reset", action="store_true", help="clear state.json before fetching, so all current feed items are new")
    args = parser.parse_args()
    if args.reset:
        reset_state(STATE_PATH)
        print("Cleared state.json.")
    result = run(timeout=args.timeout)
    print(f"Fetched {result['items_fetched']} items; {result['new_items']} new items.")
    for source in result["source_status"]:
        print(f"{source['name']}: {source['status']}{(': ' + source['detail']) if source.get('detail') else ''}")
    print(f"Wrote {REPORT_PATH} and {DASHBOARD_PATH}.")

if __name__ == "__main__":
    main()
