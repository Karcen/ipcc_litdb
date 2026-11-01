"""Portable locations for the offline type/audit replay. No network access."""
from pathlib import Path
import gzip, shutil
PACKAGE = Path(__file__).resolve().parents[1]
OUTPUT = PACKAGE / "output"
DATA = OUTPUT / "analysis" / "data"
TABLES = OUTPUT / "tables"
PROVENANCE = OUTPUT / "provenance"
VALIDATION = PACKAGE / "inputs" / "validation"
INPUTS = OUTPUT / "inputs"
DB = INPUTS / "ipcc_litdb.sqlite"
DOI_RESOLUTION = INPUTS / "doi_resolution.csv"
for path in [DATA, TABLES, PROVENANCE, INPUTS, OUTPUT / "human_validation"]:
    path.mkdir(parents=True, exist_ok=True)
def validation_source_assignments():
    target = INPUTS / "resolved_journal_source_assignments.csv"
    if not target.exists():
        with gzip.open(VALIDATION / (target.name + ".gz"), "rb") as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)
    return target
