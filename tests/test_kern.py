"""Kern: Audio, VAD, Turn-Taking, AudioHook, Audit, Werkzeuge, Policy, Sizing."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from voiceagent import audio  # noqa: E402
from voiceagent.audiohook import AudioHookSitzung, pruefe_header  # noqa: E402
from voiceagent.audit import AuditLog  # noqa: E402
from voiceagent.policy import KATALOG, klassifiziere_regelbasiert, region_erlaubt  # noqa: E402
from voiceagent.sizing import (Bedarf, MODELLE, dimensionieren, empfehlung,  # noqa: E402
                               kv_bytes_pro_token)
from voiceagent.tools import DemoBackend, SchemaFehler, standard_werkzeuge, validiere  # noqa: E402
from voiceagent.turn import (BACKCHANNEL, BARGE_IN, TURN_ENDE, VERWORFEN,  # noqa: E402
                             TurnTaking, satzende_wahrscheinlichkeit)
from voiceagent.vad import EnergieVAD  # noqa: E402


class TestAudio(unittest.TestCase):
    def test_ulaw_bekannte_werte(self):
        self.assertEqual(audio.pcm16_to_ulaw(b"\x00\x00"), b"\xff")      # Stille = 0xFF
        self.assertEqual(audio.ulaw_to_pcm16(b"\xff"), b"\x00\x00")

    def test_ulaw_rundreise_fehler_klein(self):
        pcm = audio.ton(440, 100, amp=0.5)
        zurueck = audio.ulaw_to_pcm16(audio.pcm16_to_ulaw(pcm))
        from array import array
        a, b = array("h"), array("h")
        a.frombytes(pcm)
        b.frombytes(zurueck)
        rel = max(abs(x - y) / (abs(x) + 64) for x, y in zip(a, b))
        self.assertLess(rel, 0.07, "G.711 quantisiert logarithmisch, max ~3-6 % relativ")

    def test_resampling_laenge(self):
        pcm = audio.ton(300, 200, rate=8000)
        hoch = audio.resample_linear(pcm, 8000, 16000)
        self.assertEqual(len(hoch), 2 * len(pcm))
        self.assertEqual(len(audio.resample_linear(hoch, 16000, 8000)), len(pcm))

    def test_stereo_trennen(self):
        links, rechts = audio.deinterleave_stereo(bytes([1, 2, 3, 4, 5, 6]))
        self.assertEqual((links, rechts), (bytes([1, 3, 5]), bytes([2, 4, 6])))

    def test_wav_rundreise(self):
        pcm = audio.ton(500, 50, rate=16000)
        self.assertEqual(audio.wav_to_pcm(audio.wav_bytes(pcm, 16000)), (pcm, 16000))

    def test_vad(self):
        v = EnergieVAD()
        for _ in range(20):
            self.assertFalse(v.ist_sprache(audio.stille(20, rauschen=30)))
        self.assertTrue(v.ist_sprache(audio.ton(200, 20, amp=0.3)))


def _fuettern(tt, plan, t0=0, teiltext=""):
    """plan: Liste von (ist_sprache, dauer_ms). Gibt Ereignisse mit Zeit zurück."""
    t, ev = t0, []
    for sprache, dauer in plan:
        for _ in range(dauer // 20):
            t += 20
            ev += [(e.typ, t, e.daten) for e in tt.frame(sprache, t, teiltext)]
    return ev, t


class TestTurnTaking(unittest.TestCase):
    def test_semantik(self):
        self.assertGreater(satzende_wahrscheinlichkeit("Wo ist meine Bestellung?"), 0.8)
        self.assertLess(satzende_wahrscheinlichkeit("Ich wollte fragen, ob und"), 0.2)
        self.assertLess(satzende_wahrscheinlichkeit("Meine Nummer ist 44 71"), 0.5)

    def test_fertiger_satz_endet_schnell(self):
        ev, _ = _fuettern(TurnTaking(), [(True, 1000), (False, 600)], teiltext="Wo ist meine Bestellung?")
        ende = [e for e in ev if e[0] == TURN_ENDE]
        self.assertEqual(len(ende), 1)
        self.assertLessEqual(ende[0][2]["stille_ms"], 400)

    def test_unfertiger_satz_wartet(self):
        ev, _ = _fuettern(TurnTaking(), [(True, 1000), (False, 700)], teiltext="Ich möchte und")
        self.assertFalse([e for e in ev if e[0] == TURN_ENDE], "darf bei 'und' nicht abschneiden")
        ev2, _ = _fuettern(TurnTaking(), [(True, 1000), (False, 1400)], teiltext="Ich möchte und")
        self.assertTrue([e for e in ev2 if e[0] == TURN_ENDE], "irgendwann ist trotzdem Schluss")

    def test_geraeusch_verworfen(self):
        ev, _ = _fuettern(TurnTaking(), [(True, 100), (False, 400)])
        self.assertIn(VERWORFEN, [e[0] for e in ev])

    def test_barge_in(self):
        tt = TurnTaking()
        tt.agent_beginnt(0)
        ev, _ = _fuettern(tt, [(True, 400)], teiltext="Nein, das stimmt nicht")
        self.assertIn(BARGE_IN, [e[0] for e in ev])
        self.assertFalse(tt.agent_spricht)

    def test_backchannel_ist_kein_barge_in(self):
        tt = TurnTaking()
        tt.agent_beginnt(0)
        ev, _ = _fuettern(tt, [(True, 400), (False, 600)], teiltext="mhm")
        typen = [e[0] for e in ev]
        self.assertNotIn(BARGE_IN, typen)
        self.assertIn(BACKCHANNEL, typen)
        self.assertNotIn(TURN_ENDE, typen)
        self.assertTrue(tt.agent_spricht, "Bot spricht nach 'mhm' weiter")


def _open(media, seq=1):
    return json.dumps({"version": "2", "type": "open", "seq": seq, "id": "s-1",
                       "position": "PT0.5S",
                       "parameters": {"organizationId": "o", "conversationId": "c",
                                      "language": "de-CH", "media": media}})


MONO = [{"type": "audio", "format": "PCMU", "channels": ["external"], "rate": 8000}]
STEREO = [{"type": "audio", "format": "PCMU", "channels": ["external", "internal"], "rate": 8000}]


class TestAudioHook(unittest.TestCase):
    def test_open_ping_close(self):
        s = AudioHookSitzung("connector")
        [opened] = s.text_empfangen(_open(STEREO + MONO))
        self.assertEqual(opened["type"], "opened")
        self.assertEqual((opened["seq"], opened["clientseq"]), (1, 1))
        self.assertEqual(opened["parameters"]["media"], MONO, "Connector nimmt nur 'external'")
        self.assertNotIn("position", opened)
        [pong] = s.text_empfangen(json.dumps({"version": "2", "type": "ping", "seq": 2,
                                               "id": "s-1", "position": "PT1.2S"}))
        self.assertEqual((pong["type"], pong["seq"], pong["clientseq"], pong["position"]),
                         ("pong", 2, 2, "PT1.200S"))
        s.beenden("transfer", variablen={"zielQueue": "Fachbereich"})
        [closed] = s.text_empfangen(json.dumps({"version": "2", "type": "close", "seq": 3, "id": "s-1"}))
        self.assertEqual(closed["type"], "closed")
        self.assertEqual(closed["parameters"]["outputVariables"]["zielQueue"], "Fachbereich")
        self.assertEqual(s.protokollfehler, [])

    def test_pause_blockiert_audio(self):
        s = AudioHookSitzung()
        s.text_empfangen(_open(MONO))
        self.assertIsNotNone(s.audio_empfangen(b"\xff" * 160))
        s.text_empfangen(json.dumps({"version": "2", "type": "pause", "seq": 2, "id": "s-1"}))
        self.assertIsNone(s.audio_empfangen(b"\xff" * 160))
        self.assertIsNone(s.audio_senden(b"\x00\x00" * 160))

    def test_monitor_stereo(self):
        s = AudioHookSitzung("monitor")
        [o] = s.text_empfangen(_open(STEREO))
        self.assertEqual(o["parameters"]["media"][0]["channels"], ["external", "internal"])
        kunde, agent = s.audio_empfangen(b"\xff\x00" * 160)
        self.assertEqual(len(kunde), 320)
        self.assertIsNotNone(agent)
        self.assertIsNone(s.audio_senden(b"\x00\x00" * 160), "Monitor sendet nie Audio")

    def test_fehlerfaelle(self):
        s = AudioHookSitzung()
        [e] = s.text_empfangen(_open([{"type": "audio", "format": "L16", "channels": ["external"],
                                       "rate": 16000}]))
        self.assertEqual((e["type"], e["parameters"]["code"]), ("error", 415))
        [e2] = s.text_empfangen("{kaputt")
        self.assertEqual(e2["parameters"]["code"], 400)

    def test_header(self):
        h = {"Audiohook-Organization-Id": "o", "Audiohook-Correlation-Id": "c",
             "Audiohook-Session-Id": "s", "X-API-KEY": "geheim"}
        self.assertIsNone(pruefe_header(h, "geheim"))
        self.assertIn("ungültig", pruefe_header(h, "anders"))
        self.assertIn("fehlende", pruefe_header({"X-API-KEY": "geheim"}, "geheim"))


class TestAudit(unittest.TestCase):
    def test_kette_und_manipulation(self):
        a = AuditLog(schluessel=b"k", uhr=lambda: 1.0)
        for i in range(5):
            a.schreiben("ereignis", i=i)
        self.assertIsNone(a.pruefen())
        a.eintraege[2]["daten"]["i"] = 99
        self.assertEqual(a.pruefen(), 3)

    def test_loeschen_erkannt(self):
        a = AuditLog(uhr=lambda: 1.0)
        for i in range(4):
            a.schreiben("e", i=i)
        del a.eintraege[1]
        self.assertEqual(a.pruefen(), 2)


class TestWerkzeuge(unittest.TestCase):
    def setUp(self):
        self.b = DemoBackend()
        self.k = standard_werkzeuge(self.b)

    def test_schema(self):
        s = {"type": "object", "required": ["n"], "additionalProperties": False,
             "properties": {"n": {"type": "string", "pattern": r"\d{6}"}}}
        validiere(s, {"n": "123456"})
        for falsch in ({}, {"n": 123456}, {"n": "12"}, {"n": "123456", "x": 1}):
            with self.assertRaises(SchemaFehler):
                validiere(s, falsch)

    def test_nicht_erlaubt(self):
        e = self.k.aufrufen("ticket_erstellen", {"thema": "x", "bereich": "y"},
                            erlaubt=["wissen_suchen"], anruf_id="a")
        self.assertEqual(e.status, "abgelehnt")

    def test_bestaetigung_und_idempotenz(self):
        args = {"thema": "Paket beschädigt", "bereich": "Fachbereich"}
        e = self.k.aufrufen("ticket_erstellen", args, erlaubt=["ticket_erstellen"], anruf_id="a")
        self.assertEqual(e.status, "bestaetigung_noetig")
        self.assertIn("Paket beschädigt", e.text)
        self.assertEqual(self.b.tickets, [])
        e1 = self.k.aufrufen("ticket_erstellen", args, erlaubt=["ticket_erstellen"],
                             anruf_id="a", bestaetigt=True)
        e2 = self.k.aufrufen("ticket_erstellen", args, erlaubt=["ticket_erstellen"],
                             anruf_id="a", bestaetigt=True)
        self.assertEqual((e1.status, e2.status), ("ok", "duplikat"))
        self.assertEqual(len(self.b.tickets), 1, "kein zweites Ticket bei Wiederholung")

    def test_systemfehler_nicht_durchreichen(self):
        self.k.werkzeuge["wissen_suchen"].ausfuehren = lambda a: 1 / 0
        e = self.k.aufrufen("wissen_suchen", {"frage": "x"}, erlaubt=["wissen_suchen"], anruf_id="a")
        self.assertEqual(e.status, "fehler")
        self.assertNotIn("Zero", e.text)


class TestPolicy(unittest.TestCase):
    def test_gleich_wie_handoff_router(self):
        import importlib.util
        pfad = os.path.join(os.path.dirname(__file__), "..", "beispiele", "handoff_router.py")
        spec = importlib.util.spec_from_file_location("handoff_router", pfad)
        hr = importlib.util.module_from_spec(spec)
        sys.modules["handoff_router"] = hr
        spec.loader.exec_module(hr)
        for aid, a in hr.ANFALL_KATALOG.items():
            p = KATALOG[aid]
            self.assertEqual((p.klasse, p.risiko, p.sla_tage),
                             (a.vertraulichkeitsstufe, a.risikoklasse, a.sla_tag), aid)
            self.assertEqual(region_erlaubt("eu", p.klasse),
                             hr.ALL_STUFEN[a.vertraulichkeitsstufe].darf_cloud, aid)

    def test_riskantestes_gewinnt(self):
        self.assertEqual(klassifiziere_regelbasiert(
            "Wo ist meine Bestellung, und ich will den Vertrag kündigen")[0], "vertrag-aenderung")

    def test_residenz(self):
        self.assertTrue(region_erlaubt("lokal", 4))
        self.assertFalse(region_erlaubt("ch", 2))
        self.assertTrue(region_erlaubt("eu", 1))
        self.assertFalse(region_erlaubt("us", 1))


class TestSizing(unittest.TestCase):
    def test_kv_cache_llama_8b(self):
        # 2 x 32 Schichten x 8 KV-Köpfe x 128 x 2 Byte = 131072 Byte/Token
        self.assertEqual(kv_bytes_pro_token(MODELLE["llama-3.1-8b"]), 131072)

    def test_10_anrufe_8b_braucht_keine_a100(self):
        e = empfehlung("qwen3-8b", gleichzeitige_anrufe=10)
        self.assertNotIn(e.gpu, ("A100-80", "H100-80"))
        self.assertEqual(e.anzahl, 1)
        self.assertLess(e.chf_monat, 2600)

    def test_70b_passt_nicht_auf_eine_l4(self):
        r = dimensionieren("llama-3.3-70b", Bedarf(gleichzeitige_anrufe=10))
        self.assertFalse([e for e in r if e.gpu == "L4-24" and e.anzahl == 1])

    def test_mac_nur_fuer_poc(self):
        prod = dimensionieren("qwen3-1.7b", Bedarf(gleichzeitige_anrufe=2))
        poc = dimensionieren("qwen3-1.7b", Bedarf(gleichzeitige_anrufe=2), nur_produktion=False)
        self.assertFalse([e for e in prod if e.gpu.startswith("Mac")])
        self.assertTrue([e for e in poc if e.gpu.startswith("Mac")])

    def test_redundanz_kostet_eine_karte(self):
        a = empfehlung("qwen3-8b", gleichzeitige_anrufe=10)
        b = empfehlung("qwen3-8b", gleichzeitige_anrufe=10, redundanz=True)
        self.assertEqual(b.anzahl, a.anzahl + 1)


if __name__ == "__main__":
    unittest.main()
