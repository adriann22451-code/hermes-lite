import os, json, subprocess, pathlib, requests
from openai import OpenAI

TG = f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}"
OWNER = int(os.environ["OWNER_ID"])  # hanya kamu yang boleh memakai bot
MODEL = os.environ["MODEL"]  # contoh: openai/gpt-4o-mini (harus mendukung tool calling)
client = OpenAI(base_url=os.environ.get("BASE_URL", "https://openrouter.ai/api/v1"),
                api_key=os.environ["API_KEY"])

DATA = pathlib.Path(os.environ.get("DATA_DIR", "data"))
(DATA / "skills").mkdir(parents=True, exist_ok=True)
MEM = DATA / "memory.md"
history = []  # giliran teks terakhir (user/assistant)
offset = 0


# ---------- Telegram ----------
def send(text):
    text = text or "(kosong)"
    for i in range(0, len(text), 4000):
        requests.post(f"{TG}/sendMessage", json={"chat_id": OWNER, "text": text[i:i + 4000]})


def poll(timeout=50):
    global offset
    r = requests.get(f"{TG}/getUpdates", params={"offset": offset, "timeout": timeout},
                     timeout=timeout + 10).json()
    out = []
    for u in r.get("result", []):
        offset = u["update_id"] + 1
        m = u.get("message")
        if m and m["from"]["id"] == OWNER and "text" in m:
            out.append(m["text"])
    return out


def ask_approval(cmd):
    send(f"Izinkan jalankan perintah ini?\n\n{cmd}\n\nBalas 'ya' atau 'tidak'")
    while True:
        for t in poll():
            return t.strip().lower() == "ya"


# ---------- Tools ----------
def save_memory(fact):
    with MEM.open("a") as f:
        f.write(f"- {fact}\n")
    return "tersimpan di memori"


def write_skill(name, content):
    safe = "".join(c for c in name if c.isalnum() or c in "-_")
    (DATA / "skills" / f"{safe}.md").write_text(content)
    return f"skill '{safe}' tersimpan"


def run_shell(cmd):
    if not ask_approval(cmd):
        return "ditolak oleh user"
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        return (p.stdout + p.stderr)[-3000:] or "(tanpa output)"
    except subprocess.TimeoutExpired:
        return "timeout 60 detik"


def web_fetch(url):
    r = requests.get(url, timeout=20, headers={"User-Agent": "hermes-lite"})
    return r.text[:6000]


HANDLERS = {"save_memory": save_memory, "write_skill": write_skill,
            "run_shell": run_shell, "web_fetch": web_fetch}


def tool(name, desc, props):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "required": list(props),
                       "properties": {k: {"type": "string", "description": v}
                                      for k, v in props.items()}}}}


TOOLS = [
    tool("save_memory", "Simpan fakta penting tentang user/proyek untuk sesi berikutnya.",
         {"fact": "satu fakta singkat"}),
    tool("write_skill", "Simpan cara mengerjakan tugas berulang sebagai skill markdown, "
                        "setelah berhasil menyelesaikan tugas yang kemungkinan akan terulang.",
         {"name": "nama skill (huruf/angka/-)", "content": "langkah-langkah dalam markdown"}),
    tool("run_shell", "Jalankan perintah shell (user akan diminta menyetujui).",
         {"cmd": "perintah shell"}),
    tool("web_fetch", "Ambil isi halaman web dari sebuah URL.", {"url": "URL lengkap"}),
]


# ---------- Agent loop ----------
def system():
    mem = MEM.read_text() if MEM.exists() else "(kosong)"
    skills = "\n\n".join(f"## {p.stem}\n{p.read_text()}"
                         for p in sorted((DATA / "skills").glob("*.md"))) or "(belum ada)"
    return ("Kamu agent personal yang dipakai lewat chat di HP. Jawab ringkas dalam bahasa user.\n"
            "Gunakan tool bila perlu. Simpan fakta penting dengan save_memory. "
            "Setelah menyelesaikan tugas berulang, simpan caranya dengan write_skill.\n\n"
            f"# Memori\n{mem}\n\n# Skills\n{skills}")


def run(user_text):
    msgs = ([{"role": "system", "content": system()}] + history[-20:]
            + [{"role": "user", "content": user_text}])
    for _ in range(12):  # batas langkah agar tidak loop tanpa henti
        r = client.chat.completions.create(model=MODEL, messages=msgs, tools=TOOLS,
                                           max_tokens=2000)
        m = r.choices[0].message
        msgs.append(m.model_dump(exclude_none=True))
        if not m.tool_calls:
            answer = m.content or ""
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": answer})
            return answer
        for c in m.tool_calls:
            try:
                args = json.loads(c.function.arguments or "{}")
                out = HANDLERS[c.function.name](**args)
            except Exception as e:
                out = f"error: {e}"
            msgs.append({"role": "tool", "tool_call_id": c.id, "content": str(out)})
    return "Berhenti: terlalu banyak langkah."


if __name__ == "__main__":
    send("Agent siap. Kirim /reset untuk hapus percakapan aktif.")
    while True:
        for text in poll():
            if text.strip() == "/reset":
                history.clear()
                send("Percakapan direset (memori & skill tetap).")
                continue
            try:
                send(run(text))
            except Exception as e:
                send(f"Error: {e}")
