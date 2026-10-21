# OpenThessaloniki

Scripts που κατεβάζουν τα ανοιχτά δεδομένα του Δήμου Θεσσαλονίκης από τις δύο δημόσιες πηγές:

| Script | Πηγή | Τύπος API | Φάκελος εξόδου |
|---|---|---|---|
| `thessaloniki_loader.py` | [opendata.thessaloniki.gr](https://opendata.thessaloniki.gr/el) | DKAN, κατάλογος DCAT στο `/data.json` | `opendata_thessaloniki/` |
| `datagovgr_loader.py` | [data.gov.gr](https://data.gov.gr) | CKAN API v3 (`package_search`) | `datagov_thessaloniki/` |

**Ποια πηγή να χρησιμοποιήσω;** Το opendata.thessaloniki.gr είναι η αρχική πηγή: το data.gov.gr
αντιγράφει (harvest) τα datasets από εκεί, έχει λιγότερα και συχνά χωρίς δηλωμένη άδεια.
Για τις περισσότερες χρήσεις αρκεί το `thessaloniki_loader.py`.

Κανένα από τα δύο APIs δεν χρειάζεται λογαριασμό ή API key.

## Εγκατάσταση

Χρειάζεται Python 3.10 ή νεότερη (λόγω του `truststore`).

```bash
git clone <url του repo>
cd OpenThessaloniki
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # Linux / macOS
pip install -r requirements.txt
```

## Χρήση

```bash
python thessaloniki_loader.py
python datagovgr_loader.py
```

Αν ο Δήμος αλλάξει οργανισμό στο data.gov.gr (το id περιέχει έτος), βρες το νέο id στο
<https://data.gov.gr/api/3/action/organization_list> και δώσ' το με `--org`:

```bash
python datagovgr_loader.py --org <νέο-id>
```

Η πρώτη εκτέλεση κατεβάζει περίπου 4 GB ανά πηγή και διαρκεί αρκετή ώρα.
Οι επόμενες κατεβάζουν μόνο ό,τι άλλαξε ή απέτυχε την προηγούμενη φορά.

## Τι παράγεται

```
opendata_thessaloniki/
├── opendata_catalog.json        # πλήρης κατάλογος όπως τον δίνει η πηγή
├── download_report.json         # αρχεία που απέτυχαν και γιατί
└── datasets/
    └── <όνομα-dataset>/
        ├── metadata.json        # metadata του dataset (τίτλος, άδεια, περιγραφή, ...)
        ├── _manifest.json       # url/έκδοση κάθε αρχείου, για τις επόμενες εκτελέσεις
        └── resource_<id>.<ext>  # τα ίδια τα αρχεία (csv, geojson, xls, ...)
```

Ο φάκελος `datagov_thessaloniki/` έχει την ίδια δομή με `datagov_catalog.json`.

## Πώς δουλεύει

- Σε κάθε εκτέλεση κατεβαίνει ξανά ο κατάλογος, οπότε νέα datasets και αρχεία εμφανίζονται αυτόματα.
- Ένα αρχείο ξανακατεβαίνει όταν αλλάξει το URL του ή η ημερομηνία τροποποίησης στην πηγή.
  Στο opendata.thessaloniki.gr η ημερομηνία υπάρχει μόνο ανά dataset, όχι ανά αρχείο.
- Οι λήψεις γράφονται πρώτα σε `.part`, ώστε μια διακοπή να μην αφήνει μισά αρχεία.
- Φάκελοι datasets που αφαιρέθηκαν από την πηγή αναφέρονται στο τέλος, αλλά δεν σβήνονται.

## Περιορισμοί

- **Υπηρεσίες χαρτών** (WMS, WFS, ESRI REST) δεν κατεβαίνουν, γιατί δεν είναι αρχεία.
  Τα ίδια layers υπάρχουν συνήθως και ως GeoJSON / SHP / KML.
- **Σπασμένα links στην πηγή** (π.χ. ο `gis.thessaloniki.gr` δεν υπάρχει πια) καταγράφονται στο
  `download_report.json`. Δεν διορθώνονται από εδώ.
- **Δεν κρατιέται ιστορικό**: κάθε εκτέλεση έχει την τρέχουσα εικόνα της πηγής.

## Άδειες

- Ο **κώδικας** διατίθεται με άδεια [MIT](LICENSE).
- Τα **δεδομένα** ανήκουν στον Δήμο Θεσσαλονίκης και έχουν τη δική τους άδεια ανά dataset
  (κυρίως CC BY 4.0 και ODbL, που απαιτούν αναφορά πηγής, και ένα CC BY-NC-ND).
  Η άδεια κάθε dataset βρίσκεται στο `metadata.json` του. Τα δεδομένα δεν περιλαμβάνονται στο repo.

Αν πολλά άτομα χρειάζονται τα δεδομένα, προτιμήστε να μοιράζεστε ένα αντίγραφο αντί να
τα κατεβάζει ο καθένας ξανά από τους servers του Δήμου.
