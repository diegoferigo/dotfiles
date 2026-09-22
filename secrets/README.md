# Encrypted dotfiles

## Layout

Files below `secrets/home/` are armored age ciphertext. Their destination is
derived from the path:

```text
secrets/home/<path>.age -> ~/<path>
```

The shared identity metadata is tracked as regular dotfiles:

```text
~/.config/dotfiles/age/identity.txt.age
~/.config/dotfiles/age/recipients.txt
```

The private identity is only unlocked into a temporary mode `0600` file. A
legacy plaintext `identity.txt` remains supported when the encrypted wrapper is
absent.

## Initialize

```bash
dotfiles secrets init
dotfiles git commit -m "Add encrypted age identity"
dotfiles git push
```

Store the generated high-entropy passphrase in a password manager. New machines
receive the encrypted identity through normal bootstrap and update.

## Add or update a secret

```bash
dotfiles secrets encrypt ~/.ssh/config.d/rai.conf
dotfiles git diff --cached
dotfiles git commit -m "Add encrypted RAI SSH configuration"
dotfiles git push
```

The command derives and stages
`secrets/home/.ssh/config.d/rai.conf.age` directly in the bare repository. It
never creates `$HOME/secrets` or modifies the plaintext. The recipient must
match its committed version.

## Deploy

```bash
dotfiles secrets status
dotfiles secrets apply
dotfiles --update --with-secrets
```

Bootstrap and update leave secrets untouched by default. `apply` prompts for the
passphrase once, validates all ciphertext before mutation, deploys mode `0600`
files atomically, and records each completed target. It protects local edits,
backs up first-time collisions, restores backups when ciphertext is removed, and
is safe to resume after interruption. Use `apply --force` only to replace a
locally modified target.

## Resolve conflicts

Authoring refuses unresolved Git conflicts and existing staged changes to the
same ciphertext. Resolve unrelated paths first. If the ciphertext is the sole
remaining conflict, regenerate it from the selected plaintext:

```bash
dotfiles secrets encrypt --resolve ~/.ssh/config.d/rai.conf
dotfiles git rebase --continue
```

Encrypted bytes are never merged. Staging uses a locked temporary index, and
`dotfiles --update` refuses staged changes, so failures cannot silently replace
or discard an authored ciphertext.

## Change the passphrase

```bash
dotfiles secrets change-passphrase
dotfiles git commit -m "Change encrypted identity passphrase"
dotfiles git push
```

This re-wraps the same identity, so existing ciphertext does not change. The
encrypted identity is public, therefore a weak passphrase permits offline
guessing.

## Recover from identity compromise

Changing only the passphrase is insufficient if the identity or an unlocked
machine is compromised:

1. Generate a replacement identity.
2. Replace the encrypted identity and public recipient.
3. Re-encrypt every source.
4. Update every trusted machine.
