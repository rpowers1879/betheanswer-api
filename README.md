# Be The Answer — AI Visibility Score API

Backend API that powers the AI Visibility Score widget on betheanswer.cloud.

## What it does

1. **Scrapes** the business website for structure, content, and technical signals
2. **Queries ChatGPT** with 3 prompts to test if the business gets recommended
3. **Scores** everything into a 0-100 number across 4 categories
4. **Returns** the score to the frontend widget

## Scoring Breakdown

| Category | Weight | What it checks |
|---|---|---|
| Website Structure | 15% | HTTPS, title tags, headings, navigation, canonical URLs |
| Content Signals | 20% | Meta descriptions, word count, blog presence, heading quality |
| Technical / Schema | 25% | JSON-LD schema, Open Graph tags, image alt text, SSL |
| ChatGPT Visibility | 40% | Whether ChatGPT actually recommends the business |

## Deploy to Railway (5 minutes)

### Step 1: Get your OpenAI API key
1. Go to [platform.openai.com](https://platform.openai.com)
2. Create an account and add $10 credit (Settings → Billing)
3. Go to API Keys → Create new secret key
4. Copy the key (starts with `sk-`)

### Step 2: Push to GitHub
1. Create a new GitHub repo called `betheanswer-api`
2. Upload all files from this folder to the repo

### Step 3: Deploy on Railway
1. Go to [railway.app](https://railway.app) and sign in with GitHub
2. Click "New Project" → "Deploy from GitHub Repo"
3. Select your `betheanswer-api` repo
4. Railway will auto-detect the Dockerfile and start building
5. Go to your service → Variables tab → Add:
   - `OPENAI_API_KEY` = your key from Step 1
   - `ALLOWED_ORIGINS` = `https://betheanswer.cloud` (add your Vercel preview URL too during testing)
   - `PORT` = `8000`
6. Go to Settings → Networking → Generate Domain
7. You'll get a URL like `https://betheanswer-api-production.up.railway.app`

### Step 4: Connect to your website
In your site repo's `index.html`, find this line:

```javascript
const SCORE_API_URL = 'https://YOUR-API-DOMAIN.com/api/score';
```

Replace with your Railway URL:

```javascript
const SCORE_API_URL = 'https://betheanswer-api-production.up.railway.app/api/score';
```

### Step 5: Test it
Visit your site and submit a URL in the score widget. You should see real results in ~30 seconds.

## Test locally

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your OpenAI key
uvicorn main:app --reload --port 8000
```

Test with curl:
```bash
curl -X POST http://localhost:8000/api/score \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "business": "Example Plumbing",
    "industry": "plumber",
    "city": "Denver",
    "email": "test@test.com"
  }'
```

## API Cost

- **Railway**: ~$5/month (Hobby plan)
- **OpenAI**: ~$0.01-0.03 per score check (gpt-4o-mini)
- 100 checks/month = ~$1-3 in API costs
- 1000 checks/month = ~$10-30 in API costs

## Coming soon

- Lead storage (save to database/Airtable/Google Sheets)
- Email delivery (auto-send score results)
- Rate limiting (prevent abuse)
- Result caching (same URL = cached score for 24h)
