#!/usr/bin/env python3
"""Build traceable LaTeX numeric macros from an explicit claim specification.

No claim is inferred from a manuscript or from a matching value elsewhere.
"""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
FORMATS = {"integer","fixed2","fixed3","fixed4","fixed5","percent2","sci2"}
ID_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\Z")


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()


def csv_bytes(rows,fields):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream,fieldnames=fields,lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


def read_spec(path):
    data = json.loads(path.read_text())
    spec = {"claims":data} if isinstance(data,list) else data
    if not isinstance(spec,dict) or not isinstance(spec.get("claims"),list):
        raise ValueError("Specification must be a list or an object with a claims list")
    for field in ("design_constants","structural_exclusions"):
        if field in spec and not isinstance(spec[field],list):
            raise ValueError(f"{field} must be a list")
    return spec


def json_pointer(document,pointer):
    if not isinstance(pointer,str) or (pointer and not pointer.startswith("/")):
        raise ValueError("JSON pointer must be empty or start with a slash")
    value = document
    for segment in pointer.split("/")[1:]:
        key = segment.replace("~1","/").replace("~0","~")
        if isinstance(value,list):
            if not re.fullmatch(r"0|[1-9][0-9]*",key):
                raise ValueError("JSON list pointer requires a nonnegative integer index")
            value = value[int(key)]
        elif isinstance(value,dict):
            value = value[key]
        else:
            raise ValueError("JSON pointer descends through a scalar")
    return value


def source_value(source,repo,cache):
    if not isinstance(source,dict) or not isinstance(source.get("path"),str):
        raise ValueError("Every empirical claim needs a source.path")
    raw_path = Path(source["path"])
    path = raw_path.resolve() if raw_path.is_absolute() else (repo/raw_path).resolve()
    try:
        relative = path.relative_to(repo.resolve()).as_posix()
    except ValueError as error:
        raise ValueError("Numeric sources must be inside the repository") from error
    if relative not in cache:
        raw = path.read_bytes()
        content = gzip.decompress(raw) if path.name.endswith(".gz") else raw
        if path.name.endswith((".csv",".csv.gz")):
            text = content.decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text,newline=""))
            if reader.fieldnames is None or len(reader.fieldnames) != len(set(reader.fieldnames)):
                raise ValueError(f"Missing or duplicate CSV headers: {relative}")
            document = list(reader)
            kind = "csv"
        elif path.name.endswith((".json",".json.gz")):
            document = json.loads(content)
            kind = "json"
        else:
            raise ValueError("Sources must be CSV/CSV.gz or JSON/JSON.gz")
        cache[relative] = {"raw_sha256":digest_bytes(raw),"content_sha256":digest_bytes(content),
                           "document":document,"kind":kind,"bytes":len(raw)}
    entry = cache[relative]
    if source.get("sha256") and source["sha256"] != entry["raw_sha256"]:
        raise ValueError(f"Declared source hash mismatch: {relative}")
    if entry["kind"] == "csv":
        filters = source.get("filters",{})
        if not isinstance(filters,dict) or not all(isinstance(k,str) for k in filters):
            raise ValueError("CSV filters must be a column-to-value object")
        column = source.get("column")
        if not isinstance(column,str):
            raise ValueError("CSV source requires column")
        document = entry["document"]
        if document and any(k not in document[0] for k in (*filters,column)):
            raise ValueError(f"Missing source column: {relative}")
        matches = [(i,row) for i,row in enumerate(document,1) if all(row[k] == str(v) for k,v in filters.items())]
        if len(matches) != 1:
            raise ValueError(f"CSV selector requires exactly one row; got {len(matches)}: {relative} {filters}")
        row_number,row = matches[0]
        value = row[column]
        selector = json.dumps(filters,sort_keys=True,separators=(",",":"))
    else:
        if "json_pointer" not in source:
            raise ValueError("JSON source requires json_pointer")
        pointer = source["json_pointer"]
        try:
            value = json_pointer(entry["document"],pointer)
        except (IndexError,KeyError) as error:
            raise ValueError(f"JSON pointer not found: {relative} {pointer}") from error
        row_number,column,selector = "",pointer,pointer
    if isinstance(value,(bool,list,dict)) or value is None:
        raise ValueError(f"Claim source is not a numeric scalar: {relative} {column}")
    if str(value).strip() == "":
        raise ValueError(f"Claim source value is blank: {relative} {column}")
    return str(value), {"source":relative,"row_number_1based":row_number,"row_selector":selector,"column":column,
                        "source_sha256":entry["raw_sha256"],"source_content_sha256":entry["content_sha256"]}


