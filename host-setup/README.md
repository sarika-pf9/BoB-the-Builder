# host-setup — make a machine into a build host

Run once on a fresh **Ubuntu 24.04** VM, before `install.sh`. Every script is
safe to re-run.

```bash
./host-setup/setup-host.sh        # all steps, in order
./host-setup/setup-host.sh 40     # one step
./host-setup/50-check-host.sh     # read-only health check, any time
```

| Step | What it does |
|---|---|
| `10-packages.sh` | Installs Docker (Docker's apt repo), Packer (HashiCorp's apt repo), qemu/KVM tools, `libnbd-dev`, `envsubst`, `jq`, `rsync`, `python3-venv`. Checks KVM works. |
| `20-users-groups.sh` | Adds you to the `docker` and `kvm` groups. **Log out and back in afterwards.** |
| `30-vjailbreak-checkout.sh` | Clones `BOB_VJB_REPO_URL` to `BOB_VJB_REPO_DIR` (default `/opt/build/vjailbreak`). |
| `40-local-registry.sh` | Starts the local mirror registry (`vjb-local-mirror` container on `127.0.0.1:5051`), points containerd's `quay.io` pulls at it first, and installs the `/usr/local/bin/ctr` wrapper. See the script header for why. |
| `50-check-host.sh` | Checks everything above. Changes nothing. |

## Machine requirements

- Ubuntu 24.04, x86_64
- **KVM available inside the VM** (nested virtualization on) — packer builds the qcow2 in a qemu/KVM VM
- 8 vCPU, 16 GB RAM, **≥ 150 GB free disk** (images, Go/packer caches and qcow2 artifacts add up fast)
- Passwordless or interactive `sudo` for the build user
- Outbound access to github.com, quay.io, go.dev, apt repos

## Manual steps (the scripts can't do these for you)

1. **GitHub access** — before step 30, the host needs an SSH key with read access to
   `vjailbreak-code`:
   ```bash
   ssh-keygen -t ed25519 -C "bob@$(hostname)"   # add ~/.ssh/id_ed25519.pub to GitHub
   ssh -T git@github.com                         # should greet you by name
   ```
2. **quay.io repos** — in your quay.io namespace (`BOB_QUAY_NAMESPACE`) create the five
   repos named in `bob.env` (default `proj_v2v`, `proj_controller`, `proj_ui`, `proj_sdk`,
   `proj_ai`). Make them public if appliances built from them should pull without credentials.
3. **quay.io login** — as the build user:
   ```bash
   docker login quay.io
   ```
4. **Network exposure** — the dashboard has **no authentication**. Only allow port
   `BOB_DASHBOARD_PORT` from your internal network / VPN (security group or firewall).
