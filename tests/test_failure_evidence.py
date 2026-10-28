"""Quantity proposals remain inert; every non-quantity gate still rejects."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import diagnostics as d
import review_failure as review
from shared import core, engine as e

class EvidenceTests(unittest.TestCase):
    def test_download_failure_retains_only_successful_public_inputs(self):
        conf={"repositories":{},"sources":[
            {"id":"one","url":"https://openai.com/one"},
            {"id":"two","url":"https://openai.com/two"}]}
        captured=[]
        def fetch(url):
            if url.endswith("/two"):raise e.BuildError("Download failed")
            return b"safe public input"
        with patch.object(core,"fetch",side_effect=fetch), self.assertRaises(e.BuildError):
            core.collect(conf,capture=lambda lock,files:captured.append((lock,files)))
        self.assertEqual(len(captured),1)
        lock,files=captured[0]
        self.assertEqual(set(files),{"snapshots/shadowrocket/one.txt","snapshots/clash/one.txt"})
        self.assertEqual(set(lock["files"]),set(files))
        self.assertTrue(all(lock["files"][p]["sha256"]==e.digest(v) for p,v in files.items()))

    def setup_voice(self):
        fixture=Path(__file__).parent/"fixtures"
        spec={"id":"voice","format":"voice","policy":"PROXY"}
        _,old=e.parse_source(spec,(fixture/"voice-20261025.json").read_bytes())
        rules,new=e.parse_source(spec,(fixture/"voice-20261027.json").read_bytes())
        baseline={c:{"sources":{"voice":copy.deepcopy(old)},"allowed_conflicts":[]}
                  for c in ("shadowrocket","clash")}
        previous={c:{"voice":copy.deepcopy(old)} for c in ("shadowrocket","clash")}
        return rules,new,baseline,previous

    def test_quantity_report_is_exact_and_never_changes_active_baseline(self):
        rules,new,baseline,previous=self.setup_voice()
        before=copy.deepcopy(baseline)
        conf={"limits":core.read_json("sources.yaml")["limits"]}
        with patch.object(core,"assemble",return_value=(rules,{"voice":new},[],0,{})), patch.object(core,"evaluate"), patch.object(core,"check_source_parity"):
            reports,states,proposed,gates=d.analyze(conf,{},previous,baseline)
        self.assertEqual(baseline,before)
        self.assertEqual(len(gates),2)
        self.assertEqual(len(reports["clash"]["changes"][0]["added"]),252)
        self.assertEqual(reports["clash"]["changes"][0]["removed"],[])
        self.assertEqual(proposed["clash"]["sources"]["voice"]["raw_count"],402)
        self.assertEqual(proposed["clash"]["sources"]["voice"]["reviewed_transition"]["to_sha256"],core.source_state_sha256(new))
        self.assertEqual(proposed["clash"]["allowed_conflicts"],[])
        with self.assertRaises(e.BuildError):
            e.check_limits({"voice":new},baseline["clash"]["sources"],conf["limits"])

    def test_unknown_syntax_conflict_and_routing_failure_cannot_propose(self):
        rules,new,baseline,previous=self.setup_voice()
        conf={"limits":core.read_json("sources.yaml")["limits"]}
        for target in ("assemble","evaluate"):
            with patch.object(core,"assemble",return_value=(rules,{"voice":new},[],0,{})), patch.object(core,"evaluate"), patch.object(core,target,side_effect=e.BuildError("Unknown semantic")), self.assertRaises(e.BuildError):
                d.analyze(conf,{},previous,baseline)
        with patch.object(core,"assemble",return_value=(rules,{"voice":new},[],0,{})), patch.object(e,"check_conflicts",side_effect=e.BuildError("Unknown conflict")), self.assertRaises(e.BuildError):
            d.analyze(conf,{},previous,baseline)

    def test_partial_snapshot_hashes_and_errors_do_not_leak_exception_text(self):
        conf={"sources":[{"id":"voice"}],"repositories":{}}
        data=b"public-source";name="snapshots/shadowrocket/voice.txt"
        context={"conf":conf,"inputs":{name:data},
                 "lock":{"files":{name:{"sha256":e.digest(data)}}}}
        env={"GITHUB_SHA":"a"*40,"GITHUB_RUN_ID":"1"}
        with tempfile.TemporaryDirectory() as td,patch.dict(os.environ,env):
            out=Path(td)/"evidence"
            report=d.evidence(Path(td)/"source",out,context,error=ValueError("private-placeholder"))
            self.assertFalse(report["candidate_eligible"])
            self.assertFalse(report["complete_snapshot"])
            self.assertNotIn("private-placeholder",(out/"report.json").read_text())
            self.assertFalse((out/"SHA256SUMS").exists())
            # The verifier rejects unconfigured or altered payloads.
            (out/"snapshot"/name).write_bytes(b"tampered")
            with self.assertRaises(ValueError):review.verified_evidence(out)

    def test_empty_failure_evidence_and_duplicate_or_unsafe_paths(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ,{"GITHUB_SHA":"a"*40,"GITHUB_RUN_ID":"1"}):
            p=Path(td)/"evidence"
            d.evidence(Path(td)/"source",p,{},error=ValueError())
            report,_=review.verified_evidence(p)
            self.assertFalse(report["candidate_eligible"])
            line=(p/"EVIDENCE_SHA256SUMS").read_text()
            for bad in [line+line,"0"*64+"  ../outside\n"]:
                (p/"EVIDENCE_SHA256SUMS").write_text(bad)
                with self.assertRaises(ValueError):review.verified_evidence(p)


class CandidateTests(unittest.TestCase):
    def candidate(self):
        report={"fingerprint":"f"*64,"previous_release":"b"*40}
        files={p:e.json_bytes({}) for p in ("report.json","source-states.json","proposed-baseline.json")}
        return report,files

    def test_only_review_documents_are_written_no_merge_or_baseline(self):
        report,files=self.candidate();calls=[]
        def api(path,payload=None,method=None,missing_ok=False):
            calls.append((path,payload,method))
            if path.startswith("pulls?"):return []
            if path.startswith("git/ref/heads/review/"):return None
            if path.startswith("git/commits/"):return {"tree":{"sha":"t"*40}}
            if path=="git/trees":return {"sha":"t"*40}
            if path=="git/commits":return {"sha":"c"*40}
            if path=="pulls":return {"draft":True,"head":{"sha":"c"*40},"html_url":"https://github.com/test/pull/1"}
            return {}
        with patch.object(review,"identity",return_value=("a"*40,"run")),patch.object(review,"validate_proposal"),patch.object(review,"release_sha",return_value="b"*40),patch.object(review,"api",side_effect=api):
            review.propose(report,files)
        tree=next(payload for path,payload,_ in calls if path=="git/trees")
        self.assertEqual(len(tree["tree"]),4)
        self.assertTrue(all(x["path"].startswith("reviews/upstream/") for x in tree["tree"]))
        self.assertFalse(any("/merge" in path or "/reviews" in path for path,_,_ in calls))
        self.assertTrue(next(payload for path,payload,_ in calls if path=="pulls")["draft"])

    def test_existing_open_or_closed_candidate_is_not_duplicated(self):
        for state in ("open","closed"):
            report,files=self.candidate()
            with patch.object(review,"identity",return_value=("a"*40,"run")),patch.object(review,"validate_proposal"),patch.object(review,"api",return_value=[{"state":state,"html_url":"existing"}]) as api:
                self.assertEqual(review.propose(report,files),"existing")
                api.assert_called_once()

    def test_stale_release_or_changed_main_cannot_create_candidate(self):
        report,files=self.candidate()
        with patch.object(review,"identity",side_effect=ValueError("Main advanced")),patch.object(review,"api") as api,self.assertRaises(ValueError):
            review.propose(report,files)
        api.assert_not_called()
        report.update(status="failure",category="quantity_review",candidate_eligible=True,complete_snapshot=True)
        with patch.object(review,"release_sha",return_value="c"*40),self.assertRaises(ValueError):
            review.validate_proposal(report,files)

if __name__=="__main__":unittest.main()

