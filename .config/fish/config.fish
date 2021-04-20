# Add the local bin folder to the PATH
set -gp PATH $HOME/.local/bin

# Add the dotfiles bin folder to the PATH
set -gp PATH $HOME/.dotfiles/share/bin

# Disable greetings
set -gx fish_greeting

# Enable starship
if type -q starship
    # Requires fish > 3.1.0
    if test -n $FISH_VERSION -a (echo $FISH_VERSION | tr -d .) -gt 310
        starship init fish | source
        # kill the right prompt __conda_add_prompt
        function __conda_add_prompt; end
    end
end

# Load oh-my-fish/plugin-bang-bang (after starship)
if test -f ~/.config/fish/completions/key_bindings.fish
    source ~/.config/fish/completions/key_bindings.fish
end

# Alias
alias config='git --git-dir=$HOME/.dotfiles --work-tree=$HOME'

# Go to ~ after login
cd ~
