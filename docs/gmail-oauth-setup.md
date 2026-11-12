# Gmail OAuth setup

To connect a real Gmail account the app needs an OAuth client in Google Cloud,
plus a couple of settings. Everything else (encrypted token storage, CSRF
`state` check, callback redirect) is handled by the app.

## Cloud console (one-time, by hand)

1. Open a Google Cloud project. Use one you own. The project's name appears on
   the consent screen (e.g. "catholic readings and choir resources app"), so a
   dedicated "Jalora" project avoids embarrassing mismatches.
2. **APIs & Services → Library** → enable the **Gmail API**.
3. **APIs & Services → OAuth consent screen**:
   - User type: **External**, app name/logo as you like.
   - Status **Testing** is fine for private/early use.
   - Under **Test users**, add every Google account that will sign in
     (e.g. `parmenasoduol1318@gmail.com`). While in Testing mode, any account
     *not* on this list gets `Error 403: access_denied`.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID** →
   **Web application**. In **Authorized redirect URIs** register the callback
   **byte-for-byte** (no trailing slash, exact scheme/host/port). Register the
   ones you actually use, e.g.:
   - `http://localhost:5173/api/integrations/gmail/callback` (Vite dev / default)
   - `http://127.0.0.1:5173/api/integrations/gmail/callback`
   - `http://localhost:8000/api/integrations/gmail/callback` (backend-served SPA)
   - `http://127.0.0.1:8000/api/integrations/gmail/callback`
   - plus your production domain callback in prod.
5. Copy the **Client ID** and **Client secret** into `jalora/.env`:
   ```
   JALORA_GMAIL_CLIENT_ID=<client id>
   JALORA_GMAIL_CLIENT_SECRET=<client secret>
   ```
   A mismatched client/project (ID from one project, expected users from another)
   shows as `Error 400: redirect_uri_mismatch` or `Error 403: access_denied`.

## App settings

The OAuth callback URL is a single source derived from `JALORA_PUBLIC_BASE_URL`
(the default `http://localhost:5173` → `http://localhost:5173/api/integrations/gmail/callback`).
Set an explicit `JALORA_GMAIL_REDIRECT_URI` only to override it:

- Dev with Vite proxy (default): nothing to change.
- Backend-served SPA on `:8000`: `JALORA_PUBLIC_BASE_URL=http://localhost:8000`.
- Production: set `JALORA_PUBLIC_BASE_URL` to the Render backend URL and register
  that callback in the console. Set `JALORA_FRONTEND_BASE_URL` to the deployed
  SPA URL (`https://jalora-ai.vercel.app`) so successful and failed OAuth flows
  return to Integrations instead of the backend root/health page.

## Verify

- `GET /api/health` → `capabilities.gmail_redirect_uri` shows the exact URI
  Google must have registered.
- On startup the backend logs the effective redirect URI and warns if it points
  at localhost in production.
- Connect from the Integrations page, sign in, and confirm the browser is
  redirected back to the SPA with a "Gmail connected" toast and the account
  shown as connected in Integrations.

## API clients

The callback endpoint `/api/integrations/gmail/callback` returns JSON when called
without a browser `Accept: text/html` header, and a `302` redirect back to the
SPA for browsers. Tokens are Fernet-encrypted at rest and never returned.