# Entrenamiento · prototipo Android independiente

Proyecto separado del dashboard de Training: app nativa Kotlin/Compose y API Python con Supabase. No modifica el dashboard existente. **Es un prototipo, no un producto listo para usuarios reales.** No despliega ni crea cuentas automáticamente.

## Incluye

- Registro e inicio de sesión con Supabase Auth; el primer inicio crea una solicitud pendiente. El administrador aprueba desde el SQL editor de Supabase. La API comprueba la sesión y la aprobación en cada petición.
- Perfil editable, objetivo opcional y conexión OAuth de Strava en el navegador (sin pedir la contraseña de Strava). Los tokens se guardan cifrados en la base de datos, con clave exclusiva del servidor. Puede desconectarse desde Perfil.
- Resumen y actividades de los últimos 365 días, carreras marcadas Race o inferidas por «Tast»/«Cursa», ritmo medio y actividades de ayer; datos obtenidos al iniciar sesión y al pulsar Actualizar. Solo Strava; no se inventan sueño, pasos u otras métricas de COROS.
- Pestañas móviles y botón explícito para enviar perfil y resumen de actividad a un proveedor de IA compatible con Chat Completions. El análisis es orientativo, no médico, y se genera bajo demanda; no se envía en segundo plano.

## Límites importantes

- La IA aún **no crea ni guarda un plan de entrenamiento diario**. Seguimiento muestra lo registrado ayer y orientación, pero no compara con un plan persistido. Dieta y hábitos son orientaciones generales, no un menú nutricional validado ni datos de sueño/pasos. El historial se limita a 365 días y 30 carreras visibles.
- La sesión no se conserva al cerrar la app; hay que volver a iniciar sesión. No hay ejecución diaria en segundo plano, notificaciones, recuperación de contraseña en la app, panel administrativo propio, exportación o borrado completo de la cuenta. La IA no se recalcula automáticamente, para no compartir datos ni generar costes sin acción del usuario.
- Falta verificación real de extremo a extremo, pruebas en Android, gestión robusta de errores de autorización, límites de uso, políticas de privacidad, cumplimiento legal y revisión de seguridad. La renovación de tokens Strava aún no está coordinada entre peticiones simultáneas/instancias; no usar en producción hasta resolverlo. No se debe almacenar información médica sensible en este prototipo.
- Antes de conectar una IA, comprobar que el tratamiento de datos cumple los términos vigentes de Strava y de tu proveedor, así como la legislación aplicable. Elegir una configuración que no utilice los datos de usuarios para entrenar modelos si así se requiere, e informar y obtener el consentimiento pertinente. Puede haber costes y límites tanto en el servidor como en los servicios externos.

## Configuración del servidor

1. Crea un proyecto Supabase, activa el registro por correo y la confirmación del correo en Auth. Ejecuta `backend/schema.sql` en el SQL editor. La clave `service_role` es secreta: jamás va en Android. Las tablas tienen RLS sin políticas para clientes; solo el servidor usa la clave de servicio.
2. Despliega `backend` con Python 3.11+ en un servicio HTTPS. Instala `pip install -r requirements.txt`. Configura las variables de `.env.example` en el proveedor; no subas secretos al repositorio. Crea `TOKEN_ENCRYPTION_KEY` una sola vez mediante `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` y guárdala de forma segura: cambiarla invalida los tokens guardados.
3. Registra una aplicación en Strava. Configura su dominio de autorización con el dominio público de tu API y `STRAVA_REDIRECT_URI` como `https://TU_DOMINIO/strava/callback`. Configura identificador y secreto solo en el servidor.
4. Configura `AI_API_URL` (URL completa de chat completions), `AI_MODEL` y `AI_API_KEY` en el servidor. El modelo debe devolver JSON puro con las claves solicitadas. Para pruebas usa un proveedor y una cuenta adecuados; no se incluye ninguna clave.
5. Arranca con `uvicorn main:app --host 0.0.0.0 --port 8000`. Supabase y Strava requieren conexión exterior y el acceso desde Android debe ser por HTTPS. Protege y monitoriza el servicio; añade límites de frecuencia antes de abrirlo públicamente.
6. Tras el registro confirmado, el usuario inicia sesión una vez para generar su perfil pendiente. Como administrador busca su UUID en Supabase Authentication y apruébalo en SQL editor: `update public.athlete_profiles set approved = true where user_id = 'UUID';`. Para revocar acceso cambia `true` por `false`.

## Android Studio

Abre la carpeta `android` como proyecto, instala Android SDK 35 y JDK 17 y sincroniza Gradle. Añade a `android/local.properties` (archivo local, no compartir):

```properties
API_URL=https://TU_BACKEND
SUPABASE_URL=https://TU_PROYECTO.supabase.co
SUPABASE_ANON_KEY=tu_clave_publica_anon
```

La clave anon es pública por diseño y se incluye en el APK. Nunca pongas la clave `service_role`, secretos de Strava, clave de cifrado ni clave de IA en la app. Ejecuta en un dispositivo o emulador con internet. Tras autorizar Strava en el navegador, vuelve a la aplicación y pulsa **Actualizar datos**. Si la cuenta aún no está aprobada, pulsa **Comprobar** tras la aprobación.

No se incluye Gradle Wrapper binario; Android Studio puede importar el proyecto usando una instalación Gradle compatible y generar el wrapper si se desea. No se ha generado ni validado un APK en este entorno.
