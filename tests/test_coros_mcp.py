"""
Tests del sueño vía MCP oficial de COROS (sin red, sin credenciales).

    python -m unittest discover -s tests -t .

Lo que se cubre, y por qué importa:

1. El parseo de la respuesta de `querySleepData`. COROS no documenta la forma
   exacta, así que el parseo es tolerante (segundos/minutos/horas, fechas
   YYYYMMDD o ISO, listas anidadas) y aquí se prueban varias formas plausibles.
2. Los argumentos de la llamada: se construyen a partir del `inputSchema` que
   declare el servidor, no de un nombre supuesto.
3. La secuencia HTTP (refresh → initialize → tools/list → tools/call) con un
   `_http` falso: se comprueba el orden, las URLs y los cuerpos.
4. La superposición sobre `coros_data.json` y que el correo pinte el sueño.
"""

import base64
import contextlib
import hashlib
import io
import json
import re
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import coros_data  # noqa: E402
import coros_mcp  # noqa: E402
import daily_brief as db  # noqa: E402
import get_coros_mcp_token as get_token  # noqa: E402

TODAY = date(2026, 9, 18)
STRAVA = {"yesterday": "Morning Run (Run): 8.2 km, 5:32/km, 40 m desnivel", "week_km": 41.7}


def iso(days_back):
    return (TODAY - timedelta(days=days_back)).isoformat()


def yyyymmdd(days_back):
    return (TODAY - timedelta(days=days_back)).strftime("%Y%m%d")


def coros_sin_sueno(dias=40):
    """coros_data.json con la forma real del .mjs, pero sleep_hours a null."""
    return {
        "schema_version": 1, "generated_at": "2026-09-18T05:12:33+00:00", "region": "eu",
        "date": iso(0),
        "latest": {"date": iso(0), "resting_hr": 49, "hrv": 44, "hrv_base": 45,
                   "sleep_hours": None, "load_ratio": 0.95, "has_data": True},
        "days": [{"date": iso(i), "resting_hr": 48 + i % 4, "hrv": 44,
                  "sleep_hours": None, "has_data": True} for i in range(dias, -1, -1)],
        "available": {"resting_hr": True, "hrv": True, "sleep_hours": False, "sleep_phases": False},
        "warnings": ["sleep_hours no disponible: @pinta365/coros 0.0.1 no expone el endpoint de sueño."],
        "resting_hr": 49, "sleep_hours": None, "hrv": 44,
    }


def bloque_coros(html):
    m = re.search(r"<h3[^>]*>Recuperación.*?(?=<h3>Entrenamiento)", html, re.S)
    if not m:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(0))).strip()


class TestConversiones(unittest.TestCase):
    def test_horas_desde_segundos_minutos_y_horas(self):
        casos = [(26700, 7.42), (445, 7.42), (7.4166, 7.42), ("7.5", 7.5),
                 ({"value": 26700}, 7.42), ([26700, 1800], 7.92)]
        for raw, esperado in casos:
            with self.subTest(raw=raw):
                self.assertEqual(coros_mcp._to_hours(raw), esperado)

    def test_horas_imposibles_se_descartan(self):
        for raw in (None, True, 0, -3, "mucho", 200000, {}, []):
            with self.subTest(raw=raw):
                self.assertIsNone(coros_mcp._to_hours(raw))
        self.assertIsNone(coros_mcp._to_hours(0.2))   # menos de media hora: ruido
        self.assertIsNone(coros_mcp._to_hours(20))    # más de 16 h: imposible

    def test_fechas_en_varios_formatos(self):
        self.assertEqual(coros_mcp._to_date("20260918"), date(2026, 9, 18))
        self.assertEqual(coros_mcp._to_date("2026-09-18"), date(2026, 9, 18))
        self.assertEqual(coros_mcp._to_date("2026-09-18T23:00:00Z"), date(2026, 9, 18))
        self.assertEqual(coros_mcp._to_date(1789000000000), date(2026, 9, 10))  # epoch ms
        self.assertIsNone(coros_mcp._to_date("ayer"))
        self.assertIsNone(coros_mcp._to_date(None))


