"""Offline installed wheel and sdist in fresh environments outside the checkout."""

import argparse
import json
import os
from pathlib import Path
import sys
import shutil
import subprocess
import venv
from qualify_distribution_d1a import command
from release_409_checks import digest, validate_local

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--build-python", type=Path, required=True)
    a = p.parse_args()
    a.manifest = a.manifest.resolve()
    m = json.loads(a.manifest.read_text())
    validate_local(m, a.manifest.parent, require_source=False, require_copy=False)
    out = a.output.absolute()
    if out.exists() or ROOT in out.resolve().parents:
        raise ValueError("Fresh environments outside checkout required")
    out.mkdir(parents=True, mode=0o700)
    from memory_os_v4.ops.provision import guard

    guard(out)
    sites = [s for s in sys.path if s and Path(s).name == "site-packages"]
    report = {
        "source_commit": m["source_commit"],
        "version": m["version"],
        "artifacts": m["artifacts"],
        "ok": False,
        "downloads": 0,
    }
    try:
        for kind, suffix in [("wheel", ".whl"), ("sdist", ".tar.gz")]:
            e = out / kind
            venv.EnvBuilder(with_pip=True, system_site_packages=False).create(e)
            site = next((e / "lib").glob("python*/site-packages"))
            (site / "qualified-dependencies.pth").write_text("\n".join(sites) + "\n")
            # Reuse the qualified runtime, without exposing the system closure.
            paths = json.loads(
                subprocess.check_output(
                    [
                        str(a.build_python),
                        "-B",
                        "-c",
                        "import wheel,importlib.metadata as m,json; from pathlib import Path; print(json.dumps([str(Path(wheel.__file__).parent),str(m.distribution('wheel')._path)]))",
                    ],
                    text=True,
                )
            )
            for source in paths:
                shutil.copytree(source, site / Path(source).name)
            home = out / (kind + "-home")
            home.mkdir(mode=0o700)
            env = {
                **os.environ,
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "PYTHONPATH": "",
                "PYTHONNOUSERSITE": "1",
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            artifact = next(x for x in m["artifacts"] if x["name"].endswith(suffix))
            f = a.manifest.parent / "artifacts" / artifact["name"]
            if digest(f) != artifact["sha256"]:
                raise ValueError("Artifact changed")
            command(
                [
                    str(e / "bin/python"),
                    "-B",
                    "-m",
                    "pip",
                    "--isolated",
                    "install",
                    "--no-index",
                    "--no-deps",
                    "--no-build-isolation",
                    str(f),
                ],
                out,
                env,
                out / (kind + "-install.log"),
                guard,
            )
            command(
                [str(e / "bin/python"), "-B", "-m", "pip", "check"],
                out,
                env,
                out / (kind + "-pip-check.log"),
                guard,
            )
            code = (
                "from memory_os_v4.readiness import build_identity; from memory_os_v4.ops.qualified_runtime import inspect_current; import memory_os_v4,json; b=build_identity(); assert b['identity_source']=='embedded_descriptor'; assert b['commit']=="
                + repr(m["source_commit"])
                + "; assert memory_os_v4.__version__=="
                + repr(m["version"])
                + "; assert inspect_current(extras=('ops','brief'))['ok']; print(json.dumps(b))"
            )
            command(
                [str(e / "bin/python"), "-B", "-c", code],
                out,
                env,
                out / (kind + "-identity.log"),
                guard,
            )
            report[kind + "_environment"] = str(e)
        report["ok"] = True
    finally:
        (out / "environments.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
