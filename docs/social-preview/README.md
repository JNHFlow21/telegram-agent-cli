# Social preview

The repository's link-preview card (`docs/social-preview.jpg`, 1280×640) is
generated, not hand-drawn, so it can be rebuilt any time.

![social preview](../social-preview.jpg)

## Rebuild

```sh
python3 docs/social-preview/render.py \
  --background docs/social-preview/background.jpg \
  --icon docs/social-preview/icon.svg \
  --name "Telegram|Agent CLI" \
  --out docs/social-preview.jpg
```

Needs Google Chrome (or Chromium) and ffmpeg. Upload the result in
GitHub → Settings → General → Social preview (GitHub limit: 1 MB).

## How it is made

- **Background** — an AI-generated abstract oil painting with no text,
  prompted roughly as: *"Abstract expressive oil painting filling the whole
  frame, wide 16:9: thick visible brushstrokes, canvas texture, soft painterly
  blending, diagonal sweeping strokes, editorial blog-cover aesthetic. No text,
  letters, logos, faces or objects. Calmer mid-tone center. Palette: sky blue
  and teal with accents of coral, pink and golden yellow."*
- **Icon** — the Telegram glyph from [Simple Icons](https://simpleicons.org)
  (CC0), rendered white. Telegram is a trademark of its owner; this project is
  unofficial.
- **Layout** — `render.py` lays the white icon and wordmark over the painting
  with HTML/CSS in headless Chrome, so the text is always spelled exactly.
  Everything stays inside the centered square because chat apps (iMessage,
  Slack, WeChat, Telegram) crop link previews to a square thumbnail.
