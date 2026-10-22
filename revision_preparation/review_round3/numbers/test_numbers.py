"""Provenance/rounding failures and adversarial LaTeX numeral fixtures."""
import csv
from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0,str(Path(__file__).resolve().parent))
import build_values as build
import number_audit as audit


class NumericTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root/"source.csv"
        self.source.write_text("report,value,n\nAR5,0.12265,94\nAR6,0.04755,319\n")
        self.spec = self.root/"claims.json"
        self.spec.write_text(json.dumps({"claims":[{"id":"retention","source":{"path":"source.csv","filters":{"report":"AR5"},"column":"value"},"format":"percent2"}]}))
        self.support = self.root/"private"
        self.registry = self.root/"registry"

    def tearDown(self):
        self.temp.cleanup()

    def build(self):
        build.run(self.spec,self.support,self.registry,repo=self.root)

    def test_source_cell_selection_and_decimal_rounding(self):
        _,trace,_,tex,_ = build.build(self.spec,self.root)
        self.assertEqual(trace[0]["row_number_1based"],1)
        self.assertEqual(trace[0]["formatted_value"],"12.26")
        self.assertIn(b"RThreeValue@retention",tex)
        self.assertEqual(build.format_value("0.12275","percent2")[0],"12.28")
        self.assertEqual(build.format_value("2.675","fixed2")[0],"2.68")
        self.assertEqual(build.format_value("3.299216e-12","sci2")[0],r"\ensuremath{3.30\times10^{-12}}")

    def test_four_five_decimal_formats_preserve_small_HHI_and_window_means(self):
        self.assertEqual(build.format_value("0.03249","fixed5")[0],"0.03249")
        self.assertEqual(build.format_value("0.01054","fixed5")[0],"0.01054")
        self.assertEqual(build.format_value("4.452604299740847","fixed4")[0],"4.4526")
        self.assertEqual(build.format_value("4.453424461119343","fixed4")[0],"4.4534")

    def test_ambiguous_or_missing_csv_selection_fails(self):
        spec = json.loads(self.spec.read_text())
        spec["claims"][0]["source"]["filters"] = {}
        self.spec.write_text(json.dumps(spec))
        with self.assertRaisesRegex(ValueError,"exactly one row"):
            build.build(self.spec,self.root)
        spec["claims"][0]["source"]["filters"] = {"report":"AR7"}
        self.spec.write_text(json.dumps(spec))
        with self.assertRaisesRegex(ValueError,"got 0"):
            build.build(self.spec,self.root)

    def test_hash_mismatch_or_source_mutation_fails(self):
        self.build()
        self.source.write_text(self.source.read_text().replace("0.12265","0.2"))
        tex = self.root/"paper.tex"
        tex.write_text(r"\begin{document}\Rthree{retention}\end{document}")
        with self.assertRaisesRegex(ValueError,"hashes differ"):
            audit.audit(tex,self.spec,self.support,self.registry,self.root/"report",repo=self.root)

    def test_tampered_display_macro_fails_even_if_value_exists_elsewhere(self):
        self.build()
        values_file = self.support/"values.tex"
        values_file.write_text(values_file.read_text().replace("12.26","94"))
        tex = self.root/"paper.tex"
        tex.write_text(r"\begin{document}\Rthree{retention}\end{document}")
        with self.assertRaisesRegex(ValueError,"displayed rounding"):
            audit.audit(tex,self.spec,self.support,self.registry,self.root/"report",repo=self.root)

    def test_literal_numeric_coincidence_is_not_provenance(self):
        self.build()
        tex = self.root/"paper.tex"
        tex.write_text(r"\begin{document}Matched retention was 12.26\%.\end{document}")
        report = audit.audit(tex,self.spec,self.support,self.registry,self.root/"report",repo=self.root)
        self.assertFalse(report["passed"])
        self.assertEqual(report["untraced_numeric_tokens"],1)

    def test_abstract_tables_and_appendix_all_audited(self):
        text = r"\begin{document}\abstract{Ages 4.2.}\begin{tabular}{p{.2\linewidth}r}AR5 & 12.27\\\end{tabular}\appendix n=94.\end{document}"
        _,untraced,_ = audit.mask_manuscript(text,{},[])
        self.assertEqual([r["literal"] for r in untraced],["4.2","12.27","94"])

    def test_metadata_reference_url_and_formatting_exclusions(self):
        text = r"\documentclass[12pt]{sn-jnl}\begin{document}\author[1]{Name}\affil[1]{92 Road, 300072}\email{name2026@uni.edu}\citep{study2023}\ref{tab:3}\url{https://a.test/12}\includegraphics[width=.7\linewidth]{figure2.pdf}AR5 and WG1; $D_2$; v1.4.0.\end{document}"
        _,untraced,exclusions = audit.mask_manuscript(text,{},[])
        self.assertEqual(untraced,[])
        self.assertTrue(any(r["category"] == "author_affiliation_email_date_metadata" for r in exclusions))

    def test_comments_respect_escaped_percent_and_newlines(self):
        text = "\\begin{document}\n% ignore 123\n12.27\\% % ignore 999\n94\n\\end{document}"
        _,untraced,_ = audit.mask_manuscript(text,{},[])
        self.assertEqual([(r["literal"],r["line"]) for r in untraced],[("12.27",3),("94",4)])

    def test_exact_structural_exclusion_documented_and_required_to_match(self):
        structural = [{"text":r"$x_1+x_2=1$","explanation":"Algebraic normalization identity"}]
        _,untraced,exclusions = audit.mask_manuscript(r"\begin{document}$x_1+x_2=1$ n=2\end{document}",{},structural)
        self.assertEqual([r["literal"] for r in untraced],["2"])
        self.assertTrue(any(r["category"] == "explicit_structural_identity" for r in exclusions))

    def test_no_automatic_pass_for_untraced_large_counts_or_cutoff_dates(self):
        _,untraced,_ = audit.mask_manuscript(r"\begin{document}12345678 works, cutoff 2013-03-15.\end{document}",{},[])
        self.assertIn("12345678",[r["literal"] for r in untraced])
        self.assertIn("2013",[r["literal"] for r in untraced])

    def test_embedded_years_and_leading_decimal_not_silently_skipped(self):
        _,untraced,_ = audit.mask_manuscript(r"\begin{document}2013a; .025; n_2.\end{document}",{},[])
        self.assertEqual([r["literal"] for r in untraced],["2013",".025","2"])

    def test_json_pointer_and_compressed_csv(self):
        import gzip
        (self.root/"a.json").write_text('{"sample":{"n":50}}')
        (self.root/"b.csv.gz").write_bytes(gzip.compress(b"key,n\nx,3\n",mtime=0))
        for source,expected in (({"path":"a.json","json_pointer":"/sample/n"},"50"),
                                ({"path":"b.csv.gz","filters":{"key":"x"},"column":"n"},"3")):
            raw,trace = build.source_value(source,self.root,{})
            self.assertEqual(raw,expected)
            self.assertEqual(len(trace["source_sha256"]),64)

    def test_design_constants_require_explanation_and_protocol(self):
        self.spec.write_text(json.dumps({"claims":[],"design_constants":[{"id":"seed","value":20261011,"format":"integer"}]}))
        with self.assertRaisesRegex(ValueError,"protocol_reference"):
            build.build(self.spec,self.root)
        spec = json.loads(self.spec.read_text())
        spec["design_constants"][0].update(explanation="Fixed random seed",protocol_reference="User protocol")
        self.spec.write_text(json.dumps(spec))
        _,trace,files,_,_ = build.build(self.spec,self.root)
        self.assertEqual(trace[0]["kind"],"declared_design_constant")
        self.assertIn(b"20261011",files["design_constants.csv"])

    def test_source_escape_and_nonintegral_integer_forbidden(self):
        with self.assertRaisesRegex(ValueError,"inside the repository"):
            build.source_value({"path":"../escape.csv","filters":{},"column":"n"},self.root,{})
        with self.assertRaisesRegex(ValueError,"Noninteger"):
            build.format_value("1.1","integer")

    def test_valid_macro_passes_and_undefined_macro_fails(self):
        self.build()
        tex = self.root/"paper.tex"
        tex.write_text(r"\begin{document}Retention \Rthree{retention}\%.\end{document}")
        report = audit.audit(tex,self.spec,self.support,self.registry,self.root/"report",repo=self.root)
        self.assertTrue(report["passed"])
        self.assertEqual(report["macro_usages"],1)
        tex.write_text(r"\begin{document}\Rthree{notdefined}\end{document}")
        with self.assertRaisesRegex(ValueError,"Undefined"):
            audit.audit(tex,self.spec,self.support,self.registry,self.root/"report2",repo=self.root)

    def test_external_appendix_audited_and_values_input_validated(self):
        self.build()
        (self.root/"appendix.tex").write_text("Untraced sample 94.")
        tex = self.root/"paper.tex"
        tex.write_text(r"\begin{document}\input{private/values.tex}\Rthree{retention}\input{appendix}\end{document}")
        report = audit.audit(tex,self.spec,self.support,self.registry,self.root/"report",repo=self.root)
        self.assertEqual(report["untraced_numeric_tokens"],1)
        self.assertEqual(len(report["manuscript_sources_audited"]),2)

    def test_unused_structural_exclusion_and_cyclic_input_fail(self):
        data = json.loads(self.spec.read_text())
        data["structural_exclusions"] = [{"text":"not present 123","explanation":"fixture"}]
        self.spec.write_text(json.dumps(data))
        self.build()
        tex = self.root/"paper.tex"
        tex.write_text(r"\begin{document}\Rthree{retention}\end{document}")
        with self.assertRaisesRegex(ValueError,"did not match"):
            audit.audit(tex,self.spec,self.support,self.registry,self.root/"report",repo=self.root)
        tex.write_text(r"\input{paper.tex}")
        with self.assertRaisesRegex(ValueError,"Cyclic"):
            audit.included_manuscripts(tex,self.support/"values.tex")


if __name__ == "__main__":
    unittest.main()
