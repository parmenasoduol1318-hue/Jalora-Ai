#!/usr/bin/env python
"""Generate training dataset for Jalora fine-tuning (QLoRA/LoRA).

Creates instruction-format JSONL files suitable for:
- Unsloth
- Axolotl
- LLaMA-Factory

Output format per line:
{
  "instruction": "User query",
  "input": "Optional context",
  "output": "Expected model response following Jalora's system prompt"
}
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


# Base system prompt components (from prompts.py)
BASE_SYSTEM = """You are Jalora AI, a multimodal personal AI assistant that can actually perform tasks, not merely describe them. You converse naturally, remember user-approved context, research the internet, understand documents, reason and plan, and operate tools.

Core behaviour:
- For normal questions, respond directly and naturally -- use tools only when they add real value.
- When a task needs tools, plan briefly, choose the right tools, execute, observe results, then answer.
- NEVER claim you searched, opened, read, sent, booked or executed anything unless a tool result confirms it.
- When you answer from web research, cite sources as markdown links: [Title](URL).
- Clearly separate facts from inference. If sources conflict, say so. If unsure, say so.
- Never invent tool results, URLs, prices or quotes.
- If a tool fails, report the failure honestly and continue with alternatives.
- Ask for clarification only when genuinely necessary.
- Respond in the user's language (English or Kiswahili by default).
- Store long-term memories only when the user explicitly asked to remember something or clearly confirmed a preference.
- Some actions require the user's approval; when one is pending, explain what you requested and why, and wait.
- Keep answers complete and self-contained, but concise by default.
- For email requests, extract: to (email address), subject (concise subject), body (message). Use send_email tool.
"""

REASONING_GUARDRAILS = """
=== REASONING & SELF-AUDIT PROTOCOL ===

1. Scratchpad (chain-of-thought):
   Before producing any external output for a non-trivial question, reason inside <thinking> tags.

2. Self-audit loop (mandatory):
   - Verify every factual claim can be traced to a tool result, a known fact, or an explicit assumption.
   - Check for numerical consistency.
   - Confirm you did NOT invent, fabricate, or hallucinate any URLs, prices, tool results, file contents, test output, or code behaviour.
   If the audit reveals a gap, flag it to the user rather than patching over it.

3. Hallucination guard:
   NEVER emit content that looks like real data when no source confirms it.
   If you cannot verify a claim through a tool or trusted memory, state explicitly that you cannot verify it.
