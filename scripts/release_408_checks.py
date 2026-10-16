"""Fail-closed 4.0.8 evidence: unchanged schema8, existing journal, installed S4."""

from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
from subprocess import SubprocessError
from release_copy_worker import QUERIES
from search_qualification import search_hit_counts


def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def evidence(m, directory, name):
    e = m["evidence"][name]
    p = Path(e["path"])
    require(not p.is_absolute() and ".." not in p.parts, "Unsafe evidence path")
    p = directory / p
    require(digest(p) == e["sha256"], "Evidence changed: " + name)
    return json.loads(p.read_text())


def verify_engine_version_only(root, commit, version):
    import subprocess

    baseline = "6182aa581626013e74f6c1987262171671baaf23"
    names = subprocess.check_output(
        ["git", "diff", "--name-only", baseline, commit, "--", "v4/src"],
        cwd=root,
        text=True,
    ).splitlines()
    require(
        names == ["v4/src/memory_os_v4/__init__.py"], "Engine changed beyond version"
    )
    before = subprocess.check_output(
        ["git", "show", baseline + ":" + names[0]], cwd=root, text=True
    )
    after = subprocess.check_output(
        ["git", "show", commit + ":" + names[0]], cwd=root, text=True
    )
    require(
        before.replace('__version__ = "4.0.7"', '__version__ = "' + version + '"')
        == after,
        "Engine version diff invalid",
    )


def validate_local(m, directory, *, require_copy=True, require_source=True, **kw):
    require(
        re.fullmatch(r"4\.0\.8", m["version"]) is not None,
        "Wrong RC version",
    )
    require(
        re.fullmatch(r"[0-9a-f]{40}", m["source_commit"]) is not None,
        "Complete source SHA required",
    )
    require(not m.get("open_blockers"), "Open blockers")
    root = Path(__file__).resolve().parents[1]
    verify_engine_version_only(root, m["source_commit"], m["version"])
    require(bool(m["qualification_tooling_sha256"]), "Empty tool inventory")
    for path, sha in m["qualification_tooling_sha256"].items():
        require(
            Path(path).name == path and digest(root / "scripts" / path) == sha,
            "Tool changed: " + path,
        )
    wanted = {
        f"memory_os_ai-{m['version']}-py3-none-any.whl",
        f"memory_os_ai-{m['version']}.tar.gz",
    }
    require(
        len(m["artifacts"]) == 2 and {a["name"] for a in m["artifacts"]} == wanted,
        "Artifact inventory",
    )
    for a in m["artifacts"]:
        p = directory / "artifacts" / a["name"]
        require(
            p.is_file() and digest(p) == a["sha256"] and p.stat().st_size == a["bytes"],
            "Artifact changed",
        )
    art = evidence(m, directory, "artifacts")
    require(
        art.get("ok") is True
        and art.get("artifact_checked") is True
        and art["source_commit"] == m["source_commit"]
        and art["identity"]["commit"] == m["source_commit"],
        "Installed artifact identity missing",
    )
    require(
        art["artifact_sha256"][
            "direct/"
            + next(a["name"] for a in m["artifacts"] if a["name"].endswith(".whl"))
        ]
        == m["artifacts"][0]["sha256"],
        "Wheel differs from qualification",
    )
    require(
        art["artifact_sha256"][
            "via-sdist/"
            + next(a["name"] for a in m["artifacts"] if a["name"].endswith(".tar.gz"))
        ]
        == m["artifacts"][1]["sha256"],
        "Sdist differs from qualification",
    )
    require(
        all(
            k in art["steps"]
            for k in (
                "direct_build",
                "sdist_build",
                "extra_markers",
                "install-0",
                "install-1",
                "smoke-0",
                "smoke-1",
                "quickstart-preview-0",
                "quickstart-preview-1",
            )
        ),
        "Installed smokes incomplete",
    )
    tw = directory / m["twine_evidence"]["path"]
    require(
        digest(tw) == m["twine_evidence"]["sha256"]
        and tw.read_text().count("PASSED") == 2
        and "FAILED" not in tw.read_text(),
        "Twine strict missing",
    )
    if require_source:
        full = evidence(m, directory, "full")
        require(
            full.get("ok") is True
            and full.get("tcp_qualified") is True
            and full.get("client_configs_unchanged") is True,
            "Full/TCP/isolation pending",
        )
        require(
            full["source_sha256"] == m["qualification_source_sha256"],
            "Full source mismatch",
        )
        commands = {x["name"]: x for x in full["commands"]}
        for name in ("v3", "v4"):
            c = commands[name]
            require(
                c["returncode"] == 0
                and c["counts"]["tests"] >= (700 if name == "v3" else 2000)
                and all(c["counts"][k] == 0 for k in ("errors", "failures", "skipped")),
                "Suite incomplete",
            )
        from qualify_405_test_isolation import EXPECTED_TCP

        tcp = commands["v4"]["tcp_evidence"]
        require(
            len(EXPECTED_TCP) == 32
            and tcp
            == {
                "ok": True,
                "expected_cases": 32,
                "observed_cases": 32,
                "missing": [],
                "unexpected": [],
                "unsuccessful": [],
            },
            "32 TCP exact names incomplete",
        )
        source = evidence(m, directory, "source")
        require(
            source.get("ok") is True
            and source["version"] == m["version"]
            and source["source_commit"] == m["source_commit"]
            and source["source_sha256"] == m["qualification_source_sha256"],
            "Source/semantic proof mismatch",
        )
        require(source.get("tcp_evidence") == tcp, "Source TCP manifest mismatch")
        for name, count in [
            ("source_tcp", 32),
            ("cached_semantic", 1),
            ("v3_cached", commands["v3"]["counts"]["tests"]),
        ]:
            require(
                source["checks"][name]
                == {"passed": count, "failed": 0, "errors": 0, "skipped": 0},
                "Source proof incomplete: " + name,
            )
    if require_copy:
        validate_copy(evidence(m, directory, "copy"), m)


