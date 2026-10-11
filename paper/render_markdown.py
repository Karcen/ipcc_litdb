#!/usr/bin/env python3
"""Render the LaTeX manuscript as a complete GitHub Markdown article.

Requires Pandoc 3.9 or later. Run from any directory:
    python paper/render_markdown.py --pandoc /path/to/pandoc
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent


def expand_inputs(text: str) -> str:
    def include(match: re.Match) -> str:
        path = (ROOT / match.group(1)).resolve()
        path.relative_to(ROOT)
        return expand_inputs(path.read_text(encoding="utf-8"))
    return re.sub(r"\\input\{([^}]+)\}", include, text)


def prepare_source(text: str) -> str:
    labels = {}
    counters = {"figure": 0, "table": 0}
    pattern = r"\\begin\{(figure|table)\}.*?\\end\{\1\}"
    for match in re.finditer(pattern, text, re.S):
        kind = match.group(1)
        counters[kind] += 1
        label = re.search(r"\\label\{([^}]+)\}", match.group(0))
        if label:
            labels[label.group(1)] = str(counters[kind])
    appendix = text.split(r"\appendix", 1)[1]
    for index, match in enumerate(re.finditer(r"\\section\{[^}]+\}(?:\\label\{([^}]+)\})?", appendix)):
        if match.group(1):
            labels[match.group(1)] = chr(ord("A") + index)
    text = re.sub(r"\\ref\{([^}]+)\}", lambda m: labels[m.group(1)], text)

    def caption(match: re.Match) -> str:
        block = match.group(0)
        label = re.search(r"\\label\{([^}]+)\}", block).group(1)
        prefix = ("Figure" if match.group(1) == "figure" else "Table") + " " + labels[label] + "."
        block = block.replace(r"\caption{", r"\caption{\textbf{" + prefix + "} ", 1)
        return re.sub(r"(\\includegraphics(?:\[[^]]*\])?\{[^}]+)\.pdf\}", r"\1.png}", block)
    return re.sub(pattern, caption, text, flags=re.S)


def walk(node):
    if isinstance(node, list):
        return [walk(value) for value in node]
    if not isinstance(node, dict):
        return node
    node = {key: walk(value) for key, value in node.items()}
    if node.get("t") == "Math" and node["c"][0]["t"] == "DisplayMath":
        node["c"][1] = re.sub(r"\\(?:begin|end)\{equation\}", "", node["c"][1]).strip()
    if node.get("t") == "Str":
        # Preserve range meaning in ordinary prose; bibliographic entries stay intact.
        node["c"] = node["c"].replace("Herfindahl–Hirschman", "Herfindahl-Hirschman").replace("–", " to ")
    return node


def anchor(name: str) -> dict:
    return {"t": "RawBlock", "c": ["html", f'<a id="{name}"></a>']}


def heading(text: str) -> dict:
    return {"t": "Header", "c": [2, ["", [], []], [{"t": "Str", "c": text}]]}


def flatten(blocks: list) -> list:
    result = []
    for block in blocks:
        if block["t"] == "Figure":
            attr, caption, content = block["c"]
            number = next(token["c"] for token in caption[1][0]["c"][0]["c"] if token.get("t") == "Str" and token["c"][0].isdigit()).rstrip(".")
            result.append(anchor("figure-" + number))
            for item in content:
                for inline in item.get("c", []):
                    if inline.get("t") == "Image":
                        inline["c"][0] = ["", [], []]
                        inline["c"][1] = [{"t": "Str", "c": "Figure " + number}]
                result.append(item)
            result.extend(caption[1])
        elif block["t"] == "Div":
            attr, content = block["c"]
            if attr[0].startswith("ref-"):
                result.append(anchor(attr[0]))
            result.extend(flatten(content))
        elif block["t"] == "Table":
            block["c"][0] = ["", [], []]
            caption = block["c"][1][1][0]["c"]
            number = next(token["c"] for token in caption[0]["c"] if token.get("t") == "Str" and token["c"][0].isdigit()).rstrip(".")
            result.extend([anchor("table-" + number), block])
        else:
            result.append(block)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pandoc", default=shutil.which("pandoc"))
    parser.add_argument("--output", type=Path, default=ROOT / "README.md")
    args = parser.parse_args()
    if not args.pandoc:
        parser.error("Install Pandoc or supply --pandoc /path/to/pandoc")
    source = prepare_source(expand_inputs((ROOT / "paper.tex").read_text(encoding="utf-8")))
    process = subprocess.run([args.pandoc, "--from=latex", "--to=json", "--citeproc",
                              "--bibliography=references.bib", "--metadata=link-citations:true",
                              "--metadata=reference-section-title:References"],
                             input=source, cwd=ROOT, text=True, capture_output=True, check=True)
    if process.stderr:
        raise RuntimeError(process.stderr)
    doc = json.loads(process.stdout)
    metadata = doc["meta"]
    blocks = doc["blocks"]
    reference_start = next(i for i, b in enumerate(blocks) if b["t"] == "Header" and b["c"][1][0] == "bibliography")
    references = blocks[reference_start:]
    body = walk(blocks[:reference_start])
    appendix_start = next(i for i, b in enumerate(body) if b["t"] == "Header" and b["c"][1][0] == "corpus-coverage-and-sensitivity-comparisons")
    for block in body:
        if block["t"] == "Header":
            block["c"][0] = 2
    body[appendix_start]["c"][2].insert(0, {"t": "Str", "c": "Appendix A. "})
    identity = next(b for b in body if b["t"] == "Header" and b["c"][1][0] == "sec:identity")
    identity["c"][2].insert(0, {"t": "Str", "c": "Appendix B. "})
    for block in references:
        if block["t"] == "Header":
            block["c"][0] = 2
    title = copy.deepcopy(metadata["title"]["c"])
    title = [{"t": "Space"} if inline["t"] == "LineBreak" else inline for inline in title]
    header = [{"t": "Header", "c": [1, ["", [], []], title]}]
    for author in metadata["author"]["c"]:
        if author["t"] == "MetaInlines":
            header.append({"t": "Para", "c": author["c"]})
        else:
            header.extend(author["c"])
    header.append({"t": "Para", "c": metadata["date"]["c"]})
    header.append({"t": "Para", "c": [
        {"t": "Link", "c": [["", [], []], [{"t": "Str", "c": "PDF"}], ["paper.pdf", ""]]}, {"t": "Space"},
        {"t": "Link", "c": [["", [], []], [{"t": "Str", "c": "LaTeX"}], ["paper.tex", ""]]}, {"t": "Space"},
        {"t": "Link", "c": [["", [], []], [{"t": "Str", "c": "BibTeX"}], ["references.bib", ""]]}
    ]})
    header.extend([heading("Abstract"), *walk(metadata["abstract"]["c"])])
    doc["meta"] = {}
    doc["blocks"] = flatten(header + body[:appendix_start] + references + body[appendix_start:])
    process = subprocess.run([args.pandoc, "--from=json", "--to=gfm-tex_math_gfm+tex_math_dollars", "--wrap=none"],
                             input=json.dumps(doc), text=True, capture_output=True, check=True)
    text = re.sub(r'<span class="nocase">(.*?)</span>', r"\1", process.stdout)
    text = re.sub(r"\$\$(.*?)\$\$", lambda m: "\n\n$$\n" + m.group(1).strip() + "\n$$\n\n", text, flags=re.S)
    text = text.replace("Their interquartile ranges move from two to six to three to seven years",
                        "Their interquartile ranges move from two to six years in AR5 to three to seven years in AR6")
    text = text.replace("a three to six-year interquartile range", "an interquartile range of three to six years")
    text = text.replace("The overall three to seven-year interquartile range", "The overall interquartile range of three to seven years")
    text = text.replace("*Npj Climate Action*", "*npj Climate Action*")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"(?m)^ (?=\S)", "", text)
    # Link figure/table mentions to their displayed assets without adding subheadings.
    lines = []
    for line in text.splitlines():
        if not line.startswith(("**Table", "**Figure", "|", "<a", "![")):
            line = re.sub(r"Table[\s\u00a0]+([1-6])", r"[Table \1](#table-\1)", line)
            line = re.sub(r"Fig\.[\s\u00a0]+([1-4])([ab]?)", r"[Fig. \1\2](#figure-\1)", line)
        lines.append(line.rstrip())
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Rendered full article: {args.output.name}")


if __name__ == "__main__":
    main()
