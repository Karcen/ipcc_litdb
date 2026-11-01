#!/usr/bin/env python3
"""Refuse unsupported calibration; report identifiability from real controls."""
import argparse,csv,hashlib,json,pathlib
HERE=pathlib.Path(__file__).resolve().parent
def main():
    p=argparse.ArgumentParser();p.add_argument('--run-name',required=True);a=p.parse_args()
    result=HERE/'results'/a.run_name;source=result/'control_reference_agreement.csv'
    rows=[r for r in csv.DictReader(source.open()) if r['arm']=='blind' and r['control_set']=='ledger' and r['reviewer']=='majority']
    if len(rows)!=2:raise SystemExit('Both provisional reference classes are required')
    se=next(x for x in rows if x['reference_label']=='same_study');sp=next(x for x in rows if x['reference_label']=='different_studies')
    estimate=float(se['agreement'])+float(sp['agreement'])-1
    lower=float(se['cp95_lower'])+float(sp['cp95_lower'])-1
    upper=float(se['cp95_upper'])+float(sp['cp95_upper'])-1
    output=[{'quantity':'provisional_ledger_sensitivity','numerator':se['matching_reference_labels'],'denominator':se['denominator'],'point':se['agreement'],'cp95_lower':se['cp95_lower'],'cp95_upper':se['cp95_upper'],'interpretation':'conditional on unconfirmed reference-label provenance; not population accuracy'},
            {'quantity':'provisional_ledger_specificity','numerator':sp['matching_reference_labels'],'denominator':sp['denominator'],'point':sp['agreement'],'cp95_lower':sp['cp95_lower'],'cp95_upper':sp['cp95_upper'],'interpretation':'conditional on unconfirmed reference-label provenance; unresolved counts as reference nonagreement'},
            {'quantity':'youden_denominator','numerator':'','denominator':'','point':estimate,'cp95_lower':lower,'cp95_upper':upper,'interpretation':'rectangular marginal-interval envelope, not a joint95% interval; inversion fails when zero is admitted'}]
    path=result/'calibration_feasibility.csv'
    with path.open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=output[0]);w.writeheader();w.writerows(output)
    status={'frozen_baseline_replaced':False,'rogan_gladen_adjustment_computed':False,'full_shared_census_completed':False,'reference_label_human_provenance_archived':False,'zero_youden_denominator_in_marginal_interval_envelope':lower<=0<=upper,'calibration_target':'all focal citations one study; NOT cross-report recurrence','reasons':['Reference provenance is provisional; the repository labels the correction inspections AI-assisted.','A false identity merge is not necessarily a false cross-report recurrence: a compound ID may retain one genuinely shared focal study. Therefore the existing five merge controls are not five negative recurrence controls.','Only a pilot has been completed; all1657sharedIDs have not been evaluated.','A finite informative uncertainty interval for inverse misclassification cannot be asserted if sensitivity+specificity-1 can approach zero.'],'next_step':'Complete the specified full blinded panel and establish reference-label provenance and a calibration target consistent with the recurrence estimand. Retain any identified panel-corrected estimate as a separate scenario, never a replacement baseline.','input_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'code_sha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),'output_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    (result/'calibration_status.json').write_text(json.dumps(status,indent=2)+'\n')
    print(json.dumps({'adjustment_computed':False,'zero_denominator_admitted':lower<=0<=upper}))
if __name__=='__main__':main()
