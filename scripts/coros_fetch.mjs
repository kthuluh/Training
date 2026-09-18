/**
 * Coros → coros_data.json
 * ------------------------
 * Lee tus métricas de EvoLab (FC reposo, HRV, carga de entrenamiento) desde la
 * API privada de COROS Training Hub usando la librería no oficial
 * `@pinta365/coros` (https://github.com/Pinta365/coros) y escribe
 * `coros_data.json` en la raíz del repo.
 *
 * Eso es lo que consumen `scripts/daily_brief.py` (correo) y
 * `scripts/update_dashboard.py` (dashboard).
 *
 * ⚠️  Es una API PRIVADA y NO OFICIAL: puede romperse cuando Coros cambie su
 * web, y requiere guardar tu email + contraseña de Coros como secretos de
 * GitHub. Por eso el workflow ejecuta este paso con `continue-on-error: true`:
 * si un día falla, el correo y el dashboard salen igual, solo que sin datos
 * de Coros.
 *
 * ── Requisitos ─────────────────────────────────────────────────────────────
 *   npm install                      (una sola vez; ver README, paso 4)
 *   node  >= 22.6  (usa --experimental-strip-types, ver abajo)
 *   — o —  deno run -A scripts/coros_fetch.mjs
 *
 * El `--experimental-strip-types` hace falta porque @pinta365/coros se publica
 * como TypeScript puro: Node 22.6–23.5 necesita el flag para importar `.ts`
 * sin paso de compilación (Node 23.6+ y 24 no lo necesitan, pero el flag sigue
 * siendo válido). Deno no lo necesita y puede importar `jsr:` directamente;
 * este script detecta el runtime y usa un especificador u otro.
 *
 * ── Variables de entorno ───────────────────────────────────────────────────
 *   COROS_EMAIL       obligatorio  (si falta, sale 0 sin escribir nada)
 *   COROS_PASSWORD    obligatorio
 *   COROS_REGION      "eu" | "en" | "cn"   (default: eu — España va en eu)
 *   COROS_DAYS        nº de días de historia a pedir   (default: 120)
 *   COROS_OUT         ruta de salida                   (default: ./coros_data.json)
 *   COROS_TOKEN_FILE  caché del token de sesión (opcional, recomendado si
 *                     corres esto a mano: Coros invalida tokens y limita
 *                     peticiones si haces login demasiado seguido)
 *   COROS_DEBUG       "1" → imprime las claves reales que devolvió la API
 *   COROS_FIXTURE     path a un JSON con la respuesta cruda de getAnalyse()
 *                     para probar el pipeline sin red ni credenciales
 *
 * ── Qué datos hay y cuáles no ──────────────────────────────────────────────
 *   getAnalyse() (`analyse/query`) devuelve por día: rhr (FC reposo), pasos
 *   (si la respuesta trae alguna clave tipo `steps`/`stepCount`),
 *   avgSleepHrv / sleepHrvBase (HRV nocturna y su baseline), t7d (carga
 *   corto plazo), t28d (Base Fitness), trainingLoad, trainingLoadRatio,
 *   tiredRate (fatiga), performance, distance, duration.
 *
 *   NO devuelve horas de sueño: esta librería (v0.0.1) no tiene endpoint de
 *   sueño. Este script AUN ASÍ intenta encontrarlo: mira si el día trae alguna
 *   clave suelta tipo `sleepDuration`/`totalSleep`/… (la respuesta de Coros
 *   trae más campos de los que la librería tipa) y, si la encuentra, la usa.
 *   Si no la hay, `sleep_hours` queda `null` y el correo/dashboard lo marcan
 *   como manual. Usa COROS_DEBUG=1 una vez para ver los nombres reales.
 *
 * ── Las fases del sueño NO se piden, a propósito ───────────────────────────
 *   Ligero / profundo / REM solo salen por la API **móvil** de Coros, que se
 *   autentica con claves sacadas del APK de la app. Dos problemas, y el segundo
 *   es el que manda:
 *
 *     1. Esas claves no son públicas y cambian con cada versión de la app, así
 *        que un cron de GitHub las rompería en cuanto Coros actualizara.
 *     2. El login móvil **invalida la sesión del teléfono**: ejecutarlo cada
 *        madrugada desloguearía el reloj del móvil del usuario.
 *
 *   Por eso el contrato lo deja explícito en vez de callarse: el payload sale
 *   con `available.sleep_phases = false` y ningún día trae fases. Quien pinta
 *   (dashboard, correo) lo dice en vez de insinuar un dato que no existe. Con
 *   COROS_DEBUG=1 se imprimen las claves con pinta de sueño que sí llegaron,
 *   para poder confirmar qué expone tu cuenta en concreto.
 */

