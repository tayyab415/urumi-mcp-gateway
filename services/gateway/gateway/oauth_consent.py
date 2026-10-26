"""The consent page: where a person logs in and says whether an app may act as them.

`GatewayOAuthProvider.authorize()` sends the browser here with `?request=<id>`.
The page is plain server-rendered HTML with a little inline CSS. Nothing else
loads on it, and the Content-Security-Policy forbids scripts.

Who is the person?
* If the browser holds a dashboard session (`urumi_session`), the page names
  that person and asks only for Allow or Deny.
* Otherwise it asks for email and password and checks them with the same rule
  as `POST /api/auth/login`. This does not start a dashboard session.
The person is decided again when the form is posted, never taken from the form.

The form is protected by a random token that sits in the form and in a cookie.
A page on another site can neither read the token nor set the cookie, so it
cannot post "Allow" for an authorization request it started.

Every dynamic value is HTML-escaped. That includes the app's name, which the
app chose itself and must be treated as text from a stranger.
"""

from __future__ import annotations

import hmac
from html import escape
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import BaseRoute, Route

from gateway import api
from gateway.config import Settings
from gateway.db import OAuthClient, OAuthRequest, User
from gateway.oauth import GatewayOAuthProvider
from gateway.security import sha256_hex

CSRF_COOKIE = "urumi_oauth_csrf"

STYLE = """
body { font: 16px/1.5 system-ui, sans-serif; background: #f4f4f5; color: #18181b; margin: 0; }
main { max-width: 30rem; margin: 8vh auto; padding: 2rem; background: #fff; border: 1px solid #d4d4d8; border-radius: 12px; }
h1 { font-size: 1.3rem; margin: 0 0 1rem; }
ul { padding-left: 1.2rem; }
label { display: block; margin: .8rem 0 .2rem; font-size: .9rem; }
input { width: 100%; box-sizing: border-box; padding: .5rem; font: inherit; border: 1px solid #a1a1aa; border-radius: 6px; }
button { font: inherit; padding: .55rem 1.2rem; border-radius: 6px; border: 1px solid #18181b; background: #18181b; color: #fff; cursor: pointer; margin: 1rem .5rem 0 0; }
button.deny { background: #fff; color: #18181b; }
.small { font-size: .85rem; color: #52525b; }
.error { color: #b91c1c; font-weight: 600; }
"""


def page(title: str, body: str, status: int = 200) -> HTMLResponse:
    html = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{escape(title)}</title><style>{STYLE}</style></head><body><main>{body}</main></body></html>")
    return HTMLResponse(html, status_code=status, headers={
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
        "X-Frame-Options": "DENY",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    })


def message(title: str, text: str, status: int) -> HTMLResponse:
    return page(title, f"<h1>{escape(title)}</h1><p>{escape(text)}</p>", status)


def expired() -> HTMLResponse:
    return message("This sign-in link has expired", "Go back to the app and start the connection again.", 400)


def origin_of(uri: str) -> str:
    """Where the browser goes after the decision, shown so the person can see it is their own computer or app."""
    parts = urlsplit(uri)
    return f"{parts.scheme}://{parts.netloc}" if parts.netloc else f"{parts.scheme}:"


