# Using this fork with Home Assistant's Nanoleaf integration

Home Assistant's built-in Nanoleaf integration pins the library it wants in its
`manifest.json`. `apply.py` copies that integration into `custom_components/`,
where Home Assistant loads it in preference to the built-in one, and rewrites
only the parts that choose the library. Everything else is copied byte for byte.

```bash
python3 apply.py                 # generate or refresh
python3 apply.py --check         # is it stale? exits 1 if so
python3 apply.py --revert        # remove it again
```

Only the standard library is used, so it runs inside the Home Assistant
container as-is.

## Why not just install the fork?

Because Home Assistant would undo it on the next restart. It re-checks each
integration's requirements at startup, and the pinned release does not match:

```python
>>> from homeassistant.util.package import is_installed
>>> is_installed("aionanoleaf2==1.0.2")   # with the fork's 1.1.0 installed
False                                      # -> Home Assistant reinstalls 1.0.2
```

Replacing the integration is what lets the requirement itself change. And
because the replacement's requirement is a URL, Home Assistant treats it as
unverifiable and re-resolves it on *every* start, so the pinned release never
wins it back:

```python
>>> is_installed("aionanoleaf2 @ https://github.com/.../master.tar.gz")
False                                      # -> handed to uv/pip each start
```

## Which Home Assistant you have matters

Home Assistant switched libraries in [core#8693294](https://github.com/home-assistant/core/commit/8693294ea), released in 2026.3:

| Home Assistant | Built-in integration requires | What the script rewrites |
| --- | --- | --- |
| 2026.3 and newer | `aionanoleaf2==1.0.2` | the manifest only |
| 2026.2 and older | `aionanoleaf==0.2.1` | the manifest, plus `aionanoleaf` → `aionanoleaf2` in 3 modules |

Either way the script works out which case it is and tells you:

```
Home Assistant 2026.2.3
  source: .../site-packages/homeassistant/components/nanoleaf
  target: /config/custom_components/nanoleaf
  library: aionanoleaf2 @ https://github.com/latetedemelon/aionanoleaf2/archive/refs/heads/master.tar.gz
  53 files, 3 with rewritten imports
    manifest requirements: ['aionanoleaf==0.2.1'] -> aionanoleaf2 @ .../master.tar.gz
    manifest loggers: aionanoleaf -> aionanoleaf2
    manifest version: 2026.2.3 (added; required for custom integrations)
    rewrote `aionanoleaf` -> `aionanoleaf2` in 3 module(s)
```

If you are on 2026.3 or newer, note that `aionanoleaf2==1.0.2` is the release
with the `KeyError: 'state'` crash on Essentials and Matter Wi-Fi devices, which
is what this fork fixes.

## Where to run it

The script needs to read Home Assistant's own copy of the integration, so run it
with the interpreter Home Assistant uses, or point it at the files.

**Home Assistant Core (venv)**

```bash
source /srv/homeassistant/bin/activate
python3 apply.py --config ~/.homeassistant
```

**Container**

```bash
docker cp apply.py homeassistant:/tmp/apply.py
docker exec homeassistant python3 /tmp/apply.py --config /config
```

**Home Assistant OS / Supervised**, from the SSH add-on with protection mode off:

```bash
docker exec homeassistant python3 /config/ha-patch/apply.py
```

Put `apply.py` somewhere under `/config` first so the container can see it.

If none of that is possible, pass the paths explicitly — no import needed:

```bash
python3 apply.py --config /config --ha-path /path/to/homeassistant/components/nanoleaf --version 2026.9.4
```

Restart Home Assistant afterwards. The first start is slower, because that is
when the fork gets installed.

## Choosing what to install

By default the script tracks the `master` branch, so a restart picks up whatever
is on it. For something that does not move under you, pin a tag:

```bash
python3 apply.py --ref v1.1.0
```

Or supply the requirement yourself — a wheel from a GitHub release installs
faster than a source archive, since nothing has to be built:

```bash
python3 apply.py --requirement \
  "aionanoleaf2 @ https://github.com/latetedemelon/aionanoleaf2/releases/download/v1.1.0/aionanoleaf2-1.1.0-py3-none-any.whl"
```

## Surviving upgrades

The copy is a snapshot of the integration as it was when you generated it, so a
Home Assistant upgrade leaves it behind — it keeps working against the old code
until something in Home Assistant's own API changes under it. `--check` compares
the recorded Home Assistant version and a fingerprint of the built-in
integration against what is installed now:

```
$ python3 apply.py --check
Stale: generated against Home Assistant 2026.1.0, now running 2026.2.3. Re-run this script.
$ echo $?
1
```

Re-running is idempotent and safe: it regenerates from the *current* built-in
integration, so you get Home Assistant's newest code with the library swapped
again. That is the part that makes this reappliable rather than a fork you have
to maintain.

To be told rather than having to remember, wire the check into Home Assistant:

```yaml
# configuration.yaml
shell_command:
  nanoleaf_patch_check: python3 /config/ha-patch/apply.py --check

automation:
  - alias: Warn when the Nanoleaf patch is stale
    trigger:
      - platform: homeassistant
        event: start
    action:
      - service: shell_command.nanoleaf_patch_check
        response_variable: result
      - condition: template
        value_template: "{{ result.returncode != 0 }}"
      - service: persistent_notification.create
        data:
          title: Nanoleaf patch needs reapplying
          message: "Run: docker exec homeassistant python3 /config/ha-patch/apply.py"
```

You could have the automation run `apply.py` itself and then restart, but a
restart loop on a bad upgrade is unpleasant; being notified and re-running by
hand is the safer default.

## Things to know

- Home Assistant logs `We found a custom integration nanoleaf which has not been
  tested by Home Assistant` on every start. That is expected, and is the price
  of overriding a built-in integration.
- The custom copy shadows the built-in one completely. `--revert` deletes it and
  you are back to stock on the next restart.
- The script refuses to overwrite or delete a `custom_components/nanoleaf` it did
  not create, unless you pass `--force`. It records what it did in
  `.aionanoleaf2-fork.json` inside the generated directory.
- Because the requirement is a URL, `uv` runs on every start for this
  integration. Installing the source archive took about 4.5 seconds here; a
  wheel URL is quicker.
- Translations are included: they ship inside the installed Home Assistant
  package (40 languages) even though they are not in Home Assistant's git repo,
  and the whole directory is copied.

## What was verified

Against a real Home Assistant 2026.2.3 install with the fork installed:

| Claim | Result |
| --- | --- |
| `async_get_integration("nanoleaf")` resolves to the custom copy | `is_built_in: False`, loaded from `custom_components/nanoleaf/` |
| The patched requirement is what Home Assistant sees | `['aionanoleaf2 @ https://.../master.tar.gz']` |
| Every platform loads from the copy | `light`, `button`, `event`, `config_flow`, `diagnostics` |
| The integration's modules import against the fork | all 10 modules, no errors |
| The fork covers what the integration uses | 28 members referenced, none missing |
| An unrelated domain is unaffected | `sun` still `is_built_in: True` |
| The requirement string installs | 1.1.0, in 4.5s, no `git` binary needed |
| Re-running changes nothing | byte-identical output |
| A manifest version Home Assistant would reject | refused before writing, with a message |
