import argparse
import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote

import requests

from opendata_common import get_resilient_session, load_json, slugify, write_json

# Ρυθμίσεις (σχετικά με τον φάκελο του script, όχι με το τρέχον directory)
BASE_DIR = Path(__file__).resolve().parent / "geoservices_thessaloniki"
CATALOG_FILE = BASE_DIR / "geoservices_catalog.json"
REPORT_FILE = BASE_DIR / "download_report.json"

WFS_URL = "https://sdi.thessaloniki.gr/geoserver/wfs"
WFS_PAGE_SIZE = 5000
WFS_SMALL_PAGE_SIZE = 500

ARCGIS_BASE = "https://maps.thessaloniki.gr/server/rest/services"
# Κατεβάζουμε μόνο τις ArcGIS υπηρεσίες που αναφέρει ο επίσημος κατάλογος ανοιχτών δεδομένων
OPENDATA_CATALOG_URL = "https://opendata.thessaloniki.gr/data.json"

WFS_NS = {"wfs": "http://www.opengis.net/wfs/2.0"}


def write_geojson(path, features):
    """Γράφει σε προσωρινό αρχείο και μετονομάζει, ώστε μια διακοπή να μην αφήνει μισό GeoJSON."""
    tmp_path = path.with_name(path.name + ".part")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, ensure_ascii=False)
    os.replace(tmp_path, path)


# --- WFS (GeoServer του sdi.thessaloniki.gr) ---

def list_wfs_layers(session):
    resp = session.get(WFS_URL, params={"service": "WFS", "version": "2.0.0", "request": "GetCapabilities"}, timeout=120)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    layers = []
    for ft in root.iter("{http://www.opengis.net/wfs/2.0}FeatureType"):
        layers.append({
            "name": ft.findtext("wfs:Name", namespaces=WFS_NS),
            "title": ft.findtext("wfs:Title", namespaces=WFS_NS) or "",
        })
    return layers


def get_wfs_page(session, name, paging_params):
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": name,
        "outputFormat": "application/json",
        "srsName": "EPSG:4326",
        **paging_params,
    }
    resp = session.get(WFS_URL, params=params, timeout=300)
    if "json" not in resp.headers.get("Content-Type", ""):
        # Το GeoServer επιστρέφει XML ExceptionReport όταν κάτι πάει στραβά
        message = re.search(r"<ows:ExceptionText>(.*?)</ows:ExceptionText>", resp.text, re.S)
        raise ValueError(message.group(1).strip() if message else f"HTTP {resp.status_code}: {resp.text[:300]}")
    resp.raise_for_status()
    return resp.json()


def fetch_wfs_layer(session, name):
    """Κατεβάζει όλα τα features ενός layer σε σελίδες (WFS 2.0 count/startIndex)."""
    features = []
    expected = None
    page_size = WFS_PAGE_SIZE
    while True:
        try:
            page = get_wfs_page(session, name, {"count": page_size, "startIndex": len(features)})
        except requests.exceptions.JSONDecodeError:
            # Σε κάποια layers το GeoServer κόβει τις μεγάλες απαντήσεις, οπότε ξαναδοκιμάζουμε με μικρότερες σελίδες
            if page_size == WFS_SMALL_PAGE_SIZE:
                raise
            page_size = WFS_SMALL_PAGE_SIZE
            continue
        except ValueError as e:
            # Views χωρίς primary key δεν σελιδοποιούνται, οπότε τα παίρνουμε όλα με ένα αίτημα
            if features or "without a primary key" not in str(e):
                raise
            page = get_wfs_page(session, name, {})
            return page.get("features", [])
        expected = page.get("numberMatched", expected)
        features.extend(page.get("features", []))
        if not page.get("features") or len(page["features"]) < page_size:
            break
    if isinstance(expected, int) and expected != len(features):
        raise ValueError(f"αναμένονταν {expected} features, ήρθαν {len(features)}")
    return features


# --- ArcGIS REST (maps.thessaloniki.gr) ---

def list_catalog_arcgis_services(session):
    """Βρίσκει ποιες MapServer υπηρεσίες αναφέρει ο κατάλογος του opendata.thessaloniki.gr."""
    resp = session.get(OPENDATA_CATALOG_URL, timeout=120)
    resp.raise_for_status()
    services = set()
    for ds in resp.json().get("dataset", []):
        for dist in ds.get("distribution", []):
            url = dist.get("downloadURL") or dist.get("accessURL") or ""
            m = re.search(r"maps\.thessaloniki\.gr/server/(?:rest/)?services/(.+?)/MapServer", url)
            if m:
                services.add(unquote(m.group(1)))
    return sorted(services)


