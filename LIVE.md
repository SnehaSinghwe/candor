# Live mode (real Gmail / Calendar / Slack)

Dry-run stays the default; `python assistant.py` behaves exactly as before. Live mode only runs with `--live`, and every action still asks `run it? [y/N]`.

## One-time Google setup (free)
1. console.cloud.google.com -> create a project.
2. APIs & Services -> Library -> enable **Gmail API** and **Google Calendar API**.
3. OAuth consent screen -> External -> add your own Gmail as a **Test user**. Then Credentials -> Create credentials -> OAuth client ID -> **Desktop app** -> download the JSON, save it as `credentials.json` in the project folder.
4. `pip install -r requirements-live.txt`
5. Safe first run (all mail goes to yourself, because the sample contacts are `@brightline.example.com` and would bounce):
```
$env:CANDOR_LIVE_TO="you@gmail.com"
$env:CANDOR_TZ="Asia/Kolkata"
python assistant.py --live
```
A browser opens once for consent; `token.json` is saved. While the app is in "Testing", Google expires the token after 7 days; just consent again.

## What is real
| action | live behaviour |
|---|---|
| gmail.send | Gmail API send; copy appended to `candor_outbox/sent.jsonl` in the same shape as `messages.jsonl` |
| gmail.draft | saved in your Gmail Drafts |
| calendar.create_event | Google Calendar event (real attendees get invites, sample ones are skipped) |
| calendar.update_event | patches the real event (the planner loads your real calendar in live mode) |
| reminder.create | calendar event with popup + email reminder at the due time |
| slack.send_message | needs `SLACK_BOT_TOKEN`; set `CANDOR_SLACK_CHANNEL` to a real channel id (sample ids don't exist) |
| app.open | already real |

Never commit or zip `credentials.json`, `token.json`, `.env`.
