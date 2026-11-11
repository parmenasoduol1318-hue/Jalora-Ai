# Jalora LoRA Adapter

This directory contains the trained LoRA adapter and supporting infrastructure
for running the fine-tuned Jalora model locally.

## Directory Structure

```
lora_adapters/
├── jalora_lora/              ← Trained adapter (from Colab training)
│   ├── adapter_config.json       Model & LoRA configuration
│   ├── adapter_model.safetensors  Fine-tuned weights (6.3 MB)
│   ├── training_summary.json     Training metrics summary
│   ├── tokenizer/                Tokenizer files
│   │   ├── tokenizer.json
│   │   ├── special_tokens_map.json
│   │   ├── vocab.json
│   │   ├── merges.txt
│   │   └── added_tokens.json
│   ├── chat_template.jinja
│   └── checkpoints/
│       ├── checkpoint-32/
│       └── checkpoint-48/
├── serve_local_lora.py       ← Local OpenAI-compatible inference server
├── load_jalora_adapter.py    ← Python loader for direct model access
└── README.md                 ← This file

## Training Audit Summary

| Property              | Value                          |
|-----------------------|--------------------------------|
| Base Model            | Qwen/Qwen1.5-0.5B-Chat         |
| Training Examples     | 123 (merged from 7 files)      |
| LoRA Rank (r)         | 8                              |
| LoRA Alpha            | 16                             |
| Dropout               | 0.05                           |
| Target Modules        | q_proj, k_proj, v_proj, o_proj |
| Epochs                | 3                              |
| Final Training Loss   | 5.1191                         |
| PEFT Version          | 0.18.0                         |
| Adapter Size          | 6.3 MB                         |

## New Training Data (561 examples)

These files were uploaded and merged into the training run:

| File                                      | Examples | Domain                        |
|-------------------------------------------|----------|-------------------------------|
| jalora_agent_behavior.jsonl               | 16       | Planning, tool use, execution |
| jalora_email_and_calendar_assistant.jsonl | 127      | Email & calendar automation   |
| jalora_job_search_cv_and_applications.jsonl | 83     | Job search, CV, applications  |
| jalora_memory_and_personalization.jsonl   | 87       | Memory, preferences           |
| jalora_software_engineering_and_debugging.jsonl | 147 | Coding, debugging            |
| jalora_task_planning_and_workflows.jsonl  | 81       | Task planning & workflows     |
| jalora_tool_calling.jsonl                 | 20       | Tool calling patterns         |
| **Total**                                 | **561**  |                               |

## How to Connect to the Backend

### Option 1: Local Inference Server (Recommended)

1. Install dependencies:
   ```bash
   pip install fastapi uvicorn sse-starlette transformers torch peft accelerate
   ```

2. Start the local server:
   ```bash
   cd jalora/lora_adapters
   python serve_local_lora.py
   ```

3. Enable in backend (`.env`):
   ```
   JALORA_LORA_ENABLED=true
   JALORA_LORA_ADAPTER_PATH=jalora/lora_adapters/jalora_lora
   JALORA_LORA_BASE_MODEL=Qwen/Qwen1.5-0.5B-Chat
   ```

4. Start the backend — it will route to `http://localhost:8001/v1` automatically.

### Option 2: Direct Model Loading

```python
from lora_adapters.load_jalora_adapter import load_jalora_model, generate_response
model, tokenizer = load_jalora_model()
response = generate_response(model, tokenizer, "<|im_start|>user\nWho are you?<|im_end|>\n<|im_start|>assistant\n")
```

## Positive Impact on Jalora's AI

The fine-tuned adapter improves the following capabilities:

1. **Agent Behavior** — Better planning, tool use, task execution, and recovery from failures
2. **Email & Calendar** — More accurate email drafting, calendar scheduling, and meeting prep
3. **Job Search & Applications** — Improved CV tailoring and job application workflows  
4. **Memory & Personalization** — Better understanding of user preferences and context
5. **Software Engineering** — Improved code generation, debugging, and explanation quality
6. **Task Planning** — Better multi-step task decomposition and workflow management
7. **Tool Calling** — More reliable and accurate tool invocation patterns

The adapter is registered as the **"lora"** provider in the model router and
is prioritized for privacy-sensitive tasks (alongside Ollama).