class TestParseSleep(unittest.TestCase):
    def test_structured_content_con_segundos_y_fecha_yyyymmdd(self):
        result = {"structuredContent": {"sleepList": [
            {"date": yyyymmdd(1), "mainSleepDuration": 26700, "score": 82},
            {"date": yyyymmdd(2), "mainSleepDuration": 24300},
        ]}}
        self.assertEqual(coros_mcp.parse_sleep(result), [
            {"date": iso(2), "sleep_hours": 6.75},
            {"date": iso(1), "sleep_hours": 7.42},
        ])

    def test_content_text_con_minutos_y_fecha_iso(self):
        payload = {"data": [{"sleepDate": iso(1), "totalDuration": 445}]}
        result = {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}
        self.assertEqual(coros_mcp.parse_sleep(result), [{"date": iso(1), "sleep_hours": 7.42}])

    def test_lista_anidada_y_campos_durisimos_de_encontrar(self):
        result = {"structuredContent": {"code": 0, "result": {"daily": {"list": [
            {"happenDay": yyyymmdd(3), "sleep": {"duration": 25200}},
        ]}}}}
        self.assertEqual(coros_mcp.parse_sleep(result), [{"date": iso(3), "sleep_hours": 7.0}])

    def test_sin_noches_utiles(self):
        for result in ({}, {"content": [{"type": "text", "text": "sin datos"}]},
                       {"structuredContent": {"list": [{"foo": 1}]}}, None):
            with self.subTest(result=result):
                self.assertEqual(coros_mcp.parse_sleep(result), [])

    def test_noches_repetidas_se_quedan_con_una_sola(self):
        result = {"structuredContent": {"l": [
            {"date": yyyymmdd(1), "mainSleepDuration": 26700},
            {"date": yyyymmdd(1), "mainSleepDuration": 26700},
        ]}}
        self.assertEqual(len(coros_mcp.parse_sleep(result)), 1)


class TestArgumentos(unittest.TestCase):
    def test_esquema_con_startday_endday_en_yyyymmdd(self):
        schema = {"type": "object", "properties": {
            "startDay": {"type": "string", "description": "Start date in YYYYMMDD format"},
            "endDay": {"type": "string", "description": "End date in YYYYMMDD format"},
        }}
        args = coros_mcp.build_arguments(schema, TODAY - timedelta(days=29), TODAY)
        self.assertEqual(args, {"startDay": yyyymmdd(29), "endDay": yyyymmdd(0)})

    def test_esquema_con_startdate_enddate_iso(self):
        schema = {"type": "object", "properties": {
            "startDate": {"type": "string"}, "endDate": {"type": "string"}}}
        args = coros_mcp.build_arguments(schema, TODAY - timedelta(days=29), TODAY)
        self.assertEqual(args, {"startDate": iso(29), "endDate": iso(0)})

    def test_sin_esquema_intenta_lo_mas_probable(self):
        args = coros_mcp.build_arguments(None, TODAY - timedelta(days=29), TODAY)
        self.assertEqual(args, {"startDate": iso(29), "endDate": iso(0)})

    def test_esquema_sin_fechas_no_inventa_argumentos_raros(self):
        args = coros_mcp.build_arguments({"properties": {"weeks": {"type": "integer"}}},
                                         TODAY - timedelta(days=29), TODAY)
        self.assertEqual(set(args), {"startDate", "endDate"})


