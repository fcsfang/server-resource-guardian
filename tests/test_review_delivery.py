from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo" / "guardian-beszel-review" / "index.html"
ONE_PAGE = ROOT / "docs" / "24-guardian-effectiveness-one-page.md"
ROADMAP = ROOT / "docs" / "25-leader-review-delivery-roadmap.md"
RUNBOOK = ROOT / "docs" / "26-guardian-beszel-live-demo-runbook.md"
SERVE_SCRIPT = ROOT / "scripts" / "serve-review-demo.sh"


class ReviewDeliveryTests(unittest.TestCase):
    def test_delivery_artifacts_exist(self):
        for path in (DEMO, ONE_PAGE, ROADMAP, RUNBOOK, SERVE_SCRIPT):
            self.assertTrue(path.is_file(), path)

    def test_demo_is_offline_and_read_only(self):
        html = DEMO.read_text(encoding="utf-8")
        self.assertIn("LOCAL FIXTURE · NO CREDENTIALS · NO EXECUTOR", html)
        self.assertIn("GLOBAL OOM", html)
        self.assertIn("plan.execution", html)
        self.assertIn("not_executed", html)
        self.assertNotIn("fetch(", html)
        self.assertNotIn("XMLHttpRequest", html)
        self.assertNotIn("docker restart", html.lower())
        self.assertNotIn("systemctl restart", html.lower())

    def test_review_materials_keep_effect_and_integration_separate(self):
        one_page = ONE_PAGE.read_text(encoding="utf-8")
        self.assertIn("Guardian 效果指标", one_page)
        self.assertIn("Beszel 集成效果指标", one_page)
        self.assertIn("EXP-021", one_page)
        self.assertIn("EXP-028", one_page)

    def test_runbook_preserves_local_safety_boundary(self):
        runbook = RUNBOOK.read_text(encoding="utf-8")
        self.assertIn("不连接生产", runbook)
        self.assertIn("不读取 `.env`", runbook)
        self.assertIn("不调用 Docker/systemd 变更接口", runbook)
        self.assertIn("66 tests", runbook)

    def test_delivery_script_uses_local_demo_directory(self):
        script = SERVE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("http.server", script)
        self.assertIn("demo/guardian-beszel-review", script)
        self.assertNotIn(".env", script)


if __name__ == "__main__":
    unittest.main()
