# Correo diario de entrenamiento + dieta (Kthuluh)

Envía cada mañana, desde GitHub Actions (en la nube, no necesita tu compu
encendida), un correo con: resumen de ayer (Strava), el entrenamiento de
hoy según tu plan de 10K/21K, y la dieta del día. Y actualiza el dashboard.

**Dos fuentes de datos:**

| Fuente | Cómo | Qué aporta |
|---|---|---|
| Strava | API oficial + OAuth (refrescable) | actividades, km, volumen, ritmo, desnivel |
| Coros | librería **no oficial** [`@pinta365/coros`](https://github.com/Pinta365/coros) (JSR) | FC reposo, HRV nocturna y su baseline, carga corto/largo plazo (t7d / t28d), ratio de carga, fatiga, performance |

---

## ⚠️ 0. Antes de nada: la contraseña que dejaste en el código

`scripts/coros_fetch.mjs` tenía **tu email y tu contraseña de Coros escritos a
mano** y eso se subió a Git. Aunque ahora el código los lee de secretos, la
cadena sigue en el historial del repo (`git log -p -- scripts/coros_fetch.mjs`).

1. **Cambia tu contraseña de Coros** (app/web → cuenta → seguridad). Ya.
2. Si el repo era público, asume que alguien la leyó.
3. Si quieres borrarla del historial: `git filter-repo` o, más simple, crear un
   repo nuevo sin el historial viejo. Cambiar la contraseña es lo que de verdad
   cierra el problema.

Nunca pongas credenciales en el código: para eso están los secretos del paso 4.

## 1. Crear tu app de Strava

1. Ve a https://www.strava.com/settings/api y crea una app (cualquier
   nombre, cualquier "Authorization Callback Domain" tipo `localhost`).
2. Anota el **Client ID** y el **Client Secret**.
3. En tu computadora (no en GitHub), corre:
   ```
   python3 -m pip install requests      # o `pip3 install requests`
   python scripts/get_strava_refresh_token.py
   ```
   Sigue las instrucciones en pantalla. Al final te da tres valores:
   `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, `STRAVA_REFRESH_TOKEN`.

## 2. Crear una contraseña de aplicación de Gmail

1. En tu cuenta de Google, activa la verificación en 2 pasos si no la
   tienes (Cuenta de Google → Seguridad → Verificación en 2 pasos).
2. Ve a https://myaccount.google.com/apppasswords, crea una contraseña
   de aplicación (nombre: "daily-brief", por ejemplo).
3. Copia la contraseña de 16 caracteres que te da — esa es tu
   `GMAIL_APP_PASSWORD`. Tu `GMAIL_USER` es tu correo completo
   (kthuluh@gmail.com).

## 3. Configurar los secretos en GitHub

En tu repo: **Settings → Secrets and variables → Actions → New repository
secret**. Crea estos 6 (los de Coros del paso 4 son 2 más, y los del sueño del
paso 4.6 otros 2):

| Nombre | Valor |
|---|---|
| `STRAVA_CLIENT_ID` | del paso 1 |
| `STRAVA_CLIENT_SECRET` | del paso 1 |
| `STRAVA_REFRESH_TOKEN` | del paso 1 |
| `GMAIL_USER` | kthuluh@gmail.com |
| `GMAIL_APP_PASSWORD` | del paso 2 |
| `EMAIL_TO` | kthuluh@gmail.com |
| `COROS_MCP_CLIENT_ID` | del paso 4.6 (sueño, API oficial de COROS) |
| `COROS_MCP_REFRESH_TOKEN` | del paso 4.6 (sueño, API oficial de COROS) |

## 4. Coros: instalar y activar

Coros no tiene API pública para uso personal. Se usa la librería de terceros
`@pinta365/coros`, que inicia sesión en COROS Training Hub con tu email y
contraseña reales. Es decir: **puede romperse cuando Coros cambie su web** y
**tu contraseña vive en GitHub Secrets**. Si no te vale con eso, no actives
nada: el correo y el dashboard funcionan igual solo con Strava.

### 4.1 Qué necesita cada runtime

La librería es Deno-first y se publica en **JSR**, no en npm. Por eso el repo
ya trae:

* `.npmrc` → `@jsr:registry=https://npm.jsr.io` (sin esto, `npm install` da
  404 al buscar `@jsr/pinta365__coros`)
