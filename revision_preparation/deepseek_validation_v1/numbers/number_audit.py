#!/usr/bin/env python3
"""Fail on every untraced Arabic numeral in manuscript scientific prose.

Only exact source-cell macros or explicitly documented non-scientific/structural
exclusions qualify. Numeric coincidence with an unrelated cell never qualifies.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
import build_values as values

NUMBER = re.compile(r"(?<![0-9])[-+−]?(?:(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?(?![0-9])")
MACRO = re.compile(r"\\Rthree\s*\{([A-Za-z][A-Za-z0-9_-]*)\}")


def position(text,start):
    line = text.count("\n",0,start)+1
    last = text.rfind("\n",0,start)
    return line,start-last


def group_end(text,start,opening="{",closing="}"):
    if start >= len(text) or text[start] != opening:
        raise ValueError("Expected opening delimiter")
    depth = 0
    i = start
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == opening:
            depth += 1
        elif text[i] == closing:
            depth -= 1
            if depth == 0:
                return i+1
        i += 1
    raise ValueError("Unbalanced LaTeX argument encountered by numeric audit")


def command_arguments(text,start):
    match = re.match(r"\\[A-Za-z@]+\*?",text[start:])
    if not match:
        return [],start
    i = start+len(match.group())
    args = []
    while i < len(text):
        while i < len(text) and text[i].isspace():
            i += 1
        if i < len(text) and text[i] in "[{":
            opening = text[i]
            end = group_end(text,i,opening,"]" if opening == "[" else "}")
            args.append((opening,i,end))
            i = end
        else:
            break
    return args,i


def included_manuscripts(tex_path,validated_values):
    """Inspect literal TeX inputs recursively; an appendix cannot evade audit."""
    documents = []
    seen = set()
    def visit(path,stack):
        path = path.resolve()
        if path == validated_values.resolve():
            return
        if path in stack:
            raise ValueError("Cyclic TeX input in numeric audit")
        if path in seen:
            return
        seen.add(path)
        text = path.read_text()
        documents.append((path,text))
        chars = list(text)
        for i,char in enumerate(text):
            if char != "%":
                continue
            slashes = 0
            k = i-1
            while k >= 0 and text[k] == "\\":
                slashes += 1
                k -= 1
            if slashes%2 == 0:
                end = text.find("\n",i)
                end = len(text) if end < 0 else end
                for k in range(i,end):
                    chars[k] = " "
        clean = "".join(chars)
        for match in re.finditer(r"\\(?:input|include)\b",clean):
            args,_ = command_arguments(clean,match.start())
            if not args or args[0][0] != "{":
                raise ValueError("TeX input requires a literal brace-delimited path for numeric audit")
            name = clean[args[0][1]+1:args[0][2]-1].strip()
            if any(c in name for c in "\\#{}"):
                raise ValueError("Dynamic TeX input path cannot be audited")
            child = Path(name)
            child = child if child.is_absolute() else path.parent/child
            if not child.suffix:
                child = child.with_suffix(".tex")
            if not child.is_file():
                raise ValueError("Missing TeX input: "+child.name)
            visit(child,stack+(path,))
    visit(tex_path,())
    return documents


def mask_manuscript(text,claims,structural):
    masked = list(text)
    exclusions = []
    def mask(start,end,category,explanation=""):
        original = text[start:end]
        numerals = NUMBER.findall(original)
        line,column = position(text,start)
        exclusions.append({"category":category,"line":line,"column":column,"characters":end-start,
            "numeric_tokens":len(numerals),"text":original,"explanation":explanation})
        for k in range(start,end):
            if masked[k] != "\n":
                masked[k] = " "
    # Comments are recognized by the parity of immediately preceding slashes.
    i = 0
    while i < len(text):
        if text[i] == "%":
            slashes = 0
            k = i-1
            while k >= 0 and text[k] == "\\":
                slashes += 1
                k -= 1
            if slashes%2 == 0:
                end = text.find("\n",i)
                end = len(text) if end < 0 else end
                mask(i,end,"tex_comment")
                i = end
                continue
        i += 1
    clean = "".join(masked)
    begin = re.search(r"\\begin\s*\{document\}",clean)
    if begin:
        # Standard sn-jnl source has title metadata/abstract after begin. If an
        # abstract is declared earlier, preserve that entire command for audit.
        preserve = []
        for m in re.finditer(r"\\abstract\b",clean[:begin.start()]):
            args,end = command_arguments(clean,m.start())
            if args:
                preserve.append((m.start(),end))
        cursor = 0
        for start,end in preserve:
            mask(cursor,start,"tex_preamble_formatting")
            cursor = end
        mask(cursor,begin.end(),"tex_preamble_formatting")
    clean = "".join(masked)
    all_args = {"label","ref","pageref","eqref","autoref","cref","Cref","cite","citep","citet","citealp","citeauthor","citeyear",
                "url","path","nolinkurl","email","author","affil","date","bibliography","bibliographystyle","input","include",
                "includegraphics","graphicspath","setlength","addtolength","fontsize","setcounter","addtocounter","hspace","vspace","cline","cmidrule"}
    for m in list(re.finditer(r"\\([A-Za-z@]+)\*?",clean)):
        if masked[m.start()] == " ":
            continue
        name = m.group(1)
        args,end = command_arguments(clean,m.start())
        if name in all_args:
            category = ("citation_or_cross_reference_key" if name in {"label","ref","pageref","eqref","autoref","cref","Cref","cite","citep","citet","citealp","citeauthor","citeyear"}
                else "author_affiliation_email_date_metadata" if name in {"email","author","affil","date"}
                else "URL_or_file_path" if name in {"url","path","nolinkurl","bibliography","bibliographystyle","input","include","includegraphics","graphicspath"}
                else "tex_formatting_argument")
            for _,start,stop in args:
                mask(start,stop,category)
        elif name == "href" and args:
            mask(args[0][1],args[0][2],"URL_or_file_path")
        elif name in {"multicolumn","multirow","resizebox"}:
            for _,start,stop in args[:2]:
                mask(start,stop,"tex_table_or_graphic_structure")
        elif name == "begin" and args:
            environment = clean[args[0][1]+1:args[0][2]-1]
            if environment in {"tabular","tabular*","tabularx","longtable","array"}:
                for _,start,stop in args[1:]:
                    mask(start,stop,"tex_table_or_graphic_structure")
    clean = "".join(masked)
    for item in structural:
        occurrences = list(re.finditer(re.escape(item["text"]),clean))
        for m in occurrences:
            mask(m.start(),m.end(),"explicit_structural_identity",item["explanation"])
        clean = "".join(masked)
    # Known report/group labels and diversity-order symbols are nomenclature.
    nomenclature = (r"\bAR[1-6]\b",r"\bWG[123]\b",r"\bD[012]\b",r"(?<![A-Za-z])D_\{?[012]\}?",
        r"\b(?:v)?[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?\b",
        r"\b[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9X]\b",
        r"(?<![A-Za-z0-9])[0-9a-f]{7,64}(?![A-Za-z0-9])")
    categories = ("assessment_report_nomenclature","working_group_nomenclature","diversity_order_symbol","diversity_order_symbol",
                  "software_or_snapshot_version_metadata","ORCID_metadata","hexadecimal_commit_or_digest_metadata")
    for pattern,category in zip(nomenclature,categories):
        for m in re.finditer(pattern,clean):
            # Pure integer strings are not accepted as purported hexadecimal
            # metadata: a large untraced sample count must still fail.
            if category == "hexadecimal_commit_or_digest_metadata" and not re.search("[a-f]",m.group()):
                continue
            mask(m.start(),m.end(),category)
        clean = "".join(masked)
    usages = []
    for m in MACRO.finditer(clean):
        identifier = m.group(1)
        if identifier not in claims:
            raise ValueError("Undefined numeric claim macro: "+identifier)
        line,column = position(text,m.start())
        usages.append({"claim_id":identifier,"line":line,"column":column,"formatted_value":claims[identifier]["formatted_value"],
            "source":claims[identifier]["source"],"row_selector":claims[identifier]["row_selector"],"column_name":claims[identifier]["column"]})
        mask(m.start(),m.end(),"source_traced_numeric_macro")
    clean = "".join(masked)
    # Any malformed call also fails, rather than silently disappearing as a
    # nonnumeric macro name.
    if re.search(r"\\Rthree\b",clean):
        raise ValueError("Malformed or unsupported Rthree macro invocation")
    untraced = []
    for m in NUMBER.finditer(clean):
        line,column = position(text,m.start())
        context = text[max(0,m.start()-65):min(len(text),m.end()+65)].replace("\n"," ")
        untraced.append({"line":line,"column":column,"literal":m.group(),"context":context,
                         "reason":"literal numeric token has no source-cell macro or explicit structural exclusion"})
    return usages,untraced,exclusions


def audit(tex_path,spec_path,support_dir,registry_dir,report_dir,repo=values.REPO,expanded_output=None):
    spec,trace,files,expected_values,expected_manifest = values.build(spec_path,repo)
    manifest_path = registry_dir/"manifest.json"
    current_manifest = json.loads(manifest_path.read_text())
    if current_manifest != expected_manifest:
        raise ValueError("Numeric registry manifest/source/code hashes differ; rebuild is required")
    for name,content in files.items():
        if (registry_dir/name).read_bytes() != content:
            raise ValueError("Numeric registry content differs: "+name)
    if (support_dir/"values.tex").read_bytes() != expected_values:
        raise ValueError("Macro definitions or displayed rounding differ from current source cells")
    claims = {row["claim_id"]:row for row in trace}
    text = tex_path.read_text()
    documents = included_manuscripts(tex_path,support_dir/"values.tex")
    structural = spec.get("structural_exclusions",[])
    for item in structural:
        if not any(item["text"] in content for _,content in documents):
            raise ValueError("Declared structural exclusion did not match manuscript: "+item["text"][:80])
    usages,untraced,exclusions = [],[],[]
    for path,content in documents:
        file_usages,file_untraced,file_exclusions = mask_manuscript(content,claims,structural)
        for sequence,target in ((file_usages,usages),(file_untraced,untraced),(file_exclusions,exclusions)):
            target.extend({"filename":path.name,**row} for row in sequence)
    used = {r["claim_id"] for r in usages}
    result = {"passed":not untraced,"numeric_token_scope":"Arabic numerals in scientific manuscript source, including abstract, tables and appendices",
        "manuscript_filename":tex_path.name,"manuscript_sha256":values.digest_bytes(tex_path.read_bytes()),
        "claim_spec_sha256":values.digest_bytes(spec_path.read_bytes()),"registry_manifest_sha256":values.digest_bytes(manifest_path.read_bytes()),
        "values_tex_sha256":values.digest_bytes(expected_values),"audit_script_sha256":values.digest_bytes(Path(__file__).read_bytes()),
        "source_file_hashes_verified":len(expected_manifest["sources"]),"macro_usages":len(usages),"unique_claims_used":len(used),
        "manuscript_sources_audited":[{"filename":path.name,"sha256":values.digest_bytes(path.read_bytes())} for path,content in documents],
        "unused_claim_ids":sorted(set(claims)-used),"untraced_numeric_tokens":len(untraced),
        "exclusion_event_counts":dict(sorted(Counter(r["category"] for r in exclusions).items())),
        "excluded_numeric_token_counts":dict(sorted(Counter({category:sum(r["numeric_tokens"] for r in exclusions if r["category"] == category)
            for category in {r["category"] for r in exclusions}}).items())),
        "limitations":["Spelled-out numerical words are not Arabic numeral tokens; prose review must check their quantitative meaning.",
                       "Source-cell correctness is separately established by analysis reproduction; this audit verifies provenance, selection, hashing, formatting and absence of untraced digit literals."]}
    report_dir.mkdir(parents=True,exist_ok=True)
    report_files = {"number_audit.json":values.json_bytes(result),
        "untraced_numbers.csv":values.csv_bytes(untraced,["filename","line","column","literal","context","reason"]),
        "macro_usages.csv":values.csv_bytes(usages,["filename","claim_id","line","column","formatted_value","source","row_selector","column_name"]),
        "exclusions.json":values.json_bytes(exclusions)}
    for name,content in report_files.items():
        (report_dir/name).write_bytes(content)
    audit_manifest = {"analysis_package":"1.5.0","inputs":{"tex":result["manuscript_sha256"],"spec":result["claim_spec_sha256"],
        "numeric_registry_manifest":result["registry_manifest_sha256"],"private_values_tex":result["values_tex_sha256"],
        "audit_script":result["audit_script_sha256"],"manuscript_sources":result["manuscript_sources_audited"]},
        "outputs":[{"filename":name,"bytes":len(data),"sha256":values.digest_bytes(data)} for name,data in sorted(report_files.items())]}
    (report_dir/"manifest.json").write_bytes(values.json_bytes(audit_manifest))
    if expanded_output is not None:
        expanded = MACRO.sub(lambda m:claims[m.group(1)]["formatted_value"] if m.group(1) in claims else m.group(),text)
        expanded_output.write_text(expanded)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tex",type=Path,required=True)
    parser.add_argument("--spec",type=Path,required=True)
    parser.add_argument("--support-dir",type=Path,required=True)
    parser.add_argument("--registry-dir",type=Path,default=values.HERE/"output_v1")
    parser.add_argument("--report-dir",type=Path,required=True,help="Keep this private: diagnostic contexts contain manuscript text")
    parser.add_argument("--expanded-output",type=Path)
    args = parser.parse_args()
    try:
        result = audit(args.tex.resolve(),args.spec.resolve(),args.support_dir.resolve(),args.registry_dir.resolve(),args.report_dir.resolve(),
                       expanded_output=args.expanded_output.resolve() if args.expanded_output else None)
    except (ValueError,FileNotFoundError,KeyError) as error:
        print("Numeric audit failed: "+str(error),file=sys.stderr)
        return 2
    print(json.dumps(result,indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
