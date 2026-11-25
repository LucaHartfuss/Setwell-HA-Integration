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
  status=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $CI_LESE_TOKEN" \
    "https://api.github.com/repos/$1") || true
  case $status in
    200) ;;
    301) fehler "Repo $1 umgezogen, Tabelle nachziehen (tools/einheiten.txt, HTTP 301)" ;;
    *) fehler "CI_LESE_TOKEN ungueltig oder abgelaufen (HTTP $status) ($RUNBOOK)" ;;
  esac
}
url() {
  # $1 = owner/name, $2 = privat|oeffentlich
  if [ "$2" = privat ]; then
    printf 'https://x-access-token:%s@github.com/%s.git' "$CI_LESE_TOKEN" "$1"
  else
    printf 'https://github.com/%s.git' "$1"
  fi
}
ref_for() {
  # Ausserhalb jedes Repos ausfuehren: actions/checkout hinterlegt im Checkout einen extraheader mit dem
  # GITHUB_TOKEN des eigenen Repos, der sonst die Token-URL uebersteuert ("Repository not found").
  # Exaktes Muster refs/heads/<branch> (ein nacktes Muster matcht per Suffix, z. B. "ci-pipeline" auf
  # refs/heads/feat/ci-pipeline). Exit 2 = kein Treffer -> develop; jeder andere Fehler bricht ab.
  local rc=0
  git -C / ls-remote --exit-code --heads "$(url "$1" "$2")" "refs/heads/$BRANCH" >/dev/null 2>&1 || rc=$?
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
  git clone -q --depth 1 --branch "$ref" "$(url "$1" "$2")" "$3"
  # Token nicht in $3/.git/config stehen lassen.
  git -C "$3" remote set-url origin "https://github.com/$1.git"
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
