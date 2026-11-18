"""Optional SDK transport, imported only for external inference (httpx2 SDKs).

No global HTTP client cache. Never forward credentials through proxies, redirects,
SDK options, exception text, response headers, or non-allowlisted destinations.
"""
import httpx2 as httpx


def _wire(request, store, key, token):
    provider, secret = store.credential(key, token)
    host, path, header = (("api.openai.com", "/v1/chat/completions", "authorization") if provider == "openai"
                         else ("api.anthropic.com", "/v1/messages", "x-api-key"))
    if (request.method != "POST" or request.url.scheme != "https" or request.url.host != host
            or request.url.port not in (None, 443) or request.url.path != path
            or request.url.query or request.url.username or request.url.password):
        raise RuntimeError("External provider destination rejected")
    request.headers.pop("authorization", None)
    request.headers.pop("x-api-key", None)
    request.headers[header] = ("Bearer " if provider == "openai" else "") + secret.decode("ascii")
    return secret


def _response(response, secret):
    content = (response.content.replace(secret, b"[REDACTED]") if response.is_success
               else b'{"error":{"type":"external_provider_error","message":"External provider request failed"}}')
    return httpx.Response(response.status_code, content=content, headers={"content-type": "application/json"})


class Transport(httpx.BaseTransport):
    def __init__(self, store, key, token):
        self.scope = store, key, token
        self.inner = httpx.HTTPTransport(retries=0, trust_env=False)

    def handle_request(self, request):
        try:
            secret = _wire(request, *self.scope)
            response = self.inner.handle_request(request)
            try:
                response.read()
                return _response(response, secret)
            finally:
                response.close()
        except Exception:
            raise RuntimeError("External provider request failed or session expired") from None
        finally:
            request.headers.pop("authorization", None)
            request.headers.pop("x-api-key", None)

    def close(self):
        self.inner.close()


class AsyncTransport(httpx.AsyncBaseTransport):
    def __init__(self, store, key, token):
        self.scope = store, key, token
        self.inner = httpx.AsyncHTTPTransport(retries=0, trust_env=False)

    async def handle_async_request(self, request):
        try:
            secret = _wire(request, *self.scope)
            response = await self.inner.handle_async_request(request)
            try:
                await response.aread()
                return _response(response, secret)
            finally:
                await response.aclose()
        except Exception:
            raise RuntimeError("External provider request failed or session expired") from None
        finally:
            request.headers.pop("authorization", None)
            request.headers.pop("x-api-key", None)

    async def aclose(self):
        await self.inner.aclose()