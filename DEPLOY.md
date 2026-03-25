# Deploying Ascent to Fly.io

## Prerequisites

```bash
# Install flyctl
brew install flyctl

# Login
fly auth login
```

## First Deploy

```bash
cd /Volumes/Lion2/projects/ascent-web

# Create the app (don't deploy yet)
fly launch --no-deploy --name ascent-web --region sjc

# Create persistent volume for the database
fly volumes create ascent_data --region sjc --size 3

# Set secrets (never put these in fly.toml)
fly secrets set \
  SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))") \
  STRAVA_CLIENT_ID=your_strava_client_id \
  STRAVA_CLIENT_SECRET=your_strava_client_secret \
  ANTHROPIC_API_KEY=your_anthropic_key

# Deploy
fly deploy
```

## Upload your existing database

```bash
# Copy your local DB to the Fly volume via a temporary machine
fly sftp shell
# Then in the sftp prompt:
put /Users/rob/Desktop/Ascent.ascentdb /data/Ascent.ascentdb
exit
```

Or use the simpler one-liner:
```bash
fly ssh sftp put /Users/rob/Desktop/Ascent.ascentdb /data/Ascent.ascentdb
```

## Run migrations on the server

```bash
fly ssh console
# In the console:
cd /app
ASCENT_DB_PATH=/data/Ascent.ascentdb python3 scripts/migrate_add_users.py
ASCENT_DB_PATH=/data/Ascent.ascentdb python3 scripts/migrate_step2.py
exit
```

## Update Strava OAuth callback URL

In your Strava API settings (https://www.strava.com/settings/api), add:
  Authorization Callback Domain: ascent-web.fly.dev

Also add this environment variable:
```bash
fly secrets set STRAVA_REDIRECT_URI=https://ascent-web.fly.dev/strava/callback
fly secrets set STRAVA_AUTH_REDIRECT_URI=https://ascent-web.fly.dev/auth/strava/callback
```

## Subsequent deploys

```bash
fly deploy
```

## View logs

```bash
fly logs
```

## Scale up if needed

```bash
fly scale memory 1024  # 1GB RAM
fly scale count 1      # always-on (no cold starts)
```

## Costs (approximate)

- Shared CPU + 512MB RAM: ~$0 with auto-stop (free tier)
- Always-on (min 1 machine): ~$3-4/month
- 3GB persistent volume: ~$0.75/month
- Total with always-on: ~$4-5/month
