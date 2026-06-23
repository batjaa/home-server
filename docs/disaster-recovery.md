# Operator recovery

How to get from "fresh laptop" (or a stranger's laptop, in the panic case) to
a working `ansible-playbook` run against this repo. The host-side disaster
recovery (rebuilding andromon / tentomon themselves) lives in the README —
this doc is only about restoring **you, the operator**.

This repo is public, so the encrypted `host_vars/<host>/secret.yml` files
are **gitignored** — they exist only on this laptop and in 1Password.
Recovering requires both pieces:

1. The **vault password** (decrypts the files)
2. The **encrypted files themselves** (mirrored to 1Password as Document items)

Get both back and the rest is `git clone` + `brew install`.

## Where everything lives

| Thing | 1Password location | Local location |
|---|---|---|
| Vault password | `Private` → "Homeserver Ansible Vault Key" (password field) | Keychain `ansible-vault-password` / account `batjaa` (fallback) |
| `host_vars/andromon/secret.yml` | `Private` → "Homeserver Vault — andromon" (document) | `host_vars/andromon/secret.yml` |
| `host_vars/tentomon/secret.yml` | `Private` → "Homeserver Vault — tentomon" (document) | `host_vars/tentomon/secret.yml` |
| `host_vars/wormmon/secret.yml` | `Private` → "Homeserver Vault — wormmon" (document) | `host_vars/wormmon/secret.yml` |

`pass.sh` reads the password (1P first, Keychain fallback). `vault.sh`
keeps the encrypted files in sync.

## Day-to-day: editing a secret

Always use the wrapper — it pushes to 1P on save so 1P stays current:

```bash
./vault.sh edit host_vars/andromon/secret.yml
```

After editing manually (e.g. with `ansible-vault edit` directly), push by hand:

```bash
./vault.sh push andromon       # one host
./vault.sh push                # all hosts
./vault.sh status              # check drift
```

## Fresh-laptop bootstrap

1. **Install the tools**
   ```bash
   brew install ansible
   brew install --cask 1password 1password-cli
   ```
2. **Sign into 1Password** in the desktop app, then enable
   *Settings → Developer → Integrate with 1Password CLI* so `op` can use
   biometric unlock.
3. **Verify `op` reads the vault password**
   ```bash
   op read "op://Private/Homeserver Ansible Vault Key/password" | wc -c
   ```
   Should print 19 (18 chars + newline). If it prints 0 or errors, the
   item title, vault, or field changed — fix the path in `pass.sh`.
4. **Clone the repo**
   ```bash
   git clone git@github.com:<you>/home-server.git
   cd home-server
   ```
   If your SSH key is also gone, see "If your SSH key is gone too" below.
5. **Pull the encrypted vault files from 1Password**
   ```bash
   ./vault.sh pull
   ./vault.sh status            # all three hosts should report "in sync"
   ```
6. **End-to-end smoke test**
   ```bash
   ansible-galaxy install -r requirements.yml
   ansible -m debug -a "var=pihole_password" tentomon | grep SUCCESS
   ```
   `SUCCESS` means Ansible read the vault file, decrypted it via `pass.sh`
   → `op`, and pulled a value out. Don't print the actual value.

That's it. You're back.

## If your SSH key is gone too

Without your SSH private key you can't reach the hosts to apply changes,
even with the vault password in hand.

- **andromon** — connect through pikvm (`kvm.batjaa.site`), log in at the
  console with the user password from vault (`ansible -m debug -a "var=password" andromon`),
  drop a fresh public key into `~/.ssh/authorized_keys`.
- **tentomon** — physical access (HDMI + keyboard, or another SSH session
  from an already-authorised host) to do the same.

Generate the new key on the new laptop *before* doing the above:
```bash
ssh-keygen -t ed25519 -C "batjaa@<new-host>"
pbcopy < ~/.ssh/id_ed25519.pub
```

Consider also storing `~/.ssh/id_ed25519` (the private key) as a document
attachment on a 1Password item. Then losing the laptop only costs you a
rotation, not a console trip.

## Worst case: 1Password account is gone too

If you lose your 1Password master password **and** the Secret Key (the
68-character recovery code generated when you signed up), the vault password
becomes unrecoverable from 1Password — and the encrypted `secret.yml`
files in this repo become unreadable.

Mitigations, in order of paranoia:

1. **Today**: print the 1Password Emergency Kit (Settings → Account → Emergency Kit),
   write your master password on it, put it in a fireproof safe or safe deposit box.
2. **Belt-and-braces**: separately print the vault password itself on a slip
   of paper, store somewhere different from the Emergency Kit. One-string
   recovery, completely offline.
3. **Family bus-factor**: set up a trusted person as your 1Password emergency
   contact, or share the "Homeserver Ansible Vault Key" item with a shared
   vault they have access to.

## Rotating the vault password

If you ever need to roll the vault password (suspected leak, paranoia,
periodic hygiene):

```bash
# Generate a new password and store it in 1Password
NEW=$(op item create --category=password --generate-password --title="Homeserver Ansible Vault Key NEW" --vault=Private --format=json | jq -r '.fields[] | select(.purpose=="PASSWORD") | .value')

# Re-encrypt every secret.yml with the new password
for f in host_vars/*/secret.yml; do
  ansible-vault rekey --new-vault-password-file=<(printf '%s' "$NEW") "$f"
done

# Swap the live 1P item to the new value, retire the staging item
op item edit "Homeserver Ansible Vault Key" --vault Private "password=$NEW"
op item delete "Homeserver Ansible Vault Key NEW" --vault Private
unset NEW

# Push the re-encrypted files up so the 1P document items match
./vault.sh push

# Update Keychain fallback too
security delete-generic-password -a batjaa -l ansible-vault-password
security add-generic-password -a batjaa -s ansible-vault-password -w  # paste from 1P
```

Commit the re-encrypted `secret.yml` files. They look different in the diff
but `git diff` shows only ciphertext, not the change in payload — that's
expected.
