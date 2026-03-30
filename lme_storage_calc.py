"""LongMemEval storage size calculator — runs synchronously."""
import os, sys, tempfile, logging, warnings, shutil
logging.disable(logging.CRITICAL)
warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/chitranjanmalviya/Desktop/Dhee")

from dhee import Engram


def make_session(i, n_turns=10):
    turns = "\n".join(
        f"user: Turn {t}, session {i}. Bought organic milk, eggs, bread. Spent $45."
        for t in range(n_turns)
    )
    return (
        f"Session ID: sess_{i:04d}\n"
        f"Session Date: 2024-0{(i % 9) + 1}-15\n"
        f"== Conversation ==\n{turns}"
    )


def measure(n):
    d = tempfile.mkdtemp()
    e = Engram(
        in_memory=False, data_dir=d, provider="mock",
        enable_echo=False, enable_categories=False, enable_decay=False,
    )
    for i in range(n):
        e.add(make_session(i), user_id="u", infer=False)
    v = os.path.getsize(os.path.join(d, "sqlite_vec.db")) if os.path.exists(os.path.join(d, "sqlite_vec.db")) else 0
    h = os.path.getsize(os.path.join(d, "engram.db")) if os.path.exists(os.path.join(d, "engram.db")) else 0
    stored = len(e.get_all(user_id="u", limit=n + 10))
    shutil.rmtree(d, ignore_errors=True)
    return {"n": n, "stored": stored, "vec": v, "hist": h, "total": v + h,
            "bps": (v + h) // max(stored, 1)}


print("=== LongMemEval DB Size: Mock (384-dim hash embeddings) ===")
print(f"{'Sessions':>10} | {'Stored':>6} | {'Vec KB':>7} | {'Hist KB':>8} | {'Total KB':>9} | {'B/session':>9}")

rows = []
for n in [10, 30, 115, 500]:
    sys.stdout.write(f"  measuring {n} sessions...")
    sys.stdout.flush()
    r = measure(n)
    rows.append(r)
    print(f"\r{r['n']:>10} | {r['stored']:>6} | {r['vec']//1024:>7} | {r['hist']//1024:>8} | {r['total']//1024:>9} | {r['bps']:>9}")

# Extrapolate
bps_384 = rows[-1]["bps"]
vec_bytes_384 = 384 * 4  # 1536 bytes per 384-dim vector
non_vec = bps_384 - vec_bytes_384

print(f"\nNon-vector overhead per session (metadata + text + indices): {non_vec:,} bytes")

print("\n=== Extrapolated to real embedding providers ===")
providers = [
    ("OpenAI text-embedding-3-small", 1536),
    ("Gemini text-embedding-004",     768),
    ("Gemini (dhee config)",          3072),
]
for name, dims in providers:
    bps = non_vec + dims * 4
    print(f"  {name:<40} {dims:5}d → {bps:6,} B/sess = {bps/1024:.1f} KB/sess")

print("\n=== LongMemEval Storage Scenarios (OpenAI 1536-dim) ===")
openai_bps = non_vec + 1536 * 4
gemini_bps  = non_vec + 3072 * 4

scenarios = [
    ("LME-S: peak per question (30 sess)",          30,    "reset-per-Q"),
    ("LME-L: peak per question (115 sess)",         115,   "reset-per-Q"),
    ("LME-S: persistent (500 Q × 30 sess)",         15000, "no reset"),
    ("LME-L: persistent (500 Q × 115 sess)",        57500, "no reset"),
]

for label, n, mode in scenarios:
    ob = n * openai_bps
    gb = n * gemini_bps
    def fmt(b):
        if b < 1024**2: return f"{b/1024:.0f} KB"
        return f"{b/1024**2:.1f} MB"
    print(f"  {label:<48} OpenAI={fmt(ob)}  Gemini={fmt(gb)}  [{mode}]")

# Also estimate echo-enriched size (5x text per memory due to paraphrases/keywords)
print("\n=== With full echo enrichment (adds ~3-5 KB of text per session) ===")
echo_overhead = 4096  # ~4 KB extra per session (paraphrases, keywords, question-forms, implications)
for label, n, mode in scenarios[:2]:
    enriched_bps = openai_bps + echo_overhead
    b = n * enriched_bps
    print(f"  {label:<48} {b/1024:.0f} KB")
