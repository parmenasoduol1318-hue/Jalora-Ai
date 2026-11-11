# Jalora LoRA Training Makefile

.PHONY: train unsloth axolotl peft info clean

train: unsloth
	@echo "Training complete"

unsloth:
	pip install "unsloth[accelerate]" torch
	python -c "from unsloth import FastLanguageModel; print('Unsloth installed successfully')"
	@echo "Ready to train with Unsloth"

axolotl:
	pip install "axolotl[flash-attn]"
	python -c "import axolotl; print('Axolotl installed successfully')"
	@echo "Ready to train with Axolotl"

peft:
	pip install peft transformers accelerate trl
	python scripts/train_lora.py --config config/peft_lora.py

info:
	@echo "Jalora Training Environment"
	@pip show transformers 2>/dev/null | grep Version || echo "transformers not installed"
	@pip show peft 2>/dev/null | grep Version || echo "peft not installed"
	@pip show unsloth 2>/dev/null | grep Version || echo "unsloth not installed"
	@python -c "import torch; print(f'PyTorch: {torch.__version__}')"
	@python -c "import accelerate; print(f'Accelerate: {accelerate.__version__}')"

clean:
	rm -rf lora_adapters/
	rm -rf .cache/huggingface/
