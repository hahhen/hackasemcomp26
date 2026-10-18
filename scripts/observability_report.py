"""Print a Markdown MCP incident report: python scripts/observability_report.py."""
import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path


def report(paths, order_id=None):
    groups = defaultdict(list)
    orders = Counter()
    stages = Counter()
    runs = Counter()
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if order_id and row.get("order_id") != order_id:
                continue
            if row.get("event") == "run.end":
                runs[(row.get("graph"), row.get("status"))] += 1
            if row.get("event") == "tool.call.end":
                groups[row["tool"]].append(row)
                orders[row.get("order_id") or "unscoped"] += 1
                stages[(row.get("graph"), row.get("stage"))] += 1
    print("# Relatório de observabilidade do MCP\n")
    print("Contagens de chamadas lógicas; latência inclui retentativas. Tentativas individuais constam no JSONL.\n")
    print("| Tool | Chamadas | Falhas | Canceladas | Média ms | p95 ms | MCPError por categoria |")
    print("|---|---:|---:|---:|---:|---:|---|")
    for tool, rows in sorted(groups.items()):
        times = sorted(row["duration_ms"] for row in rows)
        errors = Counter(row.get("error_category", "unknown") for row in rows if row.get("error_type") == "MCPError")
        print(f"| {tool} | {len(rows)} | {sum(r['status'] == 'error' for r in rows)} | "
              f"{sum(r['status'] == 'cancelled' for r in rows)} | {sum(times)/len(times):.1f} | "
              f"{times[min(len(times)-1, math.ceil(len(times)*.95)-1)]:.1f} | {dict(errors)} |")
    for title, values in (("Chamadas por pedido", orders), ("Chamadas por grafo / etapa", stages), ("Execuções por grafo / status", runs)):
        print(f"\n## {title}\n")
        for key, count in sorted(values.items(), key=lambda item: str(item[0])):
            print(f"- {key}: {count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default="logs/observability.jsonl")
    parser.add_argument("--order-id")
    args = parser.parse_args()
    target = Path(args.path)
    paths = sorted(target.parent.glob(target.name + ".*")) + ([target] if target.exists() else [])
    if not paths:
        parser.error("Nenhum trace encontrado; execute um fluxo com MESA_OBSERVABILITY=true.")
    report(paths, args.order_id)
