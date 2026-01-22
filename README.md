# NCT Availability Checker

A web application to check NCT (National Car Test) slot availability across test centres in Ireland.

## Features

- Check NCT availability by vehicle registration
- View all available test centres and dates
- Mobile-friendly responsive design
- Real-time data from ncts.ie

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

### `GET /health`
Health check endpoint.

## License

MIT
