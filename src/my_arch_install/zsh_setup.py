"""Curated modern Zsh stack packages and configuration templates."""

from __future__ import annotations


ZSH_STACK_PACKAGES: tuple[str, ...] = (
    "zsh",
    "git",
    "starship",
    "zoxide",
    "fzf",
    "atuin",
    "eza",
    "bat",
    "fd",
    "ripgrep",
    "git-delta",
    "pkgfile",
    "ttf-jetbrains-mono-nerd",
)

ZSHRC_CONTENT = r"""export EDITOR="nvim"
export VISUAL="nvim"
export PAGER="less"
export MANPAGER="sh -c 'col -bx | bat -l man -p'"
export BAT_THEME="Dracula"
export ATUIN_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/atuin"

(( $+commands[dircolors] )) && eval "$(dircolors -b)"

HISTFILE="${XDG_STATE_HOME:-$HOME/.local/state}/zsh/history"
[[ -d "${HISTFILE:h}" ]] || mkdir -p "${HISTFILE:h}"
HISTSIZE=50000
SAVEHIST=50000
setopt APPEND_HISTORY SHARE_HISTORY HIST_IGNORE_SPACE
setopt HIST_IGNORE_ALL_DUPS HIST_SAVE_NO_DUPS HIST_IGNORE_DUPS HIST_FIND_NO_DUPS

setopt AUTO_CD AUTO_PUSHD PUSHD_IGNORE_DUPS PUSHD_SILENT
setopt INTERACTIVE_COMMENTS NO_BEEP

autoload -Uz edit-command-line
zle -N edit-command-line
bindkey '^X^E' edit-command-line

ZINIT_HOME="${XDG_DATA_HOME:-${HOME}/.local/share}/zinit/zinit.git"
if [[ ! -d "$ZINIT_HOME" ]]; then
  mkdir -p "$(dirname "$ZINIT_HOME")"
  git clone https://github.com/zdharma-continuum/zinit.git "$ZINIT_HOME"
fi
source "${ZINIT_HOME}/zinit.zsh"

zinit ice blockf atpull'zinit creinstall -q .'
zinit light zsh-users/zsh-completions

autoload -Uz compinit
ZCOMPDUMP="${XDG_CACHE_HOME:-$HOME/.cache}/zsh/.zcompdump"
[[ -d "${ZCOMPDUMP:h}" ]] || mkdir -p "${ZCOMPDUMP:h}"
if [[ -f "$ZCOMPDUMP" && "$(date +'%Y-%m-%d')" == "$(date -r "$ZCOMPDUMP" +'%Y-%m-%d' 2>/dev/null)" ]]; then
  compinit -C -d "$ZCOMPDUMP"
else
  compinit -d "$ZCOMPDUMP"
fi
zinit cdreplay -q

export FZF_DEFAULT_COMMAND='fd --type f --hidden --follow --exclude .git'
export FZF_CTRL_T_COMMAND="$FZF_DEFAULT_COMMAND"
export FZF_ALT_C_COMMAND='fd --type d --hidden --follow --exclude .git'
export FZF_DEFAULT_OPTS="--height=60% --layout=reverse --border=rounded --info=inline"
export FZF_CTRL_T_OPTS="--preview 'bat --color=always --style=numbers --line-range=:300 {} 2>/dev/null || eza -la --icons=always --color=always {}'"
export FZF_ALT_C_OPTS="--preview 'eza --tree --level=2 --icons=always --color=always {} | head -200'"

_fzf_compgen_path() { fd --hidden --follow --exclude .git . "$1"; }
_fzf_compgen_dir()  { fd --type d --hidden --follow --exclude .git . "$1"; }

if [[ -r /usr/share/fzf/completion.zsh && -r /usr/share/fzf/key-bindings.zsh ]]; then
  source /usr/share/fzf/completion.zsh
  source /usr/share/fzf/key-bindings.zsh
elif (( $+commands[fzf] )); then
  source <(fzf --zsh)
fi

zinit light Aloxaf/fzf-tab

zstyle ':completion:*' matcher-list 'm:{a-zA-Z}={A-Za-z}' 'r:|[._-]=* r:|=*' 'l:|=* r:|=*'
zstyle ':completion:*' list-colors "${(s.:.)LS_COLORS}"
zstyle ':completion:*' menu no
zstyle ':completion:*' group-name ''
zstyle ':completion:*:descriptions' format '[%d]'
zstyle ':completion:*:git-checkout:*' sort false
zstyle ':fzf-tab:*' switch-group '<' '>'
zstyle ':fzf-tab:*' fzf-flags --height=60% --layout=reverse --border=rounded

zstyle ':fzf-tab:complete:(cd|z|__zoxide_z):*' fzf-preview 'eza -1 --icons=always --color=always --group-directories-first $realpath'
zstyle ':fzf-tab:complete:(ls|eza|bat|cat|nvim|vim|nano|hx|code|rm|cp|mv):*' fzf-preview \
  'if [[ -d $realpath ]]; then eza -la --icons=always --color=always --group-directories-first $realpath; elif [[ -f $realpath ]]; then bat --color=always --style=numbers --line-range=:200 $realpath; fi'
zstyle ':fzf-tab:complete:(kill|ps):argument-rest' fzf-preview \
  '[[ $group == "[process ID]" ]] && ps -p $word -o pid,ppid,pcpu,pmem,comm,args'

ZSH_AUTOSUGGEST_BUFFER_MAX_SIZE=40
ZSH_AUTOSUGGEST_USE_ASYNC=1
ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE='fg=8'
zinit ice wait"0a" lucid atload'_zsh_autosuggest_start; bindkey "^ " autosuggest-accept'
zinit light zsh-users/zsh-autosuggestions

zinit ice wait"0b" lucid atload'
  bindkey "^[[A" history-substring-search-up
  bindkey "^[[B" history-substring-search-down
  [[ -n "${terminfo[kcuu1]}" ]] && bindkey "${terminfo[kcuu1]}" history-substring-search-up
  [[ -n "${terminfo[kcud1]}" ]] && bindkey "${terminfo[kcud1]}" history-substring-search-down
'
zinit light zsh-users/zsh-history-substring-search

zinit ice wait"0b" lucid
zinit snippet OMZP::sudo
zinit ice wait"0b" lucid
zinit snippet OMZP::extract

zinit ice wait"0c" lucid
zinit light zdharma-continuum/fast-syntax-highlighting

[[ -r /usr/share/doc/pkgfile/command-not-found.zsh ]] && source /usr/share/doc/pkgfile/command-not-found.zsh

(( ${+aliases[zi]} )) && unalias zi
(( $+commands[zoxide] )) && eval "$(zoxide init zsh)"
(( $+commands[atuin] )) && eval "$(atuin init zsh --disable-up-arrow)"
ZSH_AUTOSUGGEST_STRATEGY=(history completion)
(( $+commands[starship] )) && eval "$(starship init zsh)"

alias ls='eza --icons=always --group-directories-first'
alias ll='eza -la --icons=always --group-directories-first --git --header'
alias lt='eza --tree --level=2 --icons=always --group-directories-first'
alias cat='bat --paging=never'
alias cls='clear'
alias zshconfig='${EDITOR:-nvim} ~/.zshrc'
alias reload='exec zsh'
"""

