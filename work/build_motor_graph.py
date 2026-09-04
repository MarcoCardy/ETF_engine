import json
from pathlib import Path

from graphify.analyze import god_nodes, suggest_questions, surprising_connections
from graphify.build import build_from_json
from graphify.cache import save_semantic_cache
from graphify.cluster import cluster, score_all
from graphify.export import to_json
from graphify.report import generate


ROOT = Path(r"C:\Users\marco\OneDrive\Desktop\ETF_infazione")
CORPUS = ROOT / "work" / "motor_docs"
OUT = ROOT / "graphify-out"

chunk = json.loads((OUT / ".graphify_chunk_01.json").read_text(encoding="utf-8"))
(OUT / ".graphify_semantic_new.json").write_text(
    json.dumps(chunk, indent=2, ensure_ascii=False), encoding="utf-8"
)
save_semantic_cache(chunk.get("nodes", []), chunk.get("edges", []), chunk.get("hyperedges", []))

cached_path = OUT / ".graphify_cached.json"
cached = (
    json.loads(cached_path.read_text(encoding="utf-8"))
    if cached_path.exists()
    else {"nodes": [], "edges": [], "hyperedges": []}
)
all_nodes = cached.get("nodes", []) + chunk.get("nodes", [])
seen = set()
deduped = []
for node in all_nodes:
    if node["id"] not in seen:
        seen.add(node["id"])
        deduped.append(node)
semantic = {
    "nodes": deduped,
    "edges": cached.get("edges", []) + chunk.get("edges", []),
    "hyperedges": cached.get("hyperedges", []) + chunk.get("hyperedges", []),
    "input_tokens": 0,
    "output_tokens": 0,
}
(OUT / ".graphify_semantic.json").write_text(
    json.dumps(semantic, indent=2, ensure_ascii=False), encoding="utf-8"
)

ast = json.loads((OUT / ".graphify_ast.json").read_text(encoding="utf-8"))
extract = {
    "nodes": ast.get("nodes", []) + semantic["nodes"],
    "edges": ast.get("edges", []) + semantic["edges"],
    "hyperedges": semantic["hyperedges"],
    "input_tokens": 0,
    "output_tokens": 0,
}
(OUT / ".graphify_extract.json").write_text(
    json.dumps(extract, indent=2, ensure_ascii=False), encoding="utf-8"
)

detection = json.loads((OUT / ".graphify_detect.json").read_text(encoding="utf-8"))
graph = build_from_json(extract, root=str(CORPUS), directed=False)
communities = cluster(graph)
cohesion = score_all(graph, communities)
gods = god_nodes(graph)
surprises = surprising_connections(graph, communities)
labels = {cid: f"Community {cid}" for cid in communities}
questions = suggest_questions(graph, communities, labels)
if not to_json(graph, communities, str(OUT / "graph.json")):
    raise SystemExit("Graph export refused")
report = generate(
    graph,
    communities,
    cohesion,
    labels,
    gods,
    surprises,
    detection,
    {"input": 0, "output": 0},
    ".",
    suggested_questions=questions,
)
(OUT / "GRAPH_REPORT.md").write_text(report, encoding="utf-8")
(OUT / ".graphify_analysis.json").write_text(
    json.dumps(
        {
            "communities": {str(k): v for k, v in communities.items()},
            "cohesion": {str(k): v for k, v in cohesion.items()},
            "gods": gods,
            "surprises": surprises,
            "questions": questions,
        },
        indent=2,
        ensure_ascii=False,
    ),
    encoding="utf-8",
)
print(f"Graph: {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges, {len(communities)} communities")