def ordered_ids(search):
    return {
        q: row.get("ids") if isinstance(row, dict) else row for q, row in search.items()
    }


def validate_copy(p, m):
    require(
        p.get("ok") is True
        and p["version"] == m["version"]
        and p["baseline_version"] == "4.0.7"
        and p["source_commit"] == m["source_commit"]
        and p["artifacts"] == m["artifacts"]
        and p.get("production_modified") is False
        and p.get("source_sql_writes") is False,
        "Copy identity/outcome",
    )
    require(p["scripts_sha256"] == m["copy_scripts_sha256"], "Copy tooling mismatch")
    rows = p["workers"]
    require(
        set(rows) == {"baseline", "wheel", "sdist", "wheel-warm", "sdist-warm"},
        "Copy worker inventory",
    )
    base = rows["baseline"]
    ident = p["expected_instance_id"]
    for name, r in rows.items():
        search_hit_counts(
            r["search"], QUERIES, worker=name, reported=r["search_hit_counts"]
        )
        require(
            r.get("ok") is True
            and r["total"] == base["total"]
            and r["store_instance_id"] == ident
            and ordered_ids(r["search"]) == ordered_ids(base["search"])
            and r["encoding_profile"] == base["encoding_profile"]
            and r["cache_misses"] == 0
            and r["corpus_encoded_texts"] == 0
            and r["startup_seconds"] <= 120,
            "Worker mismatch: " + name,
        )
    # No migration or empty-journal exemption in 4.0.8.
    for name, r in rows.items():
        unchanged = r["schema_check"]
        require(r["version"] == ("4.0.7" if name == "baseline" else m["version"]), "Worker version mismatch")
        require(r["authority"] == base["authority"] and r["authority"]["schema_version"] == 8,
                "Authority differs from baseline")
        require(unchanged["ok"] is True and unchanged["from_schema"] == unchanged["to_schema"] == 8
                and unchanged["store_instance_id"] == ident and unchanged["tables_preserved"]
                and unchanged == base["schema_check"] and r["migration"] is None,
                "Schema8 exact inventory changed or migration attempted")
        require(r["journal"] == base["journal"] and r["journal"] is not None,
                "Journal history/generation changed")
    require(p["authority_identical"] is True and p["rankings_identical_between_artifacts_and_reopen"] is True
            and p["warm_encoded_texts"] == 0, "Copy equality flags missing")
    profile = base["encoding_profile"]
    require(
        profile["device"] == "mps"
        and profile["dtype"] == "native"
        and profile["batch_size"] == 32
        and profile["max_seq_length"] == 512
        and profile["parameter_devices"] == ["mps"]
        and profile.get("model_revision")
        and profile.get("encoder_source_sha256"),
        "Production profile not attested",
    )
    require(
        p["seconds"] <= 900 and p["search_identical_to_baseline"] is True,
        "Copy budget/rankings",
    )
    doctor = p["doctor"]
    caps = doctor["ready"]["capabilities"]
    ready = doctor["ready"]
    require(
        doctor["ok"] is True
        and ready["version"] == m["version"]
        and ready["commit"] == m["source_commit"]
        and ready["identity_source"] == "embedded_descriptor"
        and ready["encoding_profile"] == profile
        and caps["store"]["backend"] == "sqlite_s1"
        and caps["store"]["instance_id"] == ident
        and caps["work_state"] is True
        and caps["retrieval_ready"] is True
        and caps["projects"]["registry_enabled"] is False
        and caps["policies"]["write_mode"] == "read_only"
        and caps["policies"]["bm25_profile"] == "scoped_literal"
        and caps["policies"]["lexical_guard_k"] == 3
        and caps["runtime"]["ok"] is True
        and caps["brief"]["tokenizer"]["ready"] is True,
        "Private ready/doctor policy",
    )
    audit = caps.get("readiness_audit", {})
    require(audit.get("max_reuse_seconds") == 1.0 and audit.get("live_checks_every_request") is True
            and audit.get("untracked_memory_fault_detection_seconds") == 1.0, "Readiness cache policy missing")
    for name in ("wheel", "sdist"):
        s = p["s4"][name]
        require(
            s["backup"].get("ok") is True
            and s["backup"]["qualified"] is True
            and s["backup"]["source_sql_writes"] is False
            and s["backup"]["store_instance_id"] == ident
            and s["backup"]["verified"] is True
            and s["drill"].get("ok") is True
            and s["drill"].get("private_reopen_verified") is True
            and s["drill"].get("verified") is True
            and s["drill"].get("forget_reconciliation") == "complete"
            and s["drill"].get("restored_from_instance_id") == ident,
            "S4 backup/private reopening",
        )
        b, d = s['backup'], s['drill']
        service = d.get('server_check', {})
        require(d.get('level') == 'service' and d.get('archive') == b.get('archive')
                and d.get('archive_sha256') == b.get('archive_sha256') and b.get('archive_sha256')
                and service.get('match') is True and service.get('version') == m['version']
                and service.get('commit') == m['source_commit'] and service.get('corpus_reencodings') == 0
                and service.get('private_only') is True and service.get('production_modified') is False,
                "S4 must qualify this artifact/backup at service level")