STARSHIP_TOML_CONTENT = r'''"$schema" = 'https://starship.rs/config-schema.json'

add_newline = true

format = """
$username\
$hostname\
$directory\
$git_branch\
$git_state\
$git_status\
$docker_context\
$python\
$nodejs\
$rust\
$golang\
$bun\
$deno\
$java\
$c\
$cpp\
$cmd_duration\
$jobs\
$status\
$line_break\
$character"""

[character]
success_symbol = '[❯](bold green)'
error_symbol = '[❯](bold red)'
vimcmd_symbol = '[❮](bold yellow)'

[directory]
style = 'bold cyan'
truncation_length = 4
truncate_to_repo = true
read_only = ' 󰌾'

[git_branch]
symbol = ' '
style = 'bold purple'
format = '[$symbol$branch(:$remote_branch)]($style) '

[git_status]
style = 'bold yellow'
format = '([$all_status$ahead_behind]($style) )'

[cmd_duration]
min_time = 2000
style = 'yellow'
format = '[󱎫 $duration]($style) '

[status]
disabled = false
style = 'bold red'
format = '[\[$status\]]($style) '

[jobs]
symbol = ' '
style = 'bold blue'
format = '[$symbol$number]($style) '

[python]
symbol = ' '
format = '[$symbol$pyenv_prefix($version )(\($virtualenv\) )]($style)'

[nodejs]
symbol = ' '
format = '[$symbol($version )]($style)'

[rust]
symbol = ' '
format = '[$symbol($version )]($style)'

[golang]
symbol = ' '
format = '[$symbol($version )]($style)'

[bun]
symbol = ' '
format = '[$symbol($version )]($style)'

[deno]
symbol = ' '
format = '[$symbol($version )]($style)'

[java]
symbol = ' '
format = '[$symbol($version )]($style)'

[c]
symbol = ' '
format = '[$symbol($version )]($style)'

[cpp]
symbol = ' '
format = '[$symbol($version )]($style)'

[docker_context]
symbol = ' '
format = '[$symbol$context]($style) '

[package]
disabled = true
'''

ATUIN_CONFIG_CONTENT = """search_mode = "fuzzy"
filter_mode = "directory"
workspaces = true
style = "compact"
inline_height = 20
enter_accept = false
secrets_filter = true

[search]
filters = ["directory", "workspace", "global", "session", "host"]
"""

KONSOLE_RC_CONTENT = """[Desktop Entry]
DefaultProfile=Default.profile
"""

KONSOLE_PROFILE_CONTENT = """[Appearance]
Font=JetBrainsMono Nerd Font,11,-1,5,400,0,0,0,0,0

[General]
Name=Default
Parent=FALLBACK/
"""
