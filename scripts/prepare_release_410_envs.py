"""Offline semantic wheel and sdist environments outside the checkout (4.1.0, ``.pth`` reuse).

No network: ``pip --no-index --no-deps``; the qualified semantic runtime is exposed through a
``.pth`` file whose paths and versions are attested in the report. The lexical environment is
never built here: it is the venv of the lexical installation (N3), attested and referenced.
"""

import argparse
import json
import os
from pathlib import Path
import sys
import shutil
import subprocess
import venv
from qualify_distribution_d1a import command
from release_410_checks import artifact, digest, refuse_forbidden, validate_local

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = (
    "from memory_os_v4.readiness import build_identity; "
    "from memory_os_v4.ops.qualified_runtime import inspect_current; "
    "import memory_os_v4, json, sys, importlib.metadata as md, importlib.util as u; from pathlib import Path; "
    "b = build_identity(); assert b['identity_source'] == 'embedded_descriptor'; "
    "assert b['commit'] == {commit!r}; assert memory_os_v4.__version__ == {version!r}; "
    "r = inspect_current(extras=('ops', 'brief'), profile={profile!r}); assert r['ok'], r; "
    "p = Path(memory_os_v4.__file__).resolve(); "
    "assert 'site-packages' in p.parts and Path(sys.prefix).resolve() in p.parents; "
    "d = json.loads(md.distribution('memory-os-ai').read_text('direct_url.json')); "
    "print(json.dumps({{'identity': b, 'package_path': str(p), "
    "'artifact_sha256': d['archive_info']['hashes']['sha256'], "
    "'ml_find_spec': {{n: (u.find_spec(n) is not None) for n in ('torch', 'sentence_transformers', "
    "'transformers', 'faiss')}}, "
    "'versions': {{x.metadata['Name']: x.version for x in md.distributions()}}}}))"
)


def attest(python, m, profile, out, env, log, guard):
    """Installed identity of one environment: commit, version, runtime of ``profile``, paths, versions."""
    code = IDENTITY.format(commit=m["source_commit"], version=m["version"], profile=profile)
    command([str(python), "-B", "-c", code], out, env, log, guard)
    return json.loads(next(line for line in reversed(log.read_text().splitlines()) if line.startswith("{")))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--build-python", type=Path, required=True)
    p.add_argument("--lexical-environment", type=Path, required=True,
                   help="venv of the lexical installation (N3); attested, never modified")
    a = p.parse_args()
    for given in (a.manifest, a.output, a.build_python, a.lexical_environment):
        refuse_forbidden(given)  # lexical then resolved, before any read
    a.manifest = a.manifest.resolve()
    m = json.loads(a.manifest.read_text())
    validate_local(m, a.manifest.parent, require_source=False, require_copy=False)
    out = a.output.absolute()
    if out.exists() or ROOT in out.resolve().parents:
        raise ValueError("Fresh environments outside checkout required")
    lexical = a.lexical_environment.absolute()
    if ROOT in lexical.resolve().parents or not (lexical / "bin/python").is_file():
        raise ValueError("Lexical environment missing or inside checkout")
    out.mkdir(parents=True, mode=0o700)
    from memory_os_v4.ops.provision import guard

    guard(out)
    sites = [s for s in sys.path if s and Path(s).name == "site-packages"]
    report = {
        "source_commit": m["source_commit"],
        "version": m["version"],
        "run_tag": m["run_tag"],
        "artifacts": m["artifacts"],
        "ok": False,
        "downloads": 0,
        "semantic_variant": "pth",
        "reused_dependency_paths": sites,
        "environments": {},
    }
    try:
        for kind, suffix in [("wheel", ".whl"), ("sdist", ".tar.gz")]:
            e = out / kind
            venv.EnvBuilder(with_pip=True, system_site_packages=False).create(e)
            site = next((e / "lib").glob("python*/site-packages"))
            (site / "qualified-dependencies.pth").write_text("\n".join(sites) + "\n")
            # Reuse the qualified runtime, without exposing the system closure.
            paths = json.loads(subprocess.check_output([
                str(a.build_python), "-B", "-c",
                "import wheel,importlib.metadata as m,json; from pathlib import Path; "
                "print(json.dumps([str(Path(wheel.__file__).parent),str(m.distribution('wheel')._path)]))"], text=True))
            for source in paths:
                shutil.copytree(source, site / Path(source).name)
            home = out / (kind + "-home")
            home.mkdir(mode=0o700)
            env = {**os.environ, "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"), "PYTHONPATH": "",
                   "PYTHONNOUSERSITE": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                   "PYTHONDONTWRITEBYTECODE": "1"}
            art = artifact(m, suffix)
            f = a.manifest.parent / "artifacts" / art["name"]
            if digest(f) != art["sha256"]:
                raise ValueError("Artifact changed")
            command([str(e / "bin/python"), "-B", "-m", "pip", "--isolated", "install", "--no-index", "--no-deps",
                     "--no-build-isolation", str(f)], out, env, out / (kind + "-install.log"), guard)
            command([str(e / "bin/python"), "-B", "-m", "pip", "check"], out, env, out / (kind + "-pip-check.log"), guard)
            row = attest(e / "bin/python", m, "semantic", out, env, out / (kind + "-identity.log"), guard)
            if row["artifact_sha256"] != art["sha256"]:
                raise ValueError("Installed payload is not the sealed artifact: " + kind)
            report[kind + "_environment"] = str(e)
            report["environments"][kind] = {"profile": "semantic", "path": str(e), **row}
        # Lexical: the installed, ML-free venv of N3. Read-only attestation, private HOME.
        home = out / "lexical-home"
        home.mkdir(mode=0o700)
        env = {k: os.environ[k] for k in ("PATH", "LANG") if k in os.environ}
        env.update(HOME=str(home), PYTHONPATH="", PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
        row = attest(lexical / "bin/python", m, "lexical", out, env, out / "lexical-identity.log", guard)
        if row["artifact_sha256"] != artifact(m, ".whl")["sha256"] or any(row["ml_find_spec"].values()):
            raise ValueError("Lexical environment is not the sealed wheel without ML")
        report["lexical_environment"] = str(lexical)
        report["environments"]["lexical"] = {"profile": "lexical", "path": str(lexical), **row}
        report["ok"] = True
    finally:
        (out / "environments.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
