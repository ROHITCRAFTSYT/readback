"""Fine-tune Muse Glimmer 30B as Readback's clearance parser, on Modal (QLoRA, one GPU).

    uv sync --group modal
    uv run python scripts/export_sft.py --n 20000                 # training data (local)
    uv run modal run scripts/finetune_glimmer_modal.py::download  # weights into a volume, once
    uv run modal run scripts/finetune_glimmer_modal.py::train --steps 40      # smoke run
    uv run modal deploy scripts/finetune_glimmer_modal.py         # parser endpoint for eval
    uv run modal run scripts/finetune_glimmer_modal.py::train --steps 1250 --spawn  # full run

The base model is loaded in 4-bit (nf4) and a LoRA adapter is trained on the language
model's attention and MLP projections; the vision tower is untouched. Each example is one
transmission in canonical form, and the answer is the clearance JSON. The assistant turn is
opened on the user channel (`<|start|>assistant to=user<|message|>`) in training and at
inference, so the model answers directly instead of reasoning first. Only the synthetic
curriculum is uploaded for training; evaluation sends transcripts and scores locally, so the
hand labels never leave this machine.
"""

import json
import time
from pathlib import Path

import modal

APP = "readback-glimmer"
BASE = "meta-models/Muse-Glimmer-30B"
VOL = "/vol"
BASE_DIR = f"{VOL}/base"
GPU = "H100"

SYSTEM = (
    "You read one air traffic control radio transmission and return the clearance it "
    "contains as JSON: the callsign in spoken canonical form (or null) and every item with "
    "its kind, value and qualifier. Leave out values that were cut off.\n\n"
    "Reasoning strength: low."
)
ANSWER = "<|start|>assistant to=user<|message|>"
END = "<|eot|>"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_pip_install(
        "torch==2.9.1",
        "transformers==5.21.0",
        "peft==0.21.2",
        "accelerate==1.15.0",
        "bitsandbytes==0.50.2",
        "safetensors",
        "huggingface_hub[hf_xet]",
        "jinja2",
    )
    .env({"HF_HOME": f"{VOL}/hf", "TOKENIZERS_PARALLELISM": "false"})
)
vol = modal.Volume.from_name("readback-glimmer", create_if_missing=True)
app = modal.App(APP, image=image)


def prompt(tok, text: str) -> str:
    """The chat-template prompt for one transmission, ending at the open answer channel."""
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}]
    head = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
    return head + ANSWER


def load(adapter: str | None = None, train: bool = False):
    import torch
    import transformers
    from transformers import AutoTokenizer, BitsAndBytesConfig

    tok = AutoTokenizer.from_pretrained(BASE_DIR)
    tok.padding_side = "left"
    if tok.pad_token is None:
        tok.pad_token = "<|finetune_right_pad|>"
    quant = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
        llm_int8_skip_modules=["vision_tower", "vision_adapter", "vision_projection", "lm_head"],
    )
    t0 = time.time()
    model = None
    for name in ("AutoModelForCausalLM", "AutoModelForImageTextToText"):
        try:
            model = getattr(transformers, name).from_pretrained(
                BASE_DIR, quantization_config=quant, dtype=torch.bfloat16, device_map={"": 0}
            )
            print(f"loaded with {name} in {time.time() - t0:.0f}s: {type(model).__name__}")
            break
        except (ValueError, KeyError) as e:
            print(f"{name}: {e}")
    if model is None:
        raise RuntimeError("could not load the base model")
    print(f"GPU memory after load: {torch.cuda.memory_allocated() / 2**30:.1f} GiB")
    if adapter:
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, adapter, is_trainable=train)
    return tok, model


@app.function(volumes={VOL: vol}, timeout=3600, cpu=4, memory=8192)
def download() -> None:
    from huggingface_hub import snapshot_download

    t0 = time.time()
    snapshot_download(
        BASE,
        local_dir=BASE_DIR,
        allow_patterns=["*.json", "*.jinja", "*.safetensors", "LICENSE", "USAGE_POLICY.md"],
    )
    vol.commit()
    size = sum(p.stat().st_size for p in Path(BASE_DIR).rglob("*") if p.is_file())
    print(f"{BASE}: {size / 1e9:.1f} GB in {time.time() - t0:.0f}s")


