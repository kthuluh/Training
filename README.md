# Correo diario de entrenamiento + dieta (Kthuluh)

Envía cada mañana, desde GitHub Actions (en la nube, no necesita tu compu
encendida), un correo con: resumen de ayer (Strava), el entrenamiento de
hoy según tu plan de 10K/21K, y la dieta del día.

## 0. Antes de empezar

Crea un **repositorio privado** en GitHub (importante: privado, porque
vas a guardar secretos ahí, aunque como GitHub Secrets, no en el código).
Sube estos archivos tal cual están.

## 1. Crear tu app de Strava

1. Ve a https://www.strava.com/settings/api y crea una app (cualquier
   nombre, cualquier "Authorization Callback Domain" tipo `localhost`).
2. Anota el **Client ID** y el **Client Secret**.
3. En tu computadora (no en GitHub), corre:
   ```
   pip install requests
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
secret**. Crea estos 6:

| Nombre | Valor |
|---|---|
| `STRAVA_CLIENT_ID` | del paso 1 |
| `STRAVA_CLIENT_SECRET` | del paso 1 |
| `STRAVA_REFRESH_TOKEN` | del paso 1 |
| `GMAIL_USER` | kthuluh@gmail.com |
| `GMAIL_APP_PASSWORD` | del paso 2 |
| `EMAIL_TO` | kthuluh@gmail.com |

## 4. (Opcional) Coros

Coros no tiene API pública para uso personal. `scripts/coros_fetch.mjs`
usa una librería de terceros no oficial que inicia sesión con tu email y
contraseña reales de Coros — puede romperse cuando Coros actualice su
web, y significa guardar tu contraseña de Coros como secreto de GitHub.

Si aceptas ese riesgo:
1. Revisa https://github.com/Pinta365/coros (o alguna alternativa
   más reciente) y completa las llamadas reales en
   `scripts/coros_fetch.mjs` — el script tiene comentarios de dónde va
   cada cosa.
2. Agrega los secretos `COROS_EMAIL` y `COROS_PASSWORD`.
3. En `.github/workflows/daily-brief.yml`, descomenta el bloque
   "Bloque opcional de Coros".

Si no, no hagas nada — el correo se manda igual, solo que sin la línea
de FC reposo/sueño/HRV.

## 5. Probarlo

En GitHub: pestaña **Actions** → "Daily training + diet brief" →
**Run workflow** → Run workflow. Revisa que llegue el correo. Si falla,
el log de Actions te dice exactamente en qué paso.

Una vez que funcione a mano, se quedará corriendo solo todos los días a
la hora que pusiste en el cron (`.github/workflows/daily-brief.yml`,
formato UTC).

## 6. Si algo cambia en tu plan

Todo el plan de entrenamiento (semana por semana) y la dieta están en
`scripts/daily_brief.py`, en las listas `WEEKS` y `DIET` al principio del
archivo — edítalas ahí directamente, sin tocar el resto del código.

## 7. Dashboard con auto-actualización

`dashboard/dashboard-kthuluh.html` es tu dashboard, con partes marcadas
en el código (comentarios `AUTO:...`) que el workflow **Update dashboard**
actualiza cada día automáticamente usando Strava: la fecha, la tarjeta de
"semana en curso" (km y sesiones), el mes actual en el gráfico mensual, y
el gráfico de tendencia semanal completo.

Lo que sale de Coros (FC reposo, sueño, HRV) queda marcado como
"(manual)" y no se toca solo — si quieres que también se actualice
solo, es cuando tendría sentido activar el bloque opcional de Coros
del paso 4.

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

