#!/usr/bin/env bash
# Prueft die SSH-Signatur eines Release-Tags (AP-04; das Tag-Ruleset prueft sie nicht, Probe AP-04-H1-Probe).
# Kanonisch in HomeAssistant_Dev_Root/tools/release/tag_pruefen.sh; identische Kopien in den oeffentlichen Repos
# (Add-on, Integration) unter scripts/ci/tag_pruefen.sh (Root-check.sh Schritt "copies" prueft das). Die privaten Repos
# pruefen keine Tag-Signatur (kein Ruleset im Free-Tarif).
#
# Aufruf im Checkout des Repos (fetch-depth 0): tag_pruefen.sh TAG SHA   (SHA = GITHUB_SHA des Laufs)
#   1. Tag mit der Endung -dryrun: nichts zu pruefen (Probelauf, nichts wird veroeffentlicht), Exit 0.
#   2. Tag und origin/main frisch holen (actions/checkout macht aus einem annotierten Tag sonst ein Tag ohne Objekt).
#   3. Schluesselliste .github/allowed_signers aus origin/main lesen, NICHT aus dem Tag: wer einen Tag setzen kann,
#      kann auch die Liste in dessen Commit aendern. Fehlt sie oder steht ein Platzhalter (SMARTHEAT-PLATZHALTER) in
#      einer Schluesselzeile, Exit 1: ein echtes Release geht erst, wenn der Nutzer den Signierschluessel eintraegt (H-1).
#   4. git tag -v mit dieser Liste (nur annotierte, signierte Tags; Schluessel aus einer fremden Liste scheitern).
#      Vorher muss der Name im Tag-Objekt (Kopfzeile "tag") genau TAG sein; refs/remotes/origin/main statt der
#      mehrdeutigen Kurzform, denn ein Tag oder Branch namens origin/main schluege den Remote-Zweig.
#   5. TAG^{commit} muss SHA sein: die Signatur gilt dem Commit, den der Lauf ausfuehrt.
# Jeder Fehler endet mit Exit 1 und einer ::error::-Zeile.
set -euo pipefail
TAG="${1:?Aufruf: tag_pruefen.sh TAG SHA}"
SHA="${2:?Aufruf: tag_pruefen.sh TAG SHA}"
fehler() { echo "::error::Tag $TAG: $*"; exit 1; }

case "$TAG" in
  *-dryrun) echo "Tag $TAG ist ein Probelauf (-dryrun): keine Signatur zu pruefen."; exit 0 ;;
esac

git fetch --force --quiet origin "refs/tags/$TAG:refs/tags/$TAG" || fehler "Tag nicht von origin zu holen"
git fetch --force --quiet origin "+refs/heads/main:refs/remotes/origin/main" || fehler "origin/main nicht zu holen"

# Die Signatur deckt die Kopfzeile "tag <name>" des Tag-Objekts, nicht den Ref-Namen: ein signierter Probelauf-Tag
# (-dryrun) liesse sich sonst unter einem echten Namen auf dasselbe Objekt setzen. Ein leichter Tag hat kein Objekt.
tag_name="$(git cat-file tag "refs/tags/$TAG" 2>/dev/null | sed -n '/^$/q;s/^tag //p')" \
  || fehler "kein annotierter Tag (kein Tag-Objekt mit Signatur)"
[ "$tag_name" = "$TAG" ] || fehler "Tag-Name im signierten Objekt ist '$tag_name', nicht '$TAG': Signatur gilt einem anderen Namen"

liste="$(mktemp)"
protokoll="$(mktemp)"
trap 'rm -f "$liste" "$protokoll"' EXIT
git show refs/remotes/origin/main:.github/allowed_signers > "$liste" 2>/dev/null \
  || fehler ".github/allowed_signers fehlt auf origin/main (Schluesselliste, H-1)"
schluesselzeilen="$(grep -v '^[[:space:]]*\(#\|$\)' "$liste" || true)"
[ -n "$schluesselzeilen" ] || fehler ".github/allowed_signers auf origin/main enthaelt keinen Schluessel (H-1)"
if grep -q 'SMARTHEAT-PLATZHALTER' <<< "$schluesselzeilen"; then
  fehler ".github/allowed_signers auf origin/main ist noch der Platzhalter SMARTHEAT-PLATZHALTER: Signierschluessel eintragen (H-1), bevor ein echtes Release moeglich ist"
fi

git -c "gpg.ssh.allowedSignersFile=$liste" verify-tag "refs/tags/$TAG" >/dev/null 2>"$protokoll" \
  || { cat "$protokoll" >&2; fehler "keine gueltige SSH-Signatur eines Schluessels aus .github/allowed_signers (origin/main)"; }

signiert="$(git rev-parse "refs/tags/$TAG^{commit}")"
[ "$signiert" = "$SHA" ] || fehler "signierter Tag zeigt auf $signiert, der Lauf fuehrt $SHA aus"
echo "Signatur von Tag $TAG gueltig (Schluessel aus origin/main), Commit $SHA."
