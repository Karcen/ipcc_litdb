#!/usr/bin/env python3
"""Render archived raw-year ECDFs without recalculating scientific estimates.

Python/Matplotlib only. Main axes show ages 0--30; insets retain the complete
0--60 support and the original full-population normalization. Solid/dashed
lines encode all extracted/matched occurrences; blue/orange encode reports.
Use --skill-directory to provide the pinned Nature Figure audit scripts in CI.
Install the optional rendering dependencies in the separate requirements-figures.txt.
Byte-identical replay requires the recorded library versions and bundled font hash;
other renderers may preserve the data while changing text placement or file bytes.
The exported SVG retains editable text and CSS dash styles. PyMuPDF's SVG preview
does not implement all those styles; use a browser or Quick Look for visual SVG QA.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.transforms import ScaledTranslation
from matplotlib import font_manager
import numpy as np
import pymupdf

REPO = Path(__file__).resolve().parents[1]
VERSION = "round2-figures-1.0.0"
STEM = "review_round2_age_comparison"
WIDTH_MM, HEIGHT_MM = 133, 140
BLUE, ORANGE = "#0072B2", "#D55E00"
FONT = "DejaVu Sans"
ORIGINS = ("source_publication_year", "documented_cutoff_year")
SERIES = (("AR5","all_extracted",BLUE,"-","AR5 · All extracted"),
          ("AR6","all_extracted",ORANGE,"-","AR6 · All extracted"),
          ("AR5","matched_occurrences",BLUE,"--","AR5 · Matched"),
          ("AR6","matched_occurrences",ORANGE,"--","AR6 · Matched"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False)+"\n")


def portable_audit(value):
    """Keep archived audit provenance without publishing workstation paths."""
    if isinstance(value, dict):
        return {k: portable_audit(v) for k,v in value.items()}
    if isinstance(value, list):
        return [portable_audit(v) for v in value]
    if isinstance(value, str) and value.startswith(str(REPO)+os.sep):
        return str(Path(value).relative_to(REPO))
    return value


def load_data(directory):
    source = directory / "raw_age_ecdf.csv"
    manifest = json.loads((directory/"manifest.json").read_text())
    item = next(x for x in manifest["output_tables"] if x["filename"] == source.name)
    if digest(source) != item["sha256"]:
        raise ValueError("ECDF input differs from the independent raw-age archive")
    with source.open(newline="",encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    selected = [r for r in rows if r["working_group"] == "All" and
                r["cited_year_field"] == "raw_leading_year" and r["weighting"] == "occurrence"]
    populations=[]
    for origin in ORIGINS:
        for report,cohort,_,_,_ in SERIES:
            rr = [r for r in selected if r["age_origin"] == origin and
                  r["report"] == report and r["cohort"] == cohort]
            rr.sort(key=lambda r:int(r["age"]))
            if [int(r["age"]) for r in rr] != list(range(61)):
                raise ValueError("Each plotted curve must contain exactly the archived 0--60 support")
            cdf = np.array([float(r["empirical_CDF"]) for r in rr])
            denominators={r["weight_denominator"] for r in rr}
            if len(denominators)!=1 or not np.isfinite(cdf).all() or np.any(np.diff(cdf)<-1e-12):
                raise ValueError("Invalid archived ECDF or population denominator")
            if not np.isclose(cdf[-1],1,atol=1e-12) or np.any((cdf<0)|(cdf>1+1e-12)):
                raise ValueError("ECDF does not retain full-population normalization")
            populations.append({"age_origin":origin,"report":report,"cohort":cohort,
                                "included_occurrences":int(float(next(iter(denominators))))})
    if len(selected)!=488:
        raise ValueError("Unexpected plotted-condition row count")
    return rows,selected,populations


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir",type=Path,default=REPO/"revision_preparation/review_round2/raw_age_v1")
    parser.add_argument("--output-dir",type=Path,default=REPO/"revision_preparation/review_round2/figures_v1")
    parser.add_argument("--skill-directory",type=Path,default=Path(os.environ.get("NATURE_FIGURE_SKILL",str(Path.home()/".codex/skills/nature-figure"))))
    parser.add_argument("--copy-to",type=Path,help="Optional existing private manuscript figure directory")
    args=parser.parse_args()
    audits=args.skill_directory/"scripts"
    required=("audit_panel_alignment.py","audit_pdf_text.py","audit_figure_collisions.py","validate_figure.py")
    if any(not(audits/name).is_file() for name in required):
        parser.error("Supply --skill-directory containing the Nature Figure audit scripts")
    sys.path.insert(0,str(audits))
    from audit_panel_alignment import require_matplotlib_panel_alignment
    from audit_pdf_text import audit_pdf
    args.output_dir.mkdir(parents=True,exist_ok=True)
    out=args.output_dir
    source=args.results_dir/"raw_age_ecdf.csv"
    before={source:digest(source),args.results_dir/"manifest.json":digest(args.results_dir/"manifest.json")}
    for directory in [REPO/"reproducibility/figures",args.copy_to]:
        if directory and directory.is_dir():
            for path in directory.iterdir():
                if path.suffix.lower() in {".pdf",".svg",".png"} and not path.name.startswith(STEM) and not path.name.startswith("._"):
                    before[path]=digest(path)
    rows,selected,populations=load_data(args.results_dir)
    with (out/"plotted_ecdf_rows.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=list(selected[0]));w.writeheader();w.writerows(selected)
    preflight=subprocess.run([sys.executable,str(audits/"validate_figure.py"),str(Path(__file__)),"--json"],capture_output=True,text=True)
    write_json(out/"source_preflight.json",portable_audit(json.loads(preflight.stdout)))
    if preflight.returncode:
        raise ValueError("Figure source preflight failed; inspect source_preflight.json")
    mpl.rcParams.update({
        "font.family":"sans-serif","font.sans-serif":[FONT],"font.size":7.2,
        "axes.labelsize":7.2,"axes.titlesize":8,"xtick.labelsize":6.8,
        "ytick.labelsize":6.8,"legend.fontsize":7,"legend.frameon":False,
        "axes.spines.top":False,"axes.spines.right":False,"axes.linewidth":.65,
        "xtick.major.width":.65,"ytick.major.width":.65,"xtick.major.size":2.5,
        "ytick.major.size":2.5,"pdf.fonttype":42,"svg.fonttype":"none",
        "svg.hashsalt":VERSION,"figure.facecolor":"white","savefig.facecolor":"white",
    })
    fig,axes=plt.subplots(2,1,figsize=(WIDTH_MM/25.4,HEIGHT_MM/25.4))
    fig.subplots_adjust(left=.145,right=.965,bottom=.085,top=.84,hspace=.68)
    fig.text(.555,.975,"Raw reference years · Occurrence weights",ha="center",va="top",fontsize=7)
    handles=[Line2D([0],[0],color=color,linestyle=style,linewidth=1.2,label=label)
             for _,_,color,style,label in SERIES]
    fig.legend(handles=handles,loc="upper center",bbox_to_anchor=(.555,.946),ncol=2,
               columnspacing=2,handletextpad=.7,handlelength=2.4,labelspacing=.6)
    insets=[]
    for index,(ax,origin,title) in enumerate(zip(axes,ORIGINS,("Source publication year","Working-group cutoff year"))):
        inset=ax.inset_axes([.60,.12,.36,.36])
        insets.append(inset)
        for report,cohort,color,style,_ in SERIES:
            rr=sorted([r for r in selected if r["age_origin"]==origin and r["report"]==report and r["cohort"]==cohort],key=lambda r:int(r["age"]))
            ages=[int(r["age"]) for r in rr]
            cdf=[float(r["empirical_CDF"]) for r in rr]
            for panel,lw in ((ax,1.25),(inset,.8)):
                panel.step(ages,cdf,where="post",color=color,linestyle=style,linewidth=lw)
        ax.set(xlim=(0,30),ylim=(0,1.015),xticks=[0,5,10,15,20,25,30],
               yticks=[0,.25,.5,.75,1],xlabel="Citation age (years)",ylabel="Cumulative fraction")
        ax.set_title(title,loc="left",pad=7)
        offset=ScaledTranslation(-18/72,7/72,fig.dpi_scale_trans)
        ax.text(0,1,chr(97+index),transform=ax.transAxes+offset,ha="left",va="bottom",
                fontsize=8.5,fontweight="bold")
        inset.set(xlim=(0,60),ylim=(0,1.015),xticks=[0,30,60],yticks=[0,1])
        inset.tick_params(labelsize=5.6,length=1.8,width=.45,pad=1.6)
        inset.set_title("Full range: 0–60 years",loc="left",fontsize=5.8,pad=3)
        for spine in inset.spines.values():spine.set_linewidth(.45)
    fig.canvas.draw()
    alignment=require_matplotlib_panel_alignment(fig,json_out=out/"alignment.json",
        overlay_svg=out/"alignment.svg",tolerance_pt=1.5,gutter_tolerance_pt=1.5,
        require_panel_labels=True,exclude_axes=insets,strict=True)
    fig.savefig(out/(STEM+".pdf"),metadata={"Creator":"Matplotlib","CreationDate":None,"ModDate":None})
    fig.savefig(out/(STEM+".svg"),metadata={"Date":None,"Creator":"Matplotlib"})
    fig.savefig(out/(STEM+".png"),dpi=600,metadata={"Software":"Matplotlib"})
    plt.close(fig)
    text=audit_pdf((out/(STEM+".pdf")).read_bytes(),minimum_pt=5)
    write_json(out/"pdf_text_audit.json",text)
    if not text["auditable"] or text["below_minimum_count"]:
        raise ValueError("PDF glyph audit did not pass")
    collision=subprocess.run([sys.executable,str(audits/"audit_figure_collisions.py"),
        str(out/(STEM+".pdf")),"--json-out",str(out/"collision_audit.json"),
        "--overlay-pdf",str(out/"collision_overlay.pdf")],capture_output=True,text=True)
    collision_data=json.loads((out/"collision_audit.json").read_text())
    collision_data=portable_audit(collision_data)
    write_json(out/"collision_audit.json",collision_data)
    if collision.returncode not in (0,):
        raise ValueError("Collision audit blocks figure delivery")
    with pymupdf.open(out/(STEM+".pdf")) as doc:
        doc[0].get_pixmap(matrix=pymupdf.Matrix(2,2),alpha=False).save(out/"pdf_render.png")
        pdf_text=doc[0].get_text()
        pdf_images=len(doc[0].get_images())
    if not all(x in pdf_text for x in ("Cumulative fraction","Citation age","AR5","AR6")):
        raise ValueError("Expected searchable PDF labels are absent")
    font=Path(font_manager.findfont(FONT,fallback_to_default=False))
    with pymupdf.open(out/(STEM+".svg")) as doc:
        doc[0].get_pixmap(matrix=pymupdf.Matrix(2,2),alpha=False).save(out/"svg_render.png")
    for path,value in before.items():
        if digest(path)!=value:raise ValueError(f"Input or older figure changed: {path.name}")
    manifest={"figure_version":VERSION,"source_data":{"file":"../raw_age_v1/raw_age_ecdf.csv","sha256":digest(source)},
        "source_manifest_sha256":digest(args.results_dir/"manifest.json"),"script_sha256":digest(Path(__file__)),
        "software":{"python":sys.version.split()[0],"matplotlib":mpl.__version__,"numpy":np.__version__,"pymupdf":pymupdf.VersionBind},
        "font":{"family":FONT,"font_file":font.name,"sha256":digest(font),"PDF_embedded_TrueType":True,"SVG_editable_text":True},
        "replay":{"dependency_file":"../requirements-figures.txt","byte_identity_requires":"Recorded Python/library versions and font hashes; fixed SVG IDs and date-free metadata are used","SVG_preview_note":"PyMuPDF SVG preview does not preserve all CSS font/dash styles; inspect in a browser or Quick Look"},
        "width_mm":WIDTH_MM,"height_mm":HEIGHT_MM,"PNG_dpi":600,
        "source_rows":len(rows),"plotted_rows":len(selected),"selection":"All working groups; raw_leading_year; occurrence weights; two origins; both reports and cohorts",
        "axis_definitions":{"x":"Integer origin year minus leading year in original reference; main0–30,insets0–60","y":"Archived empirical CDF among included0–60-year occurrences; no renormalization at30"},
        "populations":populations,"no_new_estimates_or_interpolation":True,"uncertainty":"None: fixed-snapshot empirical distributions, not resampling summaries",
        "archetype":"quantitative grid","panel_roles":{"a":"Primary comparison with source publication-year origin","b":"Timing sensitivity with documented group-cutoff-year origin"},
        "QA":{"source_preflight_exit":preflight.returncode,"alignment":alignment["verdict"],"glyphs_below_5pt":text["below_minimum_count"],
              "collision_verdict":collision_data["verdict"],"collision_exit":collision.returncode,"PDF_searchable_labels":True,"PDF_raster_images":pdf_images,
              "old_figures_unchanged":True,"visual_inspection":"Required after rendering; see visual_QA.txt"},
        "audit_code_sha256":{n:digest(audits/n) for n in required},
        "files":{STEM+"."+ext:digest(out/(STEM+"."+ext)) for ext in ("pdf","svg","png")}}
    write_json(out/"manifest.json",manifest)
    if args.copy_to:
        if not args.copy_to.is_dir():raise ValueError("Private figure-copy directory must already exist")
        for ext in ("pdf","svg","png"):
            src=out/(STEM+"."+ext);dest=args.copy_to/src.name
            shutil.copyfile(src,dest)
            if digest(src)!=digest(dest):raise ValueError("Private artifact copy hash mismatch")
    print(json.dumps({"figure":STEM,"alignment":alignment["verdict"],"collision":collision_data["verdict"],
        "glyphs_below_5pt":text["below_minimum_count"],"raster_images_in_PDF":pdf_images,
        "output":str(out),"old_figures_unchanged":True},indent=2))


if __name__=="__main__":
    main()
