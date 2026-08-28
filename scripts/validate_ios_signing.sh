#!/usr/bin/env bash

set -euo pipefail

fail() {
  printf 'ios_signing: %s\n' "$1" >&2
  exit 1
}

if [[ "$(uname -s)" != "Darwin" ]]; then
  fail "ios_signing_requires_macos"
fi

required_variables=(
  PAST_PARTNER_IOS_CERTIFICATE_BASE64
  PAST_PARTNER_IOS_CERTIFICATE_PASSWORD
  PAST_PARTNER_IOS_PROVISIONING_PROFILE_BASE64
  PAST_PARTNER_IOS_KEYCHAIN_PASSWORD
  PAST_PARTNER_IOS_TEAM_ID
  PAST_PARTNER_IOS_BUNDLE_ID
  PAST_PARTNER_IOS_EXPORT_OPTIONS_BASE64
)
missing=()
for name in "${required_variables[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    missing+=("$name")
  fi
done
if (( ${#missing[@]} > 0 )); then
  fail "ios_signing_configuration_missing"
fi

command -v security >/dev/null 2>&1 || fail "ios_security_tool_unavailable"
command -v flutter >/dev/null 2>&1 || fail "ios_flutter_tool_unavailable"
command -v base64 >/dev/null 2>&1 || fail "ios_base64_tool_unavailable"

temporary_root="$(mktemp -d "${TMPDIR:-/tmp}/past-partner-ios-signing.XXXXXX")" || fail "ios_signing_temp_unavailable"
keychain="$temporary_root/past-partner.keychain-db"
certificate="$temporary_root/certificate.p12"
profile="$temporary_root/profile.mobileprovision"
profile_plist="$temporary_root/profile.plist"
export_options="$temporary_root/export-options.plist"
profile_target="${HOME}/Library/MobileDevice/Provisioning Profiles"
installed_profile=""
existing_profile_backup=""
original_keychains_file="$temporary_root/original-keychains.txt"

security list-keychains -d user >"$original_keychains_file" 2>/dev/null || true

cleanup() {
  set +e
  if [[ -n "$installed_profile" ]]; then
    if [[ -n "$existing_profile_backup" && -f "$existing_profile_backup" ]]; then
      cp "$existing_profile_backup" "$installed_profile"
    else
      rm -f "$installed_profile"
    fi
  fi
  if [[ -s "$original_keychains_file" ]]; then
    original_keychains=()
    while IFS= read -r keychain_entry; do
      keychain_entry="$(printf '%s' "$keychain_entry" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//')"
      [[ -n "$keychain_entry" ]] && original_keychains+=("$keychain_entry")
    done <"$original_keychains_file"
    if (( ${#original_keychains[@]} > 0 )); then
      security list-keychains -d user -s "${original_keychains[@]}" >/dev/null 2>&1
    fi
  fi
  if [[ -f "$keychain" ]]; then
    security delete-keychain "$keychain" >/dev/null 2>&1
  fi
  rm -rf "$temporary_root"
}
trap cleanup EXIT

decode_base64() {
  local encoded="$1"
  local destination="$2"
  if printf '%s' "$encoded" | base64 --decode >"$destination" 2>/dev/null; then
    return 0
  fi
  printf '%s' "$encoded" | base64 -D >"$destination" 2>/dev/null
}

decode_base64 "$PAST_PARTNER_IOS_CERTIFICATE_BASE64" "$certificate" || fail "ios_certificate_invalid"
decode_base64 "$PAST_PARTNER_IOS_PROVISIONING_PROFILE_BASE64" "$profile" || fail "ios_profile_invalid"
decode_base64 "$PAST_PARTNER_IOS_EXPORT_OPTIONS_BASE64" "$export_options" || fail "ios_export_options_invalid"

[[ -s "$certificate" ]] || fail "ios_certificate_invalid"
[[ -s "$profile" ]] || fail "ios_profile_invalid"
[[ -s "$export_options" ]] || fail "ios_export_options_invalid"

security create-keychain -p "$PAST_PARTNER_IOS_KEYCHAIN_PASSWORD" "$keychain" >/dev/null 2>&1 || fail "ios_keychain_create_failed"
security set-keychain-settings -lut 21600 "$keychain" >/dev/null 2>&1 || fail "ios_keychain_config_failed"
security unlock-keychain -p "$PAST_PARTNER_IOS_KEYCHAIN_PASSWORD" "$keychain" >/dev/null 2>&1 || fail "ios_keychain_unlock_failed"
security import "$certificate" -k "$keychain" -P "$PAST_PARTNER_IOS_CERTIFICATE_PASSWORD" -T /usr/bin/codesign >/dev/null 2>&1 || fail "ios_certificate_import_failed"
security set-key-partition-list -S apple-tool:,apple: -s -k "$PAST_PARTNER_IOS_KEYCHAIN_PASSWORD" "$keychain" >/dev/null 2>&1 || fail "ios_keychain_access_failed"
security list-keychains -d user -s "$keychain" >/dev/null 2>&1 || fail "ios_keychain_select_failed"

security cms -D -i "$profile" >"$profile_plist" 2>/dev/null || fail "ios_profile_invalid"
profile_uuid="$(/usr/libexec/PlistBuddy -c 'Print :UUID' "$profile_plist" 2>/dev/null || true)"
[[ "$profile_uuid" =~ ^[A-Fa-f0-9-]{36}$ ]] || fail "ios_profile_invalid"
profile_team_id="$(/usr/libexec/PlistBuddy -c 'Print :TeamIdentifier:0' "$profile_plist" 2>/dev/null || true)"
[[ "$profile_team_id" == "$PAST_PARTNER_IOS_TEAM_ID" ]] || fail "ios_profile_identity_mismatch"
profile_application_id="$(/usr/libexec/PlistBuddy -c 'Print :Entitlements:application-identifier' "$profile_plist" 2>/dev/null || true)"
[[ "$profile_application_id" == "$PAST_PARTNER_IOS_TEAM_ID.$PAST_PARTNER_IOS_BUNDLE_ID" ]] || fail "ios_profile_identity_mismatch"
mkdir -p "$profile_target" || fail "ios_profile_install_failed"
installed_profile="$profile_target/$profile_uuid.mobileprovision"
if [[ -f "$installed_profile" ]]; then
  existing_profile_backup="$temporary_root/existing-profile.mobileprovision"
  cp "$installed_profile" "$existing_profile_backup" || fail "ios_profile_install_failed"
fi
cp "$profile" "$installed_profile" || fail "ios_profile_install_failed"

plutil -lint "$export_options" >/dev/null 2>&1 || fail "ios_export_options_invalid"
project_bundle_id="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' ios/Runner/Info.plist 2>/dev/null || true)"
[[ "$project_bundle_id" == "$PAST_PARTNER_IOS_BUNDLE_ID" ]] || fail "ios_project_identity_mismatch"
export_team_id="$(/usr/libexec/PlistBuddy -c 'Print :teamID' "$export_options" 2>/dev/null || true)"
if [[ -n "$export_team_id" && "$export_team_id" != "$PAST_PARTNER_IOS_TEAM_ID" ]]; then
  fail "ios_export_identity_mismatch"
fi
export_profile_name="$(/usr/libexec/PlistBuddy -c "Print :provisioningProfiles:$PAST_PARTNER_IOS_BUNDLE_ID" "$export_options" 2>/dev/null || true)"
[[ -n "$export_profile_name" ]] || fail "ios_export_identity_mismatch"
flutter build ipa --release --export-options-plist "$export_options" >/dev/null || fail "ios_signed_build_failed"
printf 'ios_signing: signed_build_complete\n'
