# Add the local bin folder to the PATH
set -x PATH $HOME/.local/bin:$PATH

# Add the dotfiles bin folder to the PATH
set -x PATH $HOME/.dotfiles/share/bin:$PATH

# Disable greetings
set -U fish_greeting

# Enable starship
if type -q starship
    # Requires fish > 3.1.0
    if test -n $FISH_VERSION -a (echo $FISH_VERSION | tr -d .) -gt 310
        starship init fish | source
    end
end

# Bootstrap fisher
if not functions -q fisher
    curl -sL https://git.io/fisher | source && fisher install jorgebucaran/fisher
    fisher update
end
