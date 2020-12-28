# From https://wiki.archlinux.org/index.php/fish#Make_su_launch_fish
function su
   command su --shell=/usr/bin/fish $argv
end
