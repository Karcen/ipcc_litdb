#!/usr/bin/env python3
"""Replay frozen classification, type tables and database audits offline.

The runner unpacks frozen SQLite and DOI-resolution inputs into output/inputs.
No new API calls, samples or labels are made.
"""
import argparse, json, subprocess, sys
sys.dont_write_bytecode = True
from pathlib import Path
PACKAGE = Path(__file__).resolve().parent
CURRENT_TABLES = ['AUDIT_availability_reuse.csv', 'AUDIT_base_rates.csv', 'AUDIT_earlier_transition.csv', 'AUDIT_frame_alternatives.csv', 'AUDIT_sample_flow.csv', 'AUDIT_selection_comparison.csv', 'AUDIT_selection_ratio.csv', 'AUDIT_vintage_confound.csv', 'A_classifier_validation.csv', 'A_classifier_validation_openalex.csv', 'A_classifier_validation_strata.csv', 'A_document_type_distribution.csv', 'A_invisible_pool_composition.csv', 'A_match_rate_by_type.csv', 'A_recurrence_by_type.csv', 'A_reference_frame_validation.csv', 'A_robustness_type_composition.csv', 'A_type_by_vintage.csv', 'A_type_composition_decomposition.csv', 'HV_error_rates.csv', 'HV_frame_bound.csv', 'HV_frame_effect_decomposition.csv']
SCRIPTS = [
 "A_00_extract.py", "A_type_classifier.py", "A_01_build_identity_panel.py",
 "A_02_tables.py", "00_audit_base_rates.py", "04_vintage_confound.py",
 "05_frame_alternatives.py", "06_verify_types.py", "A_03_validation.py",
 "A_04_recurrence_decomposition.py", "A_05_visibility.py",
 "11_analyse_validation.py", "16_frame_effect_decomposition.py", "17_earlier_transition.py",
]
def compare_tables():
 import numpy as np
 import pandas as pd
 rows = []
 for name in CURRENT_TABLES:
  frozen = PACKAGE / "reference" / "tables" / name
  actual = PACKAGE / "output" / "tables" / frozen.name
  if not actual.exists():
   rows.append({"table": frozen.name, "pass": False, "reason": "missing output"})
   continue
  a = pd.read_csv(frozen, low_memory=False)
  b = pd.read_csv(actual, low_memory=False)
  try:
   pd.testing.assert_frame_equal(a, b, check_dtype=False, check_exact=False, rtol=1e-10, atol=1e-10)
   rows.append({"table": frozen.name, "pass": True, "rows": len(a), "columns": len(a.columns)})
  except AssertionError as error:
   rows.append({"table": frozen.name, "pass": False, "reason": str(error)[:2000]})
 report = {"pass": all(x["pass"] for x in rows), "checked_tables": len(rows), "tables": rows,
           "method": "CSV values and row/column order compared with frozen outputs; numerical tolerance 1e-10."}
 out = PACKAGE / "output" / "provenance"
 out.mkdir(parents=True, exist_ok=True)
 (out / "type_replay_check.json").write_text(json.dumps(report, indent=2) + "\n")
 print(json.dumps({"pass": report["pass"], "checked_tables": len(rows)}, indent=2))
 if not report["pass"]:
  for row in rows:
   if not row["pass"]: print(row["table"], row["reason"])
  raise SystemExit(1)
 return report

def main():
 parser = argparse.ArgumentParser(description=__doc__)
 parser.add_argument("--compare-only", action="store_true")
 args = parser.parse_args()
 if not args.compare_only:
  from run_core import prepare_inputs
  prepare_inputs()
  for name in ["ipcc_litdb.sqlite", "doi_resolution.csv"]:
   if not (PACKAGE / "output" / "inputs" / name).is_file():
    raise SystemExit("Missing unpacked frozen input: " + name + "; run the core input preparation first.")
  logdir = PACKAGE / "output" / "logs"
  logdir.mkdir(parents=True, exist_ok=True)
  for name in SCRIPTS:
   print("Running " + name, flush=True)
   with (logdir / (name + ".log")).open("w") as log:
    result = subprocess.run([sys.executable, "-B", str(PACKAGE / "analysis" / name)], cwd=PACKAGE, stdout=log, stderr=subprocess.STDOUT)
   if result.returncode:
    raise SystemExit("Replay failed: " + name + "; inspect output/logs/" + name + ".log")
 compare_tables()
if __name__ == "__main__": main()
