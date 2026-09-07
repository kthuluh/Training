"""
Ejecuta esto UNA VEZ en tu computadora (no en GitHub Actions) para obtener
tu STRAVA_REFRESH_TOKEN. Necesitas haber creado una app en
https://www.strava.com/settings/api primero (te da un Client ID y Client Secret).

Uso:
    pip install requests
    python get_strava_refresh_token.py

Sigue las instrucciones que aparecen en pantalla.
"""

import requests
import webbrowser

CLIENT_ID = input("Tu Strava Client ID: ").strip()
CLIENT_SECRET = input("Tu Strava Client Secret: ").strip()

auth_url = (
    "https://www.strava.com/oauth/authorize"
    f"?client_id={CLIENT_ID}"
    "&response_type=code"
    "&redirect_uri=http://localhost/exchange_token"
    "&approval_prompt=force"
    "&scope=activity:read_all"
)

print("\nSe va a abrir tu navegador. Autoriza la app.")
print("Después de autorizar, el navegador te llevará a una URL que empieza con")
print("http://localhost/exchange_token?state=&code=XXXXX&scope=...")
print("Copia el valor de 'code' de esa URL (todo lo que está entre 'code=' y '&scope').\n")
webbrowser.open(auth_url)

code = input("Pega aquí el 'code' que copiaste: ").strip()

resp = requests.post(
    "https://www.strava.com/oauth/token",
    data={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
    },
)
resp.raise_for_status()
data = resp.json()

print("\n¡Listo! Guarda esto como secretos de GitHub:")
print(f"STRAVA_CLIENT_ID = {CLIENT_ID}")
print(f"STRAVA_CLIENT_SECRET = {CLIENT_SECRET}")
print(f"STRAVA_REFRESH_TOKEN = {data['refresh_token']}")
