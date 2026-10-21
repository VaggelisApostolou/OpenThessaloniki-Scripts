import hashlib
from pathlib import Path

from opendata_common import (
    SKIP_FORMATS,
    get_resilient_session,
    guess_extension,
    report_stale_dirs,
    slugify,
    sync_dataset,
    write_json,
)

# Ρυθμίσεις (σχετικά με τον φάκελο του script, όχι με το τρέχον directory)
BASE_DIR = Path(__file__).resolve().parent / "opendata_thessaloniki"
RAW_FILES_DIR = BASE_DIR / "datasets"
CATALOG_FILE = BASE_DIR / "opendata_catalog.json"
REPORT_FILE = BASE_DIR / "download_report.json"
# Η πύλη είναι DKAN (Drupal), όχι CKAN: ο πλήρης κατάλογος δίνεται σε DCAT μορφή στο /data.json
CATALOG_URL = "https://opendata.thessaloniki.gr/data.json"


def fetch_catalog(session):
    """Αντλεί όλα τα metadata από το opendata.thessaloniki.gr"""
    print(f"Λήψη καταλόγου από: {CATALOG_URL}")
    resp = session.get(CATALOG_URL, timeout=120)
    resp.raise_for_status()
    datasets = resp.json().get("dataset", [])
    if not datasets:
        raise RuntimeError("Ο κατάλογος δεν περιέχει datasets")
    print(f" -> Βρέθηκαν {len(datasets)} datasets")
    return datasets


def download_resources(session, catalog):
    """Κατεβάζει τα αρχεία, ξανακατεβάζοντας μόνο όσα άλλαξαν στην πηγή"""
    print(f"\nΈναρξη λήψης αρχείων για {len(catalog)} datasets...")
    errors = []
    dataset_dirs = set()

    for ds in catalog:
        title = ds.get("title") or ds["identifier"]
        # Ο τίτλος δεν είναι μοναδικός, οπότε προσθέτουμε την αρχή του identifier
        dir_name = f"{slugify(title)[:80].rstrip('_')}_{ds['identifier'][:8]}"
        dataset_dirs.add(dir_name)
        dataset_dir = RAW_FILES_DIR / dir_name
        dataset_dir.mkdir(parents=True, exist_ok=True)
        write_json(dataset_dir / "metadata.json", ds)

        resources = []
        for dist in ds.get("distribution", []):
            # Πολλά αρχεία (π.χ. CSV του opengov, GeoJSON του sdi) έχουν μόνο accessURL
            url = (dist.get("downloadURL") or dist.get("accessURL") or "").strip()
            fmt = (dist.get("format") or "bin").upper().strip()
            if not url or fmt in SKIP_FORMATS:
                continue
            # Τα distributions δεν έχουν id, οπότε το όνομα αρχείου βγαίνει από το URL
            url_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
            resources.append({
                "filename": f"resource_{url_hash}.{guess_extension(url, fmt)}",
                "url": url,
                # Δεν υπάρχει ημερομηνία ανά αρχείο, μόνο ανά dataset
                "version": ds.get("modified"),
            })

        errors.extend(sync_dataset(session, dataset_dir, title, resources))

    return errors, dataset_dirs


if __name__ == "__main__":
    RAW_FILES_DIR.mkdir(parents=True, exist_ok=True)
    session = get_resilient_session()

    # 1. Συλλογή Metadata
    catalog = fetch_catalog(session)

    # 2. Αποθήκευση Καταλόγου
    write_json(CATALOG_FILE, catalog)
    print(f"\n[✓] Κατάλογος αποθηκεύτηκε στο: {CATALOG_FILE}")

    # 3. Λήψη Αρχείων
    errors, dataset_dirs = download_resources(session, catalog)
    write_json(REPORT_FILE, {"errors": errors})
    report_stale_dirs(RAW_FILES_DIR, dataset_dirs)

    print(f"\n[✓] Ολοκληρώθηκε η άντληση από το opendata.thessaloniki.gr! Σφάλματα: {len(errors)} (βλ. {REPORT_FILE.name})")
