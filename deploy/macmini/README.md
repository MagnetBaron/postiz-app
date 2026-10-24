# Postiz on a fresh M4 Mac mini

Clean install for a new Apple Silicon Mac. Nothing in this folder deploys the stack. The owner runs it on the Mac on Monday. The pull request stays open.

The Mac that already has an unused `~/postiz` directory is not the install target. That directory is covered only in the appendix, and only for non-secret values.

Bots create drafts through the public API and MCP. The owner publishes. Social logins and developer-app registration are done by the owner, on this Mac. Comment and DM reading is not part of this install. That stays on `server@themagnetbaron.com`, and that mailbox is not configured here.

## Recommended public HTTPS path

Use a Cloudflare Tunnel (cloudflared, a named tunnel). The tunnel connects out from the Mac to Cloudflare and forwards one hostname to `http://127.0.0.1:4007`. No inbound router ports.

This is the path to use, for four reasons:

1. OAuth callbacks need a public `https://` hostname. The tunnel provides it.
2. The media bucket is already on Cloudflare (`cdn.magnetbaron.com`), so the DNS account already exists.
3. Caddy is not installed, and Caddy as the TLS endpoint would need ports 80 and 443 forwarded on the router.
4. The Postiz container should stay bound to `127.0.0.1`.

Caddy is optional and local. `docker compose --profile caddy up -d` starts the pinned Caddy image and publishes it only on `127.0.0.1:8088`. You can then point the tunnel at `http://127.0.0.1:8088` instead of port 4007. Do not point the same hostname at both. Do not forward router ports to Caddy. Host Caddy (`brew install caddy`) is the same idea if you would rather not run the container. The default install uses neither.

