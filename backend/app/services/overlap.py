from typing import List, Tuple
from rapidfuzz import fuzz
from app.models import Task
from app.services.vectorstore import query_tasks


def task_text(task: Task) -> str:
    parts = [task.title or "", task.description or "", task.definition_of_done or ""]
    return "\n".join([p for p in parts if p]).strip()


def score_overlap(new_task: Task, candidates: List[Task], threshold: float = 65.0) -> List[Tuple[Task, float, str]]:
    """
    Returns list of (candidate, score, evidence) sorted by score desc.
    Uses token_set_ratio for robust fuzzy matching.
    """
    base_text = task_text(new_task)
    results: List[Tuple[Task, float, str]] = []
    if not base_text:
        return results

    for cand in candidates:
        cand_text = task_text(cand)
        if not cand_text:
            continue
        score = float(fuzz.token_set_ratio(base_text, cand_text))
        if score >= threshold:
            evidence = f"Similarity {score:.1f} between task texts"
            results.append((cand, score, evidence))

    results.sort(key=lambda r: r[1], reverse=True)
    return results


def score_overlap_vector(task: Task, limit: int = 5) -> List[Tuple[int, float]]:
    base_text = task_text(task)
    if not base_text:
        return []
    matches = query_tasks(base_text, limit=limit + 1)
    results: List[Tuple[int, float]] = []
    for match in matches:
        if match["id"] == task.id:
            continue
        results.append((match["id"], match["distance"]))
    return results[:limit]
