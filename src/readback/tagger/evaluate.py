"""Scores for a parser's clearances against labeled ones."""

from __future__ import annotations

from collections import Counter

from readback.clearance import Clearance


def _items(c: Clearance) -> Counter:
    return Counter((it.kind, it.value) for it in c.items)


def same(pred: Clearance, gold: Clearance) -> bool:
    """Exactly right: callsign, and every item with its value and qualifier."""
    return pred.callsign == gold.callsign and Counter(pred.items) == Counter(gold.items)


def score(preds: list[Clearance], golds: list[Clearance],
          features: list[set[str]] | None = None) -> dict:  # fmt: skip
    """Exact match, item precision/recall/F1 on (kind, value), callsign accuracy, F1 per item
    kind and, when features are given, the error rate of examples carrying each feature."""
    tp = fp = fn = exact = cs_ok = 0
    kind_tp, kind_fp, kind_fn = Counter(), Counter(), Counter()
    feat_n, feat_err = Counter(), Counter()
    for i, (p, g) in enumerate(zip(preds, golds, strict=True)):
        pi, gi = _items(p), _items(g)
        hit = pi & gi
        tp += sum(hit.values())
        fp += sum((pi - gi).values())
        fn += sum((gi - pi).values())
        for (kind, _), n in hit.items():
            kind_tp[kind] += n
        for (kind, _), n in (pi - gi).items():
            kind_fp[kind] += n
        for (kind, _), n in (gi - pi).items():
            kind_fn[kind] += n
        ok = same(p, g)
        exact += ok
        cs_ok += p.callsign == g.callsign
        for f in features[i] if features else ():
            feat_n[f] += 1
            feat_err[f] += not ok
    n = max(1, len(golds))
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    kinds = sorted(set(kind_tp) | set(kind_fp) | set(kind_fn))
    return {
        "n": len(golds),
        "exact": exact / n,
        "item_precision": precision,
        "item_recall": recall,
        "item_f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "callsign": cs_ok / n,
        "kind_f1": {k: 2 * kind_tp[k] / (2 * kind_tp[k] + kind_fp[k] + kind_fn[k]) for k in kinds},
        "feature_error": {f: feat_err[f] / feat_n[f] for f in sorted(feat_n)},
    }