const DEBUG = ["1", "true", "yes"].includes((process.env.COROS_DEBUG ?? "").toLowerCase());

// --------------------------------------------------------------------------
// utilidades
// --------------------------------------------------------------------------

/** Número "limpio": null/undefined/NaN/0-feos → null. */
function num(v) {
    if (v === null || v === undefined || v === "") return null;
    const n = typeof v === "number" ? v : Number(v);
    return Number.isFinite(n) ? n : null;
}

/** 20260914 → "2026-09-14" (fecha de calendario, sin zona horaria). */
function ymdToISO(ymd) {
    const s = String(ymd);
    if (!/^\d{8}$/.test(s)) return null;
    return `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`;
}

/** Date → "YYYY-MM-DD" en hora LOCAL (Coros agrupa por día local). */
function dateToISO(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function isoToYMD(iso) {
    return Number(iso.replaceAll("-", ""));
}

/** Coros manda duración en s, min o h según el campo: normaliza a horas. */
function toHours(v) {
    const n = num(v);
    if (n === null || n <= 0) return null;
    const h = n >= 1000 ? n / 3600 : n > 24 ? n / 60 : n;
    return h >= 1 && h <= 16 ? Math.round(h * 100) / 100 : null;
}

/** Primera clave que exista (y dé un valor plausible) de la lista. */
function pick(obj, keys, conv = num) {
    if (!obj) return null;
    for (const k of keys) {
        if (Object.prototype.hasOwnProperty.call(obj, k)) {
            const v = conv(obj[k]);
            if (v !== null) return v;
        }
    }
    return null;
}

const SLEEP_KEYS = [
    "sleepDuration",
    "totalSleep",
    "totalSleepDuration",
    "sleepTime",
    "sleepLength",
    "asleepDuration",
    "dailySleepDuration",
];
const STEPS_KEYS = ["steps", "stepCount", "dailySteps", "totalSteps", "step"];
const HRV_KEYS = ["avgSleepHrv", "sleepHrv", "hrvAvg", "hrv"];
const HRV_BASE_KEYS = ["sleepHrvBase", "hrvBase", "hrvBaseline"];
const RHR_KEYS = ["rhr", "restingHeartRate", "restingHR"];

/** Horas de sueño: por clave y, si no, sumando una lista de tramos. */
function sleepHoursOf(day) {
    const direct = pick(day, SLEEP_KEYS, toHours);
    if (direct !== null) return direct;
    const list = day?.sleepIntervalList ?? day?.sleepSegmentList ?? day?.sleepTimeList;
    if (Array.isArray(list) && list.length > 0) {
        const total = list.reduce((acc, seg) => {
            if (typeof seg === "number") return acc + seg;
            const a = num(seg?.duration ?? seg?.totalTime ?? seg?.value);
            return acc + (a ?? 0);
        }, 0);
        return toHours(total);
    }
    return null;
}

/**
 * Pasos del día: la clave puede llamarse de varias formas y a veces viene en
 * miles (10.5 = 10.500 pasos). Devuelve un entero plausible o null.
 */
function stepsOf(day) {
    const raw = pick(day, STEPS_KEYS);
    if (raw === null) return null;
    let n = raw;
    if (n > 0 && n < 1000) n = n * 1000; // 10.5 → 10.500
    n = Math.round(n);
    return n >= 100 && n <= 100000 ? n : null;
}

function mean(values) {
    const v = values.filter((x) => x !== null && x !== undefined);
    if (v.length === 0) return null;
    return Math.round((v.reduce((a, b) => a + b, 0) / v.length) * 100) / 100;
}

function mondayOf(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    const dt = new Date(y, m - 1, d);
    dt.setDate(dt.getDate() - ((dt.getDay() + 6) % 7)); // lunes
    return dateToISO(dt);
}

// --------------------------------------------------------------------------
// carga de la librería (Deno usa jsr:, Node usa el alias de npm)
// --------------------------------------------------------------------------
async function loadCorosClient() {
    const isDeno = typeof globalThis.Deno !== "undefined";
    const specifiers = isDeno ? ["jsr:@pinta365/coros", "@pinta365/coros"] : ["@pinta365/coros", "jsr:@pinta365/coros"];
    let lastErr;
    for (const spec of specifiers) {
        try {
            const mod = await import(spec);
            if (!mod.CorosClient) throw new Error(`el módulo "${spec}" no exporta CorosClient`);
            return mod;
        } catch (err) {
            lastErr = err;
        }
    }
    throw new Error(
        `No se pudo importar @pinta365/coros (${lastErr?.message ?? lastErr}).\n` +
            `   Node: npm install   (npx jsr add @pinta365/coros la primera vez)\n` +
            `   Deno: no hace falta instalar nada — deno run -A scripts/coros_fetch.mjs`,
    );
}

// --------------------------------------------------------------------------
// main
// --------------------------------------------------------------------------
async function main() {
    // Raíz del repo, resuelta de forma portable (en Windows import.meta.url
    // da /C:/... y hay que pasar por fileURLToPath).
    const { fileURLToPath } = await import("node:url");
    const { dirname, join } = await import("node:path");
    const root = dirname(dirname(fileURLToPath(import.meta.url)));
    const outPath = process.env.COROS_OUT ?? join(root, "coros_data.json");
    const days = Math.max(7, Number(process.env.COROS_DAYS ?? 120) || 120);
    const region = (process.env.COROS_REGION ?? "eu").toLowerCase();
    const email = process.env.COROS_EMAIL?.trim();
    const password = process.env.COROS_PASSWORD;
    const tokenFile = process.env.COROS_TOKEN_FILE;
    const fixture = process.env.COROS_FIXTURE;

    if (!email || !password) {
        console.log("· Sin COROS_EMAIL / COROS_PASSWORD → omito Coros (el correo y el dashboard salen solo con Strava).");
        return 0;
    }

    const { writeFile, readFile } = await import("node:fs/promises");

    // 1) Respuesta cruda de EvoLab: red real o fixture local.
    let analyse = null;
    let account = null;

    if (fixture) {
        console.log(`· Usando fixture local: ${fixture}`);
        analyse = JSON.parse(await readFile(fixture, "utf8"));
    } else {
        const { CorosClient, ApiError, HttpError } = await loadCorosClient();

        const client = new CorosClient({ email, password }, { region });

        // Token cacheado → evita un login por ejecución (Coros rate-limea y
        // loguearte desde otro sitio invalida tokens viejos).
        let usingCachedToken = false;
        if (tokenFile) {
            try {
                client.setAccessToken((await readFile(tokenFile, "utf8")).trim());
                usingCachedToken = true;
            } catch {
                /* sin caché aún */
            }
        }

        const fetchAnalyse = async () => {
            const endDate = new Date();
            const startDate = new Date();
            startDate.setDate(startDate.getDate() - (days - 1));
            // Mediodía local: evita que el YYYYMMDD de la librería se desplace un día.
            endDate.setHours(12, 0, 0, 0);
            startDate.setHours(12, 0, 0, 0);
            return await client.getAnalyse({ startDate, endDate });
        };

        try {
            if (!usingCachedToken) await client.login();
            analyse = await fetchAnalyse();
            account = await client.getAccount().catch(() => null);
        } catch (err) {
            if (!usingCachedToken) {
                const why = err instanceof HttpError ? `HTTP ${err.status}` : err?.result ?? "?";
                throw new Error(
                    `Coros falló (${why}). Si es 401/credenciales → revisa COROS_EMAIL/COROS_PASSWORD y el region ` +
                        `(el de tu cuenta, para España "eu"). Si es 429 → espera y vuelve a lanzar el workflow.`,
                );
            }
            console.log(`· Token cacheado inválido/anticuado (${err?.message}); hago login y reintento…`);
            await client.login();
            if (tokenFile) await writeFile(tokenFile, client.getAccessToken() ?? "", "utf8");
            analyse = await fetchAnalyse();
            account = await client.getAccount().catch(() => null);
        }

        if (tokenFile && client.getAccessToken()) {
            await writeFile(tokenFile, client.getAccessToken(), "utf8").catch(() => {});
        }
    }

    const rawDays = Array.isArray(analyse?.dayList) ? analyse.dayList : [];
    if (DEBUG && rawDays.length > 0) {
        console.log("· Claves reales de un día de analyse/query:");
        console.log("  " + Object.keys(rawDays[0]).sort().join(", "));
        // Lo que de verdad importa para el bloque de sueño: qué claves tienen
        // pinta de sueño y si sleepHoursOf() saca algo de la primera.
        const conPinta = Object.keys(rawDays[0]).filter((k) => /sleep|nap|bed|rest/i.test(k)).sort();
        console.log(
            conPinta.length
                ? `· Claves con pinta de sueño: ${conPinta.join(", ")}`
                : "· Ninguna clave con pinta de sueño (sleep/nap/bed/rest) en la respuesta.",
        );
        const horasPrimero = sleepHoursOf(rawDays[0]);
        console.log(
            `· sleepHoursOf() del primer día: ${horasPrimero === null ? "null → sleep_hours queda manual" : `${horasPrimero} h`}`,
        );
        console.log(
            "· Fases (ligero/profundo/REM): no se piden — exigen la API móvil con claves del APK " +
                "y ese login desloguea el reloj del móvil. Fuera del repo a propósito.",
        );
    }

    // 2) Índice por fecha (Coros omite días sin datos) y calendario completo.
    const byDate = new Map();
    for (const d of rawDays) {
        const iso = d?.happenDay ? ymdToISO(d.happenDay) : d?.timestamp ? dateToISO(new Date(Number(d.timestamp) * 1000)) : null;
        if (iso) byDate.set(iso, d);
    }

    const series = [];
    const today = new Date();
    today.setHours(12, 0, 0, 0);
    for (let i = days - 1; i >= 0; i--) {
        const dt = new Date(today);
        dt.setDate(dt.getDate() - i);
        const iso = dateToISO(dt);
        const src = byDate.get(iso);
        const durationS = num(src?.duration);
        series.push({
            date: iso,
            has_data: src !== undefined,
            resting_hr: pick(src, RHR_KEYS),
            hrv: pick(src, HRV_KEYS),
            hrv_base: pick(src, HRV_BASE_KEYS),
            sleep_hours: src ? sleepHoursOf(src) : null,
            steps: src ? stepsOf(src) : null,
            load_short: num(src?.t7d), // 7 días: "Load Impact"
            load_long: num(src?.t28d), // 28 días: "Base Fitness"
            training_load: num(src?.trainingLoad ?? src?.tib),
            load_ratio: num(src?.trainingLoadRatio),
            fatigue: num(src?.tiredRateNew ?? src?.tiredRate),
            performance: (() => {
                const p = num(src?.performance);
                return p !== null && p >= 0 ? p : null; // -1 = sin estimación
            })(),
            distance_km: (() => {
                const m = num(src?.distance);
                return m === null ? null : Math.round((m / 1000) * 10) / 10;
            })(),
            duration_min: durationS === null ? null : Math.round(durationS / 60),
        });
    }

    // 3) "latest" = el día más reciente que traiga FC reposo o HRV.
    const withData = series.filter((d) => d.has_data && (d.resting_hr !== null || d.hrv !== null || d.sleep_hours !== null));
    const latest = withData.length > 0 ? withData[withData.length - 1] : null;
    const window14 = series.slice(-14);

    // 4) Series semanales (lunes–domingo) para el gráfico de tendencia.
    const weeksMap = new Map();
    for (const d of series) {
        if (!d.has_data) continue;
        const wk = mondayOf(d.date);
        const bucket = weeksMap.get(wk) ?? { week_start: wk, resting_hr: [], hrv: [], sleep_hours: [], load_ratio: [], distance_km: 0, n_days: 0 };
        bucket.resting_hr.push(d.resting_hr);
        bucket.hrv.push(d.hrv);
        bucket.sleep_hours.push(d.sleep_hours);
        bucket.load_ratio.push(d.load_ratio);
        bucket.distance_km += d.distance_km ?? 0;
        bucket.n_days += 1;
        weeksMap.set(wk, bucket);
    }
    const weeks = [...weeksMap.values()]
        .sort((a, b) => a.week_start.localeCompare(b.week_start))
        .map((w) => ({
            week_start: w.week_start,
            resting_hr: mean(w.resting_hr),
            hrv: mean(w.hrv),
            sleep_hours: mean(w.sleep_hours),
            load_ratio: mean(w.load_ratio),
            distance_km: Math.round(w.distance_km * 10) / 10,
            n_days: w.n_days,
        }));

    const available = {
        resting_hr: series.some((d) => d.resting_hr !== null),
        hrv: series.some((d) => d.hrv !== null),
        sleep_hours: series.some((d) => d.sleep_hours !== null),
        steps: series.some((d) => d.steps !== null),
        load_ratio: series.some((d) => d.load_ratio !== null),
        // Siempre false y a propósito: las fases (ligero/profundo/REM) solo salen
        // por la API móvil, que pide claves del APK y desloguea el reloj del
        // móvil. Se declara explícitamente para que quien pinta pueda distinguir
        // "no se pide" de "se nos olvidó". Detalle en la cabecera del fichero.
        sleep_phases: false,
    };
    const warnings = [];
    if (!available.sleep_hours) {
        warnings.push(
            "sleep_hours no disponible: @pinta365/coros 0.0.1 no expone el endpoint de sueño (sólo HRV nocturna). " +
                'Si tu respuesta de Coros trae horas de sueño, ejecuta con COROS_DEBUG=1, mira la clave y añádela a SLEEP_KEYS en scripts/coros_fetch.mjs.',
        );
    }
    if (rawDays.length === 0) {
        warnings.push("analyse/query devolvió dayList vacío (¿cuenta con EvoLab activado? ¿region correcto?).");
    }

    // 5) Contrato: claves anidadas + claves planas legacy por compatibilidad.
    const payload = {
        schema_version: 1,
        generated_at: new Date().toISOString(),
        source: "@pinta365/coros (no oficial)",
        region,
        account: account ? { nickname: account.nickname ?? null, user_id: account.userId ?? null } : null,
        date: latest?.date ?? null,
        latest,
        latest_14: {
            resting_hr_avg: mean(window14.map((d) => d.resting_hr)),
            resting_hr_min: (() => {
                const v = window14.map((d) => d.resting_hr).filter((x) => x !== null);
                return v.length ? Math.min(...v) : null;
            })(),
            resting_hr_max: (() => {
                const v = window14.map((d) => d.resting_hr).filter((x) => x !== null);
                return v.length ? Math.max(...v) : null;
            })(),
            sleep_hours_avg: mean(window14.map((d) => d.sleep_hours)),
            steps_avg: mean(window14.map((d) => d.steps)),
            hrv_avg: mean(window14.map((d) => d.hrv)),
            n_days: window14.filter((d) => d.has_data).length,
        },
        days: series,
        weeks,
        available,
        warnings,
        // --- claves planas legacy (las que leía la versión anterior de daily_brief.py)
        resting_hr: latest?.resting_hr ?? null,
        sleep_hours: latest?.sleep_hours ?? null,
        hrv: latest?.hrv ?? null,
    };

    await writeFile(outPath, JSON.stringify(payload, null, 2) + "\n", "utf8");

    const fmt = (label, v, unit) => `${label}: ${v ?? "—"}${unit}`;
    console.log(`✓ ${outPath} escrito (${payload.date ?? "sin fecha"}, ${series.length} días, ${weeks.length} semanas)`);
    console.log(
        `  ${fmt("FC reposo", payload.latest?.resting_hr, " bpm")}  ·  ` +
            `${fmt("HRV", payload.latest?.hrv, " ms")}  ·  ` +
            `${fmt("sueño", payload.latest?.sleep_hours, " h")}  ·  ` +
            `${fmt("pasos", payload.latest_14?.steps_avg !== null ? Math.round(payload.latest_14?.steps_avg) : null, "")}  ·  ` +
            `${fmt("ratio carga", payload.latest?.load_ratio, "")}`,
    );
    for (const w of warnings) console.log(`  ⚠ ${w}`);
    return 0;
}

main()
    .then((code) => process.exit(code))
    .catch((err) => {
        console.error(`✗ Coros: ${err?.message ?? err}`);
        // Código 2: el workflow usa continue-on-error, así el correo sale igual.
        process.exit(2);
    });
