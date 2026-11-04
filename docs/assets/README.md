# Assets

`gifs/` and `screens/` show the app on the synthetic sample workday, never real screen data. They were recorded with Playwright's CDP screencast from a throwaway instance:

```bash
export CHRONICLE_HOME=/tmp/chronicle-assets CHRONICLE_DISABLE_JOBS=1 CHRONICLE_DISABLE_CAPTURE=1 CHRONICLE_DEMO=1
python -m uvicorn chronicle.main:app --port 3060
```

The time spent waiting on a model is cut from the GIFs. Everything else plays at real speed. The recording in `record.gif` is fed synthetic frames through `POST /add`, so it never captures a real screen.

GIFs are encoded with:

```bash
ffmpeg -i clip.mp4 -vf "fps=10,scale=1000:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=200:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" clip.gif
```
