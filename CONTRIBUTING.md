# Contributing

Thanks for helping improve telegram-native!

## Setup

```sh
git clone https://github.com/JNHFlow21/telegram-native && cd telegram-native
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Tests

```sh
.venv/bin/python -m unittest discover -s tests
```

Tests are offline and never touch a real Telegram account. Please keep it that
way: new commands need unit tests with mocked clients, and write commands must
stay preview-by-default behind `--execute`.

## Pull requests

- One focused change per PR, with tests.
- Keep the `tg.v1` JSON envelope stable; document new fields in USAGE.md.
- Never commit session files, api_hash values of your own app, bot tokens, or
  real chat data.
