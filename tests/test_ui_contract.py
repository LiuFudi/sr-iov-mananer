# SPDX-License-Identifier: GPL-3.0-or-later

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(path):
    with open(os.path.join(ROOT, path), "r", encoding="utf-8") as handle:
        return handle.read()


class UiContractTest(unittest.TestCase):
    def test_static_text_has_no_editor_entry(self):
        html = read("app/ui/index.html")
        css = read("app/ui/style.css")
        self.assertNotIn("contenteditable", html.lower())
        self.assertIsNone(re.search(r"<textarea\b", html, re.I))
        inputs = re.findall(r'<input[^>]+type=["\']text[^>]*>', html, re.I)
        self.assertEqual(len(inputs), 1)
        self.assertIn('id="vm-alias-input"', inputs[0])
        self.assertIn("caret-color: transparent", css)

    def test_donation_entry_and_local_qr_module_exist(self):
        html = read("app/ui/index.html")
        config = read("app/ui/donate.json")
        module = read("app/ui/donate-qr.js")
        self.assertIn('id="donate-button"', html)
        self.assertIn('id="donate-modal"', html)
        self.assertIn('"enabled": true', config)
        self.assertIn("data:image/jpeg", module)
        self.assertIn("window.__SRIOV_DONATE_QR__", module)

    def test_vm_usage_is_presented_as_info_not_error(self):
        app_js = read("app/ui/app.js")
        self.assertIn('response.kind === "in_use"', app_js)
        self.assertIn('showToast(response.message || "VF 正被虚拟机使用", "info")', app_js)
        self.assertIn('status === "info"', app_js)

    def test_vm_alias_entry_and_api_are_wired(self):
        html = read("app/ui/index.html")
        app_js = read("app/ui/app.js")
        server_py = read("app/server/sriovd.py")
        self.assertIn('id="rename-modal"', html)
        self.assertIn('id="vm-alias-input"', html)
        self.assertIn('post("api/vm-alias"', app_js)
        self.assertIn('path == "/api/vm-alias"', server_py)

    def test_main_cards_use_restored_grid_and_balanced_minimum_height(self):
        css = read("app/ui/style.css")
        self.assertIn("grid-template-columns: minmax(0, 1.15fr) minmax(390px, .85fr);", css)
        self.assertIn(".dashboard-grid > .panel { min-height: 160px; }", css)
        self.assertIn(".content-grid > .panel { min-height: 240px; }", css)

    def test_mobile_quantity_header_stepper_and_card_gap_are_compact(self):
        css = read("app/ui/style.css")
        self.assertIn(".manage-panel .panel-head { flex-wrap: nowrap; gap: 10px; }", css)
        self.assertIn(".manage-panel .panel-head > div { min-width: 0; }", css)
        self.assertIn("grid-template-columns: 36px minmax(0, 1fr) 36px;", css)
        self.assertIn(".stepper-btn, .stepper-value { height: 100%; min-height: 0; }", css)
        self.assertNotIn(".stepper-btn, .stepper-value { min-height: 66px; }", css)
        self.assertIn(".device-list { grid-template-columns: repeat(2, minmax(0, 140px)); gap: 6px; }", css)
        self.assertIn(".device-card { aspect-ratio: auto; min-height: 140px; }", css)

    def test_device_card_internal_gap_is_compact_on_both_breakpoints(self):
        css = read("app/ui/style.css")
        card = css.split(".device-card {", 1)[1].split("}", 1)[0]
        self.assertIn("justify-content: flex-start;", card)
        self.assertIn("gap: 12px;", card)
        self.assertIn(".device-card .device-meta { flex-direction: column; gap: 4px; margin: 0; }", css)
        self.assertIn("margin-top: 0;", css.split(".vm-tile-foot {", 1)[1].split("}", 1)[0])

    def test_empty_state_fills_its_list_card_without_changing_device_grid(self):
        css = read("app/ui/style.css")
        app_js = read("app/ui/app.js")
        self.assertIn(".content-grid > .list-panel.is-empty { min-height: 0; }", css)
        self.assertIn(".device-list.is-empty {", css)
        self.assertIn("grid-template-columns: minmax(0, 1fr);", css)
        self.assertIn("align-content: stretch;", css)
        self.assertIn(
            ".device-list .empty-state {\n  grid-column: 1 / -1;\n  width: 100%;\n  height: 100%;\n  min-height: 120px;\n  padding: 20px 16px;\n  overflow-wrap: anywhere;\n}",
            css,
        )
        self.assertIn('vfList.classList.toggle("is-empty", vfEmpty);', app_js)
        self.assertIn('vmList.classList.toggle("is-empty", vmEmpty);', app_js)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 140px));", css)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 140px)); gap: 6px;", css)

    def test_mobile_empty_states_use_consistent_compact_spacing(self):
        css = read("app/ui/style.css")
        empty_state = css.split("\n.device-list .empty-state {", 1)[1].split("}", 1)[0]
        self.assertIn("padding: 20px 16px;", empty_state)
        mobile = css.split("@media (max-width: 620px)", 1)[1].split("@media (max-width: 380px)", 1)[0]
        self.assertIn(".device-list .empty-state { min-height: 96px; padding: 16px 14px; }", mobile)

    def test_version_is_aligned_in_summary_header_without_legacy_actions(self):
        html = read("app/ui/index.html")
        css = read("app/ui/style.css")
        summary_start = html.index('<div class="summary-main">')
        summary_end = html.index("<h2", summary_start)
        self.assertIn('id="version-text"', html[summary_start:summary_end])
        self.assertNotIn('class="summary-top"', html)
        self.assertNotIn('class="summary-actions"', html)
        self.assertNotIn('id="diagnostics-button"', html)
        self.assertIn("grid-template-columns: minmax(0, 1fr) auto;", css)
        self.assertIn("grid-column: 1; grid-row: 1;", css)
        self.assertIn("grid-column: 1 / -1; grid-row: 2;", css)

    def test_summary_state_dot_and_duplicate_diagnostic_interaction_are_removed(self):
        html = read("app/ui/index.html")
        css = read("app/ui/style.css")
        app_js = read("app/ui/app.js")
        self.assertNotIn('id="summary-state"', html)
        self.assertNotIn('id="summary-dot"', html)
        self.assertNotIn('id="summary-eyebrow"', html)
        self.assertNotIn(".summary-state", css)
        self.assertNotIn('"summary-state"', app_js)
        self.assertNotIn('"summary-dot"', app_js)
        self.assertIn('id="health-button"', html)
        self.assertIn('$("health-button").addEventListener("click"', app_js)

    def test_vf_and_vm_pagination_controls_exist(self):
        html = read("app/ui/index.html")
        app_js = read("app/ui/app.js")
        for key in ("vf", "vm"):
            self.assertIn('id="%s-pager"' % key, html)
            self.assertIn('id="%s-dots"' % key, html)
            self.assertNotIn('id="%s-prev"' % key, html)
            self.assertNotIn('id="%s-next"' % key, html)
            self.assertNotIn('id="%s-total"' % key, html)
        self.assertIn("var DESKTOP_PAGE_SIZE = 3;", app_js)
        self.assertIn("var MOBILE_PAGE_SIZE = 2;", app_js)
        self.assertIn("function paginate(items, key)", app_js)
        self.assertIn('dot.className = "pager-dot"', app_js)

    def test_vm_card_text_cannot_overlap_controls(self):
        css = read("app/ui/style.css")
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 140px));", css)
        self.assertIn("grid-template-columns: minmax(0, 1fr) 26px;", css)
        self.assertIn(".vm-tile-head > div { min-width: 0; }", css)
        self.assertIn(".vm-tile-foot {", css)
        self.assertIn("flex-direction: column", css)
        self.assertIn(".vm-device-count {", css)
        self.assertIn("overflow-wrap: anywhere", css)
        self.assertIn("overflow: hidden", css)

    def test_vf_save_is_inline_and_blocked_when_vm_is_using_vf(self):
        html = read("app/ui/index.html")
        app_js = read("app/ui/app.js")
        server_py = read("app/server/sriovd.py")
        count_start = html.index('<div class="count-layout">')
        count_end = html.index('<div class="secondary-actions">', count_start)
        count_layout = html[count_start:count_end]
        self.assertIn('id="apply-main"', count_layout)
        self.assertIn('blockedByUse', app_js)
        self.assertIn('actionLabel = "占用中不可保存"', app_js)
        self.assertGreaterEqual(server_py.count('"kind": "in_use"'), 3)

    def test_static_assets_are_cache_busted_for_release(self):
        html = read("app/ui/index.html")
        self.assertIn("style.css?v=1.1.7", html)
        self.assertIn("app.js?v=1.1.7", html)
        self.assertIn("donate-qr.js?v=1.1.7", html)

    def test_vf_count_wording_and_removed_duplicate_tips(self):
        html = read("app/ui/index.html")
        app_js = read("app/ui/app.js")
        expected = "修改VF数量，需要先关闭使用VF的虚拟机。修改后立即生效，下次重启即可应用。"
        self.assertIn(expected, html)
        self.assertNotIn("个 VF", html)
        self.assertNotIn(".stepper-value span", read("app/ui/style.css"))
        self.assertNotIn("change-preview", html)
        self.assertNotIn("action-note", html)
        self.assertNotIn("change-preview", app_js)
        self.assertNotIn("action-note", app_js)

    def test_metric_text_wraps_without_truncation(self):
        css = read("app/ui/style.css")
        self.assertIn(".summary-main > p,\n.metrics dd,\n.metrics span {", css)
        self.assertIn("white-space: normal", css)
        self.assertIn("overflow: visible", css)
        self.assertIn("text-overflow: clip", css)

    def test_release_version_is_formal_semver_1_1_7(self):
        manifest = read("manifest")
        code = read("app/server/sriovlib.py")
        changelog = read("CHANGELOG.md")
        version = next(line.split("=", 1)[1].strip() for line in manifest.splitlines()
                       if line.startswith("version"))
        self.assertEqual(version, "1.1.7")
        self.assertRegex(manifest, r"(?m)^version\s+= 1\.1\.7$")
        self.assertIn("os_min_version        = 1.2.0800", manifest)
        self.assertIn('VERSION = "1.1.7"', code)
        self.assertTrue(changelog.startswith("# 变更记录\n\n## [1.1.7]"))
        self.assertNotIn("-", version)

    def test_english_and_chinese_names_are_aligned(self):
        manifest = read("manifest")
        html = read("app/ui/index.html")
        config = read("app/ui/config")
        english_readme = read("README.en.md")
        self.assertIn("display_name          = SR-IOV 管理", manifest)
        self.assertIn("display_name_en       = SR-IOV Manager", manifest)
        self.assertIn("SR-IOV Manager · Intel i915 虚拟功能管理器", html)
        self.assertIn('"title": "SR-IOV 管理"', config)
        self.assertTrue(english_readme.startswith("# SR-IOV Manager\n"))

    def test_install_flow_has_no_declaration_or_agreement_step(self):
        build = read("build-fpk.sh")
        self.assertFalse(os.path.exists(os.path.join(ROOT, "wizard")))
        install_init = read("cmd/install_init")
        for word in ("安装声明", "用户协议", "同意并", "接受条款"):
            self.assertNotIn(word, install_init)
        self.assertIn("grep -v '^LICENSE$'", build)
        self.assertIn("FPK 顶层不得包含 LICENSE", build)

    def test_check_icon_uses_explicit_centering(self):
        css = read("app/ui/style.css")
        block = css.split(".check-icon {", 1)[1].split("}", 1)[0]
        self.assertIn("place-items: center", block)
        self.assertIn("align-content: center", block)
        self.assertIn("line-height: 0", block)

    def test_manifest_donation_statement_has_no_ascii_semicolon(self):
        manifest = read("manifest")
        desc = next(line for line in manifest.splitlines() if line.startswith("desc "))
        self.assertIn("打赏入口纯属自愿", desc)
        self.assertIn("与飞牛官方无关", desc)
        self.assertNotIn(";", desc)


if __name__ == "__main__":
    unittest.main()
