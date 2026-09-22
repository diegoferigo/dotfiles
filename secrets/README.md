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
target=secrets/home/.ssh/config.d/rai.conf.age
mkdir -p "$(dirname "$target")"
age --encrypt --armor --recipients-file secrets/recipients.txt \
    --output "$target.tmp" ~/.ssh/config.d/rai.conf
mv "$target.tmp" "$target"
```

Review and commit the resulting `secrets/home/...age` file. The plaintext remains
unchanged.

Apply or inspect encrypted files:

```bash
dotfiles secrets status
dotfiles secrets apply
```

`status` never decrypts content. `apply` decrypts and validates all sources
before changing files. It deploys each target atomically with mode `0600`,
records it immediately, preserves first-time collisions in
`~/.dotfiles_backup`, and refuses locally modified targets unless
`dotfiles secrets apply --force` is used. Deleting a ciphertext removes its
unchanged plaintext and restores any original backup.

Bootstrap and update only report that encrypted sources are available. They
never apply them automatically, so public-file operations cannot fail because of
a missing identity or malformed ciphertext. An interrupted explicit apply is
resumable.

The shared identity minimizes maintenance but creates a shared blast radius. If
any trusted machine is compromised:

1. Generate a replacement identity.
2. Replace `secrets/recipients.txt`.
3. Re-encrypt every source.
4. Replace the identity on every remaining trusted machine.
