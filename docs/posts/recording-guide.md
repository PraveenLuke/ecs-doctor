# Recording the demo GIF

## Prerequisites

```bash
brew install asciinema         # record terminal
pip install svg-term-cli       # convert to SVG (alternative to agg)
# OR
brew install agg               # Rust-based .cast → GIF converter
```

## Record the session

```bash
asciinema rec docs/demo.cast --overwrite
```

Inside the recording, run a realistic diagnosis. Use `--json` output to avoid waiting for real AWS calls, OR use a real cluster. The best demo is a real broken service:

```bash
# Inside the asciinema session:
ecs-doctor diagnose --cluster prod --service payments
# Let it run, then Ctrl+D to stop recording
```

## Convert to SVG (recommended for GitHub README)

```bash
# Using svg-term-cli (npm):
npx svg-term --in docs/demo.cast --out docs/demo.svg --window --no-optimize

# Using agg (produces GIF):
agg docs/demo.cast docs/demo.gif
```

## Tips for a good recording

- **Use a real broken service if you have one** — a real OOM kill or image pull failure looks authentic
- **Set terminal width to 100 columns** before recording: `export COLUMNS=100`
- **Slow down slightly** — type at a comfortable pace, pause 1-2s before results appear
- **Don't show credentials** — use `--profile` with a named profile, not env vars
- Aim for **20-30 seconds** total — long enough to show results, short enough to loop cleanly

## Upload to asciinema.org (optional, for sharing)

```bash
asciinema upload docs/demo.cast
```

This gives you a shareable URL you can add to the README and posts:
```markdown
[![asciicast](https://asciinema.org/a/YOUR_ID.svg)](https://asciinema.org/a/YOUR_ID)
```
