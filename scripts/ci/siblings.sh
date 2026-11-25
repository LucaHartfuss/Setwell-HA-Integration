#!/usr/bin/env bash
# Baut in CI das Dev-Root-Layout auf: Kern-Repo (tools/) + Nachbar-Repos, damit
# tools/contract_check.py und tools/release_gate.py wie lokal laufen.
# Kanonisch im Kern-Repo unter tools/ci/siblings.sh; identische Kopien liegen in den Unter-Repos (Add-on, Integration)
# unter scripts/ci/siblings.sh (Root-check.sh Schritt "copies" prueft das). Der Server ist Ordner des Kern-Repos und
# braucht keine Kopie.
#
# Aufruf: KERN_REPO=<owner/name des Kern-Repos> siblings.sh <dev-ziel> <eigenes-repo owner/name> <eigener-checkout-pfad>
#         <branch> [nachbar-ordner...]
# Owner und Name jedes Repos stammen aus tools/einheiten.txt (Spalte github): die Nachbarn werden als Ordner der Tabelle
# genannt und unter ihrem eigenen owner/name geklont. Ist das eigene Repo das Kern-Repo, liegt die Tabelle im eigenen
# Checkout; sonst wird zuerst das Kern-Repo nach <dev-ziel> geklont und der eigene Checkout dort eingehaengt. Je Nachbar-Repo
# wird der gleichnamige Branch genommen, sonst develop. Oeffentliche Repos (Spalte sicht) werden ohne Token geklont,
# fuer private braucht es CI_LESE_TOKEN (vorab je Repo per API geprueft, ohne Weiterleitung).
set -euo pipefail
DEV="$1"; SELF="$2"; SELF_PATH="$3"; BRANCH="$4"; shift 4
RUNBOOK="docs/ci-cd-runbook.md, Abschnitt 'Token erneuern'"
fehler() { echo "::error::$*"; exit 1; }
[ -n "${KERN_REPO:-}" ] || fehler "KERN_REPO fehlt (owner/name des Kern-Repos, Workflow-Umgebung)"

pruefe_token() {
  # $1 = owner/name eines privaten Repos. Kein -L: eine Weiterleitung (301, Repo umgezogen) ist ein Fehler, kein Treffer.
  [ -n "${CI_LESE_TOKEN:-}" ] || fehler "CI_LESE_TOKEN fehlt als Repository-Secret ($RUNBOOK)"
  local status
  # Das Token kommt als Konfiguration ueber stdin (printf ist eingebaut), nie als Argument: Argumente sind in der
  # Prozessliste sichtbar.
  status=$(printf 'header = "Authorization: Bearer %s"\n' "$CI_LESE_TOKEN" |
    curl -s -o /dev/null -w '%{http_code}' -K - "https://api.github.com/repos/$1") || true
  case $status in
    200) ;;
    301) fehler "Repo $1 umgezogen, Tabelle nachziehen (tools/einheiten.txt, HTTP 301)" ;;
    *) fehler "CI_LESE_TOKEN ungueltig oder abgelaufen (HTTP $status) ($RUNBOOK)" ;;
  esac
}
url() {
  # $1 = owner/name; nie mit Zugangsdaten (die URL steht in Argumenten und in .git/config)
  printf 'https://github.com/%s.git' "$1"
}
git_zugriff() {
  # $1 = privat|oeffentlich, danach die git-Argumente. Immer ohne Weiterleitungen (ein umgezogenes oder umgelenktes Repo
  # darf das Token nicht an einen anderen Host tragen). Fuer private Repos geht das Token als Kopfzeile ueber die
  # Umgebung nur dieses einen git-Aufrufs (nicht exportiert, nicht in Argumenten, nicht in .git/config). Erst wird die
  # Kopfzeilenliste geleert: ein Checkout von actions/checkout hinterlegt dort das GITHUB_TOKEN des eigenen Repos, das
  # sonst zusaetzlich mitgeschickt wird.
  local sicht=$1 basic
  shift
  if [ "$sicht" = privat ]; then
    basic=$(printf 'x-access-token:%s' "$CI_LESE_TOKEN" | base64 | tr -d '\n')
    GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=http.https://github.com/.extraheader GIT_CONFIG_VALUE_0= \
      GIT_CONFIG_KEY_1=http.https://github.com/.extraheader GIT_CONFIG_VALUE_1="Authorization: Basic $basic" \
      git -c http.followRedirects=false "$@"
  else
    git -c http.followRedirects=false "$@"
  fi
}
ref_for() {
  # Ausserhalb jedes Repos ausfuehren: der extraheader von actions/checkout im eigenen Checkout soll nicht wirken.
  # Exaktes Muster refs/heads/<branch> (ein nacktes Muster matcht per Suffix, z. B. "ci-pipeline" auf
  # refs/heads/feat/ci-pipeline). Exit 2 = kein Treffer -> develop; jeder andere Fehler bricht ab.
  local rc=0
  git_zugriff "$2" -C / ls-remote --exit-code --heads "$(url "$1")" "refs/heads/$BRANCH" >/dev/null 2>&1 || rc=$?
  case $rc in
    0) echo "$BRANCH" ;;
    2) echo develop ;;
    *) echo "::error::git ls-remote fuer $1 fehlgeschlagen (Exit $rc), kein Rueckfall auf develop" >&2; return 1 ;;
  esac
}
clone() {
  # $1 = owner/name, $2 = privat|oeffentlich, $3 = Zielordner
  local ref
  ref=$(ref_for "$1" "$2")
  echo "$1 -> $ref"
  git_zugriff "$2" clone -q --depth 1 --branch "$ref" "$(url "$1")" "$3"
}

