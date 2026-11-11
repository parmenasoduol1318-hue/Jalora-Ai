#!/usr/bin/env python3
"""
Comprehensive Jalora Training Data — ALL Sources Merged
=======================================================

This script merges EVERY training data source in the Jalora project into a
single, error-free JSONL dataset ready for Colab LoRA fine-tuning.

Sources merged:
  1. build_comprehensive_training_data.py  — generated examples (13 capability categories)
  2. generate_jalora_training_data.py      — codebase-derived examples
  3. All existing JSONL files              — existing validated training data
  4. jalora_train.json / jalora_val.json   — JSON-array format training data

Normalisation:
  - Every example becomes {"instruction", "input", "output"} (string fields).
  - The `messages` conversational format (email/calendar assistant) is converted:
    first user message → instruction, assistant reply → output.
  - Duplicate instructions are removed (keeping first occurrence).
  - Empty instructions or outputs are rejected.

Output:
  training_data/jalora_comprehensive_all.jsonl      — EVERYTHING in one file
  training_data/jalora_comprehensive_train.jsonl    — 85% split (for training)
  training_data/jalora_comprehensive_val.jsonl      — 15% split (for validation)
  training_data/jalora_comprehensive_meta.json      — metadata + statistics

Usage:
    python generate_comprehensive_training_data.py

Colab:
    Upload jalora_comprehensive_all.jsonl (or _train/_val) to Colab.
    Use train_all_in_colab.py or train_lora_simple.py to fine-tune.
"""
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path
from typing import Any

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "training_data"

# Files that are NOT JSONL (they're JSON arrays) or are meta-only
SKIP_FILES = {
    "jalora_train.json",        # JSON array — handled separately
    "jalora_val.json",          # JSON array — handled separately
    "jalora_comprehensive_meta.json",  # metadata, not training data
}


def _safe_str(val: Any) -> str:
    """Coerce any value to a non-None string."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, (list, dict)):
        return json.dumps(val, ensure_ascii=False, indent=2)
    return str(val)


def _is_valid(example: dict[str, Any]) -> bool:
    """Check that an example has non-empty instruction and output strings."""
    instr = _safe_str(example.get("instruction", "")).strip()
    outp = _safe_str(example.get("output", "")).strip()
    return bool(instr) and bool(outp)


def _normalize(example: dict[str, Any]) -> dict[str, str]:
    """Ensure an example has exactly instruction/input/output keys as strings."""
    return {
        "instruction": _safe_str(example.get("instruction", "")).strip(),
        "input": _safe_str(example.get("input", "")).strip(),
        "output": _safe_str(example.get("output", "")).strip(),
    }


def _from_messages(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Convert a conversational `messages` list to instruction/input/output.

    Strategy:
      - Collect all user messages (excluding system prompts) → instruction + input
      - Collect all assistant messages → output
      - If there are multiple turns, instruction = first user msg, input = rest
    """
    user_msgs = [m for m in messages if m.get("role") == "user"]
    asst_msgs = [m for m in messages if m.get("role") == "assistant"]

    if not user_msgs or not asst_msgs:
        return None

    instruction = _safe_str(user_msgs[0].get("content", "")).strip()
    if len(user_msgs) > 1:
        rest = _safe_str(user_msgs[1].get("content", "")).strip()
    else:
        rest = ""

    output = _safe_str(asst_msgs[0].get("content", "")).strip()
    if not instruction or not output:
        return None

    return {"instruction": instruction, "input": rest, "output": output}