Tailscale Funnel is the fallback if the tunnel cannot be used. See [Tailscale Funnel](#tailscale-funnel) at the bottom of the install steps. A hostname you control is still the one to paste into every developer portal.

## What the stack runs

`docker-compose.yml` is a full compose file, not an override. The upstream compose hardcodes `API_LIMIT: 30`, publishes ports on all interfaces, uses the floating tag `latest`, and bind-mounts `/var/lib/elasticsearch/data` and `/var/lib/postgresql/data` from the host. Those host paths are wrong on a Mac. This file replaces that with named volumes, `restart: unless-stopped`, healthchecks, and `127.0.0.1` port bindings.

| Service | Role | Published port |
| --- | --- | --- |
| `postiz` | Upstream image. nginx on container port 5000. | `127.0.0.1:4007` |
| `postiz-postgres` | Postiz database. Postgres 17. | none |
| `postiz-redis` | Throttle counters and app Redis. | none |
| `temporal-postgresql` | Temporal database. Postgres 16. | none |
| `temporal-elasticsearch` | Temporal visibility. | none |
| `temporal` | Required since Postiz v2.12.0. | none (gRPC stays on the Docker network) |
| `temporal-ui` | Workflow UI for the owner. | `127.0.0.1:8080` |
| `caddy` | Optional profile `caddy`. | `127.0.0.1:8088` |

Temporal is required. The official system requirements say Postiz needs Postgres, Redis, and Temporal, and that Temporal has been required since v2.12.0. Elasticsearch is what the official compose uses for Temporal visibility (`ENABLE_ES=true`). It is included because that is the stack the image is shipped with. Give Docker Desktop at least 8 GB of memory. The docs' 2 GB floor does not include a comfortable Elasticsearch heap plus Postiz.

The Postiz image is `ghcr.io/gitroomhq/postiz-app:v2.25.2`, index digest `sha256:6b52f56416a4a176a858c4e3b74acd2873257103f80b6564bb82edc64359b34f`. Arm64 support was checked from the OCI image index on 2026-10-24, not from the docs. The index lists `linux/arm64` as `sha256:bb44e9e4de0b419a213aa05751608754a04ca0adb87f7505b5a6a541d04c2224`. The same check was done for Postgres, Redis, Elasticsearch, Temporal, Temporal UI, and the optional Caddy image. Digests are in the header of `docker-compose.yml`.

`v2.25.2` is the upstream git tag on this fork's commit `9383f5be4d310c346e33a3748a9a7ded64c96fed` (released 2026-10-23). `version.txt` in the repo still says `v1.47.0`. That file is not the image tag. The Mac runs the upstream image. It does not build this fork.

Application code is unchanged. `API_LIMIT` is already read from the environment.

## Clean install

Do this on the new M4. `uname -m` must print `arm64`.

### 1. Homebrew

Install Homebrew if `brew` is not found, then open a new terminal so `/opt/homebrew/bin` is on `PATH`:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### 2. cloudflared

```bash
brew install cloudflared
```

### 3. Docker

Install one engine.

OrbStack:

```bash
brew install --cask orbstack
```

Open OrbStack and finish its Docker CLI install. Turn on start at login.

Docker Desktop:

```bash
brew install --cask docker
```

Open Docker Desktop. In Settings, Resources, set memory to at least 8 GB. Turn on start at login.

Wait until `docker info` works. Do not forward ports 80, 443, 4007, or 8080 on the router.

### 4. Clone this fork

```bash
git clone https://github.com/MagnetBaron/postiz-app.git
cd postiz-app
git checkout cursor/macmini-self-host-7d5d
cd deploy/macmini
```

If this branch has already been merged into the fork's default branch, check out that default branch instead. Clone `MagnetBaron/postiz-app`, not `gitroomhq/postiz-app`. The kit lives only on this fork. The container image is still upstream.

### 5. Pick the hostname and create the tunnel

Use the Cloudflare account that already serves `cdn.magnetbaron.com`.

Pick a hostname for Postiz itself. The CDN hostname is for media objects, not for the app. The placeholder in `.env.example` is `postiz.example.com`. Replace it. Do not use `social.localhost`, `localhost`, or any `http://` URL.

```bash
cloudflared tunnel login
cloudflared tunnel create postiz
```

`tunnel create` prints a UUID and writes `~/.cloudflared/<uuid>.json`. Leave that file there. It is a secret.

```bash
cp cloudflared/config.yml.example ~/.cloudflared/config.yml
```

Edit `~/.cloudflared/config.yml`. Set the tunnel UUID, your macOS username, and the hostname. The service stays `http://127.0.0.1:4007`.

```bash
cloudflared tunnel route dns postiz YOUR.HOSTNAME
```

That creates the DNS record. No router change.

Try the tunnel once in the foreground after the stack is up (`cloudflared tunnel --config ~/.cloudflared/config.yml run`). Until then, DNS may already resolve to Cloudflare while the origin is down. `preflight.sh` only checks that the name resolves.

### 6. Generate secrets and edit .env

From `deploy/macmini`:

```bash
./scripts/gen-secrets.sh
```

The script copies `.env.example` to `.env` if needed, writes `JWT_SECRET`, `POSTGRES_PASSWORD`, and `TEMPORAL_POSTGRES_PASSWORD`, and rewrites `DATABASE_URL` so the host is `postiz-postgres`. It does not print the values. Running it again leaves an existing long secret in place, so a second run does not rotate the database password out from under a volume you already started.

Then edit `.env` in a local editor:

- Set `MAIN_URL` and `FRONTEND_URL` to `https://YOUR.HOSTNAME` with no path and no trailing slash.
- Set `NEXT_PUBLIC_BACKEND_URL` to that same origin plus `/api`.
- Fill `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_ACCESS_KEY`, `CLOUDFLARE_SECRET_ACCESS_KEY`, and `CLOUDFLARE_BUCKETNAME` from the existing R2 bucket.
- Leave `CLOUDFLARE_BUCKET_URL=https://cdn.magnetbaron.com` if that is still the public bucket hostname. No trailing slash. The upload code inserts the slash itself (`cloudflare.storage.ts`, `r2.uploader.ts`).
- Leave `CLOUDFLARE_REGION=auto`.
- Leave every provider key empty until that app exists.
- Leave `STRIPE_PUBLISHABLE_KEY`, `STRIPE_SECRET_KEY`, and `RESEND_API_KEY` unset.

`chmod` is already `600`. Do not `cat .env`, do not run these scripts under `bash -x`, and do not paste the file into chat.

R2 variable names, from source:

| Variable | Role |
| --- | --- |
| `STORAGE_PROVIDER` | `cloudflare` selects R2. `local` is the other implemented provider. |
| `CLOUDFLARE_ACCOUNT_ID` | Builds the S3 endpoint `https://<accountid>.r2.cloudflarestorage.com`. |
| `CLOUDFLARE_ACCESS_KEY` | R2 access key id. |
| `CLOUDFLARE_SECRET_ACCESS_KEY` | R2 secret. |
| `CLOUDFLARE_BUCKETNAME` | Bucket. |
| `CLOUDFLARE_BUCKET_URL` | Public URL prefix. This install uses `https://cdn.magnetbaron.com`. |
| `CLOUDFLARE_REGION` | Passed to the S3 client. Use `auto`. |
| `RESTRICT_UPLOAD_DOMAINS` | When set, post media URLs must contain this string. Set to `cdn.magnetbaron.com`. |

### 7. Preflight

```bash
./scripts/preflight.sh
```

The last line must be `preflight: passed`. It checks architecture, Docker, ports 4007, 8080, and 8088, `.env` mode `600`, required variables, the https origin, DNS for that hostname, and `cloudflared`. Provider keys are allowed to be empty. It does not print secrets.

`POSTIZ_PREFLIGHT_SKIP_DNS=1` skips DNS while you are still choosing a name. `POSTIZ_PREFLIGHT_ALLOW_INCOMPLETE_R2=1` lets the UI boot before the R2 keys are filled. Uploads will fail until the keys are real. Do not leave either skip in place for the real boot.

### 8. First boot

```bash
docker compose up -d
docker compose ps
```

The first start pulls the pinned images. Temporal's auto-setup can take a few minutes. Wait until `postiz` is healthy.

Open `https://YOUR.HOSTNAME`. Do not use `http://127.0.0.1:4007` as the browser URL. Cookies are marked Secure when `NOT_SECURED` is unset, and every OAuth callback is built from `FRONTEND_URL`. Port 4007 is only the tunnel's target. Temporal's UI, if you want it, is `http://127.0.0.1:8080` on the Mac itself.

After any later edit to `.env`:

```bash
docker compose up -d --force-recreate postiz
```

The official docs say a variable change is picked up by recreating the container. `NEXT_PUBLIC_BACKEND_URL` is passed from the server layout into the browser (`apps/frontend/src/app/(app)/layout.tsx`). Confirm the app loads on the public hostname before you connect a social account.

### 9. First admin account

Open `https://YOUR.HOSTNAME/auth` and create the one owner account. With `RESEND_API_KEY` unset, signup does not wait for an activation email (root `.env.example`).

Log out and log in at `https://YOUR.HOSTNAME/auth/login` to confirm the account works.

Leave Stripe unset. With no `STRIPE_PUBLISHABLE_KEY`, `/user/self` reports the organization as `ULTIMATE` (`users.controller.ts`), and the permissions guard allows the public API (`permissions.service.ts`). Setting a Stripe publishable key makes a new organization look like `FREE` and hides Settings, Agents / Dev, which is where the API key is shown.

### 10. Turn registration off

In `.env`, set `DISABLE_REGISTRATION=true`. Then:

```bash
docker compose up -d --force-recreate postiz
```

Open `https://YOUR.HOSTNAME/auth` in a private window. The page should say registration is disabled and link to login (`apps/frontend/src/app/(app)/auth/page.tsx`).

`canRegister` in `apps/backend/src/services/auth/auth.service.ts` still allows one local signup when the flag is true and the database has zero organizations. That is only the empty-database case. `Provider.GENERIC` ignores the flag. Do not set `POSTIZ_GENERIC_OAUTH`.

### 11. Start at login

Turn on start-at-login for OrbStack or Docker Desktop first, so the engine is up when the agent runs.

```bash
./scripts/install-launchd.sh
```

This copies the plists into `~/Library/LaunchAgents` and loads them for your user. It does not use sudo. The tunnel agent is loaded only after `~/.cloudflared/config.yml` exists. Run the script again after you create that file.

Logs:

- `~/Library/Logs/postiz-stack.log`
- `~/Library/Logs/postiz-tunnel.log`

The stack agent runs `scripts/start-stack.sh`, which waits up to three minutes for `docker info`, then `docker compose up -d`. Container restart policy is `unless-stopped`, so Docker brings the containers back when the engine itself restarts.

### 12. Backups

```bash
./scripts/backup.sh
```

Each run writes new files under `deploy/macmini/backups/`, mode `600`:

- `postiz-<stamp>.sql.gz`, `pg_dump` of the Postiz database
- `temporal-<stamp>.sql.gz`, `pg_dumpall` of the Temporal database
- `uploads-<stamp>.tar.gz`, the local uploads volume

Media bytes are in R2, not in the uploads tarball. Run the script on a schedule you choose. The directory is gitignored. Keep the dumps on this Mac.

To restore the Postiz database onto an empty volume, stop the app first and pipe the dump into `psql`. Do not restore over a database that already has data unless you have planned the overwrite.

```bash
docker compose stop postiz
gunzip -c backups/postiz-STAMP.sql.gz | docker compose exec -T postiz-postgres psql -U postiz-user -d postiz-db-local
docker compose start postiz
```

## Approval gate

Bots must create drafts. The owner publishes from the Mac.

There is no environment variable that forces drafts. A client that holds the organization API key can publish.

### Public API

`POST https://YOUR.HOSTNAME/api/public/v1/posts`

nginx strips `/api` and the controller is `/public/v1` (`public.integrations.controller.ts`, `var/docker/nginx.conf`).

The `Authorization` header is the raw organization API key. It is not `Bearer`. That matches the public API docs and `public.auth.middleware.ts`, which compares the header to the stored key.

```json
{
  "type": "draft",
  "date": "2026-10-26T15:00:00.000Z",
  "shortLink": false,
  "tags": [],
  "posts": [
    {
      "integration": { "id": "INTEGRATION_ID" },
      "value": [{ "content": "<p>Draft text</p>", "image": [] }],
      "settings": { "__type": "facebook" }
    }
  ]
}
```

`type` is stored as sent (`body.type = rawBody.type` in `createPost`). Allowed values used by the API and the UI are `draft`, `schedule`, and `now`.

- `draft` saves the post and skips the settings and length checks.
- `schedule` queues it for `date`. Temporal publishes it. No second approval.
- `now` publishes immediately. The docs show this with a LinkedIn example.

The owner moves a draft into the queue with:

`PUT https://YOUR.HOSTNAME/api/public/v1/posts/<id>/status`

```json
{ "status": "schedule" }
```

`ChangePostStatusDto` allows only `draft` and `schedule`. `schedule` sets the row to `QUEUE` and starts the workflow (`posts.service.ts` `changePostStatus`). Bots must not call this endpoint.

In the product, filter the calendar to Draft and open the post. The composer saves as `draft`, `schedule`, or `now` (`manage.modal.tsx`). Use the schedule action when the draft is approved. Do not use the action that posts now.

### MCP

The MCP server is mounted at `/mcp` on the Nest app (`start.mcp.ts`). Through the public hostname that is:

- `https://YOUR.HOSTNAME/api/mcp` with header `Authorization: Bearer <api key>` (this path strips a leading `Bearer `)
- `https://YOUR.HOSTNAME/api/mcp/<api key>` and `https://YOUR.HOSTNAME/api/sse/<api key>` for clients that put the key in the URL

The URL form copies the key into access logs and into the client config. Prefer the Bearer form. Either form is a posting credential. Keep the client config on this Mac.

`schedulePostTool` (`integration.schedule.post.ts`) takes `type` of `draft`, `schedule`, or `now` and calls `PostsService.createPost` directly. `postsListTool` lists posts in a date window in every state, including draft.

Tell every bot to pass `type: "draft"` only. `schedule` and `now` publish without the owner. The tool is marked `destructiveHint: true`. Nothing in the config removes `schedule` or `now` from the schema.

`API_LIMIT` does not apply to MCP. The throttle guard runs only for `POST` URLs containing `/public/v1/posts` and for `POST /public/posts/<id>/comments` (`throttler.provider.ts`). MCP never hits those routes.

### What API_LIMIT actually throttles

`ThrottlerModule` in `apps/backend/src/app.module.ts` sets `ttl` to `3600000` (one hour) and `limit` to `Number(process.env.API_LIMIT)`, or `90` when the variable is unset. Storage is Redis.

`ThrottlerBehindProxyGuard` is a global guard, and its `canActivate` returns immediately for every request except:

- `POST` whose URL contains `/public/v1/posts` (create post)
- `POST /public/posts/<id>/comments`

The tracker key is the organization id plus `_posts` when the request has an org. So the cap is per organization, per hour, on those two writes. It is not a cap on the whole API, the dashboard, MCP, or `PUT /posts/:id/status`.

The upstream compose hardcodes `API_LIMIT: 30`. The public API docs describe the same limit as applying to the create-post endpoint and say self-hosters change it with `API_LIMIT`. The code default when the variable is missing is 90, which matches the docs' "90 requests per hour" note. This kit defaults to 300.

300 create-post calls an hour is enough for a bot to file a batch of drafts, including one call that carries several posts (the docs note that one request can schedule many posts). It still caps a leaked key that is hammering `POST /public/v1/posts`. It does not stop that key from calling MCP, and it does not stop `type: "now"`. Platform rate limits (Meta, X, TikTok) are separate and unchanged.

Set a lower number in `.env` if you want a tighter cap, then recreate the `postiz` container.

## Tailscale Funnel

Fallback only. Install Tailscale from Homebrew (`brew install --cask tailscale`), sign in, and publish the local port with Funnel. The current CLI is `tailscale funnel 4007`. Confirm the exact flags in `tailscale funnel --help` on that Mac. The public name will be an `https://<machine>.<tailnet>.ts.net` hostname.

Put that exact origin in `MAIN_URL`, `FRONTEND_URL`, and `NEXT_PUBLIC_BACKEND_URL`. Register OAuth callbacks on that origin. Funnel opens nothing on your router. Prefer the Cloudflare Tunnel: the CDN is already on Cloudflare, and a hostname you own will not change when the Tailscale machine name changes. Some providers reject a `ts.net` hostname during app review.

## Security

- Generate `JWT_SECRET` and both database passwords on this Mac. Do not copy them from another machine.
- `.env` stays mode `600` in `deploy/macmini/`. Docker reads it. A copy in Keychain is a backup, not a replacement: `security add-generic-password -U -s postiz-macmini -a JWT_SECRET -w` and then type or paste the value in the terminal so it is not in the shell history (`read -r -s` into the command). Do not put the value on the `security` command line in a script that gets logged.
- Provider client secrets, the R2 secret, the tunnel credentials JSON, and the organization API key stay on this Mac.
- The organization API key can publish. Treat it like a posting key even though bots are instructed to send drafts.
- Do not wire comment reading, DM reading, or comment-reply plugs. `server@themagnetbaron.com` is the account that reads comments and DMs. Its password does not go in `.env`.
- Postiz still requests comment-related OAuth scopes on Meta, Instagram, and Threads because `checkScopes` rejects a token that is missing them. Grant the scopes so connect succeeds. Do not use Postiz to read or answer comments.

# OWNER CHECKLIST

Work top to bottom. The first four networks are Facebook, Instagram, Pinterest, and YouTube. X, TikTok, and LinkedIn come after those. Threads, Reddit, Bluesky, and Mastodon are last.

## a. Domain and Cloudflare

- [ ] Cloudflare account is the one that already hosts `cdn.magnetbaron.com`.
- [ ] A hostname for the Postiz app is chosen. It is not the CDN hostname and not `social.localhost`.
- [ ] `cloudflared tunnel login` and `cloudflared tunnel create postiz` have been run on this Mac.
- [ ] `~/.cloudflared/config.yml` points that hostname at `http://127.0.0.1:4007`.
- [ ] `cloudflared tunnel route dns` created the record. The router has no new port forwards.
- [ ] `MAIN_URL`, `FRONTEND_URL`, and `NEXT_PUBLIC_BACKEND_URL` use that `https://` origin.

## b. Values you must put in .env

`gen-secrets.sh` fills these. You do not type them: `JWT_SECRET`, `POSTGRES_PASSWORD`, `TEMPORAL_POSTGRES_PASSWORD`, and the password inside `DATABASE_URL`.

You type these:

- [ ] The three public URL variables, matching the tunnel hostname.
- [ ] `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_ACCESS_KEY`, `CLOUDFLARE_SECRET_ACCESS_KEY`, `CLOUDFLARE_BUCKETNAME`.
- [ ] `CLOUDFLARE_BUCKET_URL` (`https://cdn.magnetbaron.com` unless the public hostname changed), `CLOUDFLARE_REGION=auto`, `RESTRICT_UPLOAD_DOMAINS=cdn.magnetbaron.com`.
- [ ] `DISABLE_REGISTRATION=true` after the first account exists, then recreate `postiz`.
- [ ] Provider keys only after the matching app exists. Empty is correct until then.
- [ ] Stripe and Resend stay unset.

`API_LIMIT=300` can stay. See the tradeoff above.

## c. Developer apps

Redirect pattern, from each provider's `generateAuthUrl` and from the provider docs: `https://<your-hostname>/integrations/social/<identifier>`.

These providers reject a non-public or non-https callback for the app mode this install needs:

- TikTok. The Postiz TikTok page says TikTok will not allow `http://` for the redirect URI. The source wraps a non-https `FRONTEND_URL` in `https://redirectmeto.com/`, which is a third-party relay. Do not use that. Register the public https URI.
- Pinterest. The OAuth redirect for a real app is https. The `http://localhost` lines in the Postiz Pinterest docs are local-dev examples and will not do for Standard access.
- Meta (Facebook, Instagram, Threads). A Live app requires https redirect URIs. `http://localhost` is accepted only while the app is in Development, and Development posts are not visible to other people. `social.localhost` is not localhost.
- YouTube. Google accepts `http://localhost` and `http://127.0.0.1` only. Any other host, including `social.localhost`, must be https.

Paste the public https URI. Do not paste `social.localhost`.

### Meta, read this first

[gitroomhq/postiz-app#1578](https://github.com/gitroomhq/postiz-app/issues/1578) is still open. The last comment was 2026-07-29. Pull requests #1579 and #1600, which tried to rename the scopes, were closed without merging. The pinned image `v2.25.2` (2026-10-23) still requests `instagram_basic` and the Facebook page scopes listed below. Confirmed in the tag, in `instagram.provider.ts` and `facebook.provider.ts`.

The failure mode in the issue is Meta's "Invalid Scopes" error when the app was created with the new use-case wizard. The current Postiz Facebook and Instagram docs tell you to avoid that wizard: on "Add use cases", filter All, scroll to "Looking for something else?", choose Other, then Business. That is the path that still accepts the scope names this image sends. If you use the new wizard, connect fails, and this image does not contain a scope-name fix.

Graph API version in this code is `v25.0` (`META_GRAPH_API_VERSION`).

Facebook and Instagram (Facebook Business) share one Meta app and the same `FACEBOOK_APP_ID` / `FACEBOOK_APP_SECRET`. Instagram Standalone is a different product and uses `INSTAGRAM_APP_ID` / `INSTAGRAM_APP_SECRET`.

Postiz requests comment and engagement scopes because connect fails `checkScopes` without them. Grant them. Do not use Postiz to read or answer comments or DMs. That mailbox is `server@themagnetbaron.com`.

### 1. Facebook Page

- Portal: [https://developers.facebook.com/apps](https://developers.facebook.com/apps)
- Create the app as Other, then Business. Add Facebook Login for Business. Docs: [https://docs.postiz.com/providers/facebook](https://docs.postiz.com/providers/facebook)
- Redirect URI: `https://<your-hostname>/integrations/social/facebook`
- A reconnect appends `?refresh=<id>` to that URI (`facebook.provider.ts`). If reconnect fails with a redirect mismatch, add that exact URI in the Meta app as well.
- Scopes the image requests: `pages_show_list`, `business_management`, `pages_manage_posts`, `pages_manage_engagement`, `pages_read_engagement`, `read_insights`
- The app must be Live. In Development, posts are visible to app roles only. The Postiz docs say this explicitly, including for images on posts.
- App Review for those advanced permissions is Meta's queue. Do not expect it the same day. The docs also say a personal-use app can function before advanced permissions are approved, but this code rejects a token that is missing any requested scope, so request the full list.
- Env: `FACEBOOK_APP_ID`, `FACEBOOK_APP_SECRET`
- Identifier: `facebook`

### 2. Instagram

Try Instagram (Facebook Business) on the same Meta app first, after the app was created as Other / Business.

- Redirect URI: `https://<your-hostname>/integrations/social/instagram`
- Same reconnect `?refresh=` note as Facebook (`instagram.provider.ts`).
- Scopes: `instagram_basic`, `pages_show_list`, `pages_read_engagement`, `business_management`, `instagram_content_publish`, `instagram_manage_comments`, `instagram_manage_insights`
- The Instagram account must be a professional account linked to a Facebook Page (provider tooltip).
- Docs: [https://docs.postiz.com/providers/instagram](https://docs.postiz.com/providers/instagram)
- Env: the same `FACEBOOK_APP_ID` and `FACEBOOK_APP_SECRET`
- Identifier: `instagram`
- Live mode, same as Facebook. Review timing is the same Meta queue.

If Meta returns Invalid Scopes for `instagram_basic`, that is issue #1578 and this image will not rename the scope. Use Instagram Standalone instead:

- A separate Meta app (or the Instagram product with Instagram Login), professional account.
- Redirect URI: `https://<your-hostname>/integrations/social/instagram-standalone`
- Scopes this image actually requests (`instagram.standalone.provider.ts`): `instagram_business_basic`, `instagram_business_content_publish`, `instagram_business_manage_comments`, `instagram_business_manage_insights`
- Env: `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET`
- Identifier: `instagram-standalone`
- The Postiz docs say to add the Instagram account as an Instagram Tester and to accept the invite in the Instagram app when the error is "Insufficient developer role".

`instagram_manage_comments` and `instagram_business_manage_comments` are requested so connect succeeds. Do not read comments in Postiz.

### 3. Pinterest

- Portal: [https://developers.pinterest.com/apps/](https://developers.pinterest.com/apps/)
- A Pinterest company account is required (Postiz docs).
- Redirect URI: `https://<your-hostname>/integrations/social/pinterest`
- Scopes: `boards:read`, `boards:write`, `pins:read`, `pins:write`, `user_accounts:read`
- This install needs Standard access. Trial access is limited to the developer. The Postiz docs say to wait until the app is approved. Pinterest sets the review time. It is not instant.
- https only. Do not register localhost.
- Env: `PINTEREST_CLIENT_ID`, `PINTEREST_CLIENT_SECRET`
- Identifier: `pinterest`
- Docs: [https://docs.postiz.com/providers/pinterest](https://docs.postiz.com/providers/pinterest)

### 4. YouTube

- Portal: [https://console.cloud.google.com/apis/credentials](https://console.cloud.google.com/apis/credentials)
- Create a project, configure the OAuth consent screen, then create an OAuth client of type Web application.
- Redirect URI: `https://<your-hostname>/integrations/social/youtube`
- Enable YouTube Data API v3, YouTube Analytics API, and YouTube Reporting API (Postiz YouTube docs).
- Scopes the image requests: `userinfo.profile`, `userinfo.email`, `youtube`, `youtube.force-ssl`, `youtube.readonly`, `youtube.upload`, `youtubepartner`, `yt-analytics.readonly` (full URLs under `https://www.googleapis.com/auth/` in `youtube.provider.ts`).
- Add yourself as a test user so you can connect before review. Google expires refresh tokens from an app that is still in Testing after 7 days, so a durable connection needs the consent screen published. YouTube's upload scopes are sensitive, and Google's verification queue is often days to a few weeks. Brand accounts have extra Workspace steps in the Postiz docs. Plan on that wait before you depend on unattended publishing.
- https on your real hostname. Google's localhost exception does not cover `social.localhost`.
- Env: `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`
- Identifier: `youtube`
- Docs: [https://docs.postiz.com/providers/youtube](https://docs.postiz.com/providers/youtube)

### 5. X

Do this after the four networks above.

- Portal: [https://developer.x.com](https://developer.x.com) (the console is also at console.x.com).
- User authentication: OAuth 1.0a, App permissions Read and Write, Type of App must be Native App. The Postiz X docs say "Web App, Automated App or Bot" fails with error 32. The source uses OAuth 1.0a (`x.provider.ts`, empty `scopes` array) because media upload still needs the v1.1 path.
- Callback / redirect URI: `https://<your-hostname>/integrations/social/x`
- Leave `X_URL` empty. When it is set, the source uses it instead of `FRONTEND_URL` as the callback origin. The provider docs describe `X_URL` as an API base URL. The code does not.
- Env: `X_API_KEY`, `X_API_SECRET` (the consumer key and secret).
- Identifier: `x`
- Cost, from X's April 2026 pay-per-use change: about $0.015 per post create, and $0.20 per post that contains a URL. X's changelog states those two rates, and TechCrunch reported the link price on 2026-04-22 ([https://techcrunch.com/2026/04/22/x-makes-it-more-expensive-to-post-links-through-its-api/](https://techcrunch.com/2026/04/22/x-makes-it-more-expensive-to-post-links-through-its-api/)). Confirm the current numbers in the developer console before you buy credits. Reads and media calls are billed separately. Do not turn on DM reads.
- Docs: [https://docs.postiz.com/providers/x-twitter](https://docs.postiz.com/providers/x-twitter)

### 6. TikTok

- Portal: [https://developers.tiktok.com/apps](https://developers.tiktok.com/apps)
- Add Login Kit and Content Posting API. Enable Direct Post. The app needs a public https site and a privacy policy (Postiz TikTok docs).
- Redirect URI: `https://<your-hostname>/integrations/social/tiktok`
- Scopes this image requests (`tiktok.provider.ts`): `video.list`, `user.info.basic`, `video.publish`, `video.upload`, `user.info.profile`, `user.info.stats`. The Postiz docs also mention `video.create`. This source does not request `video.create`. Grant the source list or `checkScopes` fails.
- TikTok must be able to fetch media by URL. `https://cdn.magnetbaron.com` must be a verified URL prefix on the TikTok app. Local `/uploads` paths will not work. That is why storage is R2.
- Until TikTok's audit finishes, Direct Post is forced to `SELF_ONLY` (private), and unaudited clients are limited to 5 users in 24 hours who must be private at post time. The Postiz docs say this, and the audit is TikTok's queue. Do not plan on public posting the week you submit.
- https only.
- Env: `TIKTOK_CLIENT_ID`, `TIKTOK_CLIENT_SECRET`
- Identifier: `tiktok`
- If you later add TikTok Business, the source redirect has a trailing slash: `https://<your-hostname>/integrations/social/tiktok-business/` (`tiktok.business.provider.ts`). Env: `TIKTOK_BUSINESS_CLIENT_ID`, `TIKTOK_BUSINESS_CLIENT_SECRET`.
- Docs: [https://docs.postiz.com/providers/tiktok](https://docs.postiz.com/providers/tiktok)

### 7. LinkedIn

Company pages and the personal profile use one app and the same scopes. The page provider's redirect is different.

- Portal: [https://www.linkedin.com/developers/apps](https://www.linkedin.com/developers/apps)
- Products the current Postiz docs require: Share on LinkedIn, Sign In with LinkedIn using OpenID Connect, and Advertising API. The docs say the organization scopes come from Advertising API, and that a missing scope does not fail LinkedIn's login. Postiz then rejects the token with `NotEnoughScopes`.
- Company pages also need the organization scopes (`rw_organization_admin`, `w_organization_social`, `r_organization_social`). LinkedIn's portal shows those under Community Management API on newer apps. If the Auth tab does not list them after Advertising API is added, request Community Management API as well. Confirm the Auth tab before you connect. Both products are reviewed by LinkedIn. It is not instant.
- Scopes: `openid`, `profile`, `w_member_social`, `r_basicprofile`, `rw_organization_admin`, `w_organization_social`, `r_organization_social`
- Redirect URIs, both of them: `https://<your-hostname>/integrations/social/linkedin` and `https://<your-hostname>/integrations/social/linkedin-page`
- Env: `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`
- Identifiers: `linkedin` and `linkedin-page`
- Docs: [https://docs.postiz.com/providers/linkedin](https://docs.postiz.com/providers/linkedin)

### 8. Threads, Reddit, Bluesky, Mastodon

Threads (Meta, its own app id):

- Portal: [https://developers.facebook.com/apps](https://developers.facebook.com/apps), Threads use case, not the Facebook app above.
- Redirect URI: `https://<your-hostname>/integrations/social/threads`
- Scopes: `threads_basic`, `threads_content_publish`, `threads_manage_replies`, `threads_manage_insights`
- `threads_manage_replies` is required by `checkScopes`. Do not use it to read replies here.
- The app needs to be Live, same Meta review queue as Facebook.
- Env: `THREADS_APP_ID`, `THREADS_APP_SECRET`
- Identifier: `threads`

Reddit:

- Portal: [https://www.reddit.com/prefs/apps](https://www.reddit.com/prefs/apps), create a "web app".
- Redirect URI: `https://<your-hostname>/integrations/social/reddit`
- Scopes: `read`, `identity`, `submit`, `flair`
- Env: `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`
- Identifier: `reddit`

Bluesky:

- No developer app and no redirect URI. In the channel dialog, Postiz asks for the service (default `https://bsky.social`), the handle, and an app password (`bluesky.provider.ts` `customFields`).
- The provider tooltip says two-factor authentication is not supported. Turn 2FA off on that Bluesky account or the login fails.
- There is no `BLUESKY_CLIENT_ID`.

Mastodon:

- On the instance (default `https://mastodon.social`, `MASTODON_URL`), create an application under Development.
- Redirect URI: `https://<your-hostname>/integrations/social/mastodon`
- Scopes: `write:statuses`, `profile`, `write:media`
- Env: `MASTODON_URL`, `MASTODON_CLIENT_ID`, `MASTODON_CLIENT_SECRET`
- Identifier: `mastodon`

After you save any new key, recreate the container:

```bash
docker compose up -d --force-recreate postiz
```

## d. API key and MCP

1. Log in on the public https hostname.
2. Open Settings, then Agents / Dev. The page is `/settings`. The tab id is `agents` (`settings.component.tsx`). It is shown when `IS_GENERAL` is true and the plan includes `public_api`. With Stripe unset, the self-hosted organization is treated as `ULTIMATE`, which includes it (`pricing.ts`).
3. Reveal the organization API key. Rotating it (`POST /user/api-key/rotate`) invalidates bots that already have it. The key is created with the organization (`organization.repository.ts`).
4. Point bots at `https://YOUR.HOSTNAME/api/mcp` with `Authorization: Bearer <key>`, from this Mac. The public API base for drafts is `https://YOUR.HOSTNAME/api/public/v1`, and that header is the raw key with no `Bearer` prefix.
5. The bot instruction is: `schedulePostTool` with `type` `draft` only. The owner approves in the calendar or with `PUT /api/public/v1/posts/<id>/status` and `{"status":"schedule"}`.
6. `postsListTool` is safe to use for listing. It does not publish.
7. Do not give bots `type` `now` or `type` `schedule`. The server will accept both.

## e. What stays on this Mac

All of these stay on the Mac mini:

- `deploy/macmini/.env` (mode `600`)
- `~/.cloudflared/<uuid>.json` and `~/.cloudflared/config.yml`
- The Postiz and Temporal Docker volumes (Postgres, Redis, Elasticsearch, uploads)
- R2 access keys, every provider client secret, and the organization API key
- MCP client configs, because they contain that API key
- Backups under `deploy/macmini/backups/`

A second copy of `JWT_SECRET` and the organization API key may sit in the login Keychain under the service name `postiz-macmini`. Docker still reads `.env`. Keychain does not replace it.

Do not put `server@themagnetbaron.com` credentials, comment tokens, or DM tokens in this `.env`.

## Appendix: older staged install

Ignore this section on the new Mac unless you are copying a few non-secret settings from the old `~/postiz/.env`.

That directory was never started. Its JWT and database password were still placeholders, provider keys were empty, and it expected Caddy, which was not installed. Do not copy it onto the M4 and do not start it. Do not copy its secrets. Secrets for this install are generated on the M4 by `gen-secrets.sh`.

If you want the old public hostname or the old bucket name, and they are not placeholders:

```bash
./scripts/gen-secrets.sh
./scripts/import-staged-settings.sh /path/to/old.env
```

The importer copies only `MAIN_URL`, `FRONTEND_URL`, `NEXT_PUBLIC_BACKEND_URL`, `STORAGE_PROVIDER`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_BUCKETNAME`, `CLOUDFLARE_BUCKET_URL`, and `CLOUDFLARE_REGION`. It skips localhost, `social.localhost`, non-https URLs, and anything whose name looks like a secret. It does not print values. It does not copy R2 access keys, provider secrets, JWT, or passwords. You still type the R2 access key and secret on the M4.

The old Caddy plan is not used. Use the tunnel.

## Sources

- Image indexes fetched from `ghcr.io` and Docker Hub on 2026-10-24. The build that publishes both architectures is `.github/workflows/build-containers.yml`.
- Upstream install docs: [https://docs.postiz.com/self-host/installation/docker-compose](https://docs.postiz.com/self-host/installation/docker-compose), [https://docs.postiz.com/self-host/installation/system-requirements](https://docs.postiz.com/self-host/installation/system-requirements). Temporal is required since v2.12.0.
- `API_LIMIT`: `apps/backend/src/app.module.ts`, `libraries/nestjs-libraries/src/throttler/throttler.provider.ts`, [https://docs.postiz.com/public-api](https://docs.postiz.com/public-api/introduction).
- Drafts: `apps/backend/src/public-api/routes/v1/public.integrations.controller.ts`, `libraries/nestjs-libraries/src/dtos/posts/change.post.status.dto.ts`, `libraries/nestjs-libraries/src/chat/tools/integration.schedule.post.ts`, `libraries/nestjs-libraries/src/chat/tools/posts.list.tool.ts`, `libraries/nestjs-libraries/src/chat/start.mcp.ts`.
- Storage: `libraries/nestjs-libraries/src/upload/upload.factory.ts`, `cloudflare.storage.ts`, `r2.uploader.ts`.
- Registration: `apps/backend/src/services/auth/auth.service.ts`, `apps/frontend/src/app/(app)/auth/page.tsx`.
- Provider redirect URIs and scopes: the `generateAuthUrl` and `scopes` fields in `libraries/nestjs-libraries/src/integrations/social/`.
- Provider setup pages: `https://docs.postiz.com/providers/facebook`, `instagram`, `pinterest`, `youtube`, `x-twitter`, `tiktok`, `linkedin`.
- Meta connect bug: [https://github.com/gitroomhq/postiz-app/issues/1578](https://github.com/gitroomhq/postiz-app/issues/1578), still open, not fixed in `v2.25.2`.

## What was not executed on the Mac

This kit was prepared on Linux, not on an M4. The OCI indexes were read from ghcr.io and Docker Hub on 2026-10-24. `shellcheck` 0.9.0 passed on the scripts. `docker compose config` (Compose 2.40.3) rendered Postiz on `127.0.0.1:4007` and Temporal UI on `127.0.0.1:8080`. The Caddy port `127.0.0.1:8088` appears only with `--profile caddy`. Postgres, Redis, and Elasticsearch are not published.

The stack was not booted, so container healthchecks were not executed. The tunnel was not created, and no OAuth flow was clicked. `preflight.sh` correctly fails on this Linux host because `uname -m` is not `arm64`. Run it again on the Mac after `.env` is filled in.
