package com.example.entrenamiento

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

private fun api(url: String, method: String = "GET", body: JSONObject? = null, headers: Map<String, String> = emptyMap()): JSONObject {
    require(url.startsWith("https://")) { "Configura una dirección HTTPS" }
    val conn = URL(url).openConnection() as HttpURLConnection
    conn.requestMethod = method
    conn.connectTimeout = 20000
    conn.readTimeout = 30000
    conn.setRequestProperty("Content-Type", "application/json")
    headers.forEach { (k, v) -> conn.setRequestProperty(k, v) }
    if (body != null) { conn.doOutput = true; conn.outputStream.use { it.write(body.toString().toByteArray()) } }
    try {
        val status = conn.responseCode
        val text = (if (status in 200..299) conn.inputStream else conn.errorStream)?.bufferedReader()?.use { it.readText() } ?: "{}"
        val obj = try { JSONObject(text) } catch (_: Exception) { JSONObject() }
        if (status !in 200..299) throw IllegalStateException(obj.optString("detail", obj.optString("msg", "Error HTTP $status")))
        return obj
    } finally { conn.disconnect() }
}

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { MaterialTheme { App { url -> startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) } } }
    }
}

@Composable
fun App(openBrowser: (String) -> Unit) {
    val scope = rememberCoroutineScope()
    var email by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var token by remember { mutableStateOf("") }
    var approval by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var tab by remember { mutableIntStateOf(0) }
    var dashboard by remember { mutableStateOf<JSONObject?>(null) }
    var sections by remember { mutableStateOf<JSONObject?>(null) }
    var profileLoaded by remember { mutableStateOf(false) }
    var name by remember { mutableStateOf("") }
    var birth by remember { mutableStateOf("") }
    var goal by remember { mutableStateOf("") }
    var goalDate by remember { mutableStateOf("") }
    var experience by remember { mutableStateOf("") }
    var days by remember { mutableStateOf("") }
    var constraints by remember { mutableStateOf("") }
    val tabs = listOf("Resumen", "Entrenamiento", "Dieta", "Hábitos", "Historial", "Recomendaciones", "Seguimiento", "Perfil")
    fun call(method: String, path: String, body: JSONObject? = null): JSONObject = api(BuildConfig.API_URL.trimEnd('/') + path, method, body, mapOf("Authorization" to "Bearer $token"))
    fun launchJob(block: () -> Unit) {
        if (busy) return
        scope.launch { busy = true; message = ""; try { withContext(Dispatchers.IO) { block() } } catch (e: Exception) { message = e.message ?: "Error de conexión" }; busy = false }
    }
    fun loadDashboard() = launchJob { dashboard = call("GET", "/dashboard?local_day=" + java.time.LocalDate.now()); sections = null }
    Scaffold(topBar = { TopAppBarSimple() }) { padding ->
        Column(Modifier.padding(padding).fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            if (message.isNotBlank()) Text(message, color = MaterialTheme.colorScheme.error)
            if (busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            if (token.isEmpty()) {
                Text("Tu entrenamiento, en un solo lugar", style = MaterialTheme.typography.headlineSmall)
                Text("Conecta Strava y consulta tu actividad. El registro requiere aprobación del administrador.")
                OutlinedTextField(email, { email = it }, label = { Text("Correo") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(password, { password = it }, label = { Text("Contraseña") }, singleLine = true, visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(enabled = !busy, onClick = { launchJob {
                        val data = api(BuildConfig.SUPABASE_URL.trimEnd('/') + "/auth/v1/token?grant_type=password", "POST", JSONObject().put("email", email).put("password", password), mapOf("apikey" to BuildConfig.SUPABASE_ANON_KEY))
                        token = data.getString("access_token"); password = ""
                        approval = call("GET", "/me").optBoolean("approved")
                        if (approval) { try { dashboard = call("GET", "/dashboard?local_day=" + java.time.LocalDate.now()) } catch (_: Exception) { dashboard = null } }
                    } }) { Text("Entrar") }
                    OutlinedButton(enabled = !busy, onClick = { launchJob {
                        api(BuildConfig.SUPABASE_URL.trimEnd('/') + "/auth/v1/signup", "POST", JSONObject().put("email", email).put("password", password), mapOf("apikey" to BuildConfig.SUPABASE_ANON_KEY))
                        password = ""; message = "Revisa tu correo para confirmar la cuenta. Después inicia sesión para solicitar aprobación."
                    } }) { Text("Registrarse") }
                }
            } else if (!approval) {
                Text("Cuenta pendiente de aprobación", style = MaterialTheme.typography.headlineSmall)
                Text("Pide al administrador que apruebe tu cuenta y vuelve a comprobar su estado.")
                Button(enabled = !busy, onClick = { launchJob { approval = call("GET", "/me").optBoolean("approved") } }) { Text("Comprobar") }
                OutlinedButton(onClick = { token = ""; approval = false }) { Text("Salir") }
            } else {
                ScrollableTabRow(selectedTabIndex = tab) { tabs.forEachIndexed { index, title -> Tab(selected = tab == index, onClick = { tab = index }, text = { Text(title) }) } }
                if (tab == 7) {
                    Text("Perfil y objetivo", style = MaterialTheme.typography.headlineSmall)
                    if (!profileLoaded) Button(enabled = !busy, onClick = { launchJob {
                        val p = call("GET", "/profile"); name = p.optString("display_name", ""); birth = p.optString("birth_year", "").let { if (it == "null") "" else it }; goal = p.optString("goal", ""); goalDate = p.optString("goal_date", "").let { if (it == "null") "" else it }; experience = p.optString("experience", ""); days = p.optString("available_days", "").let { if (it == "null") "" else it }; constraints = p.optString("constraints_text", ""); profileLoaded = true
                    } }) { Text("Cargar perfil") }
                    OutlinedTextField(name, { name = it }, label = { Text("Nombre") }, modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(birth, { birth = it }, label = { Text("Año de nacimiento (opcional)") }, modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(goal, { goal = it }, label = { Text("Objetivo (opcional)") }, modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(goalDate, { goalDate = it }, label = { Text("Fecha objetivo AAAA-MM-DD (opcional)") }, modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(experience, { experience = it }, label = { Text("Experiencia entrenando") }, modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(days, { days = it }, label = { Text("Días disponibles por semana (1–7)") }, modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(constraints, { constraints = it }, label = { Text("Preferencias o limitaciones (opcional)") }, modifier = Modifier.fillMaxWidth())
                    Button(enabled = !busy && profileLoaded, onClick = { launchJob {
                        val b = JSONObject().put("display_name", name).put("birth_year", if (birth.isBlank()) JSONObject.NULL else birth.toInt()).put("goal", goal).put("goal_date", if (goalDate.isBlank()) JSONObject.NULL else goalDate).put("experience", experience).put("available_days", if (days.isBlank()) JSONObject.NULL else days.toInt()).put("constraints_text", constraints)
                        call("PUT", "/profile", b); sections = null; message = "Perfil guardado"
                    } }) { Text("Guardar perfil") }
                    OutlinedButton(enabled = !busy, onClick = { launchJob { call("DELETE", "/strava/connect"); dashboard = null; sections = null; message = "Strava desconectado" } }) { Text("Desconectar Strava") }
                    OutlinedButton(onClick = { token = ""; approval = false; dashboard = null; sections = null; profileLoaded = false; name = ""; birth = ""; goal = ""; goalDate = ""; experience = ""; days = ""; constraints = "" }) { Text("Cerrar sesión") }
                } else {
                    if (dashboard == null) {
                        Text("Conecta tu cuenta de Strava y actualiza para empezar.")
                        Button(enabled = !busy, onClick = { launchJob { val url = call("POST", "/strava/connect").getString("url"); withContextMain { openBrowser(url) } } }) { Text("Conectar Strava") }
                        OutlinedButton(enabled = !busy, onClick = { loadDashboard() }) { Text("Actualizar datos") }
                    } else {
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedButton(enabled = !busy, onClick = { loadDashboard() }) { Text("Actualizar") }
                            OutlinedButton(enabled = !busy, onClick = { launchJob { sections = call("POST", "/analysis?local_day=" + java.time.LocalDate.now()).getJSONObject("sections") } }) { Text("Analizar con IA") }
                        }
                        Text("Al pulsar Analizar con IA, tu perfil y un resumen de tus actividades se envían al proveedor de IA configurado. No es asesoramiento médico.", style = MaterialTheme.typography.bodySmall)
                        when (tab) {
                            0 -> { Text("Últimos 7 días", style = MaterialTheme.typography.headlineSmall); Text("${dashboard!!.optDouble("week_km")} km · ${dashboard!!.optInt("week_runs")} carreras"); Text(dashboard!!.optString("note")); Text("Actualizado: ${dashboard!!.optString("generated_at")}") }
                            4 -> ActivityList(dashboard!!.optJSONArray("races"), "Carreras identificadas")
                            else -> {
                                val key = mapOf(1 to "entrenamiento", 2 to "dieta", 3 to "habitos", 5 to "recomendaciones", 6 to "seguimiento")[tab]!!
                                Text(tabs[tab], style = MaterialTheme.typography.headlineSmall)
                                if (tab == 6) ActivityList(dashboard!!.optJSONArray("yesterday"), "Actividades registradas ayer")
                                if (tab == 1) ActivityList(dashboard!!.optJSONArray("recent"), "Actividades recientes")
                                Text(sections?.optString(key)?.takeIf { it.isNotBlank() } ?: "Pulsa Analizar con IA para generar orientaciones con tus datos actuales.")
                            }
                        }
                    }
                }
            }
        }
    }
}

private fun withContextMain(action: () -> Unit) { android.os.Handler(android.os.Looper.getMainLooper()).post(action) }

@Composable fun TopAppBarSimple() { Surface(color = MaterialTheme.colorScheme.primaryContainer) { Text("Entrenamiento · Strava", Modifier.fillMaxWidth().padding(16.dp), style = MaterialTheme.typography.titleLarge) } }
@Composable fun ActivityList(items: JSONArray?, title: String) {
    Text(title, style = MaterialTheme.typography.titleMedium)
    if (items == null || items.length() == 0) Text("No hay registros disponibles.") else for (i in 0 until items.length()) {
        val a = items.getJSONObject(i)
        val pace = if (a.isNull("pace_sec_km")) "" else { val s = a.getInt("pace_sec_km"); " · ${s / 60}:${(s % 60).toString().padStart(2, '0')} min/km" }
        Text("${a.optString("date")} · ${a.optString("name")} · ${a.optDouble("km")} km · ${a.optInt("minutes")} min$pace" + if (a.optBoolean("inferred")) " · inferida por nombre" else "")
    }
}
