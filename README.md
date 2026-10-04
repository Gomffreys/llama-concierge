# Llama Concierge

A small GUI for launching `llama-server` without hand-building a command
line every time. Pick a model, pick arguments from a dropdown, see the
exact command it'll run, save model+argument combos as Model Templates,
and it opens your browser to the chat UI automatically once the model has
finished loading.

## Download

Only get Llama Concierge from this repository:
**https://github.com/Gomffreys/llama-concierge**

- Grab `llama_concierge.py` and build it yourself (see below), or
- Download the prebuilt `.exe` from the [Releases page](https://github.com/Gomffreys/llama-concierge/releases).

Anywhere else claiming to host this isn't official.

## Build it (on Windows)

1. Copy `llama_concierge.py` onto the Windows machine — anywhere is fine
   for the build step.
2. Open a terminal there and install the packager (one-time, skip if
   already done):
   ```
   pip install pyinstaller
   ```
3. In that same terminal, in the folder containing `llama_concierge.py`,
   run:
   ```
   pyinstaller --onefile --noconsole --name "Llama Concierge" llama_concierge.py
   ```
4. That creates a `dist` folder containing `Llama Concierge.exe`. Put it
   wherever you like — your Desktop is the intended spot — it doesn't need
   to sit next to `llama-server.exe`; it finds that via the path in
   Settings instead.
5. Double-click it to run. A `llama_concierge_config.json` file will
   appear next to it the first time you use it — that's where your
   argument catalog, model templates, and settings live.

Re-run step 3 only if the source changes and you want to rebuild.

## Using it

- **Model**: the dropdown lists every `.gguf` file found under your
  configured models folder (default `C:\LM Models`). Models split into
  numbered shards (`model-00001-of-00003.gguf`) show up once, tagged
  "(multi-part model)" — llama.cpp finds the other shards itself.
  **Browse...** lets you point at a `.gguf` outside that folder for a
  one-off. **Rescan** re-reads the folder if you've added new models
  without restarting. **Settings...** is where you change the models
  folder, the path to `llama-server.exe`, and the port.
- **Add argument**: pick from the dropdown — a description of what it
  does appears underneath before you commit — then click "Add →" to
  drop it into the active list below. Context-size options from 8192 up
  to 65536 in steps of 8192 are seeded in from the start; "Custom context
  size..." at the bottom of the list asks for any other number and adds
  it as its own reusable entry.
- **Manage Arguments...**: where you teach the app new arguments or edit
  existing ones. Give it a label, the exact CLI text (e.g.
  `--flash-attn on`), and a plain-English description so you can tell
  what it does later without having to remember. Edit opens a small
  popup pre-filled with the current label/flag/description. Delete
  removes an argument from the catalog entirely (and from the active
  list and any templates using it).
- **Active arguments list**: what's currently selected, in the order
  it'll run. Move Up/Down to reorder, Remove to take one out.
- **Command preview**: always shows the literal command "Launch" will
  run, so you can eyeball it first.
- **Model Templates**: "Make Model Template" snapshots the currently
  selected model *and* the active argument list together under one name
  — e.g. a "Skyfall" template that restores both the model and its
  tuned flags in one click. "Load" restores both; "Delete" removes a
  template.
- **Launch llama-server**: runs `llama-server.exe -m <model> --port
  <port> <your active arguments>` in its own console window so you can
  watch it load. Once the server actually starts responding on that
  port, your browser opens automatically to the chat UI — no need to
  watch the console and guess when it's ready.
- **Open Models Folder / Open llama.cpp Folder**: quick shortcuts to
  the two folders you're most likely to need, via File Explorer.
- Whatever's active when you close the window is what auto-loads next
  time.

## Known limitations

- **No exclusivity checks**: nothing stops you activating two
  arguments that conflict with each other — that's on you.
- **Single model path only**: there's no multi-GPU-aware model
  validation beyond what llama-server itself reports — if a model
  doesn't fit your VRAM, you'll find out from llama-server's own error.

## License

MIT — see [LICENSE](LICENSE). Provided as-is; use at your own risk.
