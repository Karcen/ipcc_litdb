#!/usr/bin/env python3
"""Render registered age comparisons and conditional source-allocation paths.

Python/Matplotlib is the established exclusive plotting backend. All points
and intervals are read from archived tables, never simulated or imputed here.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
os.environ.setdefault("MPLBACKEND","Agg")
os.environ["SOURCE_DATE_EPOCH"] = "1792540800"
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0,str(HERE/"vendor"))
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np
import pymupdf
from audit_panel_alignment import require_matplotlib_panel_alignment

SEED = 20261011
COLORS = {"source_publication_year":"#246F9B","documented_cutoff_year":"#B86B33"}
ORIGINS = tuple(COLORS)
ORIGIN_LABELS = {"source_publication_year":"Source publication year","documented_cutoff_year":"Cutoff year"}
MARKERS = {"source_publication_year":"o","documented_cutoff_year":"s"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def json_bytes(value):
    return (json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()


def write_csv(path,rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream,fieldnames=list(rows[0]),lineterminator="\n")
    writer.writeheader(); writer.writerows(rows)
    path.write_text(stream.getvalue())


def read_csv(path):
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def setup():
    mpl.rcParams.update({
        "font.family":"sans-serif","font.sans-serif":["DejaVu Sans"],
        "svg.fonttype":"none","pdf.fonttype":42,"ps.fonttype":42,
        "svg.hashsalt":"ipcc-round3-20261011","font.size":6.5,
        "axes.labelsize":6.5,"xtick.labelsize":6,"ytick.labelsize":6.5,
        "legend.fontsize":6.2,"axes.spines.right":False,"axes.spines.top":False,
        "axes.linewidth":.6,"xtick.major.width":.6,"ytick.major.width":.6,
        "xtick.major.size":2.5,"ytick.major.size":0,"legend.frameon":False,
        "axes.unicode_minus":True,"text.color":"#202020","axes.labelcolor":"#202020",
        "xtick.color":"#202020","ytick.color":"#202020","savefig.facecolor":"white"})


def save_exports(fig,base,comparable=True):
    fig.canvas.draw()
    require_matplotlib_panel_alignment(fig,json_out=base.with_suffix(".alignment.json"),
        overlay_svg=base.with_suffix(".alignment.svg"),tolerance_pt=1.5,gutter_tolerance_pt=1.5,
        require_panel_labels=comparable,strict=True)
    fig.savefig(base.with_suffix(".pdf"),metadata={"CreationDate":None,"ModDate":None})
    fig.savefig(base.with_suffix(".svg"),metadata={"Date":None})
    fig.savefig(base.with_suffix(".png"),dpi=600)


def age_figure(output):
    input_dir = REPO/"revision_preparation/review_round3/age/output_v1"
    changes = read_csv(input_dir/"age_changes.csv")
    components = read_csv(input_dir/"matching_status_decomposition.csv")
    summary = read_csv(input_dir/"age_summary.csv")
    rows = []
    fig,axes = plt.subplots(2,1,figsize=(133/25.4,128/25.4),gridspec_kw={"height_ratios":[1.08,1]})
    fig.subplots_adjust(left=.315,right=.97,top=.88,bottom=.10,hspace=.68)
    cohorts = ("all_extracted","matched_occurrences","unmatched_occurrences")
    labels = ("All extracted","Matched","Unmatched")
    fields = ("matched_within_change","unmatched_within_change","matching_composition_change")
    component_labels = ("Within matched","Within unmatched","Matching composition")
    for j,origin in enumerate(ORIGINS):
        offset = .13 if j == 0 else -.13
        for i,cohort in enumerate(cohorts):
            selected = [r for r in changes if r["age_origin"] == origin and r["cohort"] == cohort and r["weighting"] == "occurrence"]
            if len(selected) != 1:
                raise ValueError("Age contrast selector is not unique")
            r = selected[0]
            point,lo,hi = (float(r[k]) for k in ("mean_age_difference","mean_age_difference_lower_95","mean_age_difference_upper_95"))
            if not lo <= point <= hi:
                raise ValueError("Point outside its registered percentile interval")
            axes[0].errorbar(point,2-i+offset,xerr=np.array([[point-lo],[hi-point]]),
                fmt=MARKERS[origin],color=COLORS[origin],mec=COLORS[origin],
                mfc=COLORS[origin] if j == 0 else "white",ms=4,elinewidth=.95,capsize=2,capthick=.75,zorder=3)
            n = [next(s for s in summary if s["report"] == report and s["cohort"] == cohort and s["age_origin"] == origin and s["weighting"] == "occurrence")["included_occurrences"] for report in ("AR5","AR6")]
            rows.append({"panel":"a","age_origin":origin,"cohort_or_component":cohort,"estimate":point,"lower_95":lo,"upper_95":hi,
                "AR5_included_occurrences":n[0],"AR6_included_occurrences":n[1],"source_table":"age/output_v1/age_changes.csv",
                "source_column":"mean_age_difference","weighting":"occurrence","interval":"95% chapter-composition percentile"})
        selected = [r for r in components if r["age_origin"] == origin and r["weighting"] == "occurrence" and r["metric"] == "mean_age"]
        if len(selected) != 1:
            raise ValueError("Age decomposition selector is not unique")
        r = selected[0]
        if not np.isclose(sum(float(r[k]) for k in fields),float(r["all_extracted_change"]),rtol=0,atol=1e-12):
            raise ValueError("Plotted decomposition does not reconstruct age change")
        for i,field in enumerate(fields):
            point,lo,hi = (float(r[k]) for k in (field,field+"_lower_95",field+"_upper_95"))
            axes[1].errorbar(point,2-i+offset,xerr=np.array([[point-lo],[hi-point]]),
                fmt=MARKERS[origin],color=COLORS[origin],mec=COLORS[origin],
                mfc=COLORS[origin] if j == 0 else "white",ms=4,elinewidth=.95,capsize=2,capthick=.75,zorder=3)
            rows.append({"panel":"b","age_origin":origin,"cohort_or_component":field,"estimate":point,"lower_95":lo,"upper_95":hi,
                "AR5_included_occurrences":"","AR6_included_occurrences":"","source_table":"age/output_v1/matching_status_decomposition.csv",
                "source_column":field,"weighting":"occurrence","interval":"95% chapter-composition percentile"})
    for ax,letter,heading in zip(axes,("a","b"),("Age change by matching status","Components of the pooled change")):
        ax.axvline(0,color="#8A8A8A",linestyle=(0,(3,3)),linewidth=.7,zorder=1)
        ax.set_ylim(-.55,2.55)
        ax.set_yticks([2,1,0])
        ax.tick_params(axis="y",pad=7)
        ax.spines["left"].set_visible(False)
        ax.annotate(letter,xy=(0,1),xytext=(-80,9),xycoords="axes fraction",textcoords="offset points",
                    fontsize=8,fontweight="bold",va="bottom",annotation_clip=False)
        ax.annotate(heading,xy=(0,1),xytext=(0,9),xycoords="axes fraction",textcoords="offset points",
                    fontsize=7,va="bottom",annotation_clip=False)
    axes[0].set_yticklabels(labels)
    axes[0].set_xlim(-2.3,1.05);axes[0].set_xticks([-2,-1,0,1])
    axes[0].set_xlabel("AR6 − AR5 mean age (years)",labelpad=5)
    axes[1].set_yticklabels(component_labels)
    axes[1].set_xlim(-.85,.75);axes[1].set_xticks([-.75,-.5,-.25,0,.25,.5,.75])
    axes[1].set_xlabel("Contribution to mean-age change (years)",labelpad=5)
    handles = [Line2D([],[],marker=MARKERS[o],color=COLORS[o],mfc=COLORS[o] if i == 0 else "white",
                      mec=COLORS[o],linestyle="none",markersize=4,label=ORIGIN_LABELS[o]) for i,o in enumerate(ORIGINS)]
    fig.legend(handles=handles,loc="upper right",bbox_to_anchor=(.97,.985),ncol=2,handlelength=1,handletextpad=.5,columnspacing=1.5)
    save_exports(fig,output/"age_change_and_components")
    plt.close(fig)
    write_csv(output/"age_figure_source.csv",rows)
    return {"input_age_change_rows":len(changes),"selected_age_change_rows":6,
        "input_component_rows":len(components),"selected_component_rows":2,
        "selection":"Occurrence-weighted mean changes for both age origins; equal-ID and recent-share analyses remain in the age archive",
        "panels":[{"panel":"a","role":"primary age comparison with uncertainty","uncertainty":"all six contrasts have the same 95% chapter-composition interval definition","n":"included occurrence counts exported in source CSV"},
                  {"panel":"b","role":"exact algebraic decomposition with component uncertainty","uncertainty":"all six terms have the same 95% pooled chapter-composition interval definition","n":"same pooled report population used for the status decomposition"}]}


def source_figure(output):
    path = REPO/"revision_preparation/review_round3/external/offline_v1/missing_source_allocation_curve.csv"
    all_rows = read_csv(path)
    rows = [r for r in all_rows if r["report"] == "AR5"]
    for r in rows:
        counterpart = [s for s in all_rows if s["report"] == "AR6" and s["alpha"] == r["alpha"] and s["direction"] == r["direction"]]
        if len(counterpart) != 1 or not np.isclose(float(r["AR6_minus_AR5_D2"]),float(counterpart[0]["AR6_minus_AR5_D2"]),rtol=0,atol=1e-12):
            raise ValueError("Report copies of allocation contrast disagree")
    fig,ax = plt.subplots(figsize=(133/25.4,83/25.4))
    fig.subplots_adjust(left=.16,right=.97,bottom=.21,top=.86)
    styles = [("toward_largest_source","#B86B33","-","Toward the largest source"),
              ("toward_distinct_sources","#246F9B",(0,(4,2)),"Toward distinct sources")]
    for direction,color,linestyle,label in styles:
        selected = sorted((r for r in rows if r["direction"] == direction),key=lambda r:float(r["alpha"]))
        x = np.array([float(r["alpha"]) for r in selected]);y = np.array([float(r["AR6_minus_AR5_D2"]) for r in selected])
        if len(x) < 2 or not np.all(np.diff(x)>0) or x[0] != 0 or x[-1] != 1:
            raise ValueError("Allocation parameter support must be increasing from zero to one")
        ax.plot(x,y,color=color,linestyle=linestyle,linewidth=1.35,label=label)
    ax.axhline(0,color="#8A8A8A",linestyle=(0,(3,3)),linewidth=.7)
    ax.set_xlim(0,1);ax.set_xticks([0,.25,.5,.75,1])
    ax.set_ylim(-3,82);ax.set_yticks([0,20,40,60,80])
    ax.set_xlabel("Tilt from the year-stratified allocation (α)",labelpad=6)
    ax.set_ylabel("AR6 − AR5 effective source count (D₂)",labelpad=6)
    ax.legend(loc="upper center",bbox_to_anchor=(.5,1.16),ncol=1,handlelength=2.5,labelspacing=.6)
    save_exports(fig,output/"missing_source_allocation",comparable=False)
    plt.close(fig)
    write_csv(output/"source_allocation_figure_source.csv",rows)
    return {"input_rows":len(all_rows),"selected_rows":len(rows),
        "selection":"AR5-labelled copies contain the same AR6-minus-AR5 contrasts as the AR6 rows; duplicate equality checked",
        "scientific_scope":"Conditional allocation paths for unknown-type/source IDs treated as journal-eligible, known other types excluded",
        "uncertainty":"None; deterministic scenarios, not a confidence band, estimate or LLM source classification"}


def execute_qa(output):
    results = {}
    commands = [("source_preflight",[sys.executable,str(HERE/"vendor/validate_figure.py"),str(Path(__file__)),"--json"])]
    for stem in ("age_change_and_components","missing_source_allocation"):
        commands += [(stem+"_font_audit",[sys.executable,str(HERE/"vendor/audit_pdf_text.py"),str(output/(stem+".pdf")),"--min-pt","5","--json"]),
                     (stem+"_collision_audit",[sys.executable,str(HERE/"vendor/audit_figure_collisions.py"),str(output/(stem+".pdf")),"--json"])]
    for label,command in commands:
        process = subprocess.run(command,capture_output=True,text=True)
        if process.returncode:
            raise ValueError(f"Rendered QA failed ({label}): "+process.stdout+process.stderr)
        payload = json.loads(process.stdout)
        # Replace machine-local roots in QA payloads with portable labels.
        raw = json.dumps(payload).replace(str(output),"[figure_output]").replace(str(REPO),"[repository]")
        payload = json.loads(raw)
        (output/(label+".json")).write_bytes(json_bytes(payload))
        results[label] = {"exit_code":process.returncode,"report":label+".json"}
    return results


def render(output):
    if (mpl.__version__,np.__version__,pymupdf.VersionBind) != ("3.11.2","2.5.4","1.28.2"):
        raise ValueError("Use the fixed plotting runtime: Matplotlib3.11.2 NumPy2.5.4 PyMuPDF1.28.2")
    inputs = [REPO/"revision_preparation/review_round3/age/output_v1/manifest.json",
              REPO/"revision_preparation/review_round3/age/output_v1/age_changes.csv",
              REPO/"revision_preparation/review_round3/age/output_v1/age_summary.csv",
              REPO/"revision_preparation/review_round3/age/output_v1/matching_status_decomposition.csv",
              REPO/"revision_preparation/review_round3/external/offline_v1/manifest.json",
              REPO/"revision_preparation/review_round3/external/offline_v1/missing_source_allocation_curve.csv"]
    input_hashes = {p.relative_to(REPO).as_posix():sha(p) for p in inputs}
    if output.exists():
        raise ValueError("Use a fresh rendering directory, preserving any prior artifact")
    output.mkdir(parents=True)
    setup()
    notes = {"age":age_figure(output),"missing_sources":source_figure(output)}
    notes["automatic_QA"] = execute_qa(output)
    notes["visual_QA_status"] = "pending final rendered inspection; stored separately after review"
    (output/"QA_notes.json").write_bytes(json_bytes(notes))
    for p,h in input_hashes.items():
        if sha(REPO/p) != h:
            raise ValueError("Plot source changed during rendering")
    files = [p for p in sorted(output.iterdir()) if not p.name.startswith("._")]
    code_inputs = [Path(__file__),HERE/"figure_contracts.json",*sorted((HERE/"vendor").glob("*.py")),HERE/"vendor/provenance.json",HERE/"vendor/LICENSE.txt"]
    manifest = {"analysis_package":"1.4.0","seed":SEED,"new_random_draws":False,
        "backend":"Python/Matplotlib","python_version":sys.version.split()[0],"matplotlib_version":mpl.__version__,
        "numpy_version":np.__version__,"pymupdf_version":pymupdf.VersionBind,
        "input_sha256":input_hashes,"code_and_contract_sha256":{p.relative_to(HERE).as_posix():sha(p) for p in code_inputs},
        "outputs":[{"filename":p.name,"bytes":p.stat().st_size,"sha256":sha(p)} for p in files],
        "scientific_export_formats":["PDF","SVG","PNG"],"PNG_dpi":600,"editable_text":True,
        "dimensions_mm":{"age_change_and_components":[133,128],"missing_source_allocation":[133,83]}}
    (output/"manifest.json").write_bytes(json_bytes(manifest))
    print(json.dumps({"output":str(output),"files":len(files),"QA":notes["automatic_QA"]},indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",type=Path,default=HERE/"output_v1")
    args = parser.parse_args()
    render(args.output_dir.resolve())


if __name__ == "__main__":
    main()