* `package.json` → dependencias con el alias de compatibilidad:
  `"@pinta365/coros": "npm:@jsr/pinta365__coros@^0.0.1"` y
  `"@cross/fs": "npm:@jsr/cross__fs@^0.1.14"` (la usa la librería para guardar
  el token de sesión)
* `--experimental-strip-types` al llamar a `node`: `@pinta365/coros` son
  fuentes `.ts` sin compilar y Node necesita strip-types para importarlas.
  Requiere **Node 22.6+** (en 22.18+/23.6+ viene activado, el flag no molesta).

**Con Deno te saltas todo eso** (resuelve `jsr:` solo) — `scripts/coros_fetch.mjs`
detecta el runtime y usa un especificador u otro.

### 4.2 En tu máquina (para probar antes de tocar GitHub)

```bash
# node 22.6+ o deno 1.x/2.x instalado
nvm use 22                 # o: brew install deno   (mac) / winget install deno (win)
npm install                # baja la librería desde npm.jsr.io gracias a .npmrc
# (alternativa canónica, reescribe package.json y .npmrc él solo:
#  npx jsr add @pinta365/coros @cross/fs)

export COROS_EMAIL="tu@email.com"
export COROS_PASSWORD="tu contraseña"     # la nueva del paso 0
export COROS_REGION="eu"                  # España = eu (en=Americas, cn=China)

node --experimental-strip-types scripts/coros_fetch.mjs
# o con deno:   deno run -A scripts/coros_fetch.mjs
```

Si va bien escribe `coros_data.json` en la raíz y te imprime un resumen:

```
✓ /path/Training/coros_data.json escrito (2026-09-17, 120 días, 18 semanas)
  FC reposo: 49 bpm  ·  HRV: 44 ms  ·  sueño: 7.31 h  ·  ratio carga: 0.82
```

Y ya puedes ver el efecto localmente, sin GitHub ni red de Strava:

```bash
python scripts/update_dashboard.py --only-coros --dry-run   # qué tocaría
python scripts/update_dashboard.py --only-coros             # lo escribe
DRY_RUN=1 python scripts/daily_brief.py                     # el correo, impreso, no enviado
```

(`DRY_RUN=1` en `daily_brief.py` imprime el HTML en vez de enviarlo; para eso
sí necesitas los secretos de Strava exportados.)

### 4.3 En GitHub

1. Añade los secretos `COROS_EMAIL` y `COROS_PASSWORD`
   (Settings → Secrets and variables → Actions).
2. Nada más: los dos workflows ya traen los pasos de Node y del fetch.
   Si quitas los secretos, el paso de Coros termina sin hacer nada.

Variables de entorno que entiende el fetch (todas opcionales salvo email/pass):

| Variable | Default | Para qué |
|---|---|---|
| `COROS_EMAIL` / `COROS_PASSWORD` | — | login en Training Hub (secretos) |
| `COROS_REGION` | `eu` | **tiene que ser la región de tu cuenta**; un token de otra región no vale |
| `COROS_DAYS` | `120` | días de historia que se piden. El correo pide 30; el dashboard pide `370` (cubre las 14 semanas del gráfico y el año entero del gráfico mensual de FC reposo) |
| `COROS_OUT` | `coros_data.json` en la raíz | dónde se escribe el JSON |
| `COROS_TOKEN_FILE` | vacío | caché del token (`.coros_token`) para no loguear en cada ejecución; Coros rate-limea y logout en otro sitio invalida tokens |
| `COROS_DEBUG` | vacío | `1` imprime **las claves reales** que devolvió la API |
| `COROS_FIXTURE` | vacío | path a un JSON con la respuesta cruda de `getAnalyse()` para probar sin red ni credenciales |

### 4.4 Qué datos salen de Coros (y cuáles NO)

