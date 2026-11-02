# Assets

`headline.png` is the Contexts > All history > Knowledge graph view, captured from a throwaway instance that holds only the synthetic sample workday. Capture and the LLM are off, so no real screen data or model output is involved. To refresh it from the repository root:

```bash
export CHRONICLE_HOME=/tmp/chr-headline CHRONICLE_DISABLE_CAPTURE=1 CHRONICLE_LLM=off
rm -rf "$CHRONICLE_HOME"
chronicle demo
python -c "from chronicle import db; db.set_setting('onboarded', '1')"
chronicle serve --port 3060 &
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --hide-scrollbars \
  --user-data-dir="$CHRONICLE_HOME/chrome" --window-size=1400,830 --virtual-time-budget=10000 \
  --screenshot=docs/assets/headline.png "http://127.0.0.1:3060/#contexts/all/graph"
kill %1
```
