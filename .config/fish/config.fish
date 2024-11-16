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

# Initialize zoxide
type --query zoxide && zoxide init fish | source

# Exa
type --query eza && abbr --add ll eza -l
type --query eza && abbr --add la eza -la
type --query eza && abbr --add lt eza -T
type --query eza && abbr --add l eza

# Configure fzf.
type --query eza && set --export fzf_preview_dir_cmd eza -l --all --color=always
# TODO: the following does not work
# type --query delta && set --export fzf_git_log_opts --preview='git show {1} | delta'

# Source pixi completion.
type --query pixi && pixi completion -s fish | source

# Go to ~ after login
cd ~
