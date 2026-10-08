# Content Moderation

Use this category for policy and abuse decisions over user-generated content at volume.

## Submission format

```md
- [Name](URL) - Industry: one-sentence description of the Jev use case.
```

## Entries

- [Jev Moderation Bot](https://github.com/brainstormity/Jev-Moderation-Bot) - Community moderation: Discord bot that scores incoming messages for phishing, spam, and social engineering with Jev and drives a four-stage escalation ladder, injecting pardoned messages back into context as verified-safe precedent.
- [jev-spam-eval](https://github.com/bitnovus/jev-spam-eval) - Spam filtering: zero-shot spam classification with Jev `Boolean` questions, benchmarked against TF-IDF baselines.
- [mastra-jev-moderation](https://github.com/CodeAlive-AI/mastra-jev-moderation) - AI assistants: Mastra input processor that asks Jev a `Boolean` "must this message be blocked?" plus a category `Choice` in one request, aborting the turn at 0.7 and failing open behind a deadline and circuit breaker; in production it blocked 9/9 hostile and 0/49 real messages at ~0.4 s median, about 4× cheaper than an LLM moderator.
- [Jev Chat for Twitch](https://github.com/ethanplusai/jev-chat-for-twitch) `{type: extension}` - Live chat filtering: bring-your-own-key Chrome extension that reads a Twitch channel's chat over the anonymous IRC WebSocket, asks Jev one category `Choice` per message in batches of 20, and shows a second column of only the messages matching a chosen intent (helpful, questions, funny, feedback); about 504 input tokens per message, roughly $0.15 per hour on a 2-message-per-second chat and $0.76 per hour at 50 per second.
- [profanity-checker](https://github.com/4rays/profanity-checker) - Trust & safety: Cloudflare Worker that asks Jev `Noul` for literal profanity in text or usernames and a second `Noul` for phonetic or look-alike disguise (`a55h0le`, `mike_hunt`); the threshold, `max()` policy, JSON response, and OpenAPI schema live in Worker code and the endpoint is callable from other Workers via service bindings.
- [jev_antispam_bot](https://github.com/backmeupplz/jev_antispam_bot) - Telegram moderation: minimal grammY anti-spam bot that asks Jev about each message, with ten test files behind it.
- [jev-slop-guard](https://github.com/davertor/jev-slop-guard) `{type: extension}` - Social feed filtering: bring-your-own-key Chrome extension that asks Jev one `Choice` (`slop` / `not_slop`) per X and LinkedIn post as it scrolls into view, blurring and stamping anything at or above a user-set threshold (default 0.7) behind a "Show the post" override, with a three-request concurrency cap and one cached verdict per post so scrolling never blocks.
- [jev-screen-mcp](https://github.com/jiawei686/jev-screen-mcp) - Moderation: a single-tool MCP server that gates screen content through Jev, exposing `noul`, `choice`, and `score` as first-class question types alongside a deterministic mock mode and three tests.
- [Rot Guard](https://github.com/plusminushalf/rot-guard) `{type: extension}` - Digital wellbeing: bring-your-own-key Chrome extension that asks Jev a content-category `Choice`, a `Noul` on whether the page serves the user's written goals and a 0–4 waste-of-time `Score` for every YouTube video, X, Reddit or LinkedIn page and article, plus a `Choice` and `Noul` per X post and YouTube tile in batches of 10, then blocks or collapses in code with thresholds that tighten at 2, 10 and 20 minutes of engaged time, and grades a one-shot appeal with three `Noul`s that earn 15 minutes or lock the page until midnight.
