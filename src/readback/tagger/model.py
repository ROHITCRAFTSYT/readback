"""BiLSTM tagger over canonical tokens.

Each token is embedded from three views: the word itself (when it is in the vocabulary), its
characters (so a misheard "decmal" or an airline never seen in training still looks like
something), and its shape (digit, spelled letter, word, hesitation). Decoding is Viterbi under
BIO constraints, so an I- tag only ever continues a span of the same label.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import numpy as np
import torch
from torch import nn

from readback.clearance import Clearance
from readback.normalize import canonical
from readback.tagger.decode import FILLERS, TAGS, decode

PAD, UNK = "<pad>", "<unk>"
MAX_CHARS = 12
SHAPES = 5  # pad, digit, letter, word, hesitation


def shape(token: str) -> int:
    if token.isdigit():
        return 1
    if len(token) == 1:
        return 2
    return 4 if token in FILLERS else 3


class Net(nn.Module):
    def __init__(self, n_words: int, n_chars: int, n_tags: int, word_dim: int = 64,
                 char_dim: int = 24, char_out: int = 48, hidden: int = 128,
                 dropout: float = 0.25):  # fmt: skip
        super().__init__()
        self.words = nn.Embedding(n_words, word_dim, padding_idx=0)
        self.chars = nn.Embedding(n_chars, char_dim, padding_idx=0)
        self.char_conv = nn.Conv1d(char_dim, char_out, kernel_size=3, padding=1)
        self.shapes = nn.Embedding(SHAPES, 8, padding_idx=0)
        self.drop = nn.Dropout(dropout)
        self.lstm = nn.LSTM(word_dim + char_out + 8, hidden, num_layers=2, batch_first=True,
                            bidirectional=True, dropout=dropout)  # fmt: skip
        self.out = nn.Linear(2 * hidden, n_tags)

    def forward(self, words, chars, shapes):
        b, t, c = chars.shape
        ch = self.chars(chars.view(b * t, c)).transpose(1, 2)  # (b*t, dim, c)
        ch = torch.relu(self.char_conv(ch)).max(dim=2).values.view(b, t, -1)
        x = torch.cat([self.words(words), ch, self.shapes(shapes)], dim=-1)
        h, _ = self.lstm(self.drop(x))
        return self.out(self.drop(h))


def _allowed() -> np.ndarray:
    """allowed[i, j]: tag j may follow tag i."""
    n = len(TAGS)
    ok = np.ones((n, n), dtype=bool)
    for j, tag in enumerate(TAGS):
        if tag.startswith("I-"):
            label = tag[2:]
            for i, prev in enumerate(TAGS):
                ok[i, j] = prev in (f"B-{label}", f"I-{label}")
    return ok


ALLOWED = _allowed()
START_OK = np.array([not t.startswith("I-") for t in TAGS])


def viterbi(logp: np.ndarray) -> list[int]:
    """Best tag sequence for one sentence of log-probabilities (T, n_tags)."""
    trans = np.where(ALLOWED, 0.0, -1e9)
    score = np.where(START_OK, logp[0], -1e9)
    back = []
    for t in range(1, len(logp)):
        cand = score[:, None] + trans  # (prev, cur)
        back.append(cand.argmax(axis=0))
        score = cand.max(axis=0) + logp[t]
    best = [int(score.argmax())]
    for bp in reversed(back):
        best.append(int(bp[best[-1]]))
    return best[::-1]


class Tagger:
    def __init__(self, words: list[str], chars: list[str], config: dict | None = None):
        self.words = words
        self.chars = chars
        self.word_ix = {w: i for i, w in enumerate(words)}
        self.char_ix = {c: i for i, c in enumerate(chars)}
        self.config = config or {}
        self.net = Net(len(words), len(chars), len(TAGS), **self.config)

    @classmethod
    def build(cls, sentences: list[list[str]], min_count: int = 3, **config) -> Tagger:
        counts = Counter(t for s in sentences for t in s)
        words = [PAD, UNK] + sorted(w for w, n in counts.items() if n >= min_count)
        chars = [PAD, UNK] + sorted({c for s in sentences for t in s for c in t})
        return cls(words, chars, config)

    # -- tensors ------------------------------------------------------------------------
    def encode(self, sentences: list[list[str]], word_dropout: float = 0.0, rng=None):
        t_max = max(1, max(len(s) for s in sentences))
        words = torch.zeros(len(sentences), t_max, dtype=torch.long)
        chars = torch.zeros(len(sentences), t_max, MAX_CHARS, dtype=torch.long)
        shapes = torch.zeros(len(sentences), t_max, dtype=torch.long)
        for i, s in enumerate(sentences):
            for j, tok in enumerate(s):
                w = self.word_ix.get(tok, 1)
                if word_dropout and rng is not None and rng.random() < word_dropout:
                    w = 1
                words[i, j] = w
                shapes[i, j] = shape(tok)
                for k, c in enumerate(tok[:MAX_CHARS]):
                    chars[i, j, k] = self.char_ix.get(c, 1)
        return words, chars, shapes

    # -- inference ----------------------------------------------------------------------
    @torch.no_grad()
    def predict(self, sentences: list[list[str]], batch_size: int = 256) -> list[list[str]]:
        self.net.eval()
        out: list[list[str]] = []
        for k in range(0, len(sentences), batch_size):
            batch = sentences[k : k + batch_size]
            logp = torch.log_softmax(self.net(*self.encode(batch)), dim=-1).numpy()
            for i, s in enumerate(batch):
                out.append([TAGS[j] for j in viterbi(logp[i, : len(s)])] if s else [])
        return out

    def parse_tokens(self, sentences: list[list[str]]) -> list[Clearance]:
        return [decode(s, t) for s, t in zip(sentences, self.predict(sentences), strict=True)]

    def parse(self, text: str) -> Clearance:
        return self.parse_tokens([canonical(text)])[0]

    # -- persistence --------------------------------------------------------------------
    def save(self, path: Path, meta: dict | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        state = {k: v.half() for k, v in self.net.state_dict().items()}  # halves the file
        tmp = path.with_suffix(".tmp")
        torch.save({"words": self.words, "chars": self.chars, "config": self.config,
                    "state": state, "meta": meta or {}}, tmp)  # fmt: skip
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> Tagger:
        blob = torch.load(path, map_location="cpu", weights_only=True)
        tagger = cls(blob["words"], blob["chars"], blob["config"])
        tagger.net.load_state_dict({k: v.float() for k, v in blob["state"].items()})
        tagger.meta = blob.get("meta", {})
        return tagger