Lo que usa el script es `client.getAnalyse({startDate, endDate})` (endpoint
`analyse/query`, el de EvoLab) más `client.getAccount()`:

* `rhr` → **FC reposo**
* `avgSleepHrv` / `sleepHrvBase` → **HRV nocturna** y su baseline
* `t7d` → **carga corto plazo** ("Load Impact")
* `t28d` → **carga largo plazo** ("Base Fitness")
* `trainingLoadRatio` → **ratio de carga**
* `tiredRateNew` → fatiga; `performance` → rendimiento (−1 = sin estimación)
* `distance` / `duration` por día
* **pasos** del día, si la respuesta trae alguna clave tipo `steps` /
  `stepCount` / `dailySteps` (mira `STEPS_KEYS` en `scripts/coros_fetch.mjs`;
  acepta el valor en miles, `14.2` → 14.200). Si no viene, `steps` queda `null`
  y la tarjeta de pasos del dashboard se queda como esté (manual).

**Horas de sueño: por esta vía, no.** `@pinta365/coros` 0.0.1 no tiene endpoint
de sueño, solo HRV nocturna. El script aun así lo intenta: la respuesta de Coros
trae más campos de los que la librería tipa, así que busca claves sueltas
(`sleepDuration`, `totalSleep`, `sleepTime`…) y, si aparecen, las convierte a
horas. Si en tu cuenta no vienen, `sleep_hours` queda `null` en
`coros_data.json` — y entonces entra el MCP oficial (4.6), que sí las trae.

Para saber si tu cuenta los tiene: `COROS_DEBUG=1 node ... coros_fetch.mjs` y
mira la lista de claves. Con `COROS_DEBUG=1` el script además imprime qué claves
tienen pinta de sueño, cuánto saca `sleepHoursOf()` del primer día y un recordatorio
de que las fases no se piden. Si ves una de sueño con otro nombre, añádela a
`SLEEP_KEYS` arriba de todo en `scripts/coros_fetch.mjs` y ya se auto-refresca.

**Fases del sueño (ligero / profundo / REM): no, y a propósito.** Solo salen por
la API **móvil** de Coros, que se autentica con claves sacadas del APK de la app:

1. esas claves no son públicas y cambian con cada versión, así que un cron de
   GitHub las rompería en cuanto Coros actualizara la app;
2. y lo que manda: el login móvil **invalida la sesión del teléfono**, o sea que
   ejecutarlo cada madrugada desloguearía el reloj del móvil.

Por eso no se intenta siquiera. El contrato lo deja explícito en vez de callarse:
`coros_data.json` sale con `"available": { …, "sleep_phases": false }` y ningún
día trae fases, de modo que quien pinta puede distinguir "no se pide" de "se nos
olvidó". El bloque de Hábitos lo dice en sus notas, y la tarjeta "Sueño profundo
medio" del Resumen general sigue siendo manual.

### 4.5 Formato de `coros_data.json` (el contrato entre el .mjs y el .py)

```jsonc
{
  "schema_version": 1,
  "generated_at": "2026-09-17T05:12:33.123Z",
  "region": "eu",
  "date": "2026-09-17",
  "latest":  { "date": "...", "resting_hr": 49, "hrv": 44, "hrv_base": 45,
               "sleep_hours": null, "load_short": 58, "load_long": 61,
               "load_ratio": 0.95, "fatigue": 12, "performance": 78,
               "distance_km": 8.2, "duration_min": 44, "has_data": true },
  "latest_14": { "resting_hr_avg": 50.1, "resting_hr_min": 46, "resting_hr_max": 55,
                 "hrv_avg": 43.2, "sleep_hours_avg": null, "steps_avg": 15420,
                 "n_days": 12 },
  "days":  [ { "date": "2026-09-16", "resting_hr": 49, "steps": 15420, ... }, ... ],  // 1 por día
  "weeks": [ { "week_start": "2026-09-14", "resting_hr": 50, "sleep_hours": null,
               "hrv": 43, "load_ratio": 0.9, "distance_km": 12.4, "n_days": 5 }, ... ],
  "available": { "resting_hr": true, "hrv": true, "sleep_hours": false,
                 "steps": true, "load_ratio": true,
                 "sleep_phases": false },   // siempre false: ver 4.4 (API móvil)
  "warnings": [ "..." ],
  "resting_hr": 49, "sleep_hours": null, "hrv": 44   // claves planas legacy
}
```

