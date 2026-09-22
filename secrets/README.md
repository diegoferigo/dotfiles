# Encrypted dotfiles

Files below `secrets/home/` are armored age ciphertext. Their destination is
derived from the path:

```text
secrets/home/<path>.age -> ~/<path>
```

Generate one shared identity outside this repository:

```bash
age-keygen -o identity.txt
chmod 600 identity.txt
age-keygen -y identity.txt > secrets/recipients.txt
```

Copy `identity.txt` to each trusted machine through a secure channel:

```bash
install -D -m 600 identity.txt \
    ~/.config/dotfiles/age/identity.txt
```

The identity must never be committed. `recipients.txt` contains only public
recipients and is safe to track. Multiple recipients can be added later without
changing the source layout.

Encrypt a file from the repository root:

```bash
dotfiles secrets encrypt ~/.ssh/config.d/rai.conf
```

The command refuses sources outside `$HOME`, writes the ciphertext atomically,
and leaves the plaintext untouched. Review and commit the resulting
`secrets/home/...age` file.

Apply or inspect encrypted files:

```bash
dotfiles secrets status
dotfiles secrets apply
```

`status` never decrypts content. `apply` decrypts all sources before changing
any target, writes mode `0600`, preserves first-time collisions in
`~/.dotfiles_backup`, and refuses locally modified targets unless
`dotfiles secrets apply --force` is used.

Bootstrap and update treat a missing `age` binary or identity as a pending,
non-fatal state. A malformed ciphertext, unsafe path, wrong identity, or write
failure is an error. Secret mutations roll back together, while an already
completed public update remains installed.

The shared identity minimizes maintenance but creates a shared blast radius. If
any trusted machine is compromised:

1. Generate a replacement identity.
2. Replace `secrets/recipients.txt`.
3. Re-encrypt every source.
4. Replace the identity on every remaining trusted machine.
