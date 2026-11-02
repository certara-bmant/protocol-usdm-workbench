# Publishing to GitHub

Target repository: **https://github.com/certara-bmant/protocol-usdm-workbench**

## Before you push: decide what goes in

The folder contains material that may not belong in a shared repository. Check each item first, especially if the repo is or could become **public**:

| Path | Content | Recommendation |
|---|---|---|
| `inputs/219659_protocol.pdf` | Astellas/GSK protocol (anonymised, publicly posted) | OK in a **private** repo; confirm before making it public |
| `inputs/MK-1654-005_protocol.pdf` | Merck protocol (posted with its NCT record) | Same as above |
| `inputs/CTX101-001_USDM_v4.json`, `examples/CTX101_*` | Certara dummy study | Internal; fine in a private Certara repo |
| `protocol_to_usdm/data/bc_library_seed.json` | BCs harvested from CTX101 (CDISC content) | CDISC Library terms apply to production use |
| Everything else | Our code, template and docs | No licence file yet; add one, or keep the repo private |

To leave the PDFs and JSON inputs out, uncomment the two `inputs/` lines in `.gitignore` **before** step 3. `tests/regression.py` skips any protocol that isn't present.

Recommended: create the repo as **Private** under your account or the Certara organisation.

## 1. One-time setup (Windows)

1. **Install Git for Windows** from https://git-scm.com/download/win. Keep the defaults; they include Git Credential Manager, which handles GitHub sign-in.
2. **Open a terminal in the project folder.** Open *Git Bash* or *PowerShell* and run:
   ```
   cd "C:\Users\bmant\OneDrive - Certara\claude\protocol-usdm-workbench"
   ```
3. **Tell Git who you are** (once per machine):
   ```
   git config --global user.name  "Ben Mant"
   git config --global user.email "ben.mant@certara.com"
   ```

## 2. Create the empty repository on GitHub

1. Go to https://github.com/new.
2. Set **Owner** to `certara-bmant` and **Repository name** to `protocol-usdm-workbench`.
3. Choose **Private**.
4. **Do not** tick "Add a README", ".gitignore" or "license". The folder already has them, and an empty repo avoids a merge on the first push.
5. Click **Create repository**.

*If the repository already exists and already has commits (e.g. a README created on GitHub), use step 3b instead of 3a.*

## 3a. First push (empty GitHub repo)

```
git init -b main
git add .
git status                      # check the list: no __pycache__, nothing you didn't intend
git commit -m "Protocol -> USDM v4 workbench POC: extractor, Excel template, builder, validator, web UI"
git remote add origin https://github.com/certara-bmant/protocol-usdm-workbench.git
git push -u origin main
```

On the first push a browser window opens for GitHub sign-in (Git Credential Manager). If your organisation enforces SSO, authorise the Certara organisation when prompted.

## 3b. First push (GitHub repo already has commits)

```
git init -b main
git add .
git commit -m "Protocol -> USDM v4 workbench POC"
git remote add origin https://github.com/certara-bmant/protocol-usdm-workbench.git
git pull origin main --allow-unrelated-histories    # merges the README/licence created on GitHub
#   if README.md conflicts: keep ours ->  git checkout --ours README.md && git add README.md && git commit
git push -u origin main
```

## Alternative: GitHub CLI

If you have `gh` installed (https://cli.github.com) and signed in with `gh auth login`:

```
cd "C:\Users\bmant\OneDrive - Certara\claude\protocol-usdm-workbench"
git init -b main
git add .
git commit -m "Protocol -> USDM v4 workbench POC"
gh repo create certara-bmant/protocol-usdm-workbench --private --source . --remote origin --push
```

## Later updates

```
git add .
git commit -m "Describe the change"
git push
```

Run the checks before committing extractor changes:

```
python tests/test_basic.py
python tests/regression.py
python tests/roundtrip.py inputs/CTX101-001_USDM_v4.json
```

## Notes

- **OneDrive and Git:** keeping a Git repo inside a OneDrive-synced folder generally works. Pause OneDrive sync during large operations if you see "file in use" errors. Never edit the same repo from two machines through OneDrive at once; use `git pull` / `git push` instead.
- **Line endings:** `.gitattributes` keeps `run_ui.bat` with Windows line endings (required for `cmd.exe`) and the source files with LF.
- **Git-ignored local files:** `__pycache__/` folders and the Python environment created by `run_ui.bat` are excluded. The environment lives in `%LOCALAPPDATA%`, outside this folder anyway.
- **Repository size:** about 11 MB, mostly the two PDFs and example JSON. This is well within GitHub limits, and no Git LFS is needed.
