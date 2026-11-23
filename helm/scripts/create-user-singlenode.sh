#!/usr/bin/env bash
# Create a FASTDB user on a single-node deployment without using MailHog.
set -euo pipefail

NAMESPACE="${FASTDB_NAMESPACE:-fastdb-arbutus-dev}"

if [[ $EUID -eq 0 ]]; then
  echo "Error: run this script as your normal user, not with sudo." >&2
  exit 1
fi

for command_name in docker kubectl; do
  command -v "$command_name" >/dev/null || {
    echo "Error: $command_name is required." >&2
    exit 1
  }
done

docker info >/dev/null 2>&1 || {
  echo "Error: cannot access Docker as $(id -un)." >&2
  exit 1
}

kubectl get deployment/postgres --namespace "$NAMESPACE" >/dev/null || {
  echo "Error: FASTDB is not installed in namespace $NAMESPACE." >&2
  exit 1
}

# Use the same shell image as the running FASTDB deployment.
SHELL_IMAGE="$(
  kubectl get deployment/shell \
    --namespace "$NAMESPACE" \
    --output=jsonpath='{.spec.template.spec.containers[0].image}'
)"
if [[ -z "$SHELL_IMAGE" ]]; then
  echo "Error: could not determine the shell image in namespace $NAMESPACE." >&2
  exit 1
fi

docker image inspect "$SHELL_IMAGE" >/dev/null 2>&1 || {
  echo "Error: shell image not found in Docker: $SHELL_IMAGE" >&2
  echo "Pull or build this image before creating a user." >&2
  exit 1
}

read -r -p "Username [test_user]: " USERNAME
USERNAME="${USERNAME:-test_user}"
read -r -p "Display name [TESTUSER]: " DISPLAY_NAME
DISPLAY_NAME="${DISPLAY_NAME:-TESTUSER}"
read -r -p "Email [test_user@mailhog]: " EMAIL
EMAIL="${EMAIL:-test_user@mailhog}"

# Restrict the fields inserted into SQL to simple values.
# The password is never included in that SQL.
if [[ ! "$USERNAME" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  echo "Error: username may contain only letters, numbers, '.', '_' and '-'." >&2
  exit 1
fi
if [[ ! "$DISPLAY_NAME" =~ ^[A-Za-z0-9_.[:space:]-]+$ ]]; then
  echo "Error: display name contains unsupported characters." >&2
  exit 1
fi
if [[ ! "$EMAIL" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+$ ]]; then
  echo "Error: enter a simple email address without quotes or spaces." >&2
  exit 1
fi

if kubectl exec --namespace "$NAMESPACE" deployment/postgres -- \
  psql -U postgres -d fastdb -tAc \
    "SELECT 1 FROM authuser WHERE username = '$USERNAME'" |
  grep -Fxq 1; then
  echo "Error: user '$USERNAME' already exists." >&2
  exit 1
fi

# Never expose password-bearing commands if the script was invoked with
# tracing enabled (for example, with `bash -x`).
set +x
read -r -s -p "Password: " PASSWORD
echo
read -r -s -p "Confirm password: " PASSWORD_CONFIRMATION
echo
if (( ${#PASSWORD} < 4 )); then
  echo "Error: password must be at least 4 characters long." >&2
  exit 1
fi
if [[ "$PASSWORD" != "$PASSWORD_CONFIRMATION" ]]; then
  echo "Error: passwords do not match." >&2
  exit 1
fi

echo "Generating FASTDB authentication keys..."
INSERT_SQL="$({
  printf '%s\n' "$PASSWORD"
} | docker run --rm -i \
  --network none \
  --entrypoint python \
  "$SHELL_IMAGE" \
  -c '
import base64
import json
import secrets
import sys
import uuid

from Crypto.Cipher import AES
from Crypto.Hash import SHA256
from Crypto.Protocol.KDF import PBKDF2
from Crypto.PublicKey import RSA

username, email, display_name = sys.argv[1:]
password = sys.stdin.readline().rstrip("\n")
salt = secrets.token_bytes(16)
iv = secrets.token_bytes(12)
keypair = RSA.generate(4096)
pubkey = keypair.publickey().export_key("PEM").decode("ascii")
# Browser WebCrypto imports binary PKCS#8, not PEM text. The Python client
# accepts this format too. Match the browser password-reset encryption.
privkey = keypair.export_key(format="DER", pkcs=8)
aeskey = PBKDF2(password.encode("utf-8"), salt, 32, count=100000,
                hmac_hash_module=SHA256)
ciphertext, tag = AES.new(aeskey, AES.MODE_GCM, nonce=iv).encrypt_and_digest(privkey)
encoded = lambda value: base64.b64encode(value).decode("ascii")
privkeyjson = json.dumps({"privkey": encoded(ciphertext + tag),
                         "salt": encoded(salt), "iv": encoded(iv)})
# The fields were validated above; quote defensively as well.
quote = lambda value: chr(39) + str(value).replace(chr(39), chr(39) * 2) + chr(39)
values = ",".join(map(quote, [uuid.uuid4(), username, display_name, email, pubkey, privkeyjson]))
print("INSERT INTO authuser(id,username,displayname,email,pubkey,privkey) "
      + "VALUES(" + values + "::JSONB)")
' \
  "$USERNAME" \
  "$EMAIL" \
  "$DISPLAY_NAME")"
unset PASSWORD PASSWORD_CONFIRMATION

echo "Creating user '$USERNAME'..."
printf '%s;\n' "$INSERT_SQL" |
  kubectl exec -i --namespace "$NAMESPACE" deployment/postgres -- \
    psql -v ON_ERROR_STOP=1 -U postgres -d fastdb
unset INSERT_SQL

echo
echo "User '$USERNAME' is ready."
echo "Use the username and password you entered from any authorized FASTDB client."
echo "No client configuration or .fastdb.ini file was created by this script."
