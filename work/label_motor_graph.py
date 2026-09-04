import json
from pathlib import Path

from graphify.analyze import suggest_questions
from graphify.build import build_from_json
from graphify.report import generate


ROOT = Path(r"C:\Users\marco\OneDrive\Desktop\ETF_infazione")
CORPUS = ROOT / "work" / "motor_docs"
OUT = ROOT / "graphify-out"

labels = {
    0: "Architettura fiscale e beta",
    1: "Capitale reale e rendita",
    2: "Validazione della perpetuità",
    3: "Damodaran senza look-ahead",
    4: "Integrazione operativa VI",
    5: "Regola di distribuzione E",
    6: "Crisi e recupero",
    7: "Rendimenti World in EUR",
    8: "Veto Treasury e TIPS",
}
extraction = json.loads((OUT / ".graphify_extract.json").read_text(encoding="utf-8"))
detection = json.loads((OUT / ".graphify_detect.json").read_text(encoding="utf-8"))
analysis = json.loads((OUT / ".graphify_analysis.json").read_text(encoding="utf-8"))
graph = build_from_json(extraction, root=str(CORPUS), directed=False)
communities = {int(k): v for k, v in analysis["communities"].items()}
cohesion = {int(k): v for k, v in analysis["cohesion"].items()}
questions = suggest_questions(graph, communities, labels)
report = generate(
    graph,
    communities,
    cohesion,
    labels,
    analysis["gods"],
    analysis["surprises"],
    detection,
    {"input": 0, "output": 0},
    ".",
    suggested_questions=questions,
)
(OUT / "GRAPH_REPORT.md").write_text(report, encoding="utf-8")
(OUT / ".graphify_labels.json").write_text(
    json.dumps({str(k): v for k, v in labels.items()}, ensure_ascii=False), encoding="utf-8"
)
print("Report updated with community labels")
