"""Estado OAuth cifrado en GitHub. Nunca imprime ni guarda tokens en claro."""
import base64
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from cryptography.fernet import Fernet, InvalidToken

PATH = '.github/coros-mcp-state.enc'


class StateError(RuntimeError):
    pass


class GitHubTokenStore:
    def __init__(self, key, github_token, repository, client_id, initial_refresh, issuer):
        if not all((key, github_token, repository, client_id, initial_refresh)):
            raise StateError('faltan COROS_MCP_STATE_KEY, GITHUB_TOKEN o credenciales MCP')
        try:
            self.fernet = Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise StateError('COROS_MCP_STATE_KEY no es una clave Fernet válida') from exc
        self.token = github_token
        self.url = f'https://api.github.com/repos/{repository}/contents/{PATH}'
        self.fingerprint = hashlib.sha256(
            json.dumps([client_id, initial_refresh, issuer], separators=(',', ':')).encode()
        ).hexdigest()
        self.sha = None

    def _request(self, method, url, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            'Authorization': f'Bearer {self.token}', 'Accept': 'application/vnd.github+json',
            'Content-Type': 'application/json', 'X-GitHub-Api-Version': '2022-11-28'})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and method == 'GET':
                return None
            raise StateError(f'GitHub no pudo {method} el estado cifrado (HTTP {exc.code})') from exc
        except (urllib.error.URLError, ValueError, OSError) as exc:
            raise StateError('No se pudo acceder al estado cifrado en GitHub') from exc

    def load(self, initial_refresh):
        result = self._request('GET', self.url + '?ref=main')
        if result is None:
            return initial_refresh
        self.sha = result.get('sha')
        try:
            ciphertext = base64.b64decode(result['content'])
            data = json.loads(self.fernet.decrypt(ciphertext))
            if data['fingerprint'] != self.fingerprint:
                return initial_refresh  # reautorización explícita: nuevos secretos
            refresh = data['refresh_token']
            if not isinstance(refresh, str) or not refresh:
                raise ValueError('empty')
            return refresh
        except (KeyError, ValueError, InvalidToken, TypeError) as exc:
            raise StateError('No se pudo descifrar el estado MCP; comprueba COROS_MCP_STATE_KEY') from exc

    def save(self, refresh):
        payload = json.dumps({'fingerprint': self.fingerprint, 'refresh_token': refresh}).encode()
        ciphertext = self.fernet.encrypt(payload)
        body = {'message': 'Actualizar estado cifrado de COROS MCP', 'branch': 'main',
                'content': base64.b64encode(ciphertext).decode()}
        if self.sha:
            body['sha'] = self.sha
        try:
            self._request('PUT', self.url, body)
        except StateError as exc:
            raise StateError('Falló el guardado del token renovado; puede ser necesario autorizar COROS de nuevo') from exc