def format_value(raw,fmt):
    if fmt not in FORMATS:
        raise ValueError(f"Unsupported format: {fmt}")
    try:
        value = Decimal(str(raw).strip())
    except InvalidOperation as error:
        raise ValueError(f"Not a decimal number: {raw}") from error
    if not value.is_finite():
        raise ValueError("Nonfinite numeric claims are forbidden")
    with localcontext() as context:
        context.prec = max(80,len(value.as_tuple().digits)+abs(value.adjusted())+20)
        context.rounding = ROUND_HALF_EVEN
        if fmt == "integer":
            if value != value.to_integral_value():
                raise ValueError(f"Noninteger value requested as integer: {raw}")
            rendered = str(int(value))
            return rendered,"identity; exact integer"
        if fmt == "percent2":
            value *= 100
            transformation = "multiply by 100; ROUND_HALF_EVEN to 2 decimal places; percent sign supplied by manuscript"
        else:
            transformation = "identity; ROUND_HALF_EVEN to "+fmt[-1]+" decimal places" if fmt.startswith("fixed") else "scientific notation"
        if fmt == "sci2":
            mantissa,exponent = format(value,".2E").split("E")
            rendered = "\\ensuremath{"+mantissa+"\\times10^{"+str(int(exponent))+"}}"
            return rendered,"identity; scientific notation, 2 decimal places in mantissa; ROUND_HALF_EVEN"
        decimals = int(fmt[-1]) if fmt.startswith("fixed") else 2
        quantized = value.quantize(Decimal(1).scaleb(-decimals))
        # Keep an exact negative quantity distinguishable even when it rounds to
        # negative zero; the archive retains its signed raw value.
        return format(quantized,f".{decimals}f"),transformation


TRACE_FIELDS = ["claim_id","kind","source","source_sha256","source_content_sha256","row_number_1based",
                "row_selector","column","raw_value","format","transform","formatted_value","explanation"]
CONSTANT_FIELDS = ["claim_id","value","format","formatted_value","explanation","protocol_reference"]


