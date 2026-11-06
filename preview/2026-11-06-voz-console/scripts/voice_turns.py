"""Synthesize the scripted call and measure each customer turn through the real pipeline.

Both sides are spoken by Piper (local, open source): `faber` is the agent, `jeff` the
customer. Each customer clip then goes through the repo's own WhisperTranscriber, intent
classifier, TF-IDF retrieval and figure read-back rule, and every stage is timed. Text
generation is NOT measured -- the agent's lines are scripted -- and end-of-turn detection
is a configured silence window, not a measurement. The output says which is which.

usage (repo root):
  uv run --with piper-tts python preview/2026-11-06-voz-console/scripts/voice_turns.py \
      /tmp/tcc-tts/voice preview/2026-11-06-voz-console/mock/voice
"""

import json
import re
import sys
import time
import wave
from pathlib import Path

from piper import PiperVoice

from collection.agents.conversation import Conversation, build_vocabulary, resolve_intent
from collection.agents.intent import IntentClassifier
from collection.rag.documents import load_directory
from collection.rag.index import VectorIndex
from collection.voice.transcribe import WhisperTranscriber, needs_confirmation, spoken_figures

VOICES, OUT = Path(sys.argv[1]), Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)

#: (speaker, text). The agent's lines are written from knowledge/: R$ 840,00 at 35 days
#: late sits in the 31-60 band -- 15% minimum down payment, up to 10 instalments, 20%
#: discount in full, 1.5%/month -- and 3 x R$ 130,00 respects the R$ 50,00 floor.
SCRIPT = [
    ("agente", "Olá, Carlos. Aqui é o atendimento automático da Simplifica. Esta chamada é "
               "gravada e transcrita. Podemos falar sobre a sua fatura em aberto?"),
    ("cliente", "Pode falar, sim."),
    ("agente", "Sua fatura de oitocentos e quarenta reais está com trinta e cinco dias de "
               "atraso. Você pode pagar à vista com vinte por cento de desconto, ou parcelar "
               "em até dez vezes, com entrada mínima de quinze por cento."),
    ("cliente", "Quero parcelar. Consigo dar quatrocentos e cinquenta reais de entrada."),
    ("agente", "Só para confirmar: entrada de quatrocentos e cinquenta reais, certo?"),
    ("cliente", "Isso, pode ser."),
    ("agente", "Perfeito. O restante, trezentos e noventa reais, pode ficar em até dez "
               "parcelas. Em quantas vezes você prefere?"),
    ("cliente", "Em três vezes."),
    ("agente", "Combinado: entrada de quatrocentos e cinquenta reais e três parcelas de cento "
               "e trinta reais, com juros de um vírgula cinco por cento ao mês. Vou enviar o "
               "boleto da entrada por mensagem. Posso ajudar em mais alguma coisa?"),
    ("cliente", "Não, obrigado."),
    ("agente", "Obrigado, Carlos. Tenha um ótimo dia."),
]
END_OF_TURN_MS = 500  # configured silence window, not measured


def synth(voice: PiperVoice, text: str, path: Path) -> float:
    start = time.perf_counter()
    with wave.open(str(path), "wb") as wav:
        if hasattr(voice, "synthesize_wav"):
            voice.synthesize_wav(text, wav)
        else:
            voice.synthesize(text, wav)
    return (time.perf_counter() - start) * 1000


def duration(path: Path) -> float:
    with wave.open(str(path)) as wav:
        return wav.getnframes() / wav.getframerate() * 1000


def first_sentence(text: str) -> str:
    return re.split(r"(?<=[.?!:])\s", text, maxsplit=1)[0]


def words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def wer(ref: str, hyp: str) -> float:
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
    return d[len(h)] / max(len(r), 1)


agent = PiperVoice.load(str(VOICES / "pt_BR-faber-medium.onnx"))
customer = PiperVoice.load(str(VOICES / "pt_BR-jeff-medium.onnx"))
whisper = WhisperTranscriber()
classifier = IntentClassifier()
index = VectorIndex().add_documents(load_directory("knowledge")).build()
vocabulary = build_vocabulary([c.text for c in index.chunks], classifier)
thread = Conversation()

# Warm everything once, so the first measured turn is not paying for model loading.
synth(agent, "Olá.", OUT / "_warm.wav")
whisper.transcribe_path(OUT / "_warm.wav")
(OUT / "_warm.wav").unlink()

turns = []
for n, (speaker, text) in enumerate(SCRIPT, 1):
    path = OUT / f"{n:02d}-{speaker}.wav"
    voice = agent if speaker == "agente" else customer
    total_ms = synth(voice, text, path)
    turn = {"n": n, "speaker": speaker, "text": text, "audio": path.name,
            "audio_ms": round(duration(path))}
    if speaker == "agente":
        thread.add("agent", text)
        first_ms = synth(agent, first_sentence(text), OUT / "_first.wav")
        turn |= {"tts_total_ms": round(total_ms), "tts_first_audio_ms": round(first_ms)}
    else:
        t0 = time.perf_counter()
        heard = whisper.transcribe_path(path)
        stt_ms = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        raw = classifier.classify(heard.text)
        cls = resolve_intent(raw, heard.text, thread, vocabulary)
        intent_ms = (time.perf_counter() - t0) * 1000
        thread.add("customer", heard.text, intent=str(cls.intent.value))
        t0 = time.perf_counter()
        hits = index.search(heard.text, top_k=3)
        retrieval_ms = (time.perf_counter() - t0) * 1000
        turn |= {
            "heard": heard.text, "wer": round(wer(text, heard.text), 3),
            "stt_ms": round(stt_ms), "intent": str(cls.intent.value),
            "confidence": round(float(cls.confidence), 3), "intent_ms": round(intent_ms, 2),
            "intent_alone": str(raw.intent.value), "inherited": bool(cls.inherited),
            "retrieved": [h.chunk.document_id if hasattr(h, "chunk") else str(h) for h in hits],
            "retrieval_ms": round(retrieval_ms, 2),
            "figures": spoken_figures(heard.text), "read_back": needs_confirmation(heard.text),
        }
    turns.append(turn)
    print(n, speaker, {k: v for k, v in turn.items() if k not in ("text",)})
(OUT / "_first.wav").unlink(missing_ok=True)

json.dump({"end_of_turn_ms": END_OF_TURN_MS, "whisper_model": whisper.model_size,
           "turns": turns}, open(OUT / "turns.json", "w"), ensure_ascii=False, indent=1)