@app.function(gpu=GPU, volumes={VOL: vol}, timeout=6 * 3600)
def finetune(rows: list[dict], steps: int, batch: int, lr: float, rank: int, out: str) -> dict:
    import math
    import random

    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

    tok, model = load()
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    model = get_peft_model(
        model,
        LoraConfig(
            r=rank,
            lora_alpha=2 * rank,
            lora_dropout=0.05,
            target_modules=r".*language_model.*\.(q_proj|k_proj|v_proj|o_proj|gate_proj|up_proj|down_proj)",
            task_type="CAUSAL_LM",
        ),
    )
    model.print_trainable_parameters()

    def encode(r: dict) -> tuple[list[int], list[int]]:
        p = tok(prompt(tok, r["user"]), add_special_tokens=False)["input_ids"]
        a = tok(r["assistant"] + END, add_special_tokens=False)["input_ids"]
        return p + a, [-100] * len(p) + a

    data = [encode(r) for r in rows]
    print(f"{len(data)} examples, mean {sum(len(x) for x, _ in data) / len(data):.0f} tokens")
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=lr, weight_decay=0.0)
    warm = max(1, steps // 20)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt,
        lambda s: min(1, (s + 1) / warm) * 0.5 * (1 + math.cos(math.pi * min(1, s / steps))),
    )
    rng = random.Random(0)
    order: list[int] = []
    model.train()
    t0, log = time.time(), []
    for step in range(steps):
        if len(order) < batch:
            order += rng.sample(range(len(data)), len(data))
        idx, order = order[:batch], order[batch:]
        n = max(len(data[i][0]) for i in idx)
        ids = torch.full((batch, n), tok.pad_token_id)
        labels = torch.full((batch, n), -100)
        mask = torch.zeros((batch, n), dtype=torch.long)
        for j, i in enumerate(idx):
            x, y = data[i]
            ids[j, : len(x)], labels[j, : len(y)], mask[j, : len(x)] = (
                torch.tensor(x), torch.tensor(y), 1,
            )  # fmt: skip
        loss = model(input_ids=ids.cuda(), attention_mask=mask.cuda(), labels=labels.cuda()).loss
        loss.backward()
        torch.nn.utils.clip_grad_norm_(params, 1.0)
        opt.step(), sched.step(), opt.zero_grad(set_to_none=True)
        log.append(loss.item())
        if step % 10 == 0 or step == steps - 1:
            rate = (time.time() - t0) / (step + 1)
            peak = torch.cuda.max_memory_allocated() / 2**30
            recent = log[-10:]
            print(f"step {step:5d}  loss {sum(recent) / len(recent):.4f}  {rate:.2f}s/step  "
                  f"peak {peak:.1f} GiB")  # fmt: skip
        if (step + 1) % 250 == 0 and step + 1 < steps:  # a checkpoint, in case the run dies
            model.save_pretrained(out)
            vol.commit()
    model.save_pretrained(out)
    stats = {
        "steps": steps, "batch": batch, "lr": lr, "rank": rank, "examples": len(data),
        "seconds": round(time.time() - t0), "loss_first": round(sum(log[:10]) / 10, 4),
        "loss_last": round(sum(log[-20:]) / 20, 4),
    }  # fmt: skip
    Path(out, "train_stats.json").write_text(json.dumps(stats, indent=1))
    vol.commit()
    return stats


@app.cls(gpu=GPU, volumes={VOL: vol}, timeout=3600, scaledown_window=300)
class Glimmer:
    adapter: str = modal.parameter(default="adapters/clearance")

    @modal.enter()
    def start(self) -> None:
        vol.reload()
        path = f"{VOL}/{self.adapter}" if self.adapter != "base" else None
        self.tok, self.model = load(path)
        self.model.eval()

    @modal.method()
    def parse(self, texts: list[str], batch: int = 48) -> list[str]:
        """Raw answers (JSON text) for transmissions in canonical form."""
        import torch

        out = []
        end = self.tok.convert_tokens_to_ids(END)
        for i in range(0, len(texts), batch):
            enc = self.tok(
                [prompt(self.tok, t) for t in texts[i : i + batch]],
                return_tensors="pt", padding=True, add_special_tokens=False,
            ).to("cuda")  # fmt: skip
            with torch.no_grad():
                gen = self.model.generate(
                    **enc, max_new_tokens=200, do_sample=False, eos_token_id=[end],
                    pad_token_id=self.tok.pad_token_id,
                )  # fmt: skip
            new = gen[:, enc["input_ids"].shape[1] :]
            out += [s.split(END)[0].strip() for s in self.tok.batch_decode(new)]
        return out


@app.local_entrypoint()
def train(
    steps: int = 40,
    batch: int = 16,
    lr: float = 2e-4,
    rank: int = 16,
    n: int = 0,
    data: str = "data/sft/clearances.jsonl",
    name: str = "clearance",
    spawn: bool = False,
) -> None:
    lines = Path(data).read_text(encoding="utf-8").splitlines()
    rows = []
    for line in lines[: n or None]:
        m = json.loads(line)["messages"]
        rows.append({"user": m[1]["content"], "assistant": m[2]["content"]})
    print(f"uploading {len(rows)} synthetic examples; {steps} steps of {batch}")
    args = (rows, steps, batch, lr, rank, f"{VOL}/adapters/{name}")
    if spawn:  # on the deployed app, so the run doesn't depend on this connection
        call = modal.Function.from_name(APP, "finetune").spawn(*args)
        print(f"spawned {call.object_id}; the adapter lands in adapters/{name}")
        return
    print(json.dumps(finetune.remote(*args), indent=1))