`days`/`weeks` los consume `update_dashboard.py`; `latest` y las tres claves
planas los consume `daily_brief.py` (por eso puedes seguir escribiendo el JSON
a mano con solo `resting_hr`/`sleep_hours`/`hrv` si prefieres no usar Coros).
Si `coros_data.json` no existe, no pasa nada: ambos scripts siguen su camino
con Strava. **No se sube al repo** (está en `.gitignore`): son datos de salud
efímeros que se regeneran en cada workflow.

### 4.6 Sueño: el MCP **oficial** de COROS (mcp.coros.com)

El sueño no estaba en EvoLab, y la única otra puerta era la API **móvil** de
Coros (`apieu.coros.com`), cuyo login desloguea la app del teléfono: inusable en
un cron. Pero COROS publicó su **MCP oficial** (`https://mcp.coros.com/mcp`,
OAuth con PKCE), y entre sus herramientas está `querySleepData`: *"sleep score,
main sleep duration, deep/light/REM ratios, wakefulness, sleep window and nap
information"*. Es API oficial, no desloguea nada y se refresca con un refresh
token igual que Strava.

`scripts/coros_mcp.py` hace eso cada madrugada y escribe `coros_sleep.json`,
que `coros_data.load()` superpone sobre `coros_data.json`. O sea: el correo y el
dashboard se enteran **sin tocar su código**, y si el paso falla el correo sale
igual (la casilla en `—`).

**Paso único (en tu ordenador, no en GitHub):**

```bash
python3 scripts/get_coros_mcp_token.py
```

No hace falta instalar nada: ese script y `scripts/coros_mcp.py` usan solo la
librería estándar de Python (3.8+). Y no hay que crear ninguna app: el MCP oficial acepta registro dinámico de
clientes, así que el script se registra solo (cliente público, sin
client_secret), abre el navegador para que inicies sesión con tu cuenta de
COROS y te devuelve dos valores → secretos de GitHub:

| Nombre | Valor |
|---|---|
| `COROS_MCP_CLIENT_ID` | el `client_id` del registro |
| `COROS_MCP_REFRESH_TOKEN` | el refresh token (el workflow lo refresca solo) |

**Variables de entorno que entiende `scripts/coros_mcp.py`:**

| Variable | Para qué |
|---|---|
| `COROS_SLEEP_DAYS` | noches a pedir (`30` en el correo, `120` en el dashboard) |
| `COROS_SLEEP_PATH` | dónde escribir (`coros_sleep.json`) |
| `COROS_MCP_ISSUER` | `https://mcp.coros.com` por defecto; `mcpeu`/`mcpus`/`mcpcn` para fijar región |
| `COROS_MCP_URL` | `{issuer}/mcp` por defecto |

**Si COROS cambia algo**, el script se diagnostica solo:

```bash
python scripts/coros_mcp.py --schema   # herramientas + inputSchema de querySleepData
python scripts/coros_mcp.py --dump     # la respuesta cruda ya parseada
```

Los argumentos de la llamada no están escritos a mano: se construyen leyendo el
`inputSchema` que declara el servidor (`scripts/coros_mcp.py:build_arguments`), y
el parseo acepta segundos, minutos u horas y fechas `YYYYMMDD` o ISO, porque
COROS no documenta la forma exacta de la respuesta.

## 5. Probarlo

En GitHub: pestaña **Actions** → "Daily training + diet brief" →
**Run workflow** → Run workflow. Revisa que llegue el correo. Si falla,
el log de Actions te dice exactamente en qué paso.

* El paso **"Fetch Coros → coros_data.json"** puede salir en amarillo (tiene
  `continue-on-error`): el correo se manda igual, sin datos de Coros.