def build(spec_path,repo=REPO):
    spec = read_spec(spec_path)
    cache = {}
    claims,constants,ids = [],[],set()
    for item in spec["claims"]+spec.get("design_constants",[]):
        if not isinstance(item,dict):
            raise ValueError("Each claim/constant must be an object")
        identifier = item.get("id","")
        if not isinstance(identifier,str) or not ID_PATTERN.fullmatch(identifier) or identifier in ids:
            raise ValueError(f"Invalid or duplicated claim ID: {identifier}")
        ids.add(identifier)
        fmt = item.get("format")
        explanation = str(item.get("explanation",""))
        if "source" in item:
            raw,trace = source_value(item["source"],repo,cache)
            kind = "empirical_source_cell"
        else:
            if item not in spec.get("design_constants",[]):
                raise ValueError(f"Empirical claim lacks a source: {identifier}")
            if "value" not in item or not explanation.strip() or not str(item.get("protocol_reference","")).strip():
                raise ValueError("Design constants need value, explanation and protocol_reference")
            raw = str(item["value"])
            trace = {"source":"design_constants.csv","source_sha256":"","source_content_sha256":"",
                     "row_number_1based":"","row_selector":identifier,"column":"value"}
            kind = "declared_design_constant"
        rendered,transform = format_value(raw,fmt)
        row = {"claim_id":identifier,"kind":kind,**trace,"raw_value":raw,"format":fmt,"transform":transform,
               "formatted_value":rendered,"explanation":explanation}
        claims.append(row)
        if item in spec.get("design_constants",[]):
            constants.append({"claim_id":identifier,"value":raw,"format":fmt,"formatted_value":rendered,
                              "explanation":explanation,"protocol_reference":str(item.get("protocol_reference","source cell"))})
    structural = []
    for item in spec.get("structural_exclusions",[]):
        if not isinstance(item,dict) or not str(item.get("text","")).strip() or not str(item.get("explanation","")).strip():
            raise ValueError("Structural exclusions require exact text and explanation")
        structural.append({"text":item["text"],"explanation":item["explanation"]})
    tex = ["% Generated by build_values.py. Edit the claim specification, not this file.",
           "\\providecommand{\\Rthree}[1]{\\csname RThreeValue@#1\\endcsname}"]
    for row in claims:
        tex.append("\\expandafter\\def\\csname RThreeValue@"+row["claim_id"]+"\\endcsname{"+row["formatted_value"]+"}")
    files = {"numeric_claims.csv":csv_bytes(claims,TRACE_FIELDS),
             "design_constants.csv":csv_bytes(constants,CONSTANT_FIELDS),
             "structural_exclusions.json":json_bytes(structural)}
    values = ("\n".join(tex)+"\n").encode()
    manifest = {"analysis_package":"1.5.0","specification_sha256":digest_bytes(spec_path.read_bytes()),
        "build_script_sha256":digest_bytes(Path(__file__).read_bytes()),
        "sources":{key:{k:v for k,v in entry.items() if k not in ("document","kind")} | {"kind":entry["kind"]}
                   for key,entry in sorted(cache.items())},
        "outputs":[{"filename":name,"bytes":len(data),"sha256":digest_bytes(data)} for name,data in sorted(files.items())],
        "private_latex_values":{"filename":"values.tex","bytes":len(values),"sha256":digest_bytes(values)},
        "claims":len(claims),"design_constants":len(constants),"structural_exclusions":len(structural),
        "formatting_rounding":"Decimal ROUND_HALF_EVEN; no data inferred from numerical coincidence"}
    return spec,claims,files,values,manifest


def run(spec_path,support_dir,output_dir,check=False,overwrite=False,repo=REPO):
    spec,claims,files,values,manifest = build(spec_path,repo)
    files["manifest.json"] = json_bytes(manifest)
    if check:
        for name,data in files.items():
            if (output_dir/name).read_bytes() != data:
                raise ValueError(f"Numeric build output differs: {name}")
        if (support_dir/"values.tex").read_bytes() != values:
            raise ValueError("Private values.tex differs from source-derived macro definitions")
    else:
        if output_dir.exists() and not overwrite:
            raise ValueError("Output exists; use --check or --overwrite-generated for this new registry")
        output_dir.mkdir(parents=True,exist_ok=True)
        support_dir.mkdir(parents=True,exist_ok=True)
        for name,data in files.items():
            (output_dir/name).write_bytes(data)
        (support_dir/"values.tex").write_bytes(values)
    print(json.dumps({"check":check,"claims":len(claims),"source_files":len(manifest["sources"]),
                      "output_dir":str(output_dir),"support_values":str(support_dir/"values.tex")},indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec",type=Path,required=True)
    parser.add_argument("--support-dir",type=Path,required=True)
    parser.add_argument("--output-dir",type=Path,default=HERE/"output_v1")
    parser.add_argument("--check",action="store_true")
    parser.add_argument("--overwrite-generated",action="store_true")
    args = parser.parse_args()
    run(args.spec.resolve(),args.support_dir.resolve(),args.output_dir.resolve(),args.check,args.overwrite_generated)


if __name__ == "__main__":
    main()