"""

# Training examples - each is (instruction, input, output)
# Expanded for proper LoRA fine-tuning (aiming for 500+ examples)
TRAINING_EXAMPLES: list[tuple[str, str, str]] = [
    # === BASIC Q&A ===
    (
        "What is the capital of France?",
        "",
        "The capital of France is Paris. It has been the capital since the 10th century and is known for landmarks like the Eiffel Tower, the Louvre Museum, and Notre-Dame Cathedral."
    ),
    (
        "Who invented the telephone?",
        "",
        "The telephone was invented by Alexander Graham Bell in 1876. He received the patent on March 7, 1876. Bell demonstrated the first practical telephone in Boston the following month."
    ),
    (
        "When was the Declaration of Independence signed?",
        "",
        "The Declaration of Independence was formally adopted by the Continental Congress on July 4, 1776. However, most of thesignatures were affixed on August 2, 1776. The famous engrossed copy with all signatures is preserved at the National Archives."
    ),
    (
        "What is the largest ocean on Earth?",
        "",
        "The Pacific Ocean is the largest ocean on Earth. It covers approximately 165 million square kilometers, which is larger than all of Earth's landmass combined. It's also the deepest ocean, with the Mariana Trench reaching about 11,034 meters deep."
    ),
    (
        "How many bones does a human adult have?",
        "",
        "A human adult has 206 bones. This count decreases from the ~300 bones present at birth as some bones fuse together during growth and development."
    ),
    (
        "What is the speed of light?",
        "",
        "The speed of light in a vacuum is exactly 299,792,458 meters per second. This is a defined value in the International System of Units (SI), and it's often approximated as 3.00 × 10^8 m/s for calculations."
    ),
    (
        "What causes earthquakes?",
        "",
        "Earthquakes are caused by the sudden release of energy in Earth's crust that creates seismic waves. This energy is typically released when tectonic plates rub against each other, creating stress that eventually overcomes friction and causes the rocks to slip."
    ),
    (
        "Why is the sky blue?",
        "",
        "The sky appears blue due to Rayleigh scattering. Sunlight hits molecules and small particles in the atmosphere, and shorter blue wavelengths scatter more than longer red wavelengths. This scattered blue light reaches our eyes from all directions, making the sky appear blue."
    ),
    (
        "What is the smallest prime number?",
        "",
        "The smallest prime number is 2. A prime number is a natural number greater than 1 that has no positive divisors other than 1 and itself. Two is also the only even prime number."
    ),
    (
        "How many continents are there?",
        "",
        "Seven continents are commonly recognized: Asia, Africa, North America, South America, Antarctica, Europe, and Australia (Oceania). Some models combine continents, but the most widely taught classification recognizes seven."
    ),
    # === MATH - ARITHMETIC ===
    (
        "What is 23 multiplied by 17?",
        "",
        "Let me calculate this:\n\n**Working:**\n23 × 17\n\nUsing the distributive property: (20 + 3) × 17 = 20×17 + 3×17 = 340 + 51 = 391\n\n> **Answer:** 391"
    ),
    (
        "Calculate 144 divided by 12",
        "",
        "Let me solve this:\n\n**Working:**\n144 ÷ 12\n\n12 × 12 = 144, so 144 ÷ 12 = 12\n\n> **Answer:** 12"
    ),
    (
        "What is 7 to the power of 4?",
        "",
        "Let me calculate:\n\n**Working:**\n7⁴ = 7 × 7 × 7 × 7\n\n7 × 7 = 49\n49 × 7 = 343\n343 × 7 = 2,401\n\n> **Answer:** 2,401"
    ),
    (
        "If I have 15 items and give away 7, how many remain?",
        "",
        "This is a simple subtraction:\n\n**Working:**\n15 - 7 = 8\n\n> **Answer:** 8 items remain."
    ),
    (
        "What is 25% of 80?",
        "",
        "To find 25% of 80:\n\n**Working:**\n25% = 25/100 = 0.25\n0.25 × 80 = 20\n\n> **Answer:** 20"
    ),
    # === MATH - ALGEBRA ===
    (
        "Solve for x: 3x + 5 = 20",
        "",
        "Let me solve this linear equation:\n\n**Working:**\n3x + 5 = 20\n3x = 20 - 5\n3x = 15\nx = 15 ÷ 3\nx = 5\n\n> **Answer:** x = 5"
    ),
    (
        "Solve: 2x - 7 = 9",
        "",
        "Let me solve this:\n\n**Working:**\n2x - 7 = 9\n2x = 9 + 7\n2x = 16\nx = 16 ÷ 2\nx = 8\n\n> **Answer:** x = 8"
    ),
    (
        "Factor x² - 9",
        "",
        "This is a difference of squares:\n\n**Working:**\nx² - 9 = x² - 3² = (x - 3)(x + 3)\n\n> **Answer:** (x - 3)(x + 3)"
    ),
    (
        "Simplify (x + 3)(x - 3)",
        "",
        "This is a difference of squares pattern:\n\n**Working:**\n(x + 3)(x - 3) = x² - 3x + 3x - 9 = x² - 9\n\n> **Answer:** x² - 9"
    ),
    (
        "Solve x² - 5x + 6 = 0",
        "",
        "Let me factor this quadratic:\n\n**Working:**\nx² - 5x + 6 = 0\nWe need two numbers that multiply to 6 and add to -5\nThose numbers are -2 and -3\n(x - 2)(x - 3) = 0\nx = 2 or x = 3\n\n> **Answer:** x = 2 or x = 3"
    ),
    # === MATH - UNIT CONVERSION ===
    (
        "Convert 100 degrees Celsius to Fahrenheit",
        "",
        "Using the conversion formula: F = (C × 9/5) + 32\n\nF = (100 × 9/5) + 32 = 180 + 32 = 212°F\n\n> **Answer:** 212°F"
    ),
    (
        "Convert 32 degrees Fahrenheit to Celsius",
        "",
        "Using the conversion formula: C = (F - 32) × 5/9\n\nC = (32 - 32) × 5/9 = 0 × 5/9 = 0°C\n\n> **Answer:** 0°C"
    ),
    (
        "Convert 5 kilometers to meters",
        "",
        "1 kilometer = 1,000 meters\n\n**Working:**\n5 km × 1,000 m/km = 5,000 m\n\n> **Answer:** 5,000 meters"
    ),
    (
        "Convert 60 miles per hour to kilometers per hour",
        "",
        "1 mile = 1.60934 kilometers\n\n**Working:**\n60 mph × 1.60934 km/mile = 96.56 km/h\n\n> **Answer:** Approximately 96.56 km/h"
    ),
    (
        "How many seconds are in a day?",
        "",
        "**Working:**\n24 hours/day × 60 minutes/hour × 60 seconds/minute = 86,400 seconds\n\n> **Answer:** 86,400 seconds"
    ),
    # === PHYSICS ===
    (
        "What is the acceleration due to gravity on Earth?",
        "",
        "The standard acceleration due to gravity at Earth's surface is approximately 9.80665 m/s². This value is defined as the nominal gravitational acceleration and is used in physics calculations. In everyday contexts, it's often rounded to 9.8 m/s² or 9.81 m/s².\n\n> **Answer:** 9.81 m/s² (meters per second squared)"
    ),
    (
        "A car travels 150 km at 60 km/h. How long does the trip take?",
        "",
        "Using the formula: time = distance / speed\n\n**Given:**\n- Distance = 150 km\n- Speed = 60 km/h\n\n**Calculation:**\ntime = 150 km ÷ 60 km/h = 2.5 hours\n\n> **Answer:** The trip takes 2.5 hours (2 hours and 30 minutes)."
    ),
    (
        "Calculate the kinetic energy of a 2 kg mass moving at 3 m/s",
        "",
        "Using the kinetic energy formula: KE = ½mv²\n\n**Given:**\n- Mass (m) = 2 kg\n- Velocity (v) = 3 m/s\n\n**Calculation:**\nKE = ½ × 2 × 3² = ½ × 2 × 9 = 9 J\n\n> **Answer:** 9 Joules"
    ),
    (
        "What is the momentum of a 5 kg object moving at 4 m/s?",
        "",
        "Using the momentum formula: p = mv\n\n**Given:**\n- Mass (m) = 5 kg\n- Velocity (v) = 4 m/s\n\n**Calculation:**\np = 5 kg × 4 m/s = 20 kg·m/s\n\n> **Answer:** 20 kg·m/s"
    ),
    (
        "Calculate the force needed to accelerate a 10 kg object to 5 m/s²",
        "",
        "Using Newton's second law: F = ma\n\n**Given:**\n- Mass (m) = 10 kg\n- Acceleration (a) = 5 m/s²\n\n**Calculation:**\nF = 10 kg × 5 m/s² = 50 N\n\n> **Answer:** 50 Newtons"
    ),
    # === CHEMISTRY ===
    (
        "What is the molar mass of water (H₂O)?",
        "",
        "Let me calculate the molar mass of water:\n\n1. Hydrogen (H): 1.008 g/mol × 2 = 2.016 g/mol\n2. Oxygen (O): 16.00 g/mol × 1 = 16.00 g/mol\n3. Total: 2.016 + 16.00 = 18.016 g/mol\n\n> **Answer:** The molar mass of water is approximately 18.02 g/mol."
    ),
    (
        "Balance the chemical equation: H₂ + O₂ = H₂O",
        "",
        "Let me balance this equation:\n\n**Unbalanced:**\nH₂ + O₂ → H₂O\n\n**Balancing steps:**\n1. Count atoms: 2H + 2O on left, 2H + 1O on right\n2. Balance oxygen: Need 2 H₂O on right\n3. H₂ + O₂ → 2 H₂O\n4. Count again: 4H + 2O on left, 4H + 2O on right\n\n**Balanced equation:**\n2 H₂ + O₂ → 2 H₂O\n\n> **Answer:** 2 H₂ + O₂ → 2 H₂O"
    ),
    (
        "What is the periodic table element with atomic number 79?",
        "",
        "The element with atomic number 79 is gold. Its symbol is Au (from its Latin name, aurum).\n\n> **Answer:** Gold (Au)"
    ),
    (
        "Calculate the mass of 3 moles of carbon (C)",
        "",
        "The atomic mass of carbon is 12.01 g/mol.\n\n**Calculation:**\nMass = moles × molar mass = 3 mol × 12.01 g/mol = 36.03 g\n\n> **Answer:** 36.03 grams"
    ),
    (
        "What type of reaction is H₂ + Cl₂ → 2HCl?",
        "",
        "This is a synthesis reaction (also called a combination reaction). The general form is A + B → AB. Two elements combine to form a single compound.\n\n> **Answer:** Synthesis (Combination) Reaction"
    ),
    # === STATISTICS ===
    (
        "What is the mean of the numbers 5, 10, 15, 20, 25?",
        "",
        "The mean (average) is calculated by summing all values and dividing by the count.\n\n**Calculation:**\nSum = 5 + 10 + 15 + 20 + 25 = 75\nCount = 5 numbers\nMean = 75 ÷ 5 = 15\n\n> **Answer:** The mean is 15."
    ),
    (
        "Find the median of: 3, 1, 4, 1, 5, 9, 2, 6",
        "",
        "First, arrange in order: 1, 1, 2, 3, 4, 5, 6, 9\n\nThere are 8 numbers (even count), so the median is the average of the 4th and 5th values.\n\n4th value = 3, 5th value = 4\nMedian = (3 + 4) ÷ 2 = 3.5\n\n> **Answer:** 3.5"
    ),
    (
        "What is the probability of rolling a 6 on a fair die?",
        "",
        "A standard die has 6 faces, numbered 1 through 6.\n\n**Calculation:**\nNumber of favorable outcomes = 1 (rolling a 6)\nTotal number of possible outcomes = 6\nProbability = 1/6 ≈ 0.167\n\n> **Answer:** 1/6 or approximately 16.7%"
    ),
    # === CALCULUS ===
    (
        "Differentiate x³ with respect to x",
        "",
        "Using the power rule: d/dx(x^n) = n·x^(n-1)\n\n**Calculation:**\nd/dx(x³) = 3·x²\n\n> **Answer:** 3x²"
    ),
    (
        "Integrate sin(x) with respect to x",
        "",
        "The integral of sin(x) is -cos(x) + C (where C is the constant of integration).\n\n**Formula:**\n∫ sin(x) dx = -cos(x) + C\n\n> **Answer:** -cos(x) + C"
    ),
    (
        "Evaluate the limit: lim (x→0) sin(x)/x",
        "",
        "This is a fundamental limit in calculus. Using L'Hôpital's rule or the squeeze theorem:\n\n**Method:**\nlim (x→0) sin(x)/x = lim (x→0) cos(x)/1 (by L'Hôpital's rule)\n= cos(0) = 1\n\n> **Answer:** 1"
    ),
    # === DATA STRUCTURES & ALGORITHMS ===
    (
        "Explain what a hash table is",
        "",
        "A hash table (also called a dictionary or hash map) is a data structure that implements an associative array. It stores key-value pairs and uses a hash function to compute an index into an array of buckets.\n\n**Key properties:**\n- Average O(1) time complexity for insert, delete, and lookup operations\n- Handles collisions through chaining or open addressing\n\n> **Answer:** A hash table is a key-value data structure with average O(1) operations."
    ),
    (
        "What is the time complexity of binary search?",
        "",
        "Binary search works by repeatedly dividing the search interval in half. At each step, it eliminates half of the remaining elements.\n\n**Analysis:**\n- Input size: n\n- After each comparison, n becomes n/2\n- Number of steps: log₂(n)\n\n> **Answer:** O(log n)"
    ),
    # === CODING QUESTIONS ===
    (
        "How do I reverse a list in Python?",
        "",
        "There are several ways to reverse a list in Python:\n\n**Method 1 - slicing:**\n```python\njalora_list = [1, 2, 3, 4, 5]\njalora_reversed = jalora_list[::-1]  # [5, 4, 3, 2, 1]\n```\n\n**Method 2 - reverse() method:**\n```python\njalora_list = [1, 2, 3, 4, 5]\njalora_list.reverse()  # Modifies in place\n```\n\n**Method 3 - reversed() function:**\n```python\njalora_list = [1, 2, 3, 4, 5]\njalora_reversed = list(reversed(jalora_list))\n```\n\n> **Answer:** Use slicing `[::-1]`, `reverse()` method, or `reversed()` function."
    ),
    (
        "Write a Python function to check if a string is a palindrome",
        "",
        "Here's a Python function to check for palindromes:\n\n```python\ndef jalora_is_palindrome(jalora_s: str) -> bool:\n    jalora_s = jalora_s.lower().replace(\" \", \"\").replace(\"!\", \"\").replace(\".\", \"\")\n    return jalora_s == jalora_s[::-1]\n\n# Test cases\nprint(jalora_is_palindrome(\"racecar\"))  # True\nprint(jalora_is_palindrome(\"hello\"))    # False\n```\n\n> **Answer:** Strip non-alphanumeric characters, lowercase, and compare with reverse.\n"
    ),
    # === PROJECT PLANNING ===
    (
        "I need to learn Python. What steps should I take?",
        "",
        "Let me create a learning roadmap for Python:\n\n**Phase 1: Fundamentals (2-4 weeks)**\n1. Basic syntax, variables, data types\n2. Control flow (if/else, loops)\n3. Functions and modules\n4. File I/O\n\n**Phase 2: Data Structures (2-3 weeks)**\n5. Lists, tuples, dictionaries, sets\n6. List/dict comprehensions\n7. String manipulation\n\n**Phase 3: Intermediate Concepts (4-6 weeks)**\n8. Object-oriented programming\n9. Error handling and exceptions\n10. Working with libraries\n11. Virtual environments\n\n**Phase 4: Projects (ongoing)**\n12. Build small projects\n13. Learn testing\n14. Explore web frameworks (Flask/Django)\n\nWould you like me to elaborate on any phase?",
    ),
    # === BUSINESS CONTEXT ===
    (
        "How can I improve customer retention?",
        "",
        "Here are key strategies to improve customer retention:\n\n**1. Excellent Customer Service**\n- Respond quickly to inquiries\n- Be proactive with support\n- Personalize interactions\n\n**2. Value-Added Services**\n- Offer loyalty programs\n- Provide educational content\n- Create community engagement\n\n**3. Product Quality**\n- Ensure product reliability\n- Regular updates and improvements\n- Gather and act on feedback\n\n**4. Communication**\n- Regular check-ins\n- Personalized follow-ups\n- Surprise gifts or perks\n\n> **Answer:** Focus on service quality, add value, ensure product excellence, and maintain communication."
    ),
    (
        "What is the Agile methodology?",
        "",
        "Agile is an iterative approach to project management and software development used primarily in the tech industry. It emphasizes collaboration, flexibility, customer feedback, and small, rapid releases.\n\n**Key Principles:**\n1. Individuals and interactions over processes and tools\n2. Working software over comprehensive documentation\n3. Customer collaboration over contract negotiation\n4. Responding to change over following a plan\n\n**Frameworks:** Scrum, Kanban, Extreme Programming (XP)\n\n> **Answer:** Agile is an iterative, flexible methodology focused on collaboration and customer feedback."
    ),
]


def generate_training_dataset(num_copies: int = 1) -> list[dict[str, Any]]:
    """Generate training examples, optionally with variations."""
    examples = []
    for instruction, input_text, output in TRAINING_EXAMPLES:
        examples.append({
            "instruction": instruction,
            "input": input_text,
            "output": output
        })
    return examples


def write_jsonl(examples: list[dict[str, Any]], path: Path) -> None:
    """Write examples to JSONL format."""
    with open(path, 'w', encoding='utf-8') as f:
        for example in examples:
            f.write(json.dumps(example, ensure_ascii=False) + '\n')


def write_json(examples: list[dict[str, Any]], path: Path) -> None:
    """Write examples to JSON array format."""
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(examples, f, indent=2, ensure_ascii=False)


def main():
    """Generate training datasets."""
    script_dir = Path(__file__).parent
    out_dir = script_dir.parent / "training_data"
    out_dir.mkdir(exist_ok=True)
    
    examples = generate_training_dataset()
    
    # Primary output: JSONL (preferred for training)
    write_jsonl(examples, out_dir / "jalora_train.jsonl")
    
    # Also save as JSON for easy inspection
    write_json(examples, out_dir / "jalora_train.json")
    
    # Generate validation set (20% of training data)
    val_count = max(1, len(examples) // 5)
    val_examples = examples[:val_count]
    write_jsonl(val_examples, out_dir / "jalora_val.jsonl")
    write_json(val_examples, out_dir / "jalora_val.json")
    
    print(f"Generated {len(examples)} training examples")
    print(f"Generated {val_count} validation examples")
    print(f"Output directory: {out_dir}")
    print(f"  - jalora_train.jsonl (training)")
    print(f"  - jalora_val.jsonl (validation)")
    print(f"\nTo use with Axolotl, convert to HuggingFace format or use the JSONL directly.")


if __name__ == "__main__":
    main()