# Gmail OAuth setup

To connect a real Gmail account the app needs an OAuth client in Google Cloud,
plus a couple of settings. Everything else (encrypted token storage, CSRF
`state` check, callback redirect) is handled by the app.

## Cloud console (one-time, by hand)

1. Open a Google Cloud project. Use one you own. The project's name appears on
   the consent screen (e.g. "catholic readings and choir resources app"), so a
   dedicated "Jalora" project avoids embarrassing mismatches.
2. **APIs & Services → Library** → enable the **Gmail API**, **Google Calendar
   API**, and **Google Drive API**.
3. **APIs & Services → OAuth consent screen**:
   - User type: **External**, app name/logo as you like.
   - Status **Testing** is suitable for development. Add every Google account
     that will sign in under **Test users**. Accounts not on that list receive
     `access_denied`.
   - To let the general public authorize the app, publish it and complete
     Google's OAuth verification for the requested sensitive/restricted
     scopes. Testing-mode test users are not a public rollout.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID** →
   **Web application**. In **Authorized redirect URIs** register the callback
   **byte-for-byte** (no trailing slash, exact scheme/host/port). Register the
   ones you actually use. Both the Gmail-specific callback and the generic
   Calendar/Drive callback are required:
   - `http://localhost:5173/api/integrations/gmail/callback` (Vite dev / default)
   - `http://localhost:5173/api/integrations/oauth/callback` (Vite dev / default)
   - `http://127.0.0.1:5173/api/integrations/gmail/callback`
   - `http://127.0.0.1:5173/api/integrations/oauth/callback`
   - `http://localhost:8000/api/integrations/gmail/callback` (direct backend)
   - `http://127.0.0.1:8000/api/integrations/gmail/callback`
   - `http://localhost:8000/api/integrations/oauth/callback` (direct backend)
   - `http://127.0.0.1:8000/api/integrations/oauth/callback`
   - `https://jalora-ai.onrender.com/api/integrations/gmail/callback`
   - `https://jalora-ai.onrender.com/api/integrations/oauth/callback`

   Gmail uses its dedicated callback. Calendar and Drive use the generic
   callback. Google does not include a `provider` query parameter in the return
   URL; the server identifies the integration from the validated OAuth `state`.
5. Copy the **Client ID** and **Client secret** into the backend environment:
   ```
   JALORA_GMAIL_CLIENT_ID=<client id>
   JALORA_GMAIL_CLIENT_SECRET=<client secret>
   ```
   A mismatched client/project (ID from one project, expected users from another)
   shows as `Error 400: redirect_uri_mismatch` or `Error 403: access_denied`.

## App settings

OAuth callback URLs derive from `JALORA_PUBLIC_BASE_URL` (default
`http://localhost:5173`). Gmail uses
`/api/integrations/gmail/callback`; Calendar and Drive use
`/api/integrations/oauth/callback`. Set an explicit
`JALORA_GMAIL_REDIRECT_URI` only to override the Gmail callback:

- Dev with Vite proxy (default): nothing to change.
- Backend-served SPA on `:8000`: `JALORA_PUBLIC_BASE_URL=http://localhost:8000`.
- Production Render environment:
  - `JALORA_PUBLIC_BASE_URL=https://jalora-ai.onrender.com`
  - `JALORA_FRONTEND_BASE_URL=https://jalora-ai.vercel.app`
  - `JALORA_GMAIL_CLIENT_ID=<Google OAuth web client ID>`
  - `JALORA_GMAIL_CLIENT_SECRET=<Google OAuth web client secret>`
  - Leave `JALORA_GMAIL_REDIRECT_URI` unset unless deliberately overriding the
    Gmail callback. No separate Calendar or Drive client credentials are used.
- Google OAuth web-client **Authorized JavaScript origins** are not required
  for these server-side authorization-code flows. The redirect URIs above are
  required. The browser returns to the Vercel app after the Render callback.

## Verify

- `GET /api/health` → `capabilities.gmail_redirect_uri` and
  `capabilities.google_integrations_redirect_uri` show the exact Gmail and
  Calendar/Drive URIs Google must have registered. Their corresponding
  `*_oauth` fields report readiness without exposing credentials.
- On startup the backend logs the effective redirect URI and warns if it points
  at localhost in production.
- Connect from the Integrations page, sign in, and confirm the browser is
  redirected back to the SPA with a "Gmail connected" toast and the account
  shown as connected in Integrations.

## API clients

The callback endpoint `/api/integrations/gmail/callback` returns JSON when called
without a browser `Accept: text/html` header, and a `302` redirect back to the
SPA for browsers. Tokens are Fernet-encrypted at rest and never returned.