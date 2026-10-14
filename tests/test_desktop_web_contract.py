"""Trust-boundary checks for the legacy desktop/handheld web shell."""

from pathlib import Path
import re
import unittest


HTML_PATH = Path(__file__).parents[1] / "pythia" / "ui" / "desktop" / "web" / "index.html"


class DesktopWebContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = HTML_PATH.read_text(encoding="utf-8")

    def test_no_developer_machine_or_sample_identity_is_shown(self):
        forbidden = (
            r"C:\\Users\\Mech",
            "38f19d7a-8c22-45e6-a8c1-66d21a10c1d9",
            "Desktop online · 1 task",
            "Windows x86_64 · online",
        )
        for value in forbidden:
            with self.subTest(value=value):
                self.assertNotIn(value, self.html)

        self.assertIn("window.pythia.get_system_info", self.html)
        self.assertIn("id=\"localSystemId\"", self.html)
        self.assertIn("systemId.value=info.system_uuid", self.html)
        self.assertNotRegex(self.html, r"(?i)pass\s*[0-9]|final mockup")
        self.assertNotIn("16 games · 3 systems · 9 need attention", self.html)
        self.assertIn('id="librarySummary">Loading library…', self.html)
        self.assertIn('id="sidebarSystemStats">Loading local system…', self.html)
        self.assertNotIn("<option>Steam</option>", self.html)
        self.assertNotIn("<option>F95Checker</option>", self.html)

    def test_f95_session_controls_are_real_and_failure_aware(self):
        self.assertIn('data-action="clear-f95"', self.html)
        self.assertIn("window.pythia.clear_f95_session", self.html)
        self.assertIn("F95zone session save failed", self.html)

    def test_download_host_login_reports_persistence_result(self):
        self.assertIn("window.pythia.downloadHostLoginComplete.connect", self.html)
        self.assertIn("Host session save failed", self.html)

    def test_help_links_require_real_backend_success(self):
        self.assertIn("result&&result.status==='ok'?'Issue tracker opened'", self.html)
        self.assertIn("result&&result.status==='ok'?'Documentation opened'", self.html)
        self.assertNotIn("opened=>toast(opened?'Issue tracker opened'", self.html)
        self.assertNotIn("opened=>toast(opened?'Documentation opened'", self.html)

    def test_reduce_motion_is_accessible_persistent_and_system_aware(self):
        self.assertIn('id="reduceMotionToggle" role="switch" aria-checked="false"', self.html)
        self.assertIn("function applyReducedMotion(enabled,persist=true)", self.html)
        self.assertIn("localStorage.setItem('pythia-reduce-motion',String(reduced))", self.html)
        self.assertIn("window.matchMedia?.('(prefers-reduced-motion: reduce)').matches", self.html)
        self.assertIn("html.reduce-motion *", self.html)
        self.assertIn("@media(prefers-reduced-motion:reduce)", self.html)
        self.assertNotIn("<strong>Reduce motion</strong><small>Not implemented yet.</small>", self.html)

    def test_desktop_density_is_persistent_and_shared_across_controls(self):
        self.assertIn('id="desktopDensitySelect"', self.html)
        self.assertIn("function applyDensity(value,persist=true)", self.html)
        self.assertIn("localStorage.setItem('pythia-desktop-density',next)", self.html)
        self.assertIn("applyDensity(localStorage.getItem('pythia-desktop-density'),false)", self.html)
        self.assertIn("const valid=['compact','comfortable','spacious']", self.html)
        self.assertIn("$('#desktopDensitySelect').onchange=e=>applyDensity(e.target.value)", self.html)
        self.assertNotIn("<strong>Desktop density</strong><small>Use the density control in the Library toolbar.</small>", self.html)

    def test_manager_handoff_waits_for_real_completion(self):
        self.assertIn("window.pythia.managerOpenComplete.connect", self.html)
        self.assertIn("Manager hand-off complete", self.html)
        self.assertIn("Manager hand-off failed", self.html)
        self.assertNotIn("was handed to", self.html)

    def test_launch_mode_only_claims_saved_after_backend_success(self):
        self.assertIn("set_launch_mode(requested,result=>", self.html)
        self.assertIn("result&&result.status==='ok'", self.html)
        self.assertIn("Setting not saved", self.html)
        self.assertIn("window.pythia.get_launch_mode(mode=>", self.html)

    def test_saved_searches_only_claim_success_after_persistence(self):
        self.assertIn("save_search(pendingName.trim(),state.search,result=>", self.html)
        self.assertIn("remove_saved_search(b.dataset.removeSearch,result=>", self.html)
        self.assertIn("Search not saved", self.html)
        self.assertIn("Search not removed", self.html)

    def test_source_refresh_distinguishes_partial_from_success(self):
        self.assertIn("Sources partially refreshed", self.html)
        self.assertIn("status==='partial'", self.html)

    def test_update_check_distinguishes_error_partial_and_success(self):
        self.assertIn("Update check partially complete", self.html)
        self.assertIn("Update check failed", self.html)
        self.assertIn("completeJob(currentUpdateJobId,status==='ok'?'done':'error')", self.html)

    def test_bulk_rescan_is_exposed_and_selected_reconcile_stays_filtered(self):
        self.assertIn('data-bulk-action="rescan"', self.html)
        self.assertIn("mergeCandidates=result.candidates||[]", self.html)
        self.assertIn("navigate('reconcile');renderReconcile()", self.html)
        self.assertNotIn("if(a==='reconcile')requestMergeCandidates()", self.html)

    def test_bulk_export_uses_atomic_backend_file_writer(self):
        self.assertIn("window.pythia?.pick_entries_export_file", self.html)
        self.assertIn("window.pythia.export_entries_to_file(ids,path,result=>", self.html)
        self.assertNotIn("new Blob([JSON.stringify(result.entries", self.html)

    def test_account_sync_completion_reports_real_result(self):
        self.assertIn("Account sync partially complete", self.html)
        self.assertIn("Account sync failed", self.html)
        self.assertIn("result.status==='ok'||result.status==='partial'", self.html)

    def test_f95checker_database_import_is_reachable(self):
        self.assertIn('data-action="import-f95"', self.html)
        self.assertIn("window.pythia?.pick_f95_database_file", self.html)
        self.assertIn("window.pythia.import_f95(path)", self.html)

    def test_download_browser_fallback_requires_real_open_success(self):
        self.assertIn("const ok=opened&&opened.status==='ok'", self.html)
        self.assertIn("ok?'Browser fallback opened':'Download unavailable'", self.html)
        self.assertIn("Neither Download Automator nor the browser could open this link.", self.html)
        self.assertNotIn("opened=>toast('Browser fallback'", self.html)

    def test_notes_backend_slot_exposes_callback_result(self):
        backend = (Path(__file__).parents[1] / "pythia" / "ui" / "backend.py").read_text(encoding="utf-8")
        self.assertIn('@Slot(str, str, result="QVariant")\n    def save_notes', backend)
        self.assertIn("if(result&&result.status==='ok')toast('Note saved'", self.html)

    def test_reconciliation_discovery_failure_has_distinct_ui_state(self):
        self.assertIn("window.pythia.mergeCandidatesFailed.connect", self.html)
        self.assertIn("mergeCandidatesError", self.html)
        self.assertIn("Duplicate search failed", self.html)

    def test_plugin_configuration_waits_for_persistence_result(self):
        self.assertIn("save_plugin_config(p.id,JSON.stringify(values),result=>", self.html)
        self.assertIn("Configuration not saved", self.html)
        self.assertNotIn("<option>Kirikiri</option>", self.html)
        self.assertIn("function populateFilterOptions()", self.html)
        self.assertIn("state.source=fill($('#sourceFilter')", self.html)
        self.assertIn("state.engine=fill($('#engineFilter')", self.html)

    def test_plugin_mutations_distinguish_completed_but_refresh_failed(self):
        self.assertIn("Plugin installed; refresh failed", self.html)
        self.assertIn("Plugin removed; refresh failed", self.html)
        self.assertNotIn("No backend removal hook exists yet", self.html)

    def test_unavailable_actions_are_not_clickable(self):
        for label in ("Review reset…", "Set up", "Apply unavailable"):
            with self.subTest(label=label):
                pattern = rf"<button[^>]*\bdisabled\b[^>]*>{re.escape(label)}</button>"
                self.assertRegex(self.html, pattern)

        self.assertNotIn("data-action=\"watch-folder\"", self.html)
        self.assertNotIn("data-hh-apply", self.html)
        self.assertNotIn("if(a==='save-settings')", self.html)
        self.assertNotIn("toast(title,'Prototype action completed", self.html)
        self.assertNotIn(".toggle:not([data-plugin])", self.html)
        self.assertIn("$$('[data-stub-field]',root).forEach", self.html)
        self.assertIn(".toggle:not([data-stub-field])", self.html)

    def test_real_maintenance_and_readiness_actions_are_wired(self):
        self.assertIn("data-action=\"rebuild-cache\"", self.html)
        self.assertGreaterEqual(self.html.count('data-action="rebuild-cache"'), 2)
        self.assertIn("data-hh-rebuild-cache", self.html)
        self.assertIn("window.pythia.rebuild_cache", self.html)
        self.assertIn("id=\"cfgCheckRequirements\"", self.html)
        self.assertIn("window.pythia.get_engine_status", self.html)
        self.assertIn("window.pythia.get_monitored_launches", self.html)
        self.assertIn("window.pythia.stop_monitored_launch", self.html)
        self.assertIn("window.pythia.clear_finished_launches", self.html)
        self.assertIn("window.setInterval(refreshProcesses,2000)", self.html)
        self.assertEqual(self.html.count("data-open-data-folder="), 3)
        self.assertIn("window.pythia.open_data_folder", self.html)
        self.assertIn('data-action="export-diagnostics"', self.html)
        self.assertIn("window.pythia.pick_diagnostics_file", self.html)
        self.assertIn("window.pythia.export_diagnostics_to_file", self.html)
        self.assertIn("data-hh-export-diagnostics", self.html)
        self.assertIn("()=>action('export-diagnostics')", self.html)
        self.assertIn('data-hh-open-data-folder="sync"', self.html)
        self.assertIn("dataset.hhOpenDataFolder", self.html)
        self.assertIn("data-hh-add", self.html)
        self.assertIn("$$('[data-hh-add]').forEach", self.html)
        self.assertIn("Conflict warning preference unavailable", self.html)
        self.assertIn("data-hh-plugin=", self.html)
        self.assertIn("data-hh-capability-plugin=", self.html)
        self.assertIn("window.pythia.set_plugin_enabled", self.html)
        self.assertIn("window.pythia.set_plugin_capability_enabled", self.html)
        self.assertNotIn('data-cap-plugin="${p.id}"', self.html)
        self.assertNotIn('data-cap-configure="${p.id}"', self.html)
        self.assertIn("data-host-clear=", self.html)
        self.assertIn("window.pythia.clear_download_host_session", self.html)

    def test_bulk_actions_report_partial_results_honestly(self):
        self.assertIn("result.status==='ok'||result.status==='partial'", self.html)
        self.assertIn("'Bulk action partially complete'", self.html)
        self.assertIn("errors=result.errors||0", self.html)

    def test_tools_dialog_does_not_fabricate_inventory_or_actions(self):
        self.assertIn("does not currently expose a standalone tool inventory", self.html)
        self.assertNotIn("0.20.34 · Windows x86_64", self.html)
        self.assertNotIn(">Check for update</button>", self.html)
        self.assertNotIn(">Download</button>", self.html)
        self.assertNotIn("0 backends configured", self.html)
        self.assertNotIn("No runtime versions installed yet", self.html)
        self.assertNotIn("＋ Install component", self.html)
        self.assertIn("Standalone runtime inventory unavailable", self.html)

    def test_unimplemented_bulk_actions_are_disabled(self):
        self.assertRegex(
            self.html,
            r'<button class="btn small" disabled[^>]*>Back up saves \(unavailable\)</button>',
        )
        self.assertRegex(
            self.html,
            r'<button class="btn small" disabled[^>]*>Management policy… \(unavailable\)</button>',
        )
        self.assertNotIn('data-bulk-action="backup"', self.html)
        self.assertNotIn('data-bulk-action="policy"', self.html)

    def test_unimplemented_launcher_and_save_actions_are_disabled(self):
        self.assertIn("Inventory unavailable", self.html)
        self.assertNotIn("<small>0 entries</small>", self.html)
        for label in (
            "Rescan launcher entries (unavailable)",
            "Back up now (unavailable)",
        ):
            with self.subTest(label=label):
                self.assertRegex(
                    self.html,
                    rf'<button[^>]*\bdisabled\b[^>]*>{re.escape(label)}</button>',
                )

    def test_unimplemented_copy_and_plan_branches_are_not_actionable(self):
        self.assertRegex(
            self.html,
            r'<button class="btn" disabled[^>]*>Create copy plan \(unavailable\)</button>',
        )
        self.assertNotIn("data-more-copy-plan", self.html)
        self.assertNotIn("if(a==='commit')", self.html)
        self.assertNotIn("if(a==='apply-projection')", self.html)

    def test_system_and_diagnostics_controls_do_not_fake_state(self):
        self.assertRegex(
            self.html,
            r'<button class="btn" disabled[^>]*>Add system \(use Devices view\)</button>',
        )
        self.assertNotIn("<span>v3</span>", self.html)
        self.assertIn("Managed automatically", self.html)
        self.assertIn("Runtime log-level editing is not implemented", self.html)
        self.assertNotIn("<option>Verbose</option>", self.html)
        self.assertNotIn("The transfer protocol is not selected yet", self.html)
        self.assertIn("Direct device transfer is a core capability", self.html)
        self.assertIn("Optional · off by default", self.html)
        self.assertIn("NAS / cloud staging", self.html)
        self.assertNotIn("See PLAN.md", self.html)


if __name__ == "__main__":
    unittest.main()
