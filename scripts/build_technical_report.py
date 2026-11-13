"""Build the curated Markdown report as a Chinese PDF with XeLaTeX.

Supports the curated Markdown, citation and equation subset in this report.
Requires xelatex and its ctex fonts; no API, GPU or private scene data is needed.
"""

import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs/report"


def load_report(path, ancestors=()):
    """Expand explicit Markdown fragments, keeping a single source per chapter."""
    path = path.resolve()
    if path.parent != REPORT.resolve() or path.suffix != ".md":
        raise ValueError(f"Report fragment must be a Markdown file in {REPORT}")
    if path in ancestors:
        raise ValueError(f"Cyclic report include: {path}")
    return re.sub(
        r"^<!-- include: ([^\n]+) -->$",
        lambda match: load_report(path.parent / match[1], (*ancestors, path)),
        path.read_text(),
        flags=re.MULTILINE,
    )


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
    tokens = re.split(
        r"(`[^`]+`|\*\*[^*]+\*\*|\$[^$]+\$|\[图\]\(#fig-[a-z_]+\)|\[[^\]\n]+\]\([^)\n]+\)|\[\d+(?:,\s*\d+)*\]|https?://[^\s；，。]+)",
        text,
    )
    result = []
    for token in tokens:
        if token.startswith("`"):
            code = escape(token[1:-1]).replace("/", r"/\allowbreak{}")
            code = code.replace(r"\_", r"\_\allowbreak{}")
            for separator in (".", ",", "=", ":", "[", "-", r"\{"):
                code = code.replace(separator, separator + r"\allowbreak{}")
            result.append(r"\texttt{" + code + "}")
        elif token.startswith("**"):
            result.append(r"\textbf{" + escape(token[2:-2]) + "}")
        elif token.startswith("$"):
            result.append(token)
        elif token.startswith("[图](#fig-"):
            result.append(r"图~\ref{fig:" + token[9:-1] + "}")
        elif match := re.fullmatch(r"\[([^\]]+)\]\(([^)]+)\)", token):
            label, target = match.groups()
            if not target.startswith(("https://", "http://")):
                relative = (REPORT / target).resolve().relative_to(ROOT)
                target = (
                    "https://github.com/miracle-techlink/aholo-powergrid-simready/blob/main/"
                    + quote(str(relative))
                )
            result.append(r"\href{" + target + "}{" + escape(label) + "}")
        elif re.fullmatch(r"\[\d+(?:,\s*\d+)*\]", token):
            keys = ["ref" + n.strip() for n in token[1:-1].split(",")]
            result.append(r"\cite{" + ",".join(keys) + "}")
        elif token.startswith(("https://", "http://")):
            result.append(r"\url{" + token + "}")
        else:
            result.append(escape(token))
    return "".join(result)