* Una vez que funcione a mano, corre solo cada día con el cron
  (`.github/workflows/daily-brief.yml`, formato UTC).
* Los dos workflows fijan `TZ: Europe/Madrid` para que "hoy/ayer" coincidan con
  tus días locales.

## 6. Si algo cambia en tu plan

Todo el plan de entrenamiento (semana por semana) y la dieta están en
`scripts/daily_brief.py`, en las listas `WEEKS` y `DIET` al principio del
archivo — edítalas ahí directamente, sin tocar el resto del código.

Ahí arriba también están `HR_REST`/`HR_MAX` (zonas Karvonen) y
`HR_REST_FROM_COROS`: si lo pones en `True`, las zonas del correo se recalculan
cada día con la FC reposo que mida tu Coros en vez del valor fijo.

## 7. Dashboard con auto-actualización

`dashboard/dashboard-kthuluh.html` es tu dashboard. El workflow **Update
dashboard** reescribe cada día los trozos marcados con comentarios `AUTO:`.
Ahora se actualizan **todas las pestañas**, no solo la de Resumen general:

| Pestaña | Marcadores | Fuente | Qué rellenan |
|---|---|---|---|
| Encabezado / pie | `UPDATED_DATE`, `FOOTER_INFO` | Strava + Coros | fecha y cobertura real de los datos |
| Resumen general | `HERO_RHR`, `HERO_RATIO`, `RHR_CARD`, `RHR_CARD_SUB`, `HRV_CARD`, `HRV_CARD_SUB`, `STEPS_CARD`, `STEPS_CARD_SUB`, `SLEEP_14D_AVG`, `SLEEP_14D_SUB`, `LOAD_CAPTION`, `RHR30_LABELS`, `RHR30_DATA`, `LOAD14_LABELS`, `LOAD14_SHORT`, `LOAD14_LONG` | Coros | tarjetas del hero y de "Estado actual", gráficos de FC reposo (30 d) y carga (14 d) |
| Entrenamiento | `PLAN_STATUS` | — | en qué semana de las 18 estás |
| Entrenamiento | `ENTRENO_PB`, `ENTRENO_LAST4W`, `ENTRENO_VOL_NOTE`, `ENTRENO_STRENGTH_NOTE`, `CHARTVOL_LABELS`, `CHARTVOL_RUN`, `CHARTVOL_WALK`, `ZONES_META`, `ZONES_ROWS` | Strava | mejores marcas 10K/21K de los últimos 12 meses, resumen de las últimas 4 semanas, gráfico de volumen (8 semanas, carrera + caminatas apiladas), tabla de zonas Karvonen y aviso de fuerza |
| Dieta | `DIETA_ACTIVITY`, `DIETA_CONTEXT` | Coros + Strava | pasos reales de Coros y contexto de la semana (km, sesiones, ratio de carga) |
| Hábitos | `HABIT_01_BODY`, `HABIT_02_BODY`, `HABIT_03_BODY`, `HABIT_05_BODY` | Strava + Coros | última sesión de fuerza, noches más cortas, salidas que se pasan del techo de Z2 y rampa de volumen |
| Hábitos | `SLEEP_TITLE`, `SLEEP_CARDS`, `SLEEP_VERDICT`, `SLEEP_NOTES`, `SLEEP30_LABELS`, `SLEEP30_DATA`, `SLEEP30_TARGET` | Coros | bloque *Análisis del sueño* al final de la pestaña: 4 tarjetas (media, noches en objetivo, deuda y tendencia 7 vs 7), gráfico de las últimas 30 noches contra el objetivo, veredicto y notas con lo que Coros no da |
| Historial | `TREND_VOL_CARD`, `TREND_RHR_CARD`, `TREND_SLEEP_CARD`, `TREND_VERDICT` | Strava + Coros | las 4 últimas semanas completas frente a las 4 anteriores |
| Historial | `RACES` | Strava | carreras del año (`workout_type = Race`) |
| Historial | `MONTHLY_LABELS`, `MONTHLY_VOL_RUN`, `MONTHLY_VOL_WALK`, `MONTHLY_RHR`, `MONTHLY_YEAR`, `MONTHLY_VOL_YEAR`, `MONTHLY_RHR_YEAR`, `MONTHLY_RANGE_TITLE`, `MONTHLY_CARDS` | Strava + Coros | gráficos mensuales (volumen a pie apilado y FC reposo) y tarjetas de mes |
| Historial | `WEEKLY_LABELS`, `WEEKLY_VOL_RUN`, `WEEKLY_VOL_WALK`, `WEEKLY_MONDAYS`, `WEEKLY_RHR`, `WEEKLY_SLEEP`, `WEEKLY_CARDS`, `CURRENT_WEEK`, `HIST_COVERAGE_NOTE` | Strava + Coros | 12 semanas completas + la semana en curso, con sueño y FC reposo si Coros los trae |
| Recomendaciones | `RECOS` | Strava + Coros | la lista entera, generada por reglas |

