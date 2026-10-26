"""End-to-End: simulierte Anrufe, echte HTTP-Adapter gegen Fake-Server, WebSocket-Server."""
import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from voiceagent import audio  # noqa: E402
from voiceagent.adapters.openai_compat import HTTPASR, HTTPTTS, OpenAIKompatibelLLM  # noqa: E402
from voiceagent.sim import DEMO, Zug, anruf  # noqa: E402

WURZEL = os.path.join(os.path.dirname(__file__), "..")


class TestSimulierteAnrufe(unittest.TestCase):
    def test_demo_anruf(self):
        b = anruf(DEMO)
        self.assertEqual(b.werkzeug_aufrufe, ["bestellstatus_abfragen", "ticket_erstellen"])
        self.assertIn("unterwegs", b.antworten[1])
        self.assertEqual(b.uebergabe["ziel"], "Fachbereich (2. Level)")
        self.assertTrue(b.audit_intakt)
        self.assertEqual(b.audiohook[-1]["type"], "disconnect")
        self.assertEqual(b.audiohook[-1]["parameters"]["outputVariables"]["ticket"], "T-00001")
        self.assertTrue(b.budget["zeit_bis_erster_ton"]["ok"], b.budget)

    def test_denkpause_beim_diktieren_schneidet_nicht_ab(self):
        b = anruf([Zug("Wo ist meine Bestellung?"),
                   Zug("Die Nummer ist 447 100.", denkpause_nach_wort=4, denkpause_ms=700)])
        self.assertIn("unterwegs", b.antworten[1], "Nummer muss komplett ankommen")

    def test_barge_in(self):
        b = anruf([Zug("Was sind eure Öffnungszeiten?"),
                   Zug("Halt, ich will eigentlich meinen Vertrag kündigen.", unterbricht_nach_ms=800)])
        typen = [e[0] for e in b.ereignisse]
        self.assertIn("barge_in", typen)
        self.assertIn("barge_in", [e["parameters"].get("entities", [{}])[0].get("type")
                                   for e in b.audiohook if e["type"] == "event"])
        self.assertEqual(b.uebergabe["anliegen"], "vertrag-aenderung")

    def test_barge_in_kuerzt_nur_aktuelle_antwort(self):
        b = anruf([Zug("Wo ist meine Bestellung?"),
                   Zug("Die Nummer ist 447 100."),
                   Zug("Stopp, ich will lieber kündigen.", unterbricht_nach_ms=1200)])
        self.assertIn("barge_in", [e[0] for e in b.ereignisse])

    def test_barge_in_verlauf_nur_gesprochener_teil(self):
        from voiceagent.pipeline import Wiedergabe
        from voiceagent.sim import baue_pipeline
        from voiceagent.telemetry import SimUhr
        pipe, _, _ = baue_pipeline([], SimUhr())
        pipe.plan = [Wiedergabe(0, 1000, "Alte Antwort.")]
        pipe.turn_ab = 1
        pipe.plan += [Wiedergabe(5000, 6000, "Neu eins zwei drei vier."), Wiedergabe(6000, 7000, "Neu fünf.")]
        pipe.dialog.antwort_merken("Neu eins zwei drei vier. Neu fünf.")
        pipe._barge_in(5500)
        self.assertEqual(pipe.dialog.verlauf[-1]["content"], "Neu eins [unterbrochen]")
        self.assertEqual([w.ende_ms for w in pipe.plan], [1000, 5500])

    def test_asr_ausfall_fuehrt_zu_uebergabe(self):
        from voiceagent.sim import baue_pipeline
        from voiceagent.telemetry import SimUhr
        uhr = SimUhr()
        pipe, _, _ = baue_pipeline(["Hallo?"], uhr)

        def kaputt(pcm, rate):
            raise TimeoutError("ASR antwortet nicht")
        pipe.asr.transkribieren = kaputt
        t = 0
        for pcm in list(audio.frames(audio.ton(200, 800, amp=0.3), 8000)) + \
                list(audio.frames(audio.stille(1500, rauschen=30), 8000)):
            t += 20
            pipe.frame(pcm, t)
        self.assertIsNotNone(pipe.uebergabe, "nie still auflegen")
        self.assertIn("technisch", pipe.uebergabe["grund"])

    def test_backchannel_unterbricht_nicht(self):
        b = anruf([Zug("Was sind eure Öffnungszeiten?"),
                   Zug("mhm", unterbricht_nach_ms=600, ms_pro_wort=250)])
        typen = [e[0] for e in b.ereignisse]
        self.assertNotIn("barge_in", typen)
        self.assertIn("backchannel", typen)


