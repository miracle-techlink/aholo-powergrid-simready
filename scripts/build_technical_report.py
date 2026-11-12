"""Build the curated Markdown report as a Chinese PDF with XeLaTeX.

Supports only the paragraph/table/image/code subset authored in this report.
Requires xelatex and its ctex fonts; no API, GPU or private scene data is needed.
"""

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs/report"


def escape(text):
    mapping = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(mapping.get(c, c) for c in text)


def inline(text):
    tokens = re.split(r"(`[^`]+`|\*\*[^*]+\*\*|https?://[^\s；，。]+)", text)
    result = []
    for token in tokens:
        if token.startswith("`"):
            result.append(r"\texttt{" + escape(token[1:-1]) + "}")
        elif token.startswith("**"):
            result.append(r"\textbf{" + escape(token[2:-2]) + "}")
        elif token.startswith(("https://", "http://")):
            result.append(r"\url{" + token + "}")
        else:
            result.append(escape(token))
    return "".join(result)


def render(markdown):
    lines = markdown.splitlines()
    out = []
    index = 0
    landscape = False
    while index < len(lines):
        line = lines[index]
        if line == "<!-- pagebreak -->":
            out.append(r"\clearpage")
        elif line == "<!-- landscape -->":
            out.append(r"\begin{landscape}\thispagestyle{empty}")
            landscape = True
        elif line == "<!-- endlandscape -->":
            out.append(
                r"\par\vfill\vbox to 0pt{\vskip 24pt\hbox to \linewidth{\hfil\small\thepage\hfil}\vss}\end{landscape}"
            )
            landscape = False
        elif line.startswith("```"):
            code = []
            index += 1
            while index < len(lines) and not lines[index].startswith("```"):
                code.append(lines[index])
                index += 1
            out.append(
                "\\begin{Verbatim}[fontsize=\\small,breaklines=true]\n"
                + "\n".join(code)
                + "\n\\end{Verbatim}"
            )
        elif line.startswith("|"):
            table = []
            while index < len(lines) and lines[index].startswith("|"):
                cells = [c.strip() for c in lines[index].strip().strip("|").split("|")]
                if not all(re.fullmatch(r"[:\s-]+", c) for c in cells):
                    table.append(cells)
                index += 1
            index -= 1
            columns = len(table[0])
            layout = (">{\\raggedright\\arraybackslash}X " * columns).strip()
            out.append(
                r"\begin{table}[H]\small\begin{tabularx}{\linewidth}{"
                + layout
                + "}\n\\toprule"
            )
            for row, cells in enumerate(table):
                out.append(" & ".join(inline(c) for c in cells) + r" \\")
                if row == 0:
                    out.append(r"\midrule")
            out.append(r"\bottomrule\end{tabularx}\end{table}")
        elif line.startswith("!["):
            match = re.fullmatch(r"!\[(.*)\]\((.*)\)", line)
            caption, asset = match.groups()
            if asset.endswith(".svg"):
                asset = asset[:-4] + ".pdf"
            height = r"0.64\paperwidth" if landscape else r"0.36\textheight"
            out.append(
                r"\begin{figure}[H]\centering\includegraphics[width=\linewidth,height="
                + height
                + r",keepaspectratio]{"
                + asset
                + r"}\caption{"
                + inline(caption)
                + r"}\end{figure}"
            )
        elif line.startswith("### "):
            out.append(r"\subsection*{" + inline(line[4:]) + "}")
        elif line.startswith("## "):
            out.append(r"\section*{" + inline(line[3:]) + "}")
        elif line.startswith("# "):
            out.append(
                r"\begin{center}{\Huge\bfseries " + inline(line[2:]) + r"}\end{center}"
            )
        elif line.startswith("- "):
            out.append(r"\noindent\textbullet\quad " + inline(line[2:]) + r"\par")
        elif line:
            out.append(inline(line) + "\n")
        else:
            out.append("")
        index += 1
    return "\n".join(out)


def main():
    preamble = r"""\documentclass[10pt,a4paper,UTF8,fontset=fandol]{ctexart}
\usepackage[margin=1.7cm]{geometry}
\usepackage{graphicx,xcolor,booktabs,tabularx,array,float,caption,fvextra,xurl,fancyhdr,pdflscape}
\usepackage[colorlinks,linkcolor=blue!50!black,urlcolor=blue!50!black,bookmarksnumbered]{hyperref}
\hypersetup{pdftitle={视频到可交互仿真场景：方法、实现与复现},pdfauthor={SimReady Pipeline},pdfsubject={分阶段方法、输入输出合同、Agent修复、独立验收与机器人运行时}}
\setlength{\parindent}{0pt}\setlength{\parskip}{6pt}
\renewcommand{\arraystretch}{1.16}
\captionsetup{font=small,labelfont=bf,justification=justified,singlelinecheck=false,skip=6pt}
\ctexset{section={format=\Large\bfseries,beforeskip=8pt,afterskip=8pt},subsection={format=\large\bfseries}}
\pagestyle{fancy}\fancyhf{}\fancyhead[L]{\small SimReady Pipeline}\fancyhead[R]{\small 技术报告修订版 · v0.6.0}\fancyfoot[C]{\thepage}
\setlength{\headheight}{14pt}\raggedbottom
\widowpenalty=10000\clubpenalty=10000\emergencystretch=2em
\begin{document}
"""
    text = (REPORT / "technical-report.md").read_text()
    (REPORT / "main.tex").write_text(preamble + render(text) + "\n\\end{document}\n")
    for iteration in range(2):
        with (REPORT / f"build_pass{iteration + 1}.log").open("w") as log:
            subprocess.run(
                ["xelatex", "-interaction=nonstopmode", "-halt-on-error", "main.tex"],
                cwd=REPORT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
    destination = ROOT / "dist/simready-technical-report-zh.pdf"
    destination.parent.mkdir(exist_ok=True)
    shutil.copy2(REPORT / "main.pdf", destination)
    print(destination)


if __name__ == "__main__":
    main()