### 7.0 Qué cuenta como volumen

El volumen (semanal, mensual, la tarjeta de tendencia, el contexto de la dieta,
la rampa de Hábitos y el correo diario) es **todo lo que haces a pie**, no solo
las carreras:

| Cuenta | Tipos de Strava |
|---|---|
| Carrera | `Run`, `TrailRun`, `VirtualRun` |
| Caminatas | `Walk`, `Hike` |

En los gráficos salen **apilados**: la barra entera es el volumen de la semana y
el tramo más claro es lo que caminaste, así se ve de un vistazo cuánto es correr.
Lo que registra kilómetros pero no es andar (`Ride`, `Swim`, `Rowing`,
`Elliptical`…) queda **fuera** a propósito: no es carga de carrera y mezclarlo
inflaría el volumen del plan de 10K/21K.

Si quieres cambiarlo, todo vive en una línea: `FOOT_TYPES` (y `RUN_TYPES` /
`WALK_TYPES`) en `scripts/dashboard_stats.py`. Añade ahí `"ride"` y la bici
empezará a contar. Lo que sigue siendo solo de carrera, y por qué: las mejores
marcas 10K/21K, el nº de salidas/semana, la tirada larga, el conteo de fuerza y
el aviso de "salidas por encima del techo de Z2".

Tres reglas que sigue el script (y que valen para todas las pestañas):

1. **Nunca se inventa un dato.** Si Strava no devuelve actividades o Coros no
   trae un campo, ese bloque se queda exactamente como estaba.
2. **Sin datos, lo manual manda.** Puedes editar a mano cualquier cifra entre
   `<!--AUTO:…-->` y `<!--/AUTO:…-->`: se conserva mientras no haya dato real
   que la sustituya. Las tarjetas semanales y las líneas del gráfico de
   tendencia van indexadas **por su lunes**, no por posición, así que al entrar
   semanas nuevas tus valores no se desplazan.
3. **Cada sección va aislada.** Si una falla (Strava, un marcador borrado, un
   dato raro), el script anota `⚠ <sección> falló: …` en el log y sigue con las
   demás. Antes, un fallo en cualquier punto dejaba el dashboard sin actualizar.

### 7.1 Qué sigue siendo manual (a propósito)

* Peso, edad y altura (no hay conector a una báscula; Coros sigue con 70 kg).
* Las calorías y macros de la pestaña Dieta, que dependen del peso.
* Las **horas de sueño ya no**: vienen del MCP oficial de COROS (4.6) en cuanto
  configures `COROS_MCP_CLIENT_ID` / `COROS_MCP_REFRESH_TOKEN`. Sin esos
  secretos, el bloque se queda con el último valor manual, como siempre.
* Sueño profundo y fases (ligero / REM): `querySleepData` las devuelve, pero
  todavía no se pintan en ningún sitio; haría falta decidir dónde. El JSON de
  EvoLab sigue declarando `"sleep_phases": false` porque por ahí no salen.
* El **objetivo** de sueño (7,5 h por defecto): es tuyo, no un dato que se
  descargue. Se cambia con `SLEEP_TARGET_HOURS` sin tocar el código.