def main(args):
    path = args.manifest
    directory = path.parent
    m = json.loads(path.read_text())
    try:
        options = [
            bool(args.record_source_proof),
            bool(args.record_copy_proof),
            bool(args.record_relay_proof),
            bool(args.record_full_proof),
            bool(args.record_gate_proof),
        ]
        require(
            sum(options) <= 1
            and not args.copy_review
            and not args.record_brief_source
            and not args.record_brief_exact,
            "Unsupported mixed proof",
        )
        if args.record_full_proof:
            full = json.loads(args.record_full_proof.read_text())
            require(
                full.get("ok") is True
                and full.get("tcp_qualified") is True
                and full.get("client_configs_unchanged") is True
                and full["source_sha256"] == m["qualification_source_sha256"],
                "Full proof mismatch",
            )
            target = directory / "full-proof.json"
            require(not target.exists(), "Full proof exists")
            target.write_bytes(args.record_full_proof.read_bytes())
            m["evidence"]["full"] = {"path": target.name, "sha256": digest(target)}
            m.setdefault("checks", {})["full"] = "passed"
            m["source_expected_v3_tests"] = next(
                c for c in full["commands"] if c["name"] == "v3"
            )["counts"]["tests"]
            m["release_ready"] = False
            path.write_text(json.dumps(m, indent=2) + "\n")
            return 0
        if args.record_gate_proof:
            g = json.loads(args.record_gate_proof.read_text())
            require(
                g["candidate_commit"] == m["source_commit"]
                and g.get("complete") is True
                and g.get("production_modified") is False
                and g.get("neural_calls") == 0
                and g["inputs_sha256"]
                == "70ab2c61172f5401ea12b68a2b3566e593c25689a42e8f9b2eac21d2a4d05de0",
                "RC collection mismatch",
            )
            import subprocess

            root = Path(__file__).resolve().parents[1]
            archive = subprocess.check_output(
                ["git", "archive", m["source_commit"], "v4/src", "pyproject.toml"],
                cwd=root,
            )
            require(
                hashlib.sha256(archive).hexdigest() == g["source_archive_sha256"],
                "RC collection archive differs",
            )
            require(
                g["binding"]["candidate_commit"] == m["source_commit"]
                and g["binding"]["inputs_sha256"] == g["inputs_sha256"],
                "RC binding differs",
            )
            score = g["scorer_result"]
            keys = {
                "all_40_pass",
                "unexpected_invalidations_0",
                "partial_success_0",
                "hard_safety_0",
                "legit_replacements_all",
                "collection_integrity",
            }
            require(
                set(score["gates"]) == keys
                and all(x is True for x in score["gates"].values()),
                "Incomplete gate inventory",
            )
            require(
                score["passed"] == 40
                and score["total"] == 40
                and score.get("verdict") is True
                and score.get("replay") is True
                and not score.get("collection_issues")
                and not score.get("failure_categories")
                and score["inputs_sha256_recomputed"] == g["inputs_sha256"]
                and all(score["gates"].values()),
                "RC gate failed",
            )
            target = directory / "gate-rc-proof.json"
            require(not target.exists(), "Gate proof exists")
            target.write_bytes(args.record_gate_proof.read_bytes())
            m["evidence"]["gate_rc"] = {"path": target.name, "sha256": digest(target)}
            m.setdefault("checks", {})["gate_b_rc"] = "passed"
            m["release_ready"] = False
            path.write_text(json.dumps(m, indent=2) + "\n")
            return 0
        if args.record_source_proof or args.record_copy_proof:
            name = "source" if args.record_source_proof else "copy"
            original = args.record_source_proof or args.record_copy_proof
            if name == "copy":
                validate_copy(json.loads(original.read_text()), m)
            target = directory / (name + "-proof.json")
            require(not target.exists(), "Proof already exists")
            target.write_bytes(original.read_bytes())
            m["evidence"][name] = {"path": target.name, "sha256": digest(target)}
            # Validate a source record before publishing its reference; no ready guard opened here.
            validate_local(m, directory, require_copy=False, require_source=True)
            m.setdefault("checks", {})["production_copy" if name == "copy" else "source"] = "passed"
            m["release_ready"] = False
            path.write_text(json.dumps(m, indent=2) + "\n")
            print("Proof recorded; guard remains closed. No publication.")
            return 0
        validate_local(m, directory)
        g = evidence(m, directory, "gate_rc")
        require(
            g["candidate_commit"] == m["source_commit"]
            and g["scorer_result"]["verdict"] is True,
            "RC gate transfer pending",
        )
        from verify_release_manifest import validate_relay

        proof_path = args.record_relay_proof or directory / "installed-relay-proof.json"
        proof = json.loads(proof_path.read_text())
        validate_relay(proof, m)
        require(
            all(
                c.get("local_status_during_outage") is True
                and c.get("handshake_version") == m["version"]
                for c in proof["cases"]
            ),
            "Local outage status missing",
        )
        if args.record_relay_proof:
            target = directory / "installed-relay-proof.json"
            require(not target.exists(), "Relay proof exists")
            target.write_bytes(proof_path.read_bytes())
            m.setdefault("checks", {})["installed_relays"] = "passed"
            m.update(release_ready=True, relay_proof_sha256=digest(target))
            path.write_text(json.dumps(m, indent=2) + "\n")
        else:
            require(
                m.get("release_ready") is True
                and digest(proof_path) == m["relay_proof_sha256"],
                "Not fully qualified",
            )
        print("RC fully qualified; no publication/switchover authorized.")
        return 0
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        AssertionError,
        SubprocessError,
    ) as exc:
        print("REFUS: " + str(exc))
        return 1