def render(markdown):
    lines = markdown.splitlines()
    out = []
    index = 0
    portrait_figure_page = False
    portrait_float = False
    bibliography = False
    next_unnumbered = False
    unnumbered_section = False
    table_caption = None
    column_weights = None
    while index < len(lines):
        line = lines[index]
        if line == "<!-- pagebreak -->":
            out.append(r"\clearpage")
        elif line == "<!-- frontmatter -->":
            out.append(r"\begin{center}")
        elif line == "<!-- endfrontmatter -->":
            out.append(r"\end{center}")
        elif line == "<!-- toc -->":
            out.append(r"{\small\setlength{\parskip}{0pt}\tableofcontents}\clearpage")
        elif line == "<!-- appendix -->":
            out.append(r"\clearpage\appendix")
        elif line == "<!-- unnumbered -->":
            next_unnumbered = True
        elif line.startswith('<a id="fig-'):
            pass
        elif line.startswith("<!-- table: "):
            table_caption = line.removeprefix("<!-- table: ").removesuffix(" -->")
        elif line.startswith("<!-- columns: "):
            values = line.removeprefix("<!-- columns: ").removesuffix(" -->")
            column_weights = [float(x) for x in values.split(",")]
        elif line in ("<!-- portrait -->", "<!-- landscape -->"):
            out.append(r"\clearpage")
            portrait_figure_page = True
            portrait_float = False
        elif line == "<!-- portrait-float -->":
            portrait_figure_page = True
            portrait_float = True
        elif line == "<!-- endportrait-float -->":
            portrait_figure_page = False
            portrait_float = False
        elif line in ("<!-- endportrait -->", "<!-- endlandscape -->"):
            out.append(r"\clearpage")
            portrait_figure_page = False
            portrait_float = False
        elif line == "$$":
            equation = []
            index += 1
            while index < len(lines) and lines[index] != "$$":
                equation.append(lines[index])
                index += 1
            out.append(
                "\\begin{equation}\n" + "\n".join(equation) + "\n\\end{equation}"
            )
        elif line.startswith("```"):
            code = []
            index += 1
            while index < len(lines) and not lines[index].startswith("```"):
                code.append(lines[index])
                index += 1
            out.append(
                "\\begin{Verbatim}[fontsize=\\small,breaklines=true,frame=single,"
                "rulecolor=\\color{black!18},framesep=7pt]\n"
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
            weights = column_weights or [1.0] * columns
            if len(weights) != columns or any(w <= 0 for w in weights):
                raise ValueError("Table column weights must match positive columns")
            weights = [w * columns / sum(weights) for w in weights]
            layout = " ".join(
                f">{{\\hsize={w:.5f}\\hsize\\linewidth=\\hsize"
                r"\raggedright\arraybackslash}X"
                for w in weights
            )
            out.append(r"\begin{table}[H]\small\setlength{\tabcolsep}{5pt}")
            if table_caption:
                out.append(r"\caption{" + inline(table_caption) + "}")
            out.append(r"\begin{tabularx}{\linewidth}{" + layout + "}\n\\toprule")
            for row, cells in enumerate(table):
                out.append(" & ".join(inline(c) for c in cells) + r" \\")
                if row == 0:
                    out.append(r"\midrule")
            out.append(r"\bottomrule\end{tabularx}\end{table}")
            table_caption, column_weights = None, None
        elif line.startswith("!["):
            match = re.fullmatch(r"!\[(.*)\]\((.*)\)", line)
            caption, asset = match.groups()
            if asset.endswith(".svg"):
                asset = asset[:-4] + ".pdf"
            module_figure = Path(asset).stem.startswith("module_")
            height = (
                r"0.82\textheight"
                if portrait_figure_page
                else r"0.50\textheight"
                if module_figure
                else r"0.30\textheight"
            )
            width = "16cm" if module_figure or portrait_figure_page else r"\linewidth"
            placement = (
                "p" if portrait_float else "H" if portrait_figure_page else "htbp"
            )
            out.append(
                r"\begin{figure}["
                + placement
                + r"]\centering\includegraphics[width="
                + width
                + ",height="
                + height
                + r",keepaspectratio]{"
                + asset
                + r"}\caption{"
                + inline(caption)
                + r"}\label{fig:"
                + Path(asset).stem
                + r"}\end{figure}"
            )
        elif line.startswith("#### "):
            out.append(
                r"\Needspace{5\baselineskip}\subsubsection{" + inline(line[5:]) + "}"
            )
        elif line.startswith("### "):
            out.append(
                r"\Needspace{5\baselineskip}\subsection"
                + ("*" if unnumbered_section else "")
                + "{"
                + inline(line[4:])
                + "}"
            )
        elif line == "## 摘要":
            out.append(r"\section*{摘要}")
        elif line == "## 参考文献":
            out.append(
                r"\phantomsection\addcontentsline{toc}{section}{参考文献}"
                r"\begin{thebibliography}{99}"
            )
            bibliography = True
        elif line == "<!-- endreferences -->":
            out.append(r"\end{thebibliography}")
            bibliography = False
        elif line.startswith("## "):
            unnumbered_section = next_unnumbered
            next_unnumbered = False
            heading = inline(line[3:])
            out.append(
                r"\Needspace{10\baselineskip}\section"
                + ("*" if unnumbered_section else "")
                + "{"
                + heading
                + "}"
            )
            if unnumbered_section:
                out.append(r"\addcontentsline{toc}{section}{" + heading + "}")
        elif line.startswith("# "):
            out.append(r"{\LARGE\bfseries " + inline(line[2:]) + r"}\par\vspace{4pt}")
        elif bibliography and (match := re.match(r"- \[(\d+)\] (.+)", line)):
            out.append(r"\bibitem{ref" + match[1] + "}" + inline(match[2]))
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
\usepackage[margin=1.6cm]{geometry}
\usepackage{graphicx,xcolor,booktabs,tabularx,array,float,caption,fvextra,xurl,needspace,mathtools,amssymb}
\usepackage[section]{placeins}
\usepackage[colorlinks,linkcolor=blue!50!black,urlcolor=blue!50!black,bookmarksnumbered]{hyperref}
\hypersetup{pdftitle={从场景视频到机器人交互演示：SimReady 技术白皮书},pdfauthor={SimReady Pipeline},pdfsubject={视频到演示的完整技术链路：重建、资产化、物理交互、机器人控制、闭环修复与独立验收}}
\setlength{\parindent}{0pt}\setlength{\parskip}{6pt}
\setlength{\intextsep}{12pt}
\renewcommand{\arraystretch}{1.16}
\captionsetup{font=small,labelfont=bf,labelsep=quad,justification=justified,singlelinecheck=false,skip=6pt}
\captionsetup[table]{position=top}
\ctexset{section={format=\Large\bfseries,beforeskip=16pt,afterskip=8pt},subsection={format=\large\bfseries,beforeskip=12pt,afterskip=6pt}}
\setcounter{tocdepth}{1}\setcounter{secnumdepth}{3}
\makeatletter\renewcommand{\l@section}{\@dottedtocline{1}{0em}{2.3em}}\makeatother
\renewcommand{\refname}{参考文献}
\pagestyle{plain}\raggedbottom
\widowpenalty=10000\clubpenalty=10000\emergencystretch=2em
\begin{document}
"""
    text = load_report(REPORT / "technical-report.md")
    (REPORT / "main.tex").write_text(preamble + render(text) + "\n\\end{document}\n")
    for iteration in range(3):
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
    temporary = destination.with_suffix(".pdf.tmp")
    shutil.copy2(REPORT / "main.pdf", temporary)
    temporary.replace(destination)
    print(destination)


if __name__ == "__main__":
    main()
