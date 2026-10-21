import argparse
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
BASE_DIR = Path(__file__).resolve().parent / "datagov_thessaloniki"
RAW_FILES_DIR = BASE_DIR / "datasets"
CATALOG_FILE = BASE_DIR / "datagov_catalog.json"
REPORT_FILE = BASE_DIR / "download_report.json"
API_BASE = "https://data.gov.gr/api/3/action"
DEFAULT_ORG_ID = "dimos-thessalonikis-2026"


def fetch_organization_datasets(session, org_id):
    """Αντλεί όλα τα metadata για τον συγκεκριμένο οργανισμό από το data.gov.gr (CKAN API)"""
    datasets = []
    start = 0
    rows = 100

    print(f"Σάρωση data.gov.gr για τον οργανισμό: {org_id}")

    while True:
        # fq (Filter Query) αντί για ελεύθερο text search.
        # Σταθερή ταξινόμηση ώστε η σελιδοποίηση να μη χάνει/διπλασιάζει datasets.
        params = {
            "fq": f"organization:{org_id}",
            "sort": "name asc",
            "rows": rows,
            "start": start,
        }

        resp = session.get(f"{API_BASE}/package_search", params=params, timeout=30)
        resp.raise_for_status()
        result = resp.json().get("result", {})
        results = result.get("results", [])

        if not results:
            break

        datasets.extend(results)
        total_count = result.get("count", 0)
        start += len(results)
        print(f" -> Ανάκτηση {start}/{total_count} datasets...")

        if start >= total_count:
            break

    if not datasets:
        raise RuntimeError(
            f"Δεν βρέθηκαν datasets για τον οργανισμό '{org_id}' - άλλαξε το id; "
            f"Δες {API_BASE}/organization_list και τρέξε με --org <νέο id>"
        )
    return datasets


def download_resources(session, catalog):
    """Κατεβάζει τα αρχεία, ξανακατεβάζοντας μόνο όσα άλλαξαν στην πηγή"""
    print(f"\nΈναρξη λήψης αρχείων για {len(catalog)} datasets...")
    errors = []
    dataset_dirs = set()

    for pkg in catalog:
        pkg_title = pkg.get("title") or pkg.get("name")
        # Το CKAN name είναι μοναδικό, οπότε δεν το κόβουμε (η περικοπή έκανε datasets να μοιράζονται φάκελο)
        dir_name = slugify(pkg["name"])
        dataset_dirs.add(dir_name)
        dataset_dir = RAW_FILES_DIR / dir_name
        dataset_dir.mkdir(parents=True, exist_ok=True)
        write_json(dataset_dir / "metadata.json", pkg)

        resources = []
        for res in pkg.get("resources", []):
            url = (res.get("url") or "").strip()
            fmt = (res.get("format") or "bin").upper().strip()
            if not url or fmt in SKIP_FORMATS:
                continue
            resources.append({
                "filename": f"resource_{res['id'][:8]}.{guess_extension(url, fmt)}",
                "url": url,
                "version": res.get("last_modified") or res.get("metadata_modified") or pkg.get("metadata_modified"),
            })

        errors.extend(sync_dataset(session, dataset_dir, pkg_title, resources))

    return errors, dataset_dirs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Άντληση datasets ενός οργανισμού από το data.gov.gr")
    parser.add_argument("--org", default=DEFAULT_ORG_ID,
                        help=f"CKAN id του οργανισμού (προεπιλογή: {DEFAULT_ORG_ID})")
    args = parser.parse_args()

    RAW_FILES_DIR.mkdir(parents=True, exist_ok=True)
    session = get_resilient_session()

    # 1. Συλλογή Metadata
    catalog = fetch_organization_datasets(session, args.org)

    # 2. Αποθήκευση Καταλόγου
    write_json(CATALOG_FILE, catalog)
    print(f"\n[✓] Κατάλογος αποθηκεύτηκε στο: {CATALOG_FILE}")

    # 3. Λήψη Αρχείων
    errors, dataset_dirs = download_resources(session, catalog)
    write_json(REPORT_FILE, {"errors": errors})
    report_stale_dirs(RAW_FILES_DIR, dataset_dirs)

    print(f"\n[✓] Ολοκληρώθηκε η άντληση από το data.gov.gr! Σφάλματα: {len(errors)} (βλ. {REPORT_FILE.name})")
