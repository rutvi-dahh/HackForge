#!/usr/bin/env python3
"""
scripts/eval_extraction.py — offline extraction accuracy evaluator (SPEC s7 P2)

Usage:
    python scripts/eval_extraction.py --dir tests/receipts_eval/
    python scripts/eval_extraction.py --file path/to/receipt.jpg --expected expected.json
    python scripts/eval_extraction.py --db --show-cache  # show all cached extractions

Outputs a JSON report per file with accuracy metrics for:
    issue_date, total, unique_id, currency, category
"""

import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from services.extractor import extract_receipt, compute_file_hash

# ── Comparison helpers ─────────────────────────────────────────────────────────
def compare_date(extracted: str | None, expected: str | None) -> bool:
    """Allow DD-MM-YYYY and YYYY-MM-DD variants."""
    if not extracted or not expected:
        return extracted == expected
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            from datetime import datetime as dt
            e = dt.strptime(extracted.strip(), fmt).date()
            x = dt.strptime(expected.strip(), fmt).date()
            return e == x
        except ValueError:
            continue
    return extracted.strip() == expected.strip()

def compare_amount(extracted: float | None, expected: float | None, tol: float = 1.0) -> bool:
    if extracted is None or expected is None:
        return extracted == expected
    return abs(float(extracted) - float(expected)) <= tol

def score_one(extracted: dict, expected: dict) -> dict:
    """Return field-level pass/fail and an aggregate accuracy score."""
    fields = {
        "issue_date":    compare_date(extracted.get("issue_date"), expected.get("issue_date")),
        "total":         compare_amount(extracted.get("total"), expected.get("total")),
        "unique_id":     (str(extracted.get("unique_id") or "").strip().lower() ==
                          str(expected.get("unique_id") or "").strip().lower()),
        "currency":      ((extracted.get("currency") or "").upper() ==
                          (expected.get("currency") or "").upper()),
        "model_category": ((extracted.get("model_category") or "").lower() ==
                           (expected.get("model_category") or "").lower()),
    }
    passed = sum(1 for v in fields.values() if v)
    total  = len(fields)
    return {
        "fields": fields,
        "pass": passed,
        "total": total,
        "accuracy": round(passed / total, 2)
    }

# ── Run ────────────────────────────────────────────────────────────────────────
def eval_file(file_path: Path, expected: dict | None, dry_run: bool = False) -> dict:
    print(f"\n{'='*60}")
    print(f"File: {file_path.name}")
    file_bytes = file_path.read_bytes()
    file_hash  = compute_file_hash(file_bytes)
    print(f"SHA256: {file_hash[:12]}…")

    if dry_run:
        print("[DRY RUN] skipping extraction API call")
        return {"file": str(file_path), "status": "dry_run"}

    extracted = extract_receipt(file_bytes, file_path.name)

    result = {
        "file": str(file_path),
        "hash": file_hash,
        "extracted": {
            "issue_date":    extracted.get("issue_date"),
            "total":         extracted.get("total"),
            "unique_id":     extracted.get("unique_id"),
            "currency":      extracted.get("currency"),
            "model_category": extracted.get("model_category"),
            "image_quality": extracted.get("image_quality"),
            "conf":          extracted.get("conf"),
        },
        "ocr_text_present": bool(extracted.get("_ocr_text")),
    }

    if expected:
        score = score_one(extracted, expected)
        result["expected"] = {k: expected.get(k) for k in
                              ("issue_date","total","unique_id","currency","model_category")}
        result["score"] = score
        status_icon = "✅" if score["accuracy"] == 1.0 else ("⚠️" if score["accuracy"] >= 0.6 else "❌")
        print(f"Score: {score['pass']}/{score['total']} ({score['accuracy']*100:.0f}%) {status_icon}")
        for field, ok in score["fields"].items():
            ext_val = result["extracted"].get(field)
            exp_val = expected.get(field)
            icon = "✓" if ok else "✗"
            print(f"  [{icon}] {field}: got={ext_val!r}  want={exp_val!r}")
    else:
        print("Extracted (no oracle):")
        for k, v in result["extracted"].items():
            print(f"  {k}: {v!r}")

    return result

def show_db_cache(db_path: str = "receipt_graveyard.db"):
    import sqlite3
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT file_hash, model, created_at FROM extraction_cache ORDER BY created_at DESC").fetchall()
    print(f"\n{'='*60}")
    print(f"Extraction cache: {len(rows)} entries in {db_path}")
    for r in rows:
        print(f"  {r[2]}  {r[0][:12]}…  model={r[1]}")
    conn.close()

def main():
    ap = argparse.ArgumentParser(description="Receipt extraction evaluator (SPEC s7)")
    ap.add_argument("--dir",  help="Directory of image/PDF files to evaluate")
    ap.add_argument("--file", help="Single file to evaluate")
    ap.add_argument("--expected", help="JSON file with expected values for --file")
    ap.add_argument("--oracle", help="JSON file mapping filename -> expected dict (for --dir)")
    ap.add_argument("--show-cache", action="store_true", help="Show extraction_cache table")
    ap.add_argument("--dry-run", action="store_true", help="Parse files but skip API calls")
    ap.add_argument("--out", default=None, help="Write JSON report to this file")
    args = ap.parse_args()

    if args.show_cache:
        show_db_cache()
        return

    results = []

    if args.file:
        fp = Path(args.file)
        expected = None
        if args.expected:
            expected = json.loads(Path(args.expected).read_text())
        results.append(eval_file(fp, expected, dry_run=args.dry_run))

    elif args.dir:
        scan_dir = Path(args.dir)
        oracle = {}
        if args.oracle:
            oracle = json.loads(Path(args.oracle).read_text())
        exts = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}
        files = sorted(p for p in scan_dir.iterdir() if p.suffix.lower() in exts)
        if not files:
            print(f"No image/PDF files found in {scan_dir}")
            sys.exit(1)
        for fp in files:
            expected = oracle.get(fp.name) or oracle.get(fp.stem)
            results.append(eval_file(fp, expected, dry_run=args.dry_run))

    else:
        ap.print_help()
        return

    # Summary
    scored = [r for r in results if "score" in r]
    print(f"\n{'='*60}")
    print(f"SUMMARY: {len(results)} file(s) evaluated, {len(scored)} scored against oracle")
    if scored:
        avg = sum(r["score"]["accuracy"] for r in scored) / len(scored)
        fields_all = {}
        for r in scored:
            for f, ok in r["score"]["fields"].items():
                fields_all.setdefault(f, []).append(ok)
        print(f"Average accuracy: {avg*100:.1f}%")
        print("Per-field accuracy:")
        for f, vals in fields_all.items():
            print(f"  {f}: {sum(vals)}/{len(vals)} = {sum(vals)/len(vals)*100:.0f}%")

    if args.out:
        out_path = Path(args.out)
        out_path.write_text(json.dumps(results, indent=2))
        print(f"\nReport saved to: {out_path}")

if __name__ == "__main__":
    main()
