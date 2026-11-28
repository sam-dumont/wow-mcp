"""The shipper's release signature: what the auto-update checks before it
replaces itself (shipper/wow_stats.py, check_update; security review
2026-11-20, finding 2).

A release signs one statement: its version and the sha256 and size of every
file the server hands the shipper (account.SHIPPER_FILES), as compact JSON.
The statement and its Ed25519 signature go in shipper/update-signature.json,
which the server serves inside its manifest. The shipper checks the signature
with the public key pinned in it (UPDATE_KEY) and takes only the files the
statement names.

    uv run --no-project python tools/release/shipper_sign.py keygen --out FILE
    uv run --no-project python tools/release/shipper_sign.py sign --key FILE [--pin]
    uv run --no-project python tools/release/shipper_sign.py verify

keygen  A new key: the private half (PKCS#8 PEM, what `openssl genpkey
        -algorithm ed25519` writes) to FILE, mode 0600; prints the public half.
sign    Signs the tree as it is now. Refuses a key whose public half isn't the
        pinned UPDATE_KEY, unless --pin, which first writes that public half
        into shipper/wow_stats.py: a self-hoster's own key (docs/self-host/
        upgrade.md, "Signing your own build"). The key never reaches stdout.
verify  The signature file matches the tree and the pinned key; exit 1 when not.

release.yml's sign job runs `sign` on the release commit with the project's
key (environment shipper-signing); the build checks `verify` before it pushes.
Standard library only: the Ed25519 arithmetic is the shipper's own (RFC 8032),
plus the signing half here.
"""

from __future__ import annotations

import argparse
import base64
import functools
import hashlib
import importlib.util
import json
import os
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHIPPER = ROOT / "shipper"
SIGNATURE = SHIPPER / "update-signature.json"
# As wowstats.account.SHIPPER_FILES (tests/test_shipper_signing.py keeps them equal).
FILES = ("wow_stats.py", "install-mac.sh", "wow-stats.plist", "install-windows.ps1", "config.example.toml", "LICENSE",
         "foreverdb_send.py")
KEY_LINE = re.compile(r'(?m)^UPDATE_KEY = "([0-9a-f]{64})"$')
VERSION_LINE = re.compile(r'(?m)^VERSION = "([^"]+)"$')
# PKCS#8 for an Ed25519 key: this DER prefix, then the 32-byte seed (RFC 8410).
PKCS8_PREFIX = bytes.fromhex("302e020100300506032b657004220420")
# The PEM armour, put together so the secrets scan (gitleaks' private-key
# rule) doesn't read this file as holding a key.
LABEL = "PRIVATE" + " KEY"
BEGIN, END = f"-----BEGIN {LABEL}-----", f"-----END {LABEL}-----"


class Refused(Exception):
    pass