* Las tablas del plan (bloques 10K/21K) y las 3 recomendaciones fijas.

### 7.2 Probarlo a mano

```bash
python scripts/update_dashboard.py               # Strava + Coros (lo del workflow)
python scripts/update_dashboard.py --only-coros  # solo la parte de Coros, sin Strava
python scripts/update_dashboard.py --dry-run     # enseña lo que haría, no escribe
```

Variables que entiende para probar sin tocar el dashboard real:

| Variable | Para qué |
|---|---|
| `DASHBOARD_PATH` | reescribir una **copia** del HTML en vez del real |
| `STRAVA_FIXTURE` | path a un JSON con actividades crudas de Strava → no usa la API |
| `COROS_DATA_PATH` | usar otro `coros_data.json` (por ejemplo uno de prueba) |
| `HR_REST_FROM_COROS` | `1` → recalcula las zonas Karvonen con tu FC reposo real de Coros (en el correo el equivalente es `HR_REST_FROM_COROS` en `daily_brief.py`) |
| `COROS_DEBUG` | `1` → imprime las claves reales que devuelve Coros (útil para ver si hay sueño o pasos) |
| `SLEEP_TARGET_HOURS` | objetivo de sueño del bloque de Hábitos y de la línea del correo (`7.5` por defecto, definido en `scripts/dashboard_stats.py`). En GitHub va como *variable* del repo —Settings → Secrets and variables → Actions → Variables—, no como secret; si llega vacía se usa el valor por defecto |

### 7.3 Tests

La lógica de cálculo vive en `scripts/dashboard_stats.py` (pura: sin red y sin
HTML) y los tests la cubren con fixtures, sin credenciales:

```bash
python -m unittest discover -s tests -t .
```

Cubren que todos los marcadores existan (y una sola vez) en el HTML, que con
datos los números salgan bien, que **sin datos no se toque nada**, que dos
pasadas seguidas den el mismo HTML y que `PLAN_START`/`HR_REST`/`HR_MAX`/
`SLEEP_TARGET_HOURS` sigan cuadrando con `scripts/daily_brief.py`.

`tests/test_coros_mcp.py` cubre el sueño oficial: el parseo tolerante de
`querySleepData` (segundos/minutos/horas, fechas `YYYYMMDD` o ISO, listas
anidadas), que los argumentos salgan del `inputSchema` del servidor, la
secuencia HTTP completa (refresh → initialize → tools/list → tools/call) con un
`requests.post` falso, la superposición sobre `coros_data.json` y que el correo
pinte la casilla y la línea de 30 noches.

También cubren el bloque de sueño (media, deuda, tendencia 7 vs 7 y los tres
veredictos según el objetivo), que con `sleep_hours` a `null` el bloque manual no
se mueva ni una coma, y el propio `scripts/coros_fetch.mjs`: se ejecuta con
`COROS_FIXTURE` y comprueba que `sleepDuration` acaba en `sleep_hours` y que
`sleep_phases` sale `false`. Ese último necesita `node` en el PATH (el workflow
lo instala); **no** necesita `npm install`, porque la ruta de fixture no llega a
importar `@pinta365/coros`. Sin `node`, ese test se salta en vez de fallar.

El workflow **Tests** los corre en cada push y en cada PR.

### 7.4 Link fijo del dashboard

**Para tener un link fijo que siempre muestre la última versión:**

1. En tu repo: **Settings → Pages**.
2. En "Source", elige **Deploy from a branch**, rama `main`, carpeta
   `/ (root)` (o `/dashboard` si tu plan de GitHub Pages te deja elegir
   subcarpeta — si no, déjalo en root, funciona igual).
3. Guarda. GitHub te da una URL tipo
   `https://tu-usuario.github.io/tu-repo/dashboard/dashboard-kthuluh.html`
   — tarda 1-2 minutos en activarse la primera vez.
4. Guárdala en favoritos. Cada mañana, después de que corra el workflow,
   esa misma URL va a mostrar los datos del día.

Para probarlo ahora mismo sin esperar al cron: pestaña **Actions** →
"Update dashboard" → **Run workflow**.