if [ "$SELF" = "$KERN_REPO" ]; then
  DEV="$SELF_PATH"
else
  # Das Kern-Repo ist privat; seine Tabelle ist die Quelle fuer alles Weitere.
  pruefe_token "$KERN_REPO"
  clone "$KERN_REPO" privat "$DEV"
fi
# Zeilen "ordner<TAB>owner/name" je Repo der Tabelle, ganz und nur die privaten
REPOS=$(python3 "$DEV/tools/einheiten.py" repos) || fehler "tools/einheiten.txt nicht lesbar"
PRIVAT=$(python3 "$DEV/tools/einheiten.py" repos --privat) || fehler "tools/einheiten.txt nicht lesbar"
github_von() { awk -F'\t' -v o="$1" '$1 == o { print $2; f = 1 } END { exit !f }' <<<"$REPOS"; }
ordner_von() { awk -F'\t' -v g="$1" '$2 == g { print $1; f = 1; exit } END { exit !f }' <<<"$REPOS"; }
ist_privat() { awk -F'\t' -v g="$1" '$2 == g { f = 1 } END { exit !f }' <<<"$PRIVAT"; }

[ "$(ordner_von "$KERN_REPO" || true)" = . ] || fehler "KERN_REPO $KERN_REPO ist nicht das Wurzel-Repo in tools/einheiten.txt"
if [ "$SELF" != "$KERN_REPO" ]; then
  eigener_ordner=$(ordner_von "$SELF") || fehler "$SELF steht nicht in tools/einheiten.txt"
  ln -s "$SELF_PATH" "$DEV/$eigener_ordner"
fi
ziele=()
for ordner in "$@"; do
  github=$(github_von "$ordner") || fehler "$ordner steht nicht in tools/einheiten.txt"
  [ "$github" = "$SELF" ] || [ "$github" = "$KERN_REPO" ] || ziele+=("$ordner $github")
done
# Erst alle privaten Repos pruefen, dann klonen: ein Fehler laesst keinen halben Dev-Root zurueck.
for ziel in ${ziele[@]+"${ziele[@]}"}; do
  if ist_privat "${ziel#* }"; then pruefe_token "${ziel#* }"; fi
done
for ziel in ${ziele[@]+"${ziele[@]}"}; do
  sicht=oeffentlich
  if ist_privat "${ziel#* }"; then sicht=privat; fi
  clone "${ziel#* }" "$sicht" "$DEV/${ziel%% *}"
done
echo "DEV_ROOT=$DEV" >> "${GITHUB_ENV:-/dev/null}"
echo "Dev-Root: $DEV"