def consent_form(*, req_id: str, csrf: str, client: OAuthClient, redirect_uri: str, user: User | None, error: str | None = None) -> str:
    """The page body. `user` is set when the browser has a dashboard session; otherwise the form asks for a password."""
    name = escape(client.name)
    fields = f'<input type="hidden" name="request" value="{escape(req_id)}"><input type="hidden" name="csrf" value="{escape(csrf)}">'
    if user is not None:
        who = (f'<p>Signed in as <strong>{escape(user.name or user.email)}</strong> ({escape(user.email)}), '
               f'role <strong>{escape(user.role)}</strong>. '
               f'<a href="/oauth/consent?request={escape(req_id)}&amp;login=1">Not you?</a></p>')
        fields += '<input type="hidden" name="mode" value="session">'
    else:
        who = "<p>Sign in with your Urumi account to continue.</p>"
        fields += ('<input type="hidden" name="mode" value="password">'
                   '<label for="email">Email</label><input id="email" name="email" type="email" autocomplete="username" required autofocus>'
                   '<label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password" required>')
    problem = f'<p class="error" role="alert">{escape(error)}</p>' if error else ""
    return (
        f"<h1>Connect {name} to Urumi?</h1>{problem}{who}"
        f"<p><strong>{name}</strong> wants to use the gateway's tools as you.</p>"
        "<ul>"
        "<li>It can call the tools your role allows, and nothing else. Your role's limits apply.</li>"
        "<li>Every call it makes is recorded under your name.</li>"
        "<li>You can disconnect it at any time on the Connect page.</li>"
        "</ul>"
        '<p class="small">The app chose its own name, so check that you started this from your own app. '
        f"After you decide, your browser goes to <strong>{escape(origin_of(redirect_uri))}</strong>.</p>"
        f'<form method="post" action="/oauth/consent">{fields}'
        '<button type="submit" name="decision" value="allow">Allow</button>'
        '<button type="submit" name="decision" value="deny" class="deny" formnovalidate>Deny</button></form>'
    )


def routes(provider: GatewayOAuthProvider, settings: Settings) -> list[BaseRoute]:
    db = provider.db

    async def show(raw_id: str, req: OAuthRequest, client: OAuthClient, user: User | None, error: str | None = None,
                   status: int = 200) -> Response:
        csrf = await provider.new_csrf(req.id)
        response = page("Connect an app to Urumi", consent_form(
            req_id=raw_id, csrf=csrf, client=client, redirect_uri=req.redirect_uri, user=user, error=error), status)
        response.set_cookie(CSRF_COOKIE, csrf, max_age=600, path="/oauth", httponly=True, secure=settings.cookie_secure, samesite="strict")
        return response

    async def session_person(request: Request) -> User | None:
        token = request.cookies.get(api.COOKIE)
        return await api.session_user(db, token) if token else None

    async def get(request: Request) -> Response:
        raw_id = request.query_params.get("request", "")
        loaded = await provider.load_request(raw_id)
        if loaded is None:
            return expired()
        req, client = loaded
        user = None if request.query_params.get("login") else await session_person(request)
        return await show(raw_id, req, client, user)

    async def post(request: Request) -> Response:
        form = await request.form()
        raw_id = str(form.get("request") or "")
        loaded = await provider.load_request(raw_id)
        if loaded is None:
            return expired()
        req, client = loaded

        sent = str(form.get("csrf") or "")
        cookie = request.cookies.get(CSRF_COOKIE, "")
        stored = req.csrf_hash or ""
        if not sent or not stored or not hmac.compare_digest(sha256_hex(sent), stored) or not hmac.compare_digest(sent.encode(), cookie.encode()):
            return message("This form is out of date", "Reload the page and try again.", 403)

        decision = form.get("decision")
        if decision == "deny":
            url = await provider.deny(req)
            return redirect(url) if url else expired()
        if decision != "allow":
            return message("Unknown choice", "Reload the page and try again.", 400)

        if form.get("mode") == "password":
            user = await api.check_password(db, str(form.get("email") or ""), str(form.get("password") or ""))
            if user is None:
                return await show(raw_id, req, client, None, "Wrong email or password.", 401)
        else:
            user = await session_person(request)
            if user is None:
                return await show(raw_id, req, client, None, "Your session ended. Sign in again.", 401)

        url = await provider.approve(req, user)
        return redirect(url) if url else expired()

    def redirect(url: str) -> Response:
        # 303: the browser fetches the app's callback with GET, whatever the form used.
        return RedirectResponse(url, status_code=303, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    return [Route("/oauth/consent", get, methods=["GET"]), Route("/oauth/consent", post, methods=["POST"])]