## 8. Troubleshooting

| Síntoma | Causa / arreglo |
|---|---|
| `npm error 404 … @jsr/pinta365__coros` | falta el `.npmrc` con `@jsr:registry=https://npm.jsr.io`, o lo borraste |
| `SyntaxError … Unexpected token` al importar `.ts` | Node viejo (<22.6) o falta `--experimental-strip-types`. Prueba `deno run -A scripts/coros_fetch.mjs` |
| `Coros falló (HTTP 401)` / credenciales | contraseña mal, o el `COROS_REGION` no es el de tu cuenta (España → `eu`) |
| `Coros falló (429)` | rate limit; deja pasar un rato y relanza el workflow. Reduce logins usando `COROS_TOKEN_FILE` |
| Login OK pero `dayList` vacío | EvoLab sin activar en la cuenta, o región/periodo mal (`COROS_DAYS`) |
| Coros inicia sesión desde el móvil y el bot deja de funcionar | Coros invalida tokens al loguear en otro sitio; con `continue-on-error` el correo sale igual, y con `COROS_TOKEN_FILE` + re-login automático se recupera solo |
| El dashboard no cambia nada | no hay `coros_data.json` (mira el log del paso de Coros) o los datos no son de esta semana |
| `⚠ <sección> falló: …` en el log de "Update dashboard" | esa sección se quedó con los valores anteriores y el resto sí se actualizó; el mensaje dice qué pasó (lo normal: marcador borrado del HTML al editarlo a mano) |
| La tarjeta de pasos o la de sueño no se actualizan | Coros no devuelve ese campo: `COROS_DEBUG=1 node --experimental-strip-types scripts/coros_fetch.mjs` y mira las claves reales (`STEPS_KEYS` / `SLEEP_KEYS`) |
| `✗ COROS MCP: faltan COROS_MCP_CLIENT_ID / COROS_MCP_REFRESH_TOKEN` | no has hecho el paso 4.6; el correo sale igual, con la casilla de Sueño en `—` |
| Sueño en `—` con los secretos ya puestos | mira el paso "Fetch sueño (COROS MCP oficial)" del log y, en local, `python scripts/coros_mcp.py --schema` y `--dump`: dicen qué herramientas ofrece el servidor y qué forma tiene la respuesta |
| `token refresh failed` (sueño) | el refresh token caducó o COROS lo rotó: repite `python scripts/get_coros_mcp_token.py` y actualiza `COROS_MCP_REFRESH_TOKEN`. Si lo rota, el script lo avisa en el log |
| Los tests fallan al añadir un marcador al HTML | falta darlo de alta en la lista `HTML_MARKERS`/`JS_MARKERS` de `tests/test_dashboard.py` |

## 9. Ficheros

```
.github/workflows/daily-brief.yml      correo diario (Strava + Coros)
.github/workflows/update-dashboard.yml dashboard diario (Strava + Coros)
.github/workflows/tests.yml            tests del dashboard (sin secretos)
scripts/coros_fetch.mjs    Coros (EvoLab) → coros_data.json   (Node 22.6+ / Deno)
scripts/coros_mcp.py       COROS MCP oficial → coros_sleep.json (sueño)
scripts/get_coros_mcp_token.py  una vez: client_id + refresh token del MCP
scripts/coros_data.py      lector compartido del JSON (tolerante a fallos)
scripts/dashboard_stats.py cálculos puros del dashboard (semanal, mensual,
                           tendencias, carreras, zonas Karvonen…) — testeable
scripts/daily_brief.py     plan + dieta + Strava + Coros → correo
scripts/update_dashboard.py  reescribe los bloques AUTO: del HTML (todas las pestañas)
dashboard/dashboard-kthuluh.html  tu panel (GitHub Pages)
tests/test_dashboard.py    tests del dashboard (fixtures, sin red)
tests/test_coros_mcp.py    tests del sueño oficial (fixtures, sin red)
.github/workflows/tests.yml  corre esos tests en cada push y PR
package.json / .npmrc      dependencias de JSR para Node
requirements.txt           requests, para Strava
```
