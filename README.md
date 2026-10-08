# BoB-the-Builder

**Build vJailbreak from any git branch, on your own machine.**

Give BoB a component and a branch. It checks the branch out, builds the container image(s),
and, for `qcow2`, packages everything into a bootable vJailbreak appliance disk. Use it from
the command line (`build_system`) or from a small web dashboard that queues builds, streams
logs and serves the finished `.qcow2` for download.

```
build_system ui feature/my-branch            # one image
build_system controller main --push          # one image, also pushed to your quay.io
build_system qcow2 private/main/my-branch    # the full appliance disk image
```

---

## Contents

- [How it works](#how-it-works)
- [Repository layout](#repository-layout)
- [Set up a new build machine](#set-up-a-new-build-machine)
- [Updating](#updating)
- [Using it](#using-it)
- [Configuration (`bob.env`)](#configuration-bobenv)
- [What gets installed where](#what-gets-installed-where)
- [Adding a new component](#adding-a-new-component)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

---

## How it works

```
 browser ──► dashboard (FastAPI) ──► build_system ──► /opt/build/vjailbreak (vjailbreak-code checkout)
               queue · logs · DB          │               git reset --hard origin/<branch> → make <target>
                                          │
                                          ├──► local mirror registry 127.0.0.1:5051   (fast, used during the qcow2 build)
                                          ├──► quay.io/<your-namespace>/proj_*        (what the appliance pulls later)
                                          └──► packer + qemu/KVM ──► vjailbreak-image.qcow2 ──► dashboard /artifacts
```

**Single component** (`controller`, `ui`, `sdk`, `ai`): clean checkout of the branch →
`make <target>` with `REGISTRY=local TAG=<branch>-<sha>` → optionally push to your quay.io.

**`qcow2`** (7 steps):

1. Build all images (ui, sdk, ai, controller + v2v-helper).
2. Patch the checkout's `image_builder/scripts/download_images.sh` so it pulls
   `quay.io/<your-namespace>/proj_*` instead of `quay.io/platform9/vjailbreak-*`. Your dev tag
   was never pushed to `platform9`. The patch only exists in this build's working tree; the
   next build's `git reset --hard` throws it away.
3. Render the manifests baked into the image.
4. Push the images into the **local mirror**. During the build, containerd checks the mirror
   before quay.io, so the build doesn't wait for internet uploads.
5. Generate controller manifests (installs Go to `~/.local/go` if needed).
6. `packer build` → `vjailbreak_qcow2/vjailbreak-image.qcow2`. A `[QCOW2_READY]` line appears
   and the dashboard makes the file downloadable.
7. Push all images to your quay.io. This step is **mandatory** for qcow2: the appliance and the
   controller binary reference them there.

The detailed "why" behind each step lives in the code comments, especially
[`builder/cli.py`](builder/cli.py) and [`host-setup/40-local-registry.sh`](host-setup/40-local-registry.sh).

---

## Repository layout

```
BoB-the-Builder/
├── install.sh                  # deploy/update BoB on this machine (the only deploy script)
├── config/bob.env.example      # every setting, documented - copy to ~/bob_the_builder/bob.env
├── lib/config.sh               # shared by the shell scripts: loads bob.env
│
├── host-setup/                 # one-time: turn a fresh Ubuntu VM into a build machine
│   ├── setup-host.sh           #   runs the numbered steps in order
│   ├── 10-packages.sh          #   docker, packer, qemu/kvm, libnbd-dev, envsubst, python venv…
│   ├── 20-users-groups.sh      #   docker + kvm groups
│   ├── 30-vjailbreak-checkout.sh   # clone vjailbreak-code → /opt/build/vjailbreak
│   ├── 40-local-registry.sh    #   mirror registry + containerd hosts.toml + ctr wrapper
│   ├── 50-check-host.sh        #   read-only health check
│   └── README.md               #   machine requirements + the manual steps
│
├── builder/                    # the build logic → installed as `build_system`
│   ├── build_system            #   entry point
│   ├── cli.py                  #   argument parsing + design notes
│   ├── common/                 #   shared: config, shell, git, images, quay
│   └── components/             #   one folder per thing you can build
│       ├── controller/build.py #     make vjail-controller → controller + v2v-helper
│       ├── ui/build.py         #     make ui
│       ├── sdk/build.py        #     make build-vpwned
│       ├── ai/build.py         #     make vjailbreak-ai
│       └── qcow2/              #     the 7-step appliance build
│           ├── build.py        #       orchestration
│           ├── download_images.py  #   the download_images.sh patch
│           ├── mirror.py       #       local mirror publish
│           ├── manifests.py    #       envsubst / controller manifests
│           └── go_toolchain.py #       Go bootstrap
│
├── dashboard/                  # web UI
│   ├── app.py                  #   FastAPI: queue, worker, timeouts, retention, routes
│   ├── requirements.txt
│   ├── templates/              #   base, index (start build), build (log), cleanup
│   └── static/style.css
│
├── systemd/vjb-build-dashboard.service.tmpl   # rendered by install.sh
└── tests/                      # pytest unit tests (no docker/network needed)
```

> The Dockerfiles and Makefile targets live in **vjailbreak-code**, not here. BoB only drives
> them: which target to run, what the images are called, and where they get pushed.

---

## Set up a new build machine

You need an **Ubuntu 24.04 x86_64 VM with KVM enabled**, ~8 vCPU / 16 GB RAM / 150 GB+ free
disk, `sudo`, and outbound access to GitHub, quay.io and go.dev. Details are in
[`host-setup/README.md`](host-setup/README.md).

```bash
# 0. Get this repo onto the machine
git clone https://github.com/sarika-pf9/BoB-the-Builder.git
cd BoB-the-Builder

# 1. Settings - set BOB_QUAY_NAMESPACE at minimum
mkdir -p ~/bob_the_builder
cp config/bob.env.example ~/bob_the_builder/bob.env
vi ~/bob_the_builder/bob.env

# 2. Manual prerequisites (once)
ssh -T git@github.com          # this host needs a key with read access to vjailbreak-code
docker login quay.io           # after step 3 installs docker; must be able to push to your namespace
#    …and create the 5 quay.io repos named in bob.env (proj_v2v, proj_controller, …)

# 3. Prepare the machine (packages, groups, checkout, mirror registry)
./host-setup/setup-host.sh
#    log out and back in (docker/kvm group membership), then:
./host-setup/50-check-host.sh  # everything should be [OK]

# 4. Deploy BoB
./install.sh
```

When `install.sh` finishes it prints the dashboard URL (`http://<vm-ip>/`), and
`build_system` is on your `PATH`.

> ⚠️ The dashboard has **no login**. Anyone who can reach the port can start builds. Only
> expose it to your internal network or VPN.

---

## Updating

All changes go through this repo. **Don't edit files under `~/bob_the_builder` on the
machine;** the next install overwrites them.

```bash
cd BoB-the-Builder
git pull
./install.sh
```

`install.sh` restarts the dashboard, which **kills any build started from the dashboard**.
It checks first and refuses if a build is queued or running. Wait for it, or use
`./install.sh --force`. Builds you started by hand in a terminal are not affected.

`cat ~/bob_the_builder/VERSION` shows which commit is deployed.

---

## Using it

### Command line

```bash
build_system <component> <branch> [--push]
build_system --help
```

| Component | Builds | Image(s) |
|---|---|---|
| `controller` | `make vjail-controller` | controller **and** v2v-helper |
| `ui` | `make ui` | ui |
| `sdk` | `make build-vpwned` | vpwned |
| `ai` | `make vjailbreak-ai` | ai |
| `qcow2` | everything above + packer | the appliance disk, plus all 5 images |

- Images are tagged `<branch-with-slashes-as-dashes>-<short-sha>`, e.g. `private-main-x-1a2b3c4`.
- `--push` pushes a single component's image(s) to `quay.io/<namespace>/proj_*`. `qcow2`
  always pushes.
- The output qcow2 is at `/opt/build/vjailbreak/vjailbreak_qcow2/vjailbreak-image.qcow2`.
  The next build deletes it, so copy it out (the dashboard does this for you).

### Dashboard

| Page | What you do there |
|---|---|
| `/` | Start a build (component + branch), see the queue and recent builds, get `docker pull` lines |
| `/builds/<id>` | Live log, status, **Cancel**, **Download** the qcow2 |
| `/cleanup` | Free disk: prune Docker, delete builds older than N hours |
| `/artifacts/latest.qcow2` | Always the newest successful qcow2. A plain URL, so you can paste it into PCD's *Add Image* |
| `/api/builds` | JSON list of recent builds |

- One build runs at a time; the rest wait in a FIFO queue.
- A build is killed if it prints nothing for **15 min** or runs longer than **90 min**.
- qcow2 artifacts and logs are kept for **6 h** (at most 5 qcow2 files).

Tune these with environment variables on the service (see the top of `dashboard/app.py`):
`BUILD_IDLE_TIMEOUT_SECONDS`, `BUILD_MAX_DURATION_SECONDS`, `QCOW2_RETENTION_HOURS`,
`KEEP_LAST_N_QCOW2`, `BUILD_HISTORY_HOURS`.

---

## Configuration (`bob.env`)

One file, `~/bob_the_builder/bob.env`, configures everything: the CLI, the dashboard (via
systemd `EnvironmentFile=`) and the shell scripts. A real environment variable with the same
name overrides the file. Never commit it; it's in `.gitignore`.

| Key | Default | Meaning |
|---|---|---|
| `BOB_QUAY_NAMESPACE` | — **required** | Your quay.io user/org. Images go to `quay.io/<this>/<repo>` |
| `BOB_QUAY_REPO_V2V_HELPER` | `proj_v2v` | quay repo for v2v-helper |
| `BOB_QUAY_REPO_CONTROLLER` | `proj_controller` | … controller |
| `BOB_QUAY_REPO_UI` | `proj_ui` | … ui |
| `BOB_QUAY_REPO_VPWNED` | `proj_sdk` | … vpwned (sdk) |
| `BOB_QUAY_REPO_AI` | `proj_ai` | … ai |
| `BOB_VJB_REPO_DIR` | `/opt/build/vjailbreak` | Build-only checkout. **Gets hard-reset and cleaned every build** |
| `BOB_VJB_REPO_URL` | `git@github.com:platform9/vjailbreak-code.git` | What `30-vjailbreak-checkout.sh` clones |
| `BOB_MIRROR_PORT` | `5051` | Local mirror registry port (loopback only) |
| `BOB_DASHBOARD_PORT` | `80` | Dashboard port |
| `BOB_GO_VERSION` | `1.23.4` | Go that `build_system` bootstraps if none is on `PATH` |

To install somewhere other than `~/bob_the_builder`, set `BOB_THE_BUILDER_DIR=/path` when
running `install.sh` and the host-setup scripts.

---

## What gets installed where

| Path on the machine | Put there by | Notes |
|---|---|---|
| `~/bob_the_builder/bob.env` | you | settings, never overwritten |
| `~/bob_the_builder/builder/` | `install.sh` | copy of `builder/` |
| `~/bob_the_builder/dashboard/` | `install.sh` | copy of `dashboard/` |
| `~/bob_the_builder/dashboard/venv/` | `install.sh` | Python env, reused across installs |
| `~/bob_the_builder/dashboard/data/` | dashboard | `dashboard.db`, `logs/`, `artifacts/*.qcow2`, never touched by install |
| `/usr/local/bin/build_system` | `install.sh` | symlink → `builder/build_system` |
| `/etc/systemd/system/vjb-build-dashboard.service` | `install.sh` | from `systemd/*.tmpl` |
| `/opt/build/vjailbreak/` | host-setup 30 | vjailbreak-code checkout |
| `vjb-local-mirror` container, `/opt/vjb-local-mirror-data` | host-setup 40 | `registry:2` on `127.0.0.1:5051` |
| `/etc/containerd/certs.d/quay.io/hosts.toml` | host-setup 40 | mirror first, real quay.io fallback |
| `/usr/local/bin/ctr` | host-setup 40 | wrapper adding `--hosts-dir` to `ctr … pull` |
| `~/.local/go`, `~/.cache/{go-dl,packer,k8s-migration-bin}` | `build_system` | caches that survive `git clean` |

---

## Adding a new component

1. Create `builder/components/<name>/__init__.py` (empty) and `build.py` with `NAME`,
   `DESCRIPTION`, `MAKE_TARGETS`, `IMAGES` and `extra_env(tag)`. Copy `ui/build.py` as a
   starting point.
2. Register it in `IMAGE_COMPONENTS` in `builder/components/__init__.py`.
3. If the qcow2 should include it, make sure `download_images.sh` in vjailbreak-code pulls
   it. The patch step fails loudly if the expected line is missing.
4. Add `"<name>"` to `COMPONENTS` and `QUAY_REPOS_BY_COMPONENT` in `dashboard/app.py` so
   it appears in the dashboard.
5. Add a test in `tests/test_components.py`, then `git pull && ./install.sh` on the machine.

---

## Troubleshooting

First step, always: `./host-setup/50-check-host.sh`

| Symptom | Fix |
|---|---|
| `BOB_QUAY_NAMESPACE is not set` | Fill it in `~/bob_the_builder/bob.env` |
| `local mirror registry … isn't reachable` | `./host-setup/setup-host.sh 40` (or `docker start vjb-local-mirror`) |
| `expected exactly 1 occurrence of 'quay.io/platform9/…'` | vjailbreak-code changed `download_images.sh`; update the `Image(...)` names in `builder/components/*/build.py` |
| qcow2 build fails at `docker push quay.io/…` | `docker login quay.io`, and check the 5 repos exist in your namespace |
| packer: `kvm` / `/dev/kvm` errors | Enable nested virtualization for the VM; check you're in the `kvm` group (re-login) |
| `permission denied … docker.sock` | You're not in `docker` group yet: log out and back in |
| Dashboard not loading | `sudo systemctl status vjb-build-dashboard`, `journalctl -u vjb-build-dashboard -e` |
| Build stuck in queue | Another build is running; cancel it on its page, or wait for the 15/90-min timeouts |
| Disk full | Dashboard → **Cleanup**, or `docker system prune -af`; caches in `~/.cache` are safe to delete |

---

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest -q
```

Tests stub every `docker`/`git`/`make` call, so they run anywhere (including macOS) without
Docker. CI-style checks:

```bash
bash -n install.sh lib/config.sh host-setup/*.sh
shellcheck -S warning install.sh lib/config.sh host-setup/*.sh
```

Guidelines:

- Keep the comments that explain *why* a step exists. Most encode a bug that already happened.
- `build_system` prints `[QCOW2_READY] <path>` and the dashboard watches for it. Change both
  together or not at all.
- Settings belong in `bob.env` and `config/bob.env.example`, not hardcoded.