def list_arcgis_layers(session, service):
    resp = session.get(f"{ARCGIS_BASE}/{service}/MapServer/layers", params={"f": "json"}, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise ValueError(data["error"].get("message"))
    # Τα Group/Raster layers δεν έχουν features
    return [l for l in data.get("layers", []) if l.get("type") == "Feature Layer" and "Query" in (l.get("capabilities") or "")]


def fetch_arcgis_layer(session, service, layer):
    """Κατεβάζει όλα τα features ενός layer: πρώτα όλα τα objectIds, μετά τα features σε κομμάτια.
    Δουλεύει και σε layers που δεν υποστηρίζουν σελιδοποίηση (resultOffset)."""
    query_url = f"{ARCGIS_BASE}/{service}/MapServer/{layer['id']}/query"
    resp = session.get(query_url, params={"where": "1=1", "returnIdsOnly": "true", "f": "json"}, timeout=120)
    resp.raise_for_status()
    ids_data = resp.json()
    if "error" in ids_data:
        raise ValueError(ids_data["error"].get("message"))
    object_ids = sorted(ids_data.get("objectIds") or [])

    chunk_size = min(layer.get("maxRecordCount") or 1000, 1000)
    features = []
    for i in range(0, len(object_ids), chunk_size):
        chunk = object_ids[i:i + chunk_size]
        # POST γιατί η λίστα με τα ids δεν χωράει πάντα σε URL
        resp = session.post(query_url, data={
            "objectIds": ",".join(map(str, chunk)),
            "outFields": "*",
            "outSR": "4326",
            "f": "geojson",
        }, timeout=300)
        resp.raise_for_status()
        page = resp.json()
        if "error" in page:
            raise ValueError(page["error"].get("message"))
        features.extend(page.get("features", []))
    if len(features) != len(object_ids):
        raise ValueError(f"αναμένονταν {len(object_ids)} features, ήρθαν {len(features)}")
    return features


def download_layer(entry, filepath, fetch, skip_existing, catalog, errors):
    entry["file"] = str(filepath.relative_to(BASE_DIR)).replace("\\", "/")
    catalog.append(entry)
    if skip_existing and filepath.exists():
        # Κρατάμε το πλήθος features από την προηγούμενη εκτέλεση, αλλιώς το μετράμε από το αρχείο
        previous = PREVIOUS_CATALOG.get(entry["layer"], {})
        if "features" in previous:
            entry["features"] = previous["features"]
        else:
            entry["features"] = len(load_json(filepath, {}).get("features", []))
        return
    try:
        print(f"  [Λήψη] {entry['title'][:50]} -> {entry['file']}")
        features = fetch()
        write_geojson(filepath, features)
        entry["features"] = len(features)
    except Exception as e:
        print(f"  [Χ] Σφάλμα στο {entry['layer']}: {e}")
        entry["error"] = str(e)
        errors.append({"source": entry["source"], "layer": entry["layer"], "error": str(e)})


def run_wfs(session, skip_existing, catalog, errors):
    out_dir = BASE_DIR / "wfs_sdi"
    out_dir.mkdir(parents=True, exist_ok=True)
    layers = list_wfs_layers(session)
    print(f"\nWFS ({WFS_URL}): {len(layers)} layers")
    for layer in layers:
        name = layer["name"]
        entry = {"source": "wfs", "service": WFS_URL, "layer": name, "title": layer["title"] or name}
        download_layer(entry, out_dir / f"{slugify(name.replace(':', '_'))}.geojson",
                       lambda: fetch_wfs_layer(session, name), skip_existing, catalog, errors)


def run_arcgis(session, skip_existing, catalog, errors):
    services = list_catalog_arcgis_services(session)
    print(f"\nArcGIS ({ARCGIS_BASE}): {len(services)} υπηρεσίες από τον κατάλογο")
    for service in services:
        out_dir = BASE_DIR / "arcgis_maps" / slugify(service)
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            layers = list_arcgis_layers(session, service)
        except Exception as e:
            print(f"  [Χ] Σφάλμα στην υπηρεσία {service}: {e}")
            errors.append({"source": "arcgis", "layer": service, "error": str(e)})
            continue
        for layer in layers:
            entry = {"source": "arcgis", "service": f"{ARCGIS_BASE}/{service}/MapServer",
                     "layer": f"{service}/{layer['id']}", "title": layer["name"]}
            download_layer(entry, out_dir / f"{layer['id']}_{slugify(layer['name'])[:60]}.geojson",
                           lambda: fetch_arcgis_layer(session, service, layer), skip_existing, catalog, errors)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Λήψη γεωχωρικών layers του Δήμου Θεσσαλονίκης σε GeoJSON")
    parser.add_argument("--source", choices=["all", "wfs", "arcgis"], default="all",
                        help="ποια υπηρεσία να διαβαστεί (προεπιλογή: all)")
    parser.add_argument("--skip-existing", action="store_true",
                        help="παράλειψη layers που έχουν ήδη κατέβει (για συνέχιση διακοπτόμενης λήψης)")
    args = parser.parse_args()

    BASE_DIR.mkdir(parents=True, exist_ok=True)
    session = get_resilient_session()
    catalog, errors = [], []
    PREVIOUS_CATALOG = {e["layer"]: e for e in load_json(CATALOG_FILE, [])}

    if args.source in ("all", "wfs"):
        run_wfs(session, args.skip_existing, catalog, errors)
    if args.source in ("all", "arcgis"):
        run_arcgis(session, args.skip_existing, catalog, errors)
    if args.source != "all":
        # Μερική εκτέλεση: κρατάμε στον κατάλογο τις εγγραφές της άλλης πηγής
        catalog.extend(e for e in PREVIOUS_CATALOG.values() if e["source"] != args.source)

    write_json(CATALOG_FILE, catalog)
    write_json(REPORT_FILE, {"errors": errors})
    print(f"\n[✓] Ολοκληρώθηκε η λήψη {len(catalog)} layers! Σφάλματα: {len(errors)} (βλ. {REPORT_FILE.name})")