class _FakeOpenAI(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        roh = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path.endswith("/chat/completions"):
            req = json.loads(roh)
            assert self.headers["Authorization"] == "Bearer k"
            if req.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for teil in ["Hallo", " Welt.", " Zweiter", " Satz."]:
                    d = {"choices": [{"delta": {"content": teil}}]}
                    self.wfile.write(f"data: {json.dumps(d)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
                return
            text = '{"anliegen": "reklamation"}' if req.get("response_format") else "Antwort."
            return self._json({"choices": [{"message": {"content": text}}],
                               "usage": {"prompt_tokens": 7, "completion_tokens": 3}})
        if self.path.endswith("/audio/transcriptions"):
            assert b'name="file"; filename="turn.wav"' in roh and b"RIFF" in roh
            return self._json({"text": " Grüezi mitenand "})
        if self.path.endswith("/audio/speech"):
            wav = audio.wav_bytes(audio.ton(300, 100, rate=24000), 24000)
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav)))
            self.end_headers()
            self.wfile.write(wav)


class TestHTTPAdapter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), _FakeOpenAI)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_llm_complete_json_stream(self):
        llm = OpenAIKompatibelLLM(self.url, "m", api_key="k")
        r = llm.complete([{"role": "user", "content": "x"}])
        self.assertEqual((r["text"], r["tokens_ein"], r["tokens_aus"]), ("Antwort.", 7, 3))
        self.assertIn("reklamation", llm.complete([], json_modus=True)["text"])
        self.assertEqual("".join(llm.stream([{"role": "user", "content": "x"}])),
                         "Hallo Welt. Zweiter Satz.")

    def test_gateway_streamt_saetze_ueber_http(self):
        from voiceagent.gateway import Backend, LLMGateway
        gw = LLMGateway([Backend("vllm", "lokal", OpenAIKompatibelLLM(self.url, "m", api_key="k"))])
        self.assertEqual([s.text for s in gw.saetze([{"role": "user", "content": "x"}],
                                                     klasse=2, anruf_id="a")],
                         ["Hallo Welt.", "Zweiter Satz."])

    def test_asr_tts(self):
        self.assertEqual(HTTPASR(self.url).transkribieren(audio.ton(200, 100), 8000), "Grüezi mitenand")
        pcm = HTTPTTS(self.url, "piper-de").synthetisieren("Hallo")
        self.assertAlmostEqual(len(pcm) / 2 / 8000, 0.1, places=2, msg="auf 8 kHz umgetastet")


def _freier_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@unittest.skipUnless(__import__("importlib").util.find_spec("websockets"), "websockets nicht installiert")
class TestWebSocketServer(unittest.TestCase):
    def test_audiohook_ueber_netzwerk(self):
        port, mport = _freier_port(), _freier_port()
        proc = subprocess.Popen([sys.executable, "-m", "voiceagent.server", "--host", "127.0.0.1",
                                 "--port", str(port), "--metrics-port", str(mport),
                                 "--api-key", "geheim"], cwd=WURZEL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            asyncio.run(self._client(port))
            import urllib.request
            self.assertIn(b"voiceagent_aktive_sitzungen",
                          urllib.request.urlopen(f"http://127.0.0.1:{mport}/metrics", timeout=3).read())
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    async def _client(self, port):
        from websockets.asyncio.client import connect
        for _ in range(50):
            try:
                ws = await connect(f"ws://127.0.0.1:{port}", additional_headers={
                    "Audiohook-Organization-Id": "o", "Audiohook-Correlation-Id": "c",
                    "Audiohook-Session-Id": "s1", "X-API-KEY": "geheim"})
                break
            except OSError:
                await asyncio.sleep(0.1)
        async with ws:
            await ws.send(json.dumps({"version": "2", "type": "open", "seq": 1, "id": "s1",
                                      "position": "PT0S", "parameters": {
                                          "organizationId": "o", "conversationId": "c",
                                          "media": [{"type": "audio", "format": "PCMU",
                                                     "channels": ["external"], "rate": 8000}]}}))
            self.assertEqual(json.loads(await ws.recv())["type"], "opened")
            # 1.2 s "Sprache", dann Stille: der Server muss Audio zurückschicken
            for pcm in list(audio.frames(audio.ton(200, 1200, amp=0.3), 8000)) + \
                    list(audio.frames(audio.stille(1500, rauschen=30), 8000)):
                await ws.send(audio.pcm16_to_ulaw(pcm))
            binaer = 0
            deadline = time.time() + 8
            while time.time() < deadline and binaer < 5:
                m = await asyncio.wait_for(ws.recv(), timeout=8)
                if isinstance(m, bytes):
                    binaer += 1
                    self.assertEqual(len(m), 160, "20 ms PCMU = 160 Byte")
            self.assertGreaterEqual(binaer, 5, "Bot-Audio kommt zurück")
            await ws.send(json.dumps({"version": "2", "type": "ping", "seq": 2, "id": "s1"}))
            await ws.send(json.dumps({"version": "2", "type": "close", "seq": 3, "id": "s1"}))
            typen = set()
            while "closed" not in typen:
                m = await asyncio.wait_for(ws.recv(), timeout=5)
                if isinstance(m, str):
                    typen.add(json.loads(m)["type"])
            self.assertIn("pong", typen)


if __name__ == "__main__":
    unittest.main()
