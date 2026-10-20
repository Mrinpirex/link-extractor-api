# Authorized Link Extractor API (Heroku)

A small Flask API that extracts ordinary HTTP(S) anchor links from HTML pages you are authorized to access.

## Files
- `app.py`: API implementation
- `requirements.txt`: dependencies
- `Procfile`: Heroku web process
- `.python-version`: Python runtime

## 1. Configure an allowlist
Set `ALLOWED_HOSTS` to the exact domains you own or are authorized to fetch. Use comma-separated hostnames, without schemes or paths. Example:
`example.org,docs.example.org`

The service rejects hosts not on this allowlist and rejects private/non-public IP destinations. Redirect destinations are checked too.

## 2. Set a secret API key
Generate a strong random value, then configure it as `API_KEY` in Heroku Config Vars. Never put the secret in source code or commit it to Git.

## 3. Deploy
Install Git and the Heroku CLI, sign in, then from this folder run:

```bash
git init
git add app.py requirements.txt Procfile .python-version .gitignore README.md
git commit -m "Initial authorized link extractor API"
heroku login
heroku create YOUR-UNIQUE-APP-NAME
heroku config:set API_KEY="YOUR_LONG_RANDOM_SECRET" ALLOWED_HOSTS="example.org,docs.example.org" -a YOUR-UNIQUE-APP-NAME
git push heroku main
```

If your local branch is named `master`, use `git push heroku master` instead.

## 4. Test
Health check:
`https://YOUR-UNIQUE-APP-NAME.herokuapp.com/`

Link extraction (replace the URL and secret):
```bash
curl -H "X-API-Key: YOUR_LONG_RANDOM_SECRET" \
  --get --data-urlencode "url=https://example.org/" \
  "https://YOUR-UNIQUE-APP-NAME.herokuapp.com/api/links"
```

Example response:
```json
{
  "success": true,
  "source_url": "https://example.org/",
  "final_url": "https://example.org/",
  "count": 1,
  "links": [
    {"text": "More information", "url": "https://example.org/more"}
  ]
}
```

## Notes
- The API returns links present in HTML; it does not execute JavaScript.
- It does not bypass login, CAPTCHA, paywalls, download gates, or access controls.
- Temporary links may expire independently of this API.
- Keep the allowlist narrow, keep the API key private, and review usage/logs.
