# NCT Availability Checker

A web application to check NCT (National Car Test) slot availability across test centres in Ireland.

![CI/CD](https://github.com/YOUR_USERNAME/nct-checker/actions/workflows/ci.yml/badge.svg)

## Features

- Check NCT availability by vehicle registration
- View all available test centres and dates
- **Email alerts** when earlier slots become available
- Mobile-friendly responsive design
- Real-time data from ncts.ie
- Background monitoring for slot changes

## Screenshots

The app shows:
- Vehicle details (make, model, year, colour, NCT due date)
- Available test centres sorted by earliest date
- Days until each slot
- Option to set email alerts for earlier availability

## Local Development

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Run the app
python app.py
```

Visit http://localhost:5000

## Deployment Options

### Option 1: Render.com (Recommended - Free tier available)

1. Create a free account at [render.com](https://render.com)
2. Click "New +" → "Web Service"
3. Connect your GitHub repository
4. Render will auto-detect the `render.yaml` configuration
5. Click "Create Web Service"

**Note:** First deploy may take 5-10 minutes due to Playwright browser installation.

### Option 2: Railway.app

1. Create account at [railway.app](https://railway.app)
2. Click "New Project" → "Deploy from GitHub repo"
3. Select your repository
4. Railway will use the Dockerfile automatically

### Option 3: Fly.io

```bash
# Install flyctl
brew install flyctl

# Login and deploy
fly auth login
fly launch
fly deploy
```

### Option 4: Google Cloud Run (Free tier)

```bash
# Build and push Docker image
gcloud builds submit --tag gcr.io/YOUR_PROJECT/nct-checker

# Deploy
gcloud run deploy nct-checker \
  --image gcr.io/YOUR_PROJECT/nct-checker \
  --platform managed \
  --allow-unauthenticated \
  --memory 1Gi \
  --timeout 120
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PORT` | Server port | 5000 |
| `SMTP_HOST` | SMTP server for email alerts | smtp.gmail.com |
| `SMTP_PORT` | SMTP port | 587 |
| `SMTP_USER` | SMTP username (email) | - |
| `SMTP_PASS` | SMTP password or app password | - |
| `FROM_EMAIL` | From address for alerts | SMTP_USER |

### Setting up Gmail for email alerts

1. Enable 2-Factor Authentication on your Google account
2. Go to [Google App Passwords](https://myaccount.google.com/apppasswords)
3. Create a new app password for "Mail"
4. Use your Gmail address as `SMTP_USER` and the app password as `SMTP_PASS`

## API Endpoints

### `GET /`
Home page with the web interface.

### `POST /api/check`
Check NCT availability for a registration.

**Request:**
```json
{
  "registration": "05D77568"
}
```

**Response:**
```json
{
  "success": true,
  "vehicle": {
    "registration": "05D77568",
    "make": "HONDA",
    "model": "ACCORD",
    "year": "2005",
    "colour": "BLUE",
    "nct_due_date": "03/02/2026"
  },
  "slots": [
    {
      "centre": "Greenhills (Exit 11,M50)",
      "date": "2026-05-28",
      "date_formatted": "Thu 28 May 2026",
      "days_away": 125,
      "is_selected": true
    }
  ],
  "checked_at": "2026-01-22T23:30:00"
}
```

### `POST /api/alerts`
Create an email alert for earlier slot availability.

**Request:**
```json
{
  "registration": "05D77568",
  "email": "user@example.com",
  "target_days": 30
}
```

**Response:**
```json
{
  "success": true,
  "alert_id": "05D77568_user@example.com",
  "message": "Alert created! You'll be notified when slots within 30 days become available.",
  "current_earliest": 125
}
```

### `DELETE /api/alerts/<alert_id>`
Delete an alert subscription.

### `GET /api/alerts`
List all active alerts (admin endpoint).

### `GET /health`
Health check endpoint.

## CI/CD

This project includes GitHub Actions for:
- Automated testing on push/PR
- Linting with flake8
- Auto-deploy to Render.com on merge to main

## Architecture

```
┌─────────────────┐     ┌─────────────────┐
│   Web Browser   │────▶│   Flask App     │
└─────────────────┘     └────────┬────────┘
                                 │
                    ┌────────────┴────────────┐
                    │                         │
              ┌─────▼─────┐           ┌───────▼───────┐
              │ Playwright │           │ Alert Service │
              │  (Browser) │           │  (Background) │
              └─────┬─────┘           └───────┬───────┘
                    │                         │
              ┌─────▼─────┐           ┌───────▼───────┐
              │  ncts.ie  │           │  SMTP Server  │
              └───────────┘           └───────────────┘
```

## License

MIT