# ----------------------------------------------------------------------
# Source 1: Generated examples from build_comprehensive_training_data.py
# ----------------------------------------------------------------------
def _source_generated() -> list[dict[str, str]]:
    """Import and run the _examples() generator from build_comprehensive_training_data.py."""
    import importlib.util

    spec_path = SCRIPT_DIR / "build_comprehensive_training_data.py"
    if not spec_path.exists():
        return []

    spec = importlib.util.spec_from_file_location("_gen_comprehensive", spec_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    examples = mod.get_all_examples()  # returns list of dicts
    return [_normalize(e) for e in examples if _is_valid(e)]


# ----------------------------------------------------------------------
# Source 2: Generated examples from generate_jalora_training_data.py
# ----------------------------------------------------------------------
def _source_jalora_generated() -> list[dict[str, str]]:
    """Import and run generate_training_data() from generate_jalora_training_data.py."""
    import importlib.util

    spec_path = SCRIPT_DIR / "generate_jalora_training_data.py"
    if not spec_path.exists():
        return []

    spec = importlib.util.spec_from_file_location("_gen_jalora", spec_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    examples = mod.generate_training_data()
    return [_normalize(e) for e in examples if _is_valid(e)]


# ----------------------------------------------------------------------
# Source 3: Existing JSONL files
# ----------------------------------------------------------------------
def _source_jsonl_files() -> tuple[list[dict[str, str]], dict[str, int]]:
    """Read all valid JSONL files in training_data/. Returns (examples, stats)."""
    examples: list[dict[str, str]] = []
    stats: dict[str, int] = {}

    for fname in sorted(os.listdir(DATA_DIR)):
        if not fname.endswith(".jsonl"):
            continue
        if fname in SKIP_FILES:
            continue

        fpath = DATA_DIR / fname
        count = 0
        with open(fpath, encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    # Skip invalid lines — they won't make it into the final dataset
                    continue

                if not isinstance(obj, dict):
                    continue

                if "instruction" in obj and "output" in obj:
                    # Standard format
                    norm = _normalize(obj)
                    if _is_valid(norm):
                        examples.append(norm)
                        count += 1
                elif "messages" in obj:
                    # Conversational format — convert
                    conv = _from_messages(obj["messages"])
                    if conv:
                        norm = _normalize(conv)
                        if _is_valid(norm):
                            examples.append(norm)
                            count += 1
                else:
                    # Unknown format — skip
                    pass

        stats[fname] = count

    return examples, stats


# ----------------------------------------------------------------------
# Source 4: JSON-array files (jalora_train.json, jalora_val.json)
# ----------------------------------------------------------------------
def _source_json_arrays() -> list[dict[str, str]]:
    """Read JSON-array training files (jalora_train.json, jalora_val.json)."""
    examples: list[dict[str, str]] = []
    for fname in ("jalora_train.json", "jalora_val.json"):
        fpath = DATA_DIR / fname
        if not fpath.exists():
            continue
        try:
            with open(fpath, encoding="utf-8") as f:
                arr = json.load(f)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(arr, list):
            continue
        for obj in arr:
            if isinstance(obj, dict):
                norm = _normalize(obj)
                if _is_valid(norm):
                    examples.append(norm)
    return examples


# ----------------------------------------------------------------------
# Merge, deduplicate, split, validate, write
# ----------------------------------------------------------------------
def merge_all() -> list[dict[str, str]]:
    """Collect examples from all sources, deduplicate by instruction."""
    # Collect
    all_examples: list[dict[str, str]] = []

    gen = _source_generated()
    print(f"  build_comprehensive_training_data.py: {len(gen)} examples")
    all_examples.extend(gen)

    jal = _source_jalora_generated()
    print(f"  generate_jalora_training_data.py:   {len(jal)} examples")
    all_examples.extend(jal)

    jsonl_exs, stats = _source_jsonl_files()
    for fname, count in stats.items():
        print(f"  {fname}: {count} examples")
    all_examples.extend(jsonl_exs)

    arr_exs = _source_json_arrays()
    print(f"  jalora_train.json + jalora_val.json: {len(arr_exs)} examples")
    all_examples.extend(arr_exs)

    # Deduplicate by instruction (case-insensitive, stripped)
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for ex in all_examples:
        key = ex["instruction"].strip().lower()
        if key not in seen:
            seen.add(key)
            unique.append(ex)

    dupes = len(all_examples) - len(unique)
    print(f"\n  Total collected: {len(all_examples)}")
    print(f"  After dedup:     {len(unique)} (removed {dupes} duplicates)")
    return unique


def validate_jsonl(path: Path) -> int:
    """Read back every line and validate. Returns valid count."""
    count = 0
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)  # raises if invalid
            assert set(obj.keys()) == {"instruction", "input", "output"}, \
                f"Line {i}: unexpected keys {set(obj.keys())}"
            assert obj["instruction"].strip(), f"Line {i}: empty instruction"
            assert obj["output"].strip(), f"Line {i}: empty output"
            count += 1
    return count


def write_jsonl(examples: list[dict[str, str]], path: Path) -> None:
    """Write examples to JSONL, ensuring valid UTF-8 JSON on each line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for ex in examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")


def main() -> None:
    print("=" * 70)
    print("  Jalora Comprehensive Training Data Generator (ALL Sources)")
    print("=" * 70)

    print("\n[1/5] Collecting examples from all sources...")
    examples = merge_all()

    print("\n[2/5] Writing single comprehensive JSONL file...")
    all_path = DATA_DIR / "jalora_comprehensive_all.jsonl"
    write_jsonl(examples, all_path)
    all_count = validate_jsonl(all_path)
    print(f"  Written: {all_path}  ({all_count} valid examples)")

    print("\n[3/5] Splitting 85/15 for train/validation...")
    random.seed(42)
    shuffled = examples[:]
    random.shuffle(shuffled)

    split_idx = int(len(shuffled) * 0.85)
    train_set = shuffled[:split_idx]
    val_set = shuffled[split_idx:]

    print(f"  Train: {len(train_set)}  Val: {len(val_set)}")

    print("\n[4/5] Writing train/val files...")
    train_path = DATA_DIR / "jalora_comprehensive_train.jsonl"
    val_path = DATA_DIR / "jalora_comprehensive_val.jsonl"
    write_jsonl(train_set, train_path)
    write_jsonl(val_set, val_path)

    train_count = validate_jsonl(train_path)
    val_count = validate_jsonl(val_path)
    print(f"  {train_path}  ({train_count} examples)")
    print(f"  {val_path}  ({val_count} examples)")

    print("\n[5/5] Writing metadata...")
    meta = {
        "description": "Comprehensive Jalora training dataset covering ALL capabilities.",
        "version": "2.0",
        "format": "JSONL with {instruction, input, output}",
        "total_examples": len(train_set) + len(val_set),
        "train_examples": train_count,
        "val_examples": val_count,
        "all_in_one_file": "jalora_comprehensive_all.jsonl",
        "train_file": "jalora_comprehensive_train.jsonl",
        "val_file": "jalora_comprehensive_val.jsonl",
        "model_recommendation": "Qwen/Qwen1.5-7B-Instruct",
        "training_epochs_recommended": 3,
        "learning_rate": 2e-05,
        "lora_rank": 64,
        "lora_alpha": 128,
        "lora_dropout": 0.1,
        "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj",
                           "gate_proj", "up_proj", "down_proj"],
        "max_seq_length": 2048,
        "sources_merged": [
            "build_comprehensive_training_data.py (_examples)",
            "generate_jalora_training_data.py (generate_training_data)",
            "jalora_train.jsonl",
            "jalora_val.jsonl",
            "jalora_ollama_train.jsonl",
            "jalora_train_jalora.jsonl",
            "jalora_val_jalora.jsonl",
            "jalora_agent_behavior.jsonl",
            "jalora_task_planning_and_workflows.jsonl",
            "jalora_software_engineering_and_debugging.jsonl",
            "jalora_tool_calling.jsonl",
            "jalora_person_search.jsonl",
            "jalora_memory_and_personalization.jsonl",
            "jalora_job_search_cv_and_applications.jsonl",
            "jalora_email_and_calendar_assistant.jsonl",
            "jalora_train.json (JSON array)",
            "jalora_val.json (JSON array)",
        ],
    }
    meta_path = DATA_DIR / "jalora_comprehensive_meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print(f"  {meta_path}")

    print("\n" + "=" * 70)
    print("  SUCCESS: Comprehensive training data generated and validated!")
    print(f"  All files are valid JSONL — zero format errors.")
    print(f"  Total unique examples: {len(examples)}")
    print("=" * 70)


if __name__ == "__main__":
    main()
