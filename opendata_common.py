"""Κοινές βοηθητικές συναρτήσεις για τους loaders (data.gov.gr & opendata.thessaloniki.gr)."""
import os
import re
import json
import requests
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, unquote
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    # Χρησιμοποιεί τα πιστοποιητικά του λειτουργικού (Windows/macOS) αντί για του certifi.
    # Χωρίς αυτό, σε κάποια μηχανήματα πέφτει CERTIFICATE_VERIFY_FAILED.
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

USER_AGENT = "OpenThessaloniki-loader/1.0"

# Formats που είναι υπηρεσίες/σελίδες και όχι αρχεία προς λήψη
SKIP_FORMATS = {"HTML", "WEB", "WMS", "WFS", "ESRI REST", "API"}

MANIFEST_NAME = "_manifest.json"

GREEK_TO_LATIN = {
    "α": "a", "β": "b", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i", "θ": "th",
    "ι": "i", "κ": "k", "λ": "l", "μ": "m", "ν": "n", "ξ": "x", "ο": "o", "π": "p",
    "ρ": "r", "σ": "s", "ς": "s", "τ": "t", "υ": "y", "φ": "f", "χ": "ch", "ψ": "ps",
    "ω": "o", "ά": "a", "έ": "e", "ή": "i", "ί": "i", "ό": "o", "ύ": "y", "ώ": "o",
    "ϊ": "i", "ΐ": "i", "ϋ": "y", "ΰ": "y",
}


def get_resilient_session():
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def slugify(text):
    text = "".join(GREEK_TO_LATIN.get(ch, ch) for ch in text.lower())
    text = re.sub(r"[^a-z0-9\s_-]", "", text).strip()
    return re.sub(r"[-\s_]+", "_", text).strip("_")


def guess_extension(url, fmt):
    ext = fmt.lower() if fmt.isalnum() and len(fmt) <= 7 else "dat"
    basename = os.path.basename(unquote(urlparse(url).path))
    if "." in basename:
        url_ext = basename.rsplit(".", 1)[-1].lower()
        if url_ext.isalnum() and len(url_ext) <= 5:
            ext = url_ext
    return ext


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def download_file(session, url, filepath):
    """Κατεβάζει σε προσωρινό .part και μετονομάζει μόνο αν ολοκληρωθεί, ώστε
    μισοκατεβασμένα αρχεία να μη μετράνε ως 'έτοιμα' στην επόμενη εκτέλεση."""
    tmp_path = filepath.with_name(filepath.name + ".part")
    try:
        with session.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            expects_html = filepath.suffix in (".html", ".htm")
            content_type = r.headers.get("Content-Type", "").lower()
            # Αν περιμένουμε αρχείο δεδομένων και πάρουμε ιστοσελίδα, δεν είναι το dataset
            if "text/html" in content_type and not expects_html:
                raise ValueError("ο server επέστρεψε HTML σελίδα αντί για αρχείο")
            with open(tmp_path, "wb") as f:
                for i, chunk in enumerate(r.iter_content(chunk_size=65536)):
                    # Κάποια σπασμένα links επιστρέφουν HTML χωρίς σωστό Content-Type
                    if i == 0 and not expects_html and chunk.lstrip()[:15].lower().startswith((b"<!doctype html", b"<html")):
                        raise ValueError("ο server επέστρεψε HTML σελίδα αντί για αρχείο")
                    if chunk:
                        f.write(chunk)
        os.replace(tmp_path, filepath)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def sync_dataset(session, dataset_dir, title, resources):
    """resources: λίστα από dict με κλειδιά filename, url, version.

    Κάθε φάκελος dataset κρατά ένα _manifest.json με την έκδοση (π.χ. last_modified)
    κάθε αρχείου, ώστε να ξανακατεβαίνει μόνο ό,τι άλλαξε στην πηγή.
    Επιστρέφει τη λίστα σφαλμάτων.
    """
    manifest_path = dataset_dir / MANIFEST_NAME
    manifest = load_json(manifest_path, {})
    errors = []

    for res in resources:
        filepath = dataset_dir / res["filename"]
        previous = manifest.get(res["filename"], {})
        up_to_date = (
            filepath.exists()
            and previous.get("url") == res["url"]
            and previous.get("version") == res["version"]
        )
        if up_to_date:
            continue

        try:
            print(f"  [Λήψη] {title[:40]} -> {res['filename']}")
            download_file(session, res["url"], filepath)
            manifest[res["filename"]] = {
                "url": res["url"],
                "version": res["version"],
                "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        except Exception as e:
            print(f"  [Χ] Σφάλμα στο {res['url']}: {e}")
            errors.append({"dataset": title, "url": res["url"], "error": str(e)})

    write_json(manifest_path, manifest)
    return errors


def report_stale_dirs(datasets_dir, current_dirs):
    """Τυπώνει φακέλους που δεν αντιστοιχούν πλέον σε dataset της πηγής (δεν τους σβήνει)."""
    stale = sorted(d.name for d in datasets_dir.iterdir() if d.is_dir() and d.name not in current_dirs)
    if stale:
        print(f"\n[!] {len(stale)} φάκελοι δεν αντιστοιχούν πλέον σε dataset (μπορείς να τους σβήσεις):")
        for name in stale:
            print(f"    {name}")
    return stale
