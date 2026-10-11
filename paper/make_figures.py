"""Render four manuscript figures and retain their Nature-style QA records."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import subprocess
import sys

import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.transforms import ScaledTranslation
import numpy as np

ROOT = Path(__file__).resolve().parent
BLUE = "#4477AA"
TEAL = "#228888"
ORANGE = "#CC8844"
GREY = "#B7BBC2"
DARK = "#3E4650"
REPORTS = [f"AR{i}" for i in range(1, 7)]

mpl.rcParams.update({
    "font.family": "sans-serif", "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
    "font.size": 7, "axes.labelsize": 7, "axes.titlesize": 7,
    "xtick.labelsize": 6, "ytick.labelsize": 6, "legend.fontsize": 6,
    "pdf.fonttype": 42, "svg.fonttype": "none", "axes.linewidth": 0.65,
    "axes.spines.top": False, "axes.spines.right": False, "legend.frameon": False,
    "xtick.major.size": 2.5, "ytick.major.size": 2.5,
})


def load_csv(name):
    with (ROOT / "results" / name).open(encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def panel_labels(fig, axes):
    for index, axis in enumerate(axes):
        shift = ScaledTranslation(-18 / 72, 10 / 72, fig.dpi_scale_trans)
        axis.text(0, 1, chr(97 + index), transform=axis.transAxes + shift,
                  fontsize=8, fontweight="bold", va="bottom", ha="left")


def canvas(left=0.10, gap=0.46):
    fig, axes = plt.subplots(1, 2, figsize=(7.20472440945, 3.42519685039))
    fig.subplots_adjust(left=left, right=0.975, bottom=0.24, top=0.80, wspace=gap)
    panel_labels(fig, axes)
    return fig, axes


def render(skill, *, render_only=False):
    if not render_only:
        sys.path.insert(0, str(skill / "scripts"))
        from audit_panel_alignment import require_matplotlib_panel_alignment
        from audit_pdf_text import audit_pdf as audit_text

    directory = ROOT / "figures"
    directory.mkdir(exist_ok=True)
    summary = json.loads((ROOT / "results" / "summary.json").read_text())
    contracts = []
    qa_reports = []

    def save(fig, name, contract):
        fig.canvas.draw()
        if not render_only:
            alignment = require_matplotlib_panel_alignment(
                fig, json_out=directory / f"{name}.alignment.json",
                overlay_svg=directory / f"{name}.alignment.svg", tolerance_pt=1.5,
                gutter_tolerance_pt=1.5, require_panel_labels=True, strict=True,
            )
        # The fixed canvas preserves the 183 mm final width across formats.
        fig.savefig(directory / f"{name}.pdf")
        fig.savefig(directory / f"{name}.svg")
        fig.savefig(directory / f"{name}.png", dpi=600)
        if render_only:
            print(name, "rendered PDF/SVG/PNG; audits skipped, existing QA files preserved")
            plt.close(fig)
            return
        text = audit_text((directory / f"{name}.pdf").read_bytes(), minimum_pt=5)
        (directory / f"{name}.text-audit.json").write_text(json.dumps(text, indent=2) + "\n")
        collision_command = [sys.executable, str(skill / "scripts" / "audit_figure_collisions.py"),
                             str(directory / f"{name}.pdf"), "--json-out", str(directory / f"{name}.collision-audit.json"),
                             "--overlay-pdf", str(directory / f"{name}.collision-audit.pdf")]
        outcome = subprocess.run(collision_command, capture_output=True, text=True)
        collision = json.loads((directory / f"{name}.collision-audit.json").read_text())
        qa_reports.append({"figure": name, "alignment": alignment["verdict"],
                           "pdf_text_auditable": text["auditable"], "below_5pt": text["below_minimum_count"],
                           "collision_verdict": collision["verdict"], "collision_exit": outcome.returncode,
                           "findings": collision["findings"]})
        contracts.append({"figure": name, "archetype": "quantitative grid", "backend": "Python/matplotlib",
                          "stage": "initial_submission", "width_mm": 183, "height_mm": 87,
                          "statistics": "Descriptive enumeration; no sampling error bars or hypothesis tests.", **contract})
        print(name, qa_reports[-1]["alignment"], "text<5pt", text["below_minimum_count"], collision["verdict"], len(collision["findings"]))
        plt.close(fig)

    # Figure 1 defines the extracted corpus and its matching/coverage context.
    coverage = summary["coverage"]
    x = np.arange(6)
    fig, (a, b) = canvas()
    a.bar(x - 0.18, [row["distinct_papers"] / 1000 for row in coverage], 0.34, color=BLUE, label="Distinct papers")
    a.bar(x + 0.18, [row["reference_occurrences"] / 1000 for row in coverage], 0.34, color=GREY, label="Reference occurrences")
    a.set_xticks(x, REPORTS)
    a.set_ylabel("Extracted records (thousands)")
    a.set_ylim(0, 70)
    a.legend(loc="upper left", bbox_to_anchor=(0, 1.22), ncol=1)
    b.plot(x, [row["matched_fraction"] * 100 for row in coverage], "o-", color=BLUE, markersize=3, label="Matched references")
    b.plot(x, [row["chapters_with_occurrences"] / row["catalogue_chapters"] * 100 for row in coverage], "s--", color=DARK, markersize=3, label="Units with references")
    b.set_xticks(x, REPORTS)
    b.set_ylabel("Observed fraction (%)")
    b.set_ylim(0, 108)
    b.legend(loc="upper left", bbox_to_anchor=(0, 1.22), ncol=1)
    save(fig, "figure1_corpus", {
        "question": "What extracted corpus supports the report-cohort comparisons?",
        "conclusion": "Extraction volume and record matching differ across report generations.",
        "panels": {"a": "Distinct-paper and occurrence grains define the dataset.", "b": "Matching fractions and units containing references qualify cross-generation interpretation."},
        "source_data": ["coverage.csv", "coverage_by_report_wg.csv"],
        "coverage_note": "Catalogue units include front matter and annexes; a unit without occurrences does not establish missing scientific content.",
    })

    # Figure 2 distinguishes report-cohort replacement from publication vintage.
    fig, (a, b) = canvas(gap=0.54)
    comparison_pairs = [("AR4", "AR5"), ("AR5", "AR6"), ("AR4", "AR6")]
    xx = np.arange(3)
    maximum = 0
    for index, (variant, color, label) in enumerate([
        ("all_extracted", GREY, "All extracted"),
        ("matched_occurrences_only", BLUE, "Matched only"),
        ("matched_chronological_coherence", ORANGE, "Matched, date-coherent"),
    ]):
        selected = [next(row for row in summary["transitions"] if row["variant"] == variant and (row["report_a"],row["report_b"]) == pair) for pair in comparison_pairs]
        values = [row["retention"] * 100 for row in selected]
        maximum = max(maximum, *values)
        a.bar(xx+(index-1)*0.24,values,0.22,color=color,label=label)
    a.set_xticks(xx,["AR4 to AR5","AR5 to AR6","AR4 to AR6"])
    a.set_ylabel("Earlier paper cohort retained (%)")
    a.set_ylim(0,maximum*1.25)
    a.legend(loc="upper left",bbox_to_anchor=(0,1.24))
    categories = ["Reused from AR5", "New to AR6"]
    left = np.zeros(2)
    for period, color in [("Through 2013", BLUE), ("After 2013", ORANGE), ("Unknown year", GREY)]:
        values = np.array([next(row["papers"] for row in summary["ar6_vintage_matched"] if row["cohort"] == cohort and row["vintage"] == period) / 1000 for cohort in categories])
        b.barh(np.arange(2), values, left=left, color=color, height=0.48, label=period)
        left += values
    for y, value in enumerate(left):
        b.annotate(f"{value:.1f}", (value, y), xytext=(3, 0), textcoords="offset points", va="center", fontsize=6)
    b.set_yticks([0, 1], ["Reused\nfrom AR5", "New to\nAR6"])
    b.set_xlabel("Matched AR6 papers (thousands)")
    b.set_xlim(0, 37)
    b.set_ylim(-0.55, 1.65)
    b.legend(loc="upper left", bbox_to_anchor=(0, 1.23), ncol=1)
    save(fig, "figure2_renewal", {
        "question": "Does renewal of the AR6 paper cohort consist only of recent publications?",
        "conclusion": "Low paper-ID reuse coexists with additions of both older and recent publications.",
        "panels": {"a": "Contrast all-extracted and matched/coherent paper-ID retention for AR4–AR6 transitions.", "b": "Partition matched AR6 reused and newly represented papers by publication vintage."},
        "source_data": ["transitions.csv", "ar6_vintage_summary_matched.csv", "ar6_vintage_papers_matched.csv"],
        "vintage_note": "Through 2013/after 2013 is descriptive and does not assert a source-specific bibliographic cutoff.",
    })

    # Figure 3 separates citation recency from cross-working-group sharing.
    age_reports = ["AR4","AR5","AR6"]
    fig, (a, b) = canvas()
    age_x = np.arange(3)
    for offset,key,color,label in [(-0.08,"citation_age",GREY,"All extracted"),(0.08,"citation_age_matched",BLUE,"Matched only")]:
        ages = [next(row for row in summary[key] if row["report_id"] == report) for report in age_reports]
        medians = np.array([row["median_age"] for row in ages])
        bounds = np.array([[row["median_age"]-row["p25"] for row in ages],[row["p75"]-row["median_age"] for row in ages]])
        a.errorbar(age_x+offset,medians,yerr=bounds,fmt="o",capsize=3,markersize=3.5,color=color,linewidth=1.2,label=label)
    matched_ages = [next(row for row in summary["citation_age_matched"] if row["report_id"] == report) for report in age_reports]
    a.set_xticks(age_x,[f"{row['report_id']}\n{row['dated_included']:,}" for row in matched_ages])
    a.set_xlabel("Assessment report and matched dated occurrences (n)")
    a.set_ylabel("Citation age (years)")
    a.set_ylim(0,9)
    a.legend(loc="upper left",bbox_to_anchor=(0,1.22),ncol=2)
    pairs = [("WG1", "WG2"), ("WG1", "WG3"), ("WG2", "WG3")]
    for i, (report, color) in enumerate(zip(("AR4", "AR5", "AR6"), (GREY, BLUE, ORANGE))):
        values = [next(row["jaccard"] * 100 for row in summary["working_group_overlap_matched"] if row["report_id"] == report and (row["wg_a"], row["wg_b"]) == pair) for pair in pairs]
        b.bar(np.arange(3) + (i - 1) * 0.24, values, 0.22, label=report, color=color)
    b.set_xticks(np.arange(3), ["WG1–WG2", "WG1–WG3", "WG2–WG3"])
    b.set_ylabel("Matched paper-ID Jaccard (%)")
    b.set_ylim(0, 6.5)
    b.legend(loc="upper left", bbox_to_anchor=(0, 1.20), ncol=3)
    save(fig, "figure3_age_groups", {
        "question": "How do publication-age distributions and working-group sharing characterize the extracted assessment corpus?",
        "conclusion": "Median citation age remains short while working-group paper cohorts overlap only partially.",
        "panels": {"a": "All-extracted and matched occurrence-weighted publication ages use source-volume years for AR4–AR6.", "b": "Matched-paper overlaps quantify sharing among working groups within AR4–AR6."},
        "source_data": ["citation_age.csv", "citation_age_matched.csv", "citation_age_occurrences_matched.csv", "working_group_overlap_matched.csv"],
        "statistics": "Points are medians; whiskers span the 25th–75th percentiles, not confidence intervals. Ages outside 0–60 or lacking a valid publication year are counted in source data.",
    })

    # Figure 4 treats journals as outlets and reports missing metadata separately.
    mix = load_csv("journal_mix_matched.csv")
    pooled = {}
    for row in mix:
        if row["report_id"] in {"AR4", "AR5", "AR6"} and row["journal_key"]:
            pooled[row["journal_key"]] = pooled.get(row["journal_key"], 0) + int(row["papers"])
    keys = sorted(pooled, key=lambda key: (-pooled[key], key))[:8]
    journal_labels = {row["journal_key"]: row["journal_label"] for row in mix}
    canonical_labels = {"journal of climate": "Journal of Climate", "climatic change": "Climatic Change", "nature": "Nature", "science": "Science", "geophysical research letters": "Geophysical Research Letters", "journal of geophysical research": "Journal of Geophysical Research", "global environmental change": "Global Environmental Change", "energy policy": "Energy Policy", "proceedings of the national academy of sciences": "PNAS"}
    lookup = {(row["report_id"],row["journal_key"]):float(row["share_all_papers"] or 0)*100 for row in mix}
    data = np.array([[lookup.get((report,key),0) for report in ("AR4","AR5","AR6")] for key in keys])
    fig, (a, b) = canvas(left=0.27, gap=0.64)
    a.pcolormesh(np.arange(4), np.arange(9), data, cmap="Blues", vmin=0, vmax=float(data.max()) * 1.8, shading="flat")
    for i in range(8):
        for j in range(3):
            a.text(j + 0.5, i + 0.5, f"{data[i, j]:.1f}", ha="center", va="center", fontsize=6)
    a.set_xticks(np.arange(3) + 0.5, ["AR4", "AR5", "AR6"])
    a.set_yticks(np.arange(8) + 0.5, [canonical_labels.get(key, journal_labels[key]) for key in keys])
    a.invert_yaxis()
    a.set_title("Matched paper share by outlet (%)", loc="left", pad=16)
    a.tick_params(axis="y", length=0)
    for key,color,label in [("journal_concentration",GREY,"All extracted"),("journal_concentration_matched",BLUE,"Matched only")]:
        concentration = [next(row for row in summary[key] if row["report_id"]==report) for report in ("AR4","AR5","AR6")]
        b.plot(np.arange(3),[row["hhi_known_journals"]*1000 for row in concentration],"o-",color=color,markersize=3,label=label)
    b.set_xticks(np.arange(3),["AR4","AR5","AR6"])
    b.set_ylabel("Named-journal HHI (×10³)")
    b.set_ylim(0,35)
    b.legend(loc="upper left",bbox_to_anchor=(0,1.22))
    save(fig, "figure4_journals", {
        "question": "How does the outlet mix change between the extracted assessment cohorts?",
        "conclusion": "The AR6 named-journal distribution is less concentrated than the AR5 distribution.",
        "panels": {"a": "The eight largest named outlets pooled over AR4–AR6 show report-specific paper shares.", "b": "Matched-only and all-extracted HHI summarize named-journal distributions in AR4–AR6."},
        "source_data": ["journal_mix_matched.csv", "journal_concentration.csv", "journal_concentration_matched.csv"],
        "exclusions": "Panel a displays eight leading outlets rather than every journal; full journal rows are retained. HHI excludes missing journals; missing counts/shares and an alternative HHI treating missing as one category are supplied separately.",
        "interpretation": "Journal titles are publication outlets, not formal disciplinary classifications.",
        "label_alias": "PNAS abbreviates Proceedings of the National Academy of Sciences; the source table retains the full title.",
    })
    if render_only:
        return
    (ROOT / "results" / "figure_contracts.json").write_text(json.dumps(contracts, indent=2) + "\n")
    (ROOT / "results" / "figure_qa.json").write_text(json.dumps(qa_reports, indent=2) + "\n")
    if any(row["collision_exit"] in (1, 2) or row["below_5pt"] or not row["pdf_text_auditable"] for row in qa_reports):
        raise SystemExit("Figure QA requires repair; inspect the preserved reports")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill-dir", type=Path, default=Path.home() / "Downloads/SKILLS/nature-skills-main/skills/nature-figure")
    parser.add_argument(
        "--render-only", action="store_true",
        help="Render PDF/SVG/PNG without external skill audits; preserve existing QA reports and figure contracts.",
    )
    arguments = parser.parse_args()
    render(arguments.skill_dir, render_only=arguments.render_only)
