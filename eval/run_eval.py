import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
PROJECT_FOLDER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_FOLDER / "src"))
import router
from retriever import Retriever

data = json.loads((Path(__file__).parent / "questions.json").read_text(encoding="utf-8"))
data = {split: [item for item in items if item["q"].strip()] for split, items in data.items()}

retriever = Retriever()
print(f"backend: {retriever.backend}, chunks: {len(retriever.chunks)}")


def search_all(items):
    return [(item, retriever.search(item["q"], top_k=3), router.classify(item["q"])) for item in items]


def refused(row, threshold, use_router):
    return (use_router and row[2] == "out_of_scope") or row[1][0][1] < threshold


def score(rows, threshold, use_router):
    in_scope = [row for row in rows if not row[0].get("oos")]
    off_topic = [row for row in rows if row[0].get("oos")]

    def ratio(count, group):
        return count / len(group) if group else float("nan")

    top1 = ratio(sum(row[1][0][0]["id"] == row[0]["id"] for row in in_scope), in_scope)
    top3 = ratio(sum(row[0]["id"] in [hit[0]["id"] for hit in row[1]] for row in in_scope), in_scope)
    correct = ratio(sum((not refused(row, threshold, use_router)) and row[1][0][0]["id"] == row[0]["id"]
                   for row in in_scope), in_scope)
    wrong_refused = ratio(sum(refused(row, threshold, use_router) for row in in_scope), in_scope)
    off_topic_refused = ratio(sum(refused(row, threshold, use_router) for row in off_topic), off_topic)
    return {"in_scope_n": len(in_scope), "oos_n": len(off_topic), "top1": top1, "top3": top3,
            "answered_correctly": correct, "wrong_refusal": wrong_refused, "oos_refused": off_topic_refused}


def pick_threshold(rows, use_router):
    best = None
    for candidate in [percent / 100 for percent in range(5, 80)]:
        scores = score(rows, candidate, use_router)
        total = scores["answered_correctly"] + scores["oos_refused"]
        if best is None or total >= best[0] - 1e-9:
            best = (total, candidate)
    return best[1]


def format_scores(scores):
    return "  ".join(f"{name}={value:.2f}" if isinstance(value, float) else f"{name}={value}" for name, value in scores.items())


dev, test = search_all(data["dev"]), search_all(data["test"])
results = {"backend": retriever.backend, "threshold_in_config": retriever.threshold}

scores = score(test, -1.0, False)
results["baseline_always_answer"] = {"test": scores}
print(f"\n[baseline_always_answer] no refusals\n  TEST  {format_scores(scores)}")

for use_router in (False, True):
    name = "with_router" if use_router else "no_router"
    threshold = pick_threshold(dev, use_router)
    scores = score(test, threshold, use_router)
    results[name] = {"threshold": threshold, "test": scores}
    print(f"\n[{name}] threshold chosen on dev = {threshold:.2f}\n  TEST  {format_scores(scores)}")

threshold = results["with_router"]["threshold"]
print("\nTest failures (with router):")
for row in test:
    item, hits, _ = row
    was_refused = refused(row, threshold, True)
    top_id = hits[0][0]["id"]
    if item.get("oos") and not was_refused:
        print(f"  [should refuse] {item['q']!r} -> {top_id} ({hits[0][1]:.2f})")
    elif not item.get("oos") and (was_refused or top_id != item["id"]):
        print(f"  [expected {item['id']}] {item['q']!r} -> {'REFUSED' if was_refused else top_id} ({hits[0][1]:.2f})")

results_folder = Path(__file__).parent / "results"
results_folder.mkdir(exist_ok=True)
(results_folder / f"results_{retriever.backend}.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