class TestSecuenciaHTTP(unittest.TestCase):
    """Refresh → initialize → tools/list → tools/call, con un requests.post falso."""

    ISSUER = "https://mcpeu.coros.com"
    MCP = "https://mcpeu.coros.com/mcp"

    def setUp(self):
        self.llamadas = []
        self.respuestas = {
            f"{self.ISSUER}/oauth2/token": {"access_token": "AT", "refresh_token": "RT2",
                                            "expires_in": 3600},
            f"{self.MCP}#initialize": {"jsonrpc": "2.0", "id": 1,
                                       "result": {"protocolVersion": "2025-06-18"}},
            f"{self.MCP}#tools/list": {"jsonrpc": "2.0", "id": 2, "result": {"tools": [
                {"name": "querySleepData", "inputSchema": {"properties": {
                    "startDay": {"description": "YYYYMMDD"}, "endDay": {"description": "YYYYMMDD"}}}},
                {"name": "queryDailyHealthData"},
            ]}},
            f"{self.MCP}#tools/call": {"jsonrpc": "2.0", "id": 2, "result": {"structuredContent": {
                "sleepList": [{"date": yyyymmdd(1), "mainSleepDuration": 26700}]}}},
        }

        def falso_http(url, form=None, json_body=None, headers=None):
            clave = f"{url}#{(json_body or {}).get('method')}" if url.endswith("/mcp") else url
            self.llamadas.append({"url": url, "form": form, "json": json_body, "headers": headers})
            if clave not in self.respuestas:
                raise AssertionError(f"llamada inesperada: {clave}")
            return coros_mcp._Response(200, json.dumps(self.respuestas[clave]))

        parche = mock.patch.object(coros_mcp, "_http", side_effect=falso_http)
        parche.start()
        self.addCleanup(parche.stop)

    def test_fetch_sleep_hace_la_secuencia_completa(self):
        noches, nuevo_refresh = coros_mcp.fetch_sleep(
            days=30, client_id="CID", refresh_token="RT",
            issuer=self.ISSUER, mcp_url=self.MCP)

        self.assertEqual([(c["url"], (c["json"] or {}).get("method")) for c in self.llamadas], [
            ("https://mcpeu.coros.com/oauth2/token", None),
            ("https://mcpeu.coros.com/mcp", "initialize"),
            ("https://mcpeu.coros.com/mcp", "tools/list"),
            ("https://mcpeu.coros.com/mcp", "tools/call"),
        ])
        # El refresco manda el grant correcto y el client_id del registro.
        self.assertEqual(self.llamadas[0]["form"]["grant_type"], "refresh_token")
        self.assertEqual(self.llamadas[0]["form"]["client_id"], "CID")
        # Las llamadas MCP van autenticadas y aceptan SSE (lo pide el servidor).
        self.assertEqual(self.llamadas[1]["headers"]["Authorization"], "Bearer AT")
        self.assertIn("text/event-stream", self.llamadas[1]["headers"]["Accept"])
        # Los argumentos salen del inputSchema, no de un nombre supuesto.
        self.assertEqual(self.llamadas[3]["json"]["params"]["name"], "querySleepData")
        self.assertEqual(set(self.llamadas[3]["json"]["params"]["arguments"]), {"startDay", "endDay"})
        # Y el resultado acaba en noches parseadas + el refresh token rotado.
        self.assertEqual(noches, [{"date": iso(1), "sleep_hours": 7.42}])
        self.assertEqual(nuevo_refresh, "RT2")

    def test_sin_credenciales_no_llama_a_nada(self):
        with self.assertRaises(coros_mcp.CorosMCPError):
            coros_mcp.fetch_sleep(days=30, client_id=None, refresh_token=None)
        self.assertEqual(self.llamadas, [])

    def test_si_el_servidor_no_ofrece_sueño_se_queja_con_la_lista(self):
        self.respuestas[f"{self.MCP}#tools/list"] = {
            "jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "queryUserInfo"}]}}
        with self.assertRaises(coros_mcp.CorosMCPError) as ctx:
            coros_mcp.fetch_sleep(days=30, client_id="CID", refresh_token="RT",
                                  issuer=self.ISSUER, mcp_url=self.MCP)
        self.assertIn("queryUserInfo", str(ctx.exception))


class TestSuperposicion(unittest.TestCase):
    def test_load_sleep_lee_el_fichero_y_descarta_lo_ilegible(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "coros_sleep.json"
            p.write_text(json.dumps({"days": [
                {"date": iso(1), "sleep_hours": 7.42},
                {"date": "ayer", "sleep_hours": 7},        # fecha ilegible
                {"date": iso(2), "sleep_hours": "mucho"},  # horas ilegibles
                {"date": iso(3), "sleep_hours": 30},       # fuera de rango
                "basura",
            ]}), encoding="utf-8")
            self.assertEqual(coros_data.load_sleep(p), {iso(1): 7.42})

    def test_load_sleep_sin_fichero_o_roto(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(coros_data.load_sleep(Path(tmp) / "no.json"), {})
            roto = Path(tmp) / "roto.json"
            roto.write_text("{ sin cerrar", encoding="utf-8")
            self.assertEqual(coros_data.load_sleep(roto), {})

    def test_overlay_rellena_y_marca_la_fuente(self):
        data = coros_sin_sueno()
        out = coros_data.overlay_sleep(data, {iso(1): 7.42, iso(5): 6.5})
        dias = {d["date"]: d.get("sleep_hours") for d in out["days"]}
        self.assertEqual(dias[iso(1)], 7.42)
        self.assertEqual(dias[iso(5)], 6.5)
        self.assertEqual(out["sleep_hours"], 7.42)          # clave plana: la lee el correo
        self.assertTrue(out["available"]["sleep_hours"])
        self.assertEqual(out["sleep_source"], coros_data.SLEEP_SOURCE)
        self.assertNotIn("sleep_hours no disponible", " ".join(out["warnings"]))
        self.assertNotIn("sleep_source", data)              # no muta el original

    def test_overlay_actualiza_latest_si_coincide(self):
        out = coros_data.overlay_sleep(coros_sin_sueno(), {iso(0): 8.0})
        self.assertEqual(out["latest"]["sleep_hours"], 8.0)
        self.assertEqual(out["latest"]["resting_hr"], 49)   # no pierde lo demás

    def test_overlay_sin_coros_data_fabrica_un_payload_minimo(self):
        out = coros_data.overlay_sleep(None, {iso(1): 6.25})
        self.assertEqual(out["sleep_hours"], 6.25)
        self.assertEqual(out["latest"]["date"], iso(1))
        self.assertEqual([d["date"] for d in out["days"]], [iso(1)])

    def test_sin_sueño_devuelve_el_original(self):
        data = coros_sin_sueno()
        self.assertIs(coros_data.overlay_sleep(data, {}), data)
        self.assertIsNone(coros_data.overlay_sleep(None, {}))

    def test_load_superpone_los_dos_ficheros(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = Path(tmp) / "coros_data.json"
            c.write_text(json.dumps(coros_sin_sueno()), encoding="utf-8")
            s = Path(tmp) / "coros_sleep.json"
            s.write_text(json.dumps({"days": [{"date": iso(1), "sleep_hours": 7.42}]}),
                        encoding="utf-8")
            data = coros_data.load(c, s)
            self.assertEqual(coros_data.summary(data)["sleep_hours"], 7.42)
            self.assertEqual(coros_data.summary(data)["sleep_source"], coros_data.SLEEP_SOURCE)

    def test_load_sin_fichero_de_sueño_no_cambia_nada(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = Path(tmp) / "coros_data.json"
            c.write_text(json.dumps(coros_sin_sueno()), encoding="utf-8")
            data = coros_data.load(c, Path(tmp) / "no_hay.json")
            self.assertIsNone(coros_data.summary(data)["sleep_hours"])
            self.assertIsNone(coros_data.summary(data)["sleep_source"])


class TestCorreoConSuenoOficial(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name)
        self.coros_path = tmp / "coros_data.json"
        self.sleep_path = tmp / "coros_sleep.json"
        self._old = (coros_data.COROS_DATA_PATH, coros_data.SLEEP_PATH)
        coros_data.COROS_DATA_PATH, coros_data.SLEEP_PATH = self.coros_path, self.sleep_path
        self.addCleanup(setattr, coros_data, "COROS_DATA_PATH", self._old[0])
        self.addCleanup(setattr, coros_data, "SLEEP_PATH", self._old[1])

    def correo(self, noches):
        self.coros_path.write_text(json.dumps(coros_sin_sueno()), encoding="utf-8")
        self.sleep_path.write_text(json.dumps({"days": [
            {"date": iso(i), "sleep_hours": h} for i, h in noches.items()]}), encoding="utf-8")
        coros = db.get_coros_summary(TODAY)
        return coros, db.build_email_html(TODAY, db.get_today_plan(TODAY), STRAVA, coros)

    def test_el_correo_pinta_casilla_y_linea_de_30_noches(self):
        noches = {i: round(5.9 + (i % 5) * 0.1, 2) for i in range(0, 30)}
        coros, html = self.correo(noches)
        bloque = bloque_coros(html)
        self.assertEqual(coros["sleep_30d"]["n"], 30)
        self.assertIn("Sueño 5 h 54 m", bloque)   # la noche más reciente
        self.assertIn("Sueño (30 días):", bloque)
        self.assertIn("deuda acumulada", bloque)
        self.assertIn("sueño: MCP oficial de COROS", bloque)
        # El resto de Coros sigue ahí y el aviso falso desaparece.
        self.assertIn("FC reposo 49 bpm", bloque)
        self.assertNotIn("sleep_hours no disponible", bloque)

    def test_una_sola_noche_ya_rellena_la_casilla(self):
        coros, html = self.correo({1: 7.42})
        bloque = bloque_coros(html)
        self.assertEqual(coros["sleep_hours"], 7.42)
        self.assertEqual(coros["sleep_30d"]["n"], 1)
        self.assertIn("Sueño 7 h 25 m", bloque)
        self.assertIn("Sueño (30 días): 7h25m de media en 1 noche", bloque)

    def test_sin_fichero_de_sueño_sale_como_antes(self):
        self.coros_path.write_text(json.dumps(coros_sin_sueno()), encoding="utf-8")
        coros = db.get_coros_summary(TODAY)
        html = db.build_email_html(TODAY, db.get_today_plan(TODAY), STRAVA, coros)
        bloque = bloque_coros(html)
        self.assertIsNone(coros["sleep_hours"])
        self.assertEqual(coros["sleep_30d"]["n"], 0)
        self.assertIn("Sueño —", bloque)
        self.assertNotIn("Sueño (30 días)", bloque)
        self.assertIn("sleep_hours no disponible", bloque)

    def test_sueno_solo_sin_coros_data_json(self):
        self.sleep_path.write_text(json.dumps({"days": [{"date": iso(1), "sleep_hours": 6.25}]}),
                                  encoding="utf-8")
        coros = db.get_coros_summary(TODAY)
        html = db.build_email_html(TODAY, db.get_today_plan(TODAY), STRAVA, coros)
        bloque = bloque_coros(html)
        self.assertIsNotNone(coros)
        self.assertIn("Sueño 6 h 15 m", bloque)
        self.assertIn("Menos de 6,5 h de sueño", bloque)


class TestEscribirFichero(unittest.TestCase):
    def test_write_sleep_y_vuelta_a_leer(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = coros_mcp.write_sleep([{"date": iso(1), "sleep_hours": 7.42}], Path(tmp) / "s.json")
            self.assertEqual(coros_data.load_sleep(p), {iso(1): 7.42})

    def test_write_sleep_sin_noches_deja_aviso(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = coros_mcp.write_sleep([], Path(tmp) / "s.json")
            payload = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(payload["days"], [])
            self.assertTrue(payload["warnings"])
            self.assertEqual(coros_data.load_sleep(p), {})


class TestLoginLocal(unittest.TestCase):
    """El paso único lo hace el usuario en su máquina; aquí se prueba la mecánica."""

    PUERTO = 43199

    def _llama(self, code, state):
        resultado = {}

        def hilo():
            resultado["code"] = get_token.espera_codigo(puerto=self.PUERTO, state=state, timeout=5)

        t = threading.Thread(target=hilo, daemon=True)
        t.start()
        time.sleep(0.3)  # deja que el servidor se ponga a escuchar
        qs = urllib.parse.urlencode({"code": code, "state": state})
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{self.PUERTO}/callback?{qs}", timeout=5).read()
        except Exception:
            pass  # un 400 (state mal) también es una respuesta válida para el test
        t.join(timeout=8)
        return resultado.get("code")

    def test_pkce_el_challenge_es_s256_del_verifier(self):
        verifier, challenge = get_token.pkce()
        esperado = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        self.assertEqual(challenge, esperado)
        self.assertNotIn("=", verifier)
        self.assertNotEqual(verifier, get_token.pkce()[0])  # distinto en cada ejecución

    def test_recoge_el_code_del_callback(self):
        self.assertEqual(self._llama("CODE123", "ST-OK"), "CODE123")

    def test_rechaza_un_state_que_no_coincide(self):
        # Servidor esperando state "BUENO"; le llega un callback con otro state.
        resultado = {}

        def hilo():
            resultado["code"] = get_token.espera_codigo(puerto=self.PUERTO, state="BUENO", timeout=2)

        t = threading.Thread(target=hilo, daemon=True)
        t.start()
        time.sleep(0.3)
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{self.PUERTO}/callback?code=ROBADO&state=MALO", timeout=5).read()
        except Exception:
            pass
        t.join(timeout=8)
        self.assertIsNone(resultado["code"])

    def test_sin_callback_devuelve_none_al_cabarse_el_tiempo(self):
        inicio = time.time()
        self.assertIsNone(get_token.espera_codigo(puerto=self.PUERTO, state="X", timeout=1))
        self.assertLess(time.time() - inicio, 5)

    def test_main_completo_sin_red(self):
        """main() de verdad: registro + navegador + callback + intercambio.

        Solo se parchean las dos llamadas a COROS (sin red aquí); el callback
        es una petición HTTP real a 127.0.0.1, como la haría el navegador.
        """
        vistos = {}

        def falso_open(url):
            vistos["url"] = url
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

            def disparar():
                time.sleep(0.3)
                qs = urllib.parse.urlencode({"code": "CODE-REAL",
                                             "state": query["state"][0]})
                try:
                    urllib.request.urlopen(
                        f"http://127.0.0.1:{get_token.PUERTO_CALLBACK}/callback?{qs}",
                        timeout=5).read()
                except Exception:
                    pass

            threading.Thread(target=disparar, daemon=True).start()
            return True

        salida = io.StringIO()
        with mock.patch.object(coros_mcp, "register_client", return_value="CID-123"), \
             mock.patch.object(coros_mcp, "exchange_code",
                               return_value={"refresh_token": "RT-999", "expires_in": 3600}) as cambio, \
             mock.patch("builtins.input", return_value=""), \
             mock.patch.object(get_token.webbrowser, "open", side_effect=falso_open), \
             contextlib.redirect_stdout(salida):
            self.assertEqual(get_token.main(), 0)

        texto = salida.getvalue()
        self.assertIn("COROS_MCP_CLIENT_ID = CID-123", texto)
        self.assertIn("COROS_MCP_REFRESH_TOKEN = RT-999", texto)
        # El code que llegó por el callback es el que se canjea, con su verifier.
        self.assertEqual(cambio.call_args[0][0], "CID-123")
        self.assertEqual(cambio.call_args[0][1], "CODE-REAL")
        self.assertTrue(cambio.call_args[0][2])            # code_verifier
        # Y la URL de autorización lleva PKCE, el resource del MCP y el state.
        params = urllib.parse.parse_qs(urllib.parse.urlparse(vistos["url"]).query)
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertEqual(params["scope"], [coros_mcp.DEFAULT_SCOPES])
        self.assertEqual(params["resource"], ["https://mcp.coros.com/mcp"])
        self.assertEqual(params["redirect_uri"], [get_token.REDIRECT_URI])

    def test_puerto_ocupado_no_revienta(self):
        import socket

        ocupado = socket.socket()
        ocupado.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ocupado.bind(("127.0.0.1", self.PUERTO))
        ocupado.listen(1)
        try:
            self.assertIsNone(get_token.espera_codigo(puerto=self.PUERTO, state="X", timeout=1))
        finally:
            ocupado.close()


if __name__ == "__main__":
    unittest.main()