@functools.cache
def shipper_module():
    spec = importlib.util.spec_from_file_location("wow_stats_for_signing", SHIPPER / "wow_stats.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def file_bytes(name: str, root: Path = ROOT) -> bytes:
    return (root / "LICENSE" if name == "LICENSE" else root / "shipper" / name).read_bytes()


def statement(root: Path = ROOT) -> str:
    """The text a release signs: compact JSON, keys sorted."""
    src = file_bytes("wow_stats.py", root).decode()
    m = VERSION_LINE.search(src)
    if not m:
        raise Refused("shipper/wow_stats.py has no VERSION line")
    files = {}
    for n in FILES:
        b = file_bytes(n, root)
        files[n] = {"sha256": hashlib.sha256(b).hexdigest(), "size": len(b)}
    return json.dumps({"files": files, "version": m.group(1)}, sort_keys=True, separators=(",", ":"))


def pinned(root: Path = ROOT) -> str:
    m = KEY_LINE.search(file_bytes("wow_stats.py", root).decode())
    if not m:
        raise Refused("shipper/wow_stats.py has no UPDATE_KEY line")
    return m.group(1)


# ------------------------------------------------------------------ keys


def read_seed(path: Path) -> bytes:
    """The 32-byte seed from a PKCS#8 PEM file, or from 64 hex characters."""
    text = path.read_text().strip()
    if re.fullmatch(r"[0-9a-fA-F]{64}", text):
        return bytes.fromhex(text)
    m = re.search(re.escape(BEGIN) + "(.+?)" + re.escape(END), text, re.S)
    if m:
        der = base64.b64decode("".join(m.group(1).split()))
        if len(der) == 48 and der[:16] == PKCS8_PREFIX:
            return der[16:]
    raise Refused(f"{path}: not an Ed25519 private key (PKCS#8 PEM or 64 hex characters)")


def _expand(seed: bytes) -> tuple[int, bytes]:
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(seed: bytes) -> bytes:
    w = shipper_module()
    return w._ed_compress(w._ed_mul(_expand(seed)[0], w._ED_G))


def sign_bytes(seed: bytes, msg: bytes) -> bytes:
    """RFC 8032 section 6's sign, on the shipper's curve arithmetic."""
    w = shipper_module()
    a, prefix = _expand(seed)
    pub = w._ed_compress(w._ed_mul(a, w._ED_G))
    r = w._ed_hash_q(prefix + msg)
    Rs = w._ed_compress(w._ed_mul(r, w._ED_G))
    s = (r + w._ed_hash_q(Rs + pub + msg) * a) % w._ED_Q
    return Rs + int.to_bytes(s, 32, "little")


# ------------------------------------------------------------------ commands


def keygen(out: Path) -> str:
    if out.exists():
        raise Refused(f"{out} exists: never overwrite a key")
    seed = secrets.token_bytes(32)
    pem = base64.encodebytes(PKCS8_PREFIX + seed).decode().strip()
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f"{BEGIN}\n{pem}\n{END}\n")
    return public_key(seed).hex()


def pin(pub: str, root: Path = ROOT) -> None:
    p = root / "shipper" / "wow_stats.py"
    src = p.read_text(encoding="utf-8")
    new, n = KEY_LINE.subn(f'UPDATE_KEY = "{pub}"', src, count=1)
    if n != 1:
        raise Refused("shipper/wow_stats.py has no UPDATE_KEY line")
    p.write_text(new, encoding="utf-8")


def sign(key: Path, do_pin: bool = False, root: Path = ROOT) -> dict:
    seed = read_seed(key)
    pub = public_key(seed).hex()
    if do_pin:
        pin(pub, root)
    elif pub != pinned(root):
        raise Refused("this key isn't the one shipper/wow_stats.py pins (UPDATE_KEY): --pin to pin it")
    text = statement(root)
    out = {"statement": text, "signature": base64.b64encode(sign_bytes(seed, text.encode())).decode()}
    (root / "shipper" / "update-signature.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    return json.loads(text)


def verify(root: Path = ROOT) -> dict:
    try:
        sig = json.loads((root / "shipper" / "update-signature.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Refused(f"no signature file ({e})") from None
    if not sig.get("statement"):
        raise Refused("shipper/update-signature.json is unsigned (a tree between releases)")
    if sig["statement"] != statement(root):
        raise Refused("the signed statement doesn't match the shipper files in this tree")
    w = shipper_module()
    try:
        raw = base64.b64decode(sig.get("signature") or "", validate=True)
    except ValueError:
        raw = b""
    if not w.ed25519_verify(bytes.fromhex(pinned(root)), sig["statement"].encode(), raw):
        raise Refused("the signature doesn't verify with the pinned UPDATE_KEY")
    return json.loads(sig["statement"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("--out", required=True, type=Path)
    s = sub.add_parser("sign")
    s.add_argument("--key", required=True, type=Path, help="the private key file (PKCS#8 PEM or hex)")
    s.add_argument("--pin", action="store_true", help="pin this key's public half in shipper/wow_stats.py first")
    sub.add_parser("verify")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "keygen":
            print(f"public key: {keygen(a.out)}")
        elif a.cmd == "sign":
            rel = sign(a.key, a.pin)
            print(f"signed {rel['version']}: shipper/update-signature.json")
        else:
            rel = verify()
            print(f"signature OK: {rel['version']}, {len(rel['files'])} files")
    except Refused as e:
        print(f"shipper_sign: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
